"""Regression tests for exporting recorded routes without inventing GPS data."""

import datetime
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "run_page"))

import polyline
from generator import Generator
from generator.db import Activity
from gpxtrackposter.track import Track
from polyline_processor import filter_out, start_end_hiding
from strava_sync import run_strava_sync
from stravalib.exc import ObjectNotFound, RateLimitExceeded
from stravalib.model import Activity as StravaActivity

LIUZHOU_POINTS = [
    (24.40819, 109.53127),
    (24.40780, 109.53100),
    (24.40730, 109.53070),
    (24.40680, 109.53040),
    (24.40630, 109.53010),
    (24.40580, 109.52980),
]
NANNING_POINTS = [
    (22.83203, 108.24882),
    (22.83180, 108.25020),
    (22.83150, 108.25210),
    (22.83120, 108.25400),
    (22.83080, 108.25610),
    (22.83044, 108.25821),
]
COLLAPSED_NANNING_POINTS = [
    (22.83203, 108.24882),
    (22.83204, 108.24884),
    (22.83205, 108.24887),
    (22.83206, 108.24893),
]
LIUZHOU_LOCATION = "柳州职业技术大学, 柳州市, 广西壮族自治区, 中国"
NANNING_LOCATION = "清川, 南宁市, 广西壮族自治区, 中国"


def strava_activity(run_id, summary="", detailed_route=None):
    return StravaActivity.deserialize(
        {
            "id": run_id,
            "resource_state": 3 if detailed_route else 2,
            "name": "Morning Run",
            "type": "Run",
            "distance": 1100.0,
            "moving_time": 720,
            "elapsed_time": 720,
            "start_date": "2026-09-12T10:48:15Z",
            "start_date_local": "2026-09-12T18:48:15Z",
            "start_latlng": list(NANNING_POINTS[0]),
            "average_speed": 1.5,
            "total_elevation_gain": 0,
            "map": {
                "id": f"a{run_id}",
                "resource_state": 3 if detailed_route else 2,
                "summary_polyline": summary,
                "polyline": detailed_route,
            },
        }
    )


