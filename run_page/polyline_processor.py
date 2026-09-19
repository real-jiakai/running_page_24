import itertools
import os
import warnings

import polyline
from haversine import haversine

# Initialize IGNORE_POLYLINE with graceful error handling
IGNORE_POLYLINE = []
ignore_polyline_env = os.getenv("IGNORE_POLYLINE")
if ignore_polyline_env:
    try:
        IGNORE_POLYLINE = polyline.decode(ignore_polyline_env)
    except Exception as e:  # noqa: BLE001
        warnings.warn(
            f"IGNORE_POLYLINE is not a valid polyline: {e}. "
            "Privacy filtering for specific polylines will be disabled.",
            UserWarning,
        )
        IGNORE_POLYLINE = []

# Initialize IGNORE_RANGE and IGNORE_START_END_RANGE with graceful error handling
IGNORE_RANGE = 0.0
IGNORE_START_END_RANGE = 0.0

ignore_range_env = os.getenv("IGNORE_RANGE", "0")
ignore_start_end_range_env = os.getenv("IGNORE_START_END_RANGE", "0")

try:
    IGNORE_RANGE = int(ignore_range_env) / 1000
except ValueError:
    warnings.warn(
        f"IGNORE_RANGE is not a valid number: '{ignore_range_env}'. "
        "Using default value of 0. Privacy filtering by range will be disabled.",
        UserWarning,
    )
    IGNORE_RANGE = 0.0

try:
    IGNORE_START_END_RANGE = int(ignore_start_end_range_env) / 1000
except ValueError:
    warnings.warn(
        f"IGNORE_START_END_RANGE is not a valid number: '{ignore_start_end_range_env}'. "
        "Using default value of 0. Start/end point filtering will be disabled.",
        UserWarning,
    )
    IGNORE_START_END_RANGE = 0.0


def point_distance_in_range(
    point: tuple[float], center_point: tuple[float], distance: int
) -> bool:
    return haversine(point, center_point) < distance


def point_in_list_points_range(
    point: tuple[float], points: list[tuple[float]], distance: int
) -> bool:
    # Use generator expression instead of list comprehension for better performance
    return any(point_distance_in_range(point, p, distance) for p in points)


def range_hiding(
    polyline: list[tuple[float]], points: list[tuple[float]], distance: int
) -> list[tuple[float]]:
    return [
        point
        for point in polyline
        if not point_in_list_points_range(point, points, distance)
    ]


def _interpolate_point(start, end, fraction):
    if fraction <= 0:
        return start
    if fraction >= 1:
        return end

    # Follow the recorded segment even when it crosses the antimeridian.
    longitude_delta = (end[1] - start[1] + 180) % 360 - 180
    longitude = (start[1] + longitude_delta * fraction + 180) % 360 - 180
    return (start[0] + (end[0] - start[0]) * fraction, longitude)


def start_end_hiding(
    polyline: list[tuple[float]], distance: float
) -> list[tuple[float]]:
    """Trim a distance in kilometers from both ends along the recorded route."""
    if distance <= 0:
        return polyline[:]

    cumulative_distances = [0.0]
    for start, end in itertools.pairwise(polyline):
        cumulative_distances.append(cumulative_distances[-1] + haversine(start, end))

    route_length = cumulative_distances[-1]
    if route_length <= distance * 2:
        return []

    end_distance = route_length - distance
    trimmed = []
    for i in range(1, len(polyline)):
        segment_start = cumulative_distances[i - 1]
        segment_end = cumulative_distances[i]
        segment_length = segment_end - segment_start

        # Interpolate at the boundary instead of discarding a whole GPS segment.
        # Strict lower bounds also avoid division by zero for duplicate points.
        if segment_start < distance <= segment_end:
            trimmed.append(
                _interpolate_point(
                    polyline[i - 1],
                    polyline[i],
                    (distance - segment_start) / segment_length,
                )
            )
        if distance < segment_end < end_distance:
            trimmed.append(polyline[i])
        if segment_start < end_distance <= segment_end:
            trimmed.append(
                _interpolate_point(
                    polyline[i - 1],
                    polyline[i],
                    (end_distance - segment_start) / segment_length,
                )
            )
            break

    return trimmed


def filter_out(polyline_str):
    if not polyline_str:
        return
    pl = polyline.decode(polyline_str)
    if not pl:
        return polyline_str

    new_pl = start_end_hiding(pl, IGNORE_START_END_RANGE)
    new_pl = range_hiding(new_pl, IGNORE_POLYLINE, IGNORE_RANGE)

    if not new_pl:
        return
    return polyline.encode(new_pl)
