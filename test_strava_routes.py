"""Recover Strava GPS omitted from summary activities without inventing routes."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent / "run_page"))

import polyline
from requests import Response
from strava_routes import resolve_activity_route
from stravalib.exc import AccessUnauthorized, Fault, ObjectNotFound, RateLimitExceeded

NANNING_POINTS = [
    (22.83203, 108.24882),
    (22.83180, 108.25020),
    (22.83150, 108.25210),
    (22.83120, 108.25400),
]
LIUZHOU_POINTS = [
    (24.40819, 109.53127),
    (24.40780, 109.53100),
    (24.40730, 109.53070),
    (24.40680, 109.53040),
]
NANNING_ROUTE = polyline.encode(NANNING_POINTS)
LIUZHOU_ROUTE = polyline.encode(LIUZHOU_POINTS)
SPARSE_ROUTE = polyline.encode(NANNING_POINTS[:2])


def make_activity(run_id=20142182471, summary=None, detail_polyline=None):
    return SimpleNamespace(
        id=run_id,
        map=SimpleNamespace(summary_polyline=summary, polyline=detail_polyline),
    )


def api_error(status):
    response = Response()
    response.status_code = status
    return Fault(f"HTTP {status}", response=response)


class StravaRouteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock(spec=["get_activity", "get_activity_streams"])
        self.client.get_activity.return_value = make_activity()
        self.client.get_activity_streams.return_value = {}

    def test_complete_summary_needs_no_additional_api_requests(self):
        activity = make_activity(summary=NANNING_ROUTE)

        self.assertEqual(resolve_activity_route(self.client, activity), NANNING_ROUTE)

        self.client.get_activity.assert_not_called()
        self.client.get_activity_streams.assert_not_called()

    def test_missing_summary_recovers_same_activity_detailed_polyline(self):
        activity = make_activity(summary="")
        self.client.get_activity.return_value = make_activity(
            detail_polyline=NANNING_ROUTE
        )

        recovered = resolve_activity_route(self.client, activity)

        self.assertEqual(polyline.decode(recovered), NANNING_POINTS)
        self.client.get_activity.assert_called_once_with(activity.id)
        self.client.get_activity_streams.assert_not_called()

    def test_sparse_summary_is_replaced_by_the_complete_detailed_route(self):
        activity = make_activity(summary=SPARSE_ROUTE)
        self.client.get_activity.return_value = make_activity(
            detail_polyline=NANNING_ROUTE
        )

        self.assertEqual(resolve_activity_route(self.client, activity), NANNING_ROUTE)
        self.client.get_activity_streams.assert_not_called()

    def test_missing_summary_map_can_still_have_a_detailed_route(self):
        activity = SimpleNamespace(id=20142182471, map=None)
        self.client.get_activity.return_value = make_activity(
            detail_polyline=NANNING_ROUTE
        )

        self.assertEqual(resolve_activity_route(self.client, activity), NANNING_ROUTE)

    def test_detailed_polyline_takes_precedence_over_detailed_summary(self):
        self.client.get_activity.return_value = make_activity(
            summary=SPARSE_ROUTE, detail_polyline=NANNING_ROUTE
        )

        self.assertEqual(
            resolve_activity_route(self.client, make_activity()), NANNING_ROUTE
        )
        self.client.get_activity_streams.assert_not_called()

    def test_detailed_summary_is_used_when_full_polyline_is_absent(self):
        self.client.get_activity.return_value = make_activity(summary=NANNING_ROUTE)

        self.assertEqual(
            resolve_activity_route(self.client, make_activity()), NANNING_ROUTE
        )
        self.client.get_activity_streams.assert_not_called()

    def test_latlng_stream_recovers_route_when_both_detail_routes_are_absent(self):
        activity = make_activity()
        self.client.get_activity_streams.return_value = {
            "latlng": SimpleNamespace(data=[list(point) for point in NANNING_POINTS])
        }

        recovered = resolve_activity_route(self.client, activity)

        self.assertEqual(polyline.decode(recovered), NANNING_POINTS)
        self.client.get_activity_streams.assert_called_once()
        args, kwargs = self.client.get_activity_streams.call_args
        self.assertEqual(args[0] if args else kwargs["activity_id"], activity.id)
        self.assertIn("latlng", kwargs.get("types", args[1] if len(args) > 1 else []))

    def test_missing_gps_stays_missing_for_empty_or_absent_latlng_stream(self):
        for streams in ({}, {"latlng": SimpleNamespace(data=[])}):
            with self.subTest(streams=streams):
                self.client.get_activity_streams.return_value = streams

                self.assertIsNone(
                    resolve_activity_route(self.client, make_activity(summary=""))
                )

    def test_no_gps_fallback_keeps_an_existing_sparse_source_route(self):
        self.assertEqual(
            resolve_activity_route(self.client, make_activity(summary=SPARSE_ROUTE)),
            SPARSE_ROUTE,
        )

    def test_detail_not_found_keeps_original_route_without_failing_the_sync(self):
        self.client.get_activity.side_effect = ObjectNotFound("Activity not found")

        self.assertEqual(
            resolve_activity_route(self.client, make_activity(summary=SPARSE_ROUTE)),
            SPARSE_ROUTE,
        )

    def test_stream_not_found_keeps_original_route_without_failing_the_sync(self):
        self.client.get_activity_streams.side_effect = ObjectNotFound("No GPS stream")

        self.assertEqual(
            resolve_activity_route(self.client, make_activity(summary=SPARSE_ROUTE)),
            SPARSE_ROUTE,
        )

    def test_not_found_does_not_create_a_route_when_the_source_has_none(self):
        self.client.get_activity.side_effect = ObjectNotFound("Activity not found")
        self.client.get_activity_streams.side_effect = ObjectNotFound("No GPS stream")

        self.assertIsNone(resolve_activity_route(self.client, make_activity()))

    def test_api_failures_propagate_instead_of_silently_discarding_source_gps(self):
        for endpoint in ("get_activity", "get_activity_streams"):
            for error in (
                AccessUnauthorized("Invalid token"),
                api_error(403),
                RateLimitExceeded("Quota exhausted"),
                api_error(429),
                api_error(500),
            ):
                with self.subTest(endpoint=endpoint, error=type(error).__name__):
                    client = MagicMock(spec=["get_activity", "get_activity_streams"])
                    client.get_activity.return_value = make_activity()
                    client.get_activity_streams.return_value = {}
                    getattr(client, endpoint).side_effect = error

                    with self.assertRaises(type(error)) as raised:
                        resolve_activity_route(
                            client, make_activity(summary=SPARSE_ROUTE)
                        )

                    self.assertIs(raised.exception, error)

    def test_routes_are_never_reused_for_another_activity(self):
        self.client.get_activity.side_effect = [
            make_activity(run_id=19997632434, detail_polyline=LIUZHOU_ROUTE),
            make_activity(run_id=20142182471),
        ]

        first = resolve_activity_route(self.client, make_activity(run_id=19997632434))
        second = resolve_activity_route(self.client, make_activity(run_id=20142182471))

        self.assertEqual(first, LIUZHOU_ROUTE)
        self.assertIsNone(second)
        self.assertEqual(
            [call.args[0] for call in self.client.get_activity.call_args_list],
            [19997632434, 20142182471],
        )


if __name__ == "__main__":
    unittest.main()