class GeneratorRouteTests(unittest.TestCase):
    def setUp(self):
        self.generator = Generator(":memory:")
        self.session = self.generator.session
        self.engine = self.session.get_bind()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.patch = patch("generator.IGNORE_BEFORE_SAVING", False)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def add_activity(
        self,
        run_id,
        date,
        points=None,
        location=None,
        subtype="Run",
    ):
        activity = Activity(
            run_id=run_id,
            name="Morning Run",
            distance=1100.0,
            moving_time=datetime.timedelta(minutes=12),
            elapsed_time=datetime.timedelta(minutes=12),
            type="Run",
            subtype=subtype,
            start_date=date,
            start_date_local=date,
            location_country=location,
            summary_polyline=polyline.encode(points) if points is not None else None,
            average_speed=1.5,
            elevation_gain=0,
        )
        self.session.add(activity)
        self.session.commit()
        return activity

    def test_missing_gps_does_not_inherit_a_previous_city_route_or_location(self):
        self.add_activity(1, "2026-09-02 06:43:15", LIUZHOU_POINTS, LIUZHOU_LOCATION)
        self.add_activity(2, "2026-09-04 06:19:36", location=NANNING_LOCATION)
        self.add_activity(3, "2026-09-05 06:51:20")

        with patch("generator.filter_out", side_effect=lambda value: value):
            exported = self.generator.load()

        self.assertFalse(exported[1]["summary_polyline"])
        self.assertEqual(exported[1]["location_country"], NANNING_LOCATION)
        self.assertEqual(exported[1]["subtype"], "Run")
        self.assertFalse(exported[2]["summary_polyline"])
        self.assertIsNone(exported[2]["location_country"])
        self.assertEqual(exported[2]["subtype"], "Run")

    def test_recorded_route_survives_an_indoor_subtype(self):
        self.add_activity(1, "2026-09-02 06:43:15", LIUZHOU_POINTS, LIUZHOU_LOCATION)
        self.add_activity(
            2, "2026-09-12 18:48:15", NANNING_POINTS, NANNING_LOCATION, "indoor"
        )

        with patch("generator.filter_out", side_effect=lambda value: value):
            exported = self.generator.load()

        self.assertEqual(
            exported[1]["summary_polyline"], polyline.encode(NANNING_POINTS)
        )
        self.assertEqual(exported[1]["location_country"], NANNING_LOCATION)
        self.assertEqual(exported[1]["subtype"], "indoor")

    def test_repeated_privacy_exports_do_not_mutate_or_commit_source_data(self):
        outdoor = self.add_activity(
            1, "2026-09-02 06:43:15", LIUZHOU_POINTS, LIUZHOU_LOCATION
        )
        indoor = self.add_activity(
            2, "2026-09-04 06:19:36", NANNING_POINTS, NANNING_LOCATION, "treadmill"
        )
        missing = self.add_activity(3, "2026-09-05 06:51:20")
        source_rows = [outdoor, indoor, missing]
        original = [row.to_dict() for row in source_rows]

        def trim_endpoints(value):
            return polyline.encode(polyline.decode(value)[1:-1]) if value else value

        with (
            patch("generator.filter_out", side_effect=trim_endpoints),
            patch.object(self.session, "commit", wraps=self.session.commit) as commit,
        ):
            first = self.generator.load()
            second = self.generator.load()

        self.assertEqual(first, second)
        self.assertEqual(
            first[0]["summary_polyline"], polyline.encode(LIUZHOU_POINTS[1:-1])
        )
        self.assertEqual(
            first[1]["summary_polyline"], polyline.encode(NANNING_POINTS[1:-1])
        )
        self.assertFalse(first[2]["summary_polyline"])
        self.assertEqual([row["streak"] for row in first], [1, 1, 2])
        commit.assert_not_called()
        self.assertFalse(self.session.dirty)
        self.assertEqual([row.to_dict() for row in source_rows], original)

        # An unrelated later write must not accidentally persist export filtering.
        outdoor.name = "Updated title"
        original[0]["name"] = outdoor.name
        self.session.commit()
        self.session.expire_all()
        self.assertEqual([row.to_dict() for row in source_rows], original)

    def test_streak_is_exported_without_changing_activity_objects(self):
        dates = [
            "2026-09-01 06:00:00",
            "2026-09-01 19:00:00",
            "2026-09-02 06:00:00",
            "2026-09-04 06:00:00",
        ]
        source_rows = [self.add_activity(i, date) for i, date in enumerate(dates, 1)]

        with patch("generator.filter_out", side_effect=lambda value: value):
            exported = self.generator.load()

        self.assertEqual([row["streak"] for row in exported], [1, 1, 2, 1])
        self.assertTrue(all(row.streak is None for row in source_rows))

    def test_svg_filters_raw_routes_once_without_changing_source_data(self):
        filtered_points = NANNING_POINTS[1:-1]
        filtered_polyline = polyline.encode(filtered_points)
        for already_filtered in (False, True):
            with self.subTest(already_filtered=already_filtered):
                source_points = filtered_points if already_filtered else NANNING_POINTS
                source = self.add_activity(
                    int(already_filtered) + 1,
                    "2026-09-12 18:48:15",
                    source_points,
                    NANNING_LOCATION,
                )
                original = source.to_dict()
                track = Track()
                with (
                    patch(
                        "gpxtrackposter.track.IGNORE_BEFORE_SAVING", already_filtered
                    ),
                    patch(
                        "gpxtrackposter.track.filter_out",
                        return_value=filtered_polyline,
                    ) as privacy_filter,
                ):
                    track.load_from_db(source)

                if already_filtered:
                    privacy_filter.assert_not_called()
                else:
                    privacy_filter.assert_called_once_with(original["summary_polyline"])
                displayed_points = [
                    (round(point.lat().degrees, 5), round(point.lng().degrees, 5))
                    for point in track.polylines[0]
                ]
                self.assertEqual(displayed_points, filtered_points)
                self.assertFalse(self.session.dirty)
                self.session.commit()
                self.session.expire_all()
                self.assertEqual(source.to_dict(), original)

    def test_force_sync_refreshes_all_history_instead_of_recent_window(self):
        self.add_activity(1, "2026-09-12 18:48:15", NANNING_POINTS, NANNING_LOCATION)
        for force in (False, True):
            with self.subTest(force=force):
                with (
                    patch.object(self.generator, "check_access"),
                    patch.object(
                        self.generator.client, "get_activities", return_value=[]
                    ) as get_activities,
                ):
                    before_sync = datetime.datetime.now(datetime.UTC)
                    self.generator.sync(force=force)
                    after_sync = datetime.datetime.now(datetime.UTC)

                filters = get_activities.call_args.kwargs
                get_activities.assert_called_once()
                if force:
                    self.assertEqual(set(filters), {"before"})
                    self.assertLessEqual(before_sync, filters["before"])
                    self.assertLessEqual(filters["before"], after_sync)
                else:
                    self.assertEqual(
                        filters,
                        {
                            "after": datetime.datetime(
                                2026, 9, 5, 18, 48, 15, tzinfo=datetime.UTC
                            )
                        },
                    )

    def test_sync_recovers_detailed_gps_and_preserves_activity_metrics(self):
        earlier = self.add_activity(
            19997632434, "2026-09-02 06:43:15", LIUZHOU_POINTS, LIUZHOU_LOCATION
        )
        repaired = self.add_activity(
            20142182471,
            "2026-09-12 18:48:15",
            LIUZHOU_POINTS,
            NANNING_LOCATION,
            "indoor",
        )
        metadata_keys = (
            "run_id",
            "distance",
            "moving_time",
            "elapsed_time",
            "start_date",
            "start_date_local",
            "location_country",
        )
        original_metadata = [
            tuple(getattr(row, key) for key in metadata_keys)
            for row in (earlier, repaired)
        ]
        recorded_route = polyline.encode(NANNING_POINTS)
        summary = strava_activity(repaired.run_id)
        summary.map = None
        detail = strava_activity(repaired.run_id, detailed_route=recorded_route)
        with (
            patch.object(self.generator, "check_access"),
            patch.object(
                self.generator.client, "get_activities", return_value=[summary]
            ),
            patch.object(
                self.generator.client, "get_activity", return_value=detail
            ) as get_detail,
            patch.object(self.generator.client, "get_activity_streams") as get_streams,
            patch.multiple(
                "polyline_processor",
                IGNORE_START_END_RANGE=0.01,
                IGNORE_RANGE=0,
                IGNORE_POLYLINE=[],
            ),
        ):
            self.generator.sync(force=True)
            self.session.expire_all()
            exported = self.generator.load()

        get_detail.assert_called_once_with(repaired.run_id)
        get_streams.assert_not_called()
        self.assertEqual(self.session.query(Activity).count(), 2)
        self.assertEqual(
            [
                tuple(getattr(row, key) for key in metadata_keys)
                for row in (earlier, repaired)
            ],
            original_metadata,
        )
        self.assertEqual(earlier.summary_polyline, polyline.encode(LIUZHOU_POINTS))
        self.assertEqual(repaired.summary_polyline, recorded_route)
        self.assertEqual(repaired.subtype, "Run")
        public_route = polyline.decode(exported[1]["summary_polyline"])
        self.assertGreaterEqual(len(public_route), 2)
        self.assertTrue(
            all(22.6 < lat < 23 and 108 < lon < 108.6 for lat, lon in public_route)
        )
        self.assertNotEqual(exported[1]["summary_polyline"], recorded_route)
        self.assertEqual(exported[1]["location_country"], NANNING_LOCATION)

    def test_repeated_incremental_sync_recovers_empty_summary_without_erasing_gps(self):
        activity = self.add_activity(
            20142182471, "2026-09-12 18:48:15", location=NANNING_LOCATION
        )
        recorded_route = polyline.encode(NANNING_POINTS)
        summaries = [strava_activity(activity.run_id), strava_activity(activity.run_id)]
        with (
            patch.object(self.generator, "check_access"),
            patch.object(
                self.generator.client,
                "get_activities",
                side_effect=[[summary] for summary in summaries],
            ) as get_summaries,
            patch.object(
                self.generator.client,
                "get_activity",
                return_value=strava_activity(
                    activity.run_id, detailed_route=recorded_route
                ),
            ) as get_detail,
            patch.object(self.generator.client, "get_activity_streams") as get_streams,
        ):
            for _ in summaries:
                self.generator.sync(force=False)
                self.session.expire_all()
                self.assertEqual(activity.summary_polyline, recorded_route)
                self.assertEqual(self.session.query(Activity).count(), 1)
                self.assertEqual(activity.distance, 1100.0)
                self.assertEqual(activity.moving_time, datetime.timedelta(minutes=12))
                self.assertEqual(activity.elapsed_time, datetime.timedelta(minutes=12))

        self.assertEqual(get_detail.call_count, 2)
        self.assertTrue(
            all("after" in call.kwargs for call in get_summaries.call_args_list)
        )
        get_streams.assert_not_called()

    def test_failed_detail_request_cannot_commit_an_empty_summary_over_existing_gps(
        self,
    ):
        activity = self.add_activity(
            20142182471, "2026-09-12 18:48:15", NANNING_POINTS, NANNING_LOCATION
        )
        original = activity.to_dict()
        error = RateLimitExceeded("Read quota exhausted")
        with (
            patch.object(self.generator, "check_access"),
            patch.object(
                self.generator.client,
                "get_activities",
                return_value=[strava_activity(activity.run_id)],
            ),
            patch.object(self.generator.client, "get_activity", side_effect=error),
            patch.object(self.session, "commit", wraps=self.session.commit) as commit,
            self.assertRaises(RateLimitExceeded) as raised,
        ):
            self.generator.sync(force=False)

        self.assertIs(raised.exception, error)
        commit.assert_not_called()
        self.assertFalse(self.session.dirty)
        self.session.expire_all()
        self.assertEqual(activity.to_dict(), original)
        self.assertEqual(self.session.query(Activity).count(), 1)

    def add_collapsed_historical_activity(self):
        historical = self.add_activity(
            18384622772,
            "2026-05-05 19:59:12",
            COLLAPSED_NANNING_POINTS,
            NANNING_LOCATION,
        )
        historical.distance = 1650.8
        historical.moving_time = datetime.timedelta(minutes=21, seconds=41)
        historical.elapsed_time = datetime.timedelta(minutes=22)
        self.session.commit()
        self.add_activity(
            20233117633, "2026-09-19 07:22:45", NANNING_POINTS, NANNING_LOCATION
        )
        return historical

    def test_incremental_sync_recovers_historical_summary_hidden_by_privacy(self):
        historical = self.add_collapsed_historical_activity()
        original = historical.to_dict()
        original_elapsed_time = historical.elapsed_time
        recorded_route = polyline.encode(NANNING_POINTS)
        with (
            patch.object(self.generator, "check_access"),
            patch.object(
                self.generator.client, "get_activities", return_value=[]
            ) as get_summaries,
            patch.object(
                self.generator.client,
                "get_activity",
                return_value=strava_activity(
                    historical.run_id,
                    summary=original["summary_polyline"],
                    detailed_route=recorded_route,
                ),
            ) as get_detail,
            patch.object(self.generator.client, "get_activity_streams") as get_streams,
            patch.multiple(
                "polyline_processor",
                IGNORE_START_END_RANGE=0.01,
                IGNORE_RANGE=0,
                IGNORE_POLYLINE=[],
            ),
        ):
            self.assertFalse(filter_out(original["summary_polyline"]))
            self.generator.sync(force=False)
            self.session.expire_all()
            exported = self.generator.load()

        self.assertGreater(
            get_summaries.call_args.kwargs["after"],
            datetime.datetime(2026, 5, 5, tzinfo=datetime.UTC),
        )
        get_detail.assert_called_once_with(historical.run_id)
        get_streams.assert_not_called()
        self.assertEqual(self.session.query(Activity).count(), 2)
        self.assertEqual(historical.summary_polyline, recorded_route)
        self.assertEqual(historical.elapsed_time, original_elapsed_time)
        self.assertEqual(
            {
                key: value
                for key, value in historical.to_dict().items()
                if key != "summary_polyline"
            },
            {
                key: value
                for key, value in original.items()
                if key != "summary_polyline"
            },
        )
        public_route = polyline.decode(exported[0]["summary_polyline"])
        self.assertGreaterEqual(len(public_route), 2)
        self.assertTrue(
            all(22.6 < lat < 23 and 108 < lon < 108.6 for lat, lon in public_route)
        )
        self.assertEqual(exported[0]["run_id"], historical.run_id)

    def test_historical_detail_that_is_truly_short_remains_hidden_without_fabrication(
        self,
    ):
        historical = self.add_collapsed_historical_activity()
        original = historical.to_dict()
        with (
            patch.object(self.generator, "check_access"),
            patch.object(self.generator.client, "get_activities", return_value=[]),
            patch.object(
                self.generator.client,
                "get_activity",
                return_value=strava_activity(
                    historical.run_id,
                    summary=original["summary_polyline"],
                    detailed_route=original["summary_polyline"],
                ),
            ) as get_detail,
            patch.multiple(
                "polyline_processor",
                IGNORE_START_END_RANGE=0.01,
                IGNORE_RANGE=0,
                IGNORE_POLYLINE=[],
            ),
        ):
            self.generator.sync(force=False)
            self.session.expire_all()
            exported = self.generator.load()

        get_detail.assert_called_once_with(historical.run_id)
        self.assertEqual(historical.to_dict(), original)
        self.assertFalse(exported[0]["summary_polyline"])
        self.assertEqual(exported[0]["run_id"], historical.run_id)
        self.assertTrue(exported[1]["summary_polyline"])
        self.assertEqual(self.session.query(Activity).count(), 2)

    def test_historical_detail_not_found_keeps_the_original_source_record(self):
        historical = self.add_collapsed_historical_activity()
        original = historical.to_dict()
        with (
            patch.object(self.generator, "check_access"),
            patch.object(self.generator.client, "get_activities", return_value=[]),
            patch.object(
                self.generator.client,
                "get_activity",
                side_effect=ObjectNotFound("Historical activity unavailable"),
            ) as get_detail,
            patch.multiple(
                "polyline_processor",
                IGNORE_START_END_RANGE=0.01,
                IGNORE_RANGE=0,
                IGNORE_POLYLINE=[],
            ),
        ):
            self.generator.sync(force=False)
            self.session.expire_all()
            exported = self.generator.load()

        get_detail.assert_called_once_with(historical.run_id)
        self.assertEqual(historical.to_dict(), original)
        self.assertFalse(exported[0]["summary_polyline"])
        self.assertEqual(self.session.query(Activity).count(), 2)


