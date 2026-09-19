"""Recover routes omitted or over-simplified by Strava's activity list."""

import math

import polyline
from stravalib.exc import ObjectNotFound


def route_points(encoded):
    if not encoded:
        return []
    try:
        points = polyline.decode(encoded)
    except (IndexError, TypeError, ValueError):
        return []
    if any(
        not (math.isfinite(lat) and math.isfinite(lon))
        or not (-90 <= lat <= 90 and -180 <= lon <= 180)
        for lat, lon in points
    ):
        return []
    return points


def resolve_activity_route(client, activity):
    """Use only this activity's summary, detailed map, or recorded GPS stream.

    A missing summary is not evidence of missing GPS. Authentication, quota,
    and service failures deliberately propagate so a failed request cannot
    silently erase routes during an upsert.
    """
    activity_map = getattr(activity, "map", None)
    detailed = getattr(activity_map, "polyline", None)
    if len(route_points(detailed)) >= 2:
        return detailed
    summary = getattr(activity_map, "summary_polyline", None)
    points = route_points(summary)
    if len(points) >= 3:
        return summary
    best = summary if len(points) >= 2 else None
    try:
        detail = client.get_activity(activity.id)
    except ObjectNotFound:
        return best

    detail_map = getattr(detail, "map", None)
    for field in ("polyline", "summary_polyline"):
        candidate = getattr(detail_map, field, None)
        points = route_points(candidate)
        if len(points) >= 3:
            return candidate
        if len(points) >= 2 and best is None:
            best = candidate

    try:
        streams = client.get_activity_streams(activity.id, types=["latlng"])
    except ObjectNotFound:
        return best
    stream = streams.get("latlng") if streams else None
    coordinates = getattr(stream, "data", None)
    if coordinates:
        encoded = polyline.encode(coordinates)
        if len(route_points(encoded)) >= 2:
            return encoded
    return best
