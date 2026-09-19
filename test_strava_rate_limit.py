"""Quota callbacks must prevent requests while either Strava limit is full."""

import datetime
import unittest
from unittest.mock import patch

from requests.structures import CaseInsensitiveDict

from run_page.strava_rate_limit import wait_for_strava_quota


class FakeClock:
    def __init__(self, timestamp):
        self.now = timestamp
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class StravaRateLimitTests(unittest.TestCase):
    def invoke(self, headers, at="2026-09-19T12:14:50+00:00"):
        clock = FakeClock(datetime.datetime.fromisoformat(at).timestamp())
        with (
            patch("run_page.strava_rate_limit.time.time", side_effect=clock.time),
            patch("run_page.strava_rate_limit.time.sleep", side_effect=clock.sleep),
            patch("builtins.print") as print_wait,
        ):
            wait_for_strava_quota(headers)
        return clock, print_wait

    def test_below_both_quotas_does_not_sleep(self):
        clock, print_wait = self.invoke(
            {
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "199,1999",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "99,999",
            }
        )
        self.assertEqual(clock.sleeps, [])
        print_wait.assert_not_called()

    def test_equality_and_excess_both_wait_until_next_quarter(self):
        for prefix, short_usage in (
            ("X-RateLimit", 200),
            ("X-RateLimit", 205),
            ("X-ReadRateLimit", 200),
            ("X-ReadRateLimit", 205),
        ):
            with self.subTest(prefix=prefix, short_usage=short_usage):
                clock, print_wait = self.invoke(
                    {
                        f"{prefix}-Limit": "200,2000",
                        f"{prefix}-Usage": f"{short_usage},500",
                    }
                )
                self.assertEqual(clock.sleeps, [11])
                print_wait.assert_called_once_with(
                    "Strava API quota reached; waiting 11.0 seconds.", flush=True
                )

    def test_read_quota_is_respected_when_overall_quota_has_capacity(self):
        clock, _ = self.invoke(
            {
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "100,200",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,200",
            }
        )
        self.assertEqual(clock.sleeps, [11])

    def test_header_names_are_case_insensitive_and_values_allow_spaces(self):
        clock, _ = self.invoke(
            {
                "x-READRateLimit-LIMIT": " 100, 1000 ",
                "X-readratelimit-usage": "100, 200",
            }
        )
        self.assertEqual(clock.sleeps, [11])

    def test_requests_case_insensitive_response_headers_are_supported(self):
        clock, _ = self.invoke(
            CaseInsensitiveDict(
                {
                    "X-ReadRateLimit-Limit": "100,1000",
                    "X-ReadRateLimit-Usage": "100,200",
                }
            )
        )
        self.assertEqual(clock.sleeps, [11])

    def test_daily_exhaustion_waits_until_midnight_utc(self):
        for prefix in ("X-RateLimit", "X-ReadRateLimit"):
            with self.subTest(prefix=prefix):
                clock, _ = self.invoke(
                    {f"{prefix}-Limit": "100,1000", f"{prefix}-Usage": "1,1000"},
                    at="2026-09-19T23:59:50+00:00",
                )
                self.assertEqual(clock.sleeps, [11])

    def test_daily_quota_controls_wait_if_short_quota_is_also_exhausted(self):
        clock, _ = self.invoke(
            {
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "200,500",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "50,1000",
            },
            at="2026-09-19T23:14:50+00:00",
        )
        self.assertEqual(sum(clock.sleeps), 45 * 60 + 11)
        self.assertTrue(all(0 < seconds <= 60 for seconds in clock.sleeps))

    def test_quarter_boundary_waits_for_the_next_window_with_margin(self):
        clock, _ = self.invoke(
            {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "100,200"},
            at="2026-09-19T12:15:00+00:00",
        )
        self.assertEqual(sum(clock.sleeps), 901)
        self.assertTrue(all(0 < seconds <= 60 for seconds in clock.sleeps))

    def test_missing_or_malformed_headers_do_not_sleep(self):
        for headers in (
            None,
            {},
            {"X-RateLimit-Limit": "100,1000"},
            {"X-RateLimit-Usage": "100,1000"},
            {"X-RateLimit-Limit": "invalid", "X-RateLimit-Usage": "100,1000"},
            {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "100"},
            {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,2,3"},
            {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "100.0,200"},
            {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "-1,200"},
            {"X-RateLimit-Limit": None, "X-RateLimit-Usage": "100,200"},
        ):
            with self.subTest(headers=headers):
                clock, print_wait = self.invoke(headers)
                self.assertEqual(clock.sleeps, [])
                print_wait.assert_not_called()

    def test_malformed_overall_headers_do_not_hide_valid_read_quota(self):
        clock, _ = self.invoke(
            {
                "X-RateLimit-Limit": "invalid",
                "X-RateLimit-Usage": "100,1000",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "100,200",
            }
        )
        self.assertEqual(clock.sleeps, [11])


if __name__ == "__main__":
    unittest.main()