class StravaSyncTests(unittest.TestCase):
    def test_force_argument_reaches_sync_and_export_is_written(self):
        for force in (False, True):
            with self.subTest(force=force), TemporaryDirectory() as temporary_dir:
                output = Path(temporary_dir) / "activities.json"
                exported = [{"run_id": 1, "summary_polyline": None}]
                with (
                    patch("strava_sync.Generator") as generator_class,
                    patch("strava_sync.JSON_FILE", str(output)),
                ):
                    generator = generator_class.return_value
                    generator.load.return_value = exported
                    options = {"force": True} if force else {}
                    run_strava_sync(
                        "client-id", "client-secret", "refresh-token", **options
                    )

                generator.set_strava_config.assert_called_once_with(
                    "client-id", "client-secret", "refresh-token"
                )
                generator.sync.assert_called_once_with(force)
                generator.load.assert_called_once()
                self.assertEqual(json.loads(output.read_text()), exported)


class PrivacyRouteTests(unittest.TestCase):
    def test_disabled_start_end_hiding_preserves_all_recorded_points(self):
        for points in ([], NANNING_POINTS[:1], NANNING_POINTS[:2], NANNING_POINTS):
            for distance in (0, -1):
                with self.subTest(points=len(points), distance=distance):
                    self.assertEqual(start_end_hiding(points, distance), points)

    def test_no_privacy_configuration_preserves_encoded_endpoints(self):
        with patch.multiple(
            "polyline_processor",
            IGNORE_START_END_RANGE=0,
            IGNORE_RANGE=0,
            IGNORE_POLYLINE=[],
        ):
            for points in (NANNING_POINTS[:1], NANNING_POINTS[:2], NANNING_POINTS):
                with self.subTest(points=len(points)):
                    encoded = polyline.encode(points)
                    self.assertEqual(filter_out(encoded), encoded)
            self.assertIsNone(filter_out(None))
            self.assertFalse(filter_out(""))

    def test_enabled_start_end_hiding_handles_short_tracks(self):
        self.assertEqual(start_end_hiding([], 0.01), [])
        self.assertEqual(start_end_hiding(NANNING_POINTS[:1], 0.01), [])
        sparse = start_end_hiding(NANNING_POINTS[:2], 0.01)
        self.assertEqual(len(sparse), 2)
        self.assertNotEqual(sparse, NANNING_POINTS[:2])
        full = start_end_hiding(NANNING_POINTS, 0.01)
        self.assertEqual(full[1:-1], NANNING_POINTS[1:-1])
        self.assertNotEqual(full[0], NANNING_POINTS[0])
        self.assertNotEqual(full[-1], NANNING_POINTS[-1])


if __name__ == "__main__":
    unittest.main()
