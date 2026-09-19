"""Regression tests for privacy trimming of sparse recorded GPS routes."""

import itertools
import unittest
from unittest.mock import patch

import polyline
from haversine import haversine

from run_page.polyline_processor import filter_out, start_end_hiding


class StartEndHidingTests(unittest.TestCase):
    def test_two_point_nanning_route_keeps_its_geometry_after_ten_meter_trim(self):
        recorded = [(22.83825, 108.2512), (22.84031, 108.25088)]

        trimmed = start_end_hiding(recorded, 0.01)

        self.assertEqual(len(trimmed), 2)
        self.assertAlmostEqual(haversine(recorded[0], trimmed[0]), 0.01, places=6)
        self.assertAlmostEqual(haversine(recorded[-1], trimmed[-1]), 0.01, places=6)
        self.assertAlmostEqual(
            haversine(*trimmed), haversine(*recorded) - 0.02, places=6
        )
        for latitude, longitude in trimmed:
            self.assertGreater(latitude, recorded[0][0])
            self.assertLess(latitude, recorded[-1][0])
            self.assertGreater(longitude, recorded[-1][1])
            self.assertLess(longitude, recorded[0][1])

    def test_privacy_export_preserves_a_sparse_line(self):
        recorded = polyline.encode([(22.83825, 108.2512), (22.84031, 108.25088)])
        with (
            patch("run_page.polyline_processor.IGNORE_START_END_RANGE", 0.01),
            patch("run_page.polyline_processor.IGNORE_POLYLINE", []),
        ):
            exported = filter_out(recorded)

        self.assertTrue(exported)
        points = polyline.decode(exported)
        self.assertEqual(len(points), 2)
        self.assertGreater(haversine(*points), 0.20)

    def test_trim_covering_whole_route_removes_it(self):
        recorded = [(0, 0), (0, 0.001), (0, 0.002)]
        length = sum(haversine(a, b) for a, b in itertools.pairwise(recorded))
        for distance in (length / 2, length, length * 2):
            with self.subTest(distance=distance):
                self.assertEqual(start_end_hiding(recorded, distance), [])

    def test_trim_crosses_segments_and_preserves_interior_vertices(self):
        recorded = [
            (0, 0),
            (0, 0.001),
            (0.001, 0.001),
            (0.001, 0.002),
            (0.002, 0.002),
        ]
        distance = 0.15
        first_length = haversine(recorded[0], recorded[1])
        last_length = haversine(recorded[-2], recorded[-1])

        trimmed = start_end_hiding(recorded, distance)

        self.assertEqual(len(trimmed), 3)
        self.assertEqual(trimmed[1], recorded[2])
        self.assertAlmostEqual(
            first_length + haversine(recorded[1], trimmed[0]), distance, places=6
        )
        self.assertAlmostEqual(
            last_length + haversine(recorded[-2], trimmed[-1]), distance, places=6
        )

    def test_trim_boundary_can_be_an_existing_vertex(self):
        recorded = [(0, 0), (0, 0.001), (0, 0.002), (0, 0.003)]
        distance = haversine(recorded[0], recorded[1])

        trimmed = start_end_hiding(recorded, distance)

        self.assertEqual(len(trimmed), 2)
        for actual, expected in zip(trimmed, recorded[1:-1]):
            self.assertAlmostEqual(haversine(actual, expected), 0, places=10)

    def test_duplicate_vertices_do_not_prevent_trimming(self):
        recorded = [
            (0, 0),
            (0, 0),
            (0, 0.001),
            (0, 0.001),
            (0, 0.002),
            (0, 0.002),
        ]
        trimmed = start_end_hiding(recorded, 0.01)

        self.assertEqual(trimmed[1:-1], recorded[2:4])
        self.assertAlmostEqual(haversine(recorded[0], trimmed[0]), 0.01, places=6)
        self.assertAlmostEqual(haversine(recorded[-1], trimmed[-1]), 0.01, places=6)

    def test_empty_single_point_and_zero_length_routes_have_nothing_to_keep(self):
        for recorded in ([], [(0, 0)], [(0, 0), (0, 0), (0, 0)]):
            with self.subTest(recorded=recorded):
                self.assertEqual(start_end_hiding(recorded, 0.01), [])

    def test_trimming_does_not_modify_recorded_points(self):
        recorded = [(22.83825, 108.2512), (22.84031, 108.25088)]
        original = recorded[:]

        start_end_hiding(recorded, 0.01)

        self.assertEqual(recorded, original)

    def test_zero_or_negative_trim_returns_an_unchanged_copy(self):
        recorded = [(22.83825, 108.2512), (22.84031, 108.25088)]
        for distance in (0, -0.01):
            with self.subTest(distance=distance):
                trimmed = start_end_hiding(recorded, distance)
                self.assertEqual(trimmed, recorded)
                self.assertIsNot(trimmed, recorded)

    def test_antimeridian_segment_stays_near_the_recorded_route(self):
        recorded = [(0, 179.999), (0, -179.999)]

        trimmed = start_end_hiding(recorded, 0.01)

        self.assertEqual(len(trimmed), 2)
        self.assertTrue(all(abs(longitude) > 179.999 for _, longitude in trimmed))
        self.assertAlmostEqual(haversine(recorded[0], trimmed[0]), 0.01, places=6)
        self.assertAlmostEqual(haversine(recorded[-1], trimmed[-1]), 0.01, places=6)


if __name__ == "__main__":
    unittest.main()
