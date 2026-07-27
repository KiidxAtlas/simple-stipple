"""Pure trim and extend calculations for linework."""

from __future__ import annotations

import math

from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, split, unary_union

PointTuple = tuple[float, float]


def trim_polyline(
    target: list[PointTuple], cutters: list[list[PointTuple]], click: PointTuple
) -> list[list[PointTuple]]:
    cutter_geometry = unary_union([LineString(points) for points in cutters if len(points) >= 2])
    pieces = [
        item
        for item in split(LineString(target), cutter_geometry).geoms
        if isinstance(item, LineString) and len(item.coords) >= 2
    ]
    if len(pieces) < 2:
        return []
    removed = min(pieces, key=lambda item: item.distance(Point(click)))
    kept = [item for item in pieces if item is not removed]
    merged = linemerge(kept) if len(kept) > 1 else kept[0]
    outputs = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
    return [[(float(x), float(y)) for x, y in item.coords] for item in outputs]


def trim_preview(
    target: list[PointTuple], cutters: list[list[PointTuple]], click: PointTuple
) -> list[PointTuple] | None:
    """Return only the portion that a trim operation would remove."""
    cutter_geometry = unary_union([LineString(points) for points in cutters if len(points) >= 2])
    pieces = [
        item
        for item in split(LineString(target), cutter_geometry).geoms
        if isinstance(item, LineString) and len(item.coords) >= 2
    ]
    if len(pieces) < 2:
        return None
    removed = min(pieces, key=lambda item: item.distance(Point(click)))
    return [(float(x), float(y)) for x, y in removed.coords]


def extension_point(
    points: list[PointTuple], cutters: list[list[PointTuple]], *, start: bool, reach: float
) -> PointTuple | None:
    if len(points) < 2:
        return None
    tip = points[0] if start else points[-1]
    neighbor = points[1] if start else points[-2]
    dx, dy = tip[0] - neighbor[0], tip[1] - neighbor[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    ray = LineString([tip, (tip[0] + dx / length * reach, tip[1] + dy / length * reach)])
    intersection = ray.intersection(
        unary_union([LineString(item) for item in cutters if len(item) >= 2])
    )
    candidates: list[tuple[float, PointTuple]] = []
    for item in getattr(intersection, "geoms", [intersection]):
        coordinates = (
            [(item.x, item.y)] if isinstance(item, Point) else list(getattr(item, "coords", []))
        )
        for x, y in coordinates:
            distance = math.hypot(x - tip[0], y - tip[1])
            if distance > 1e-6:
                candidates.append((distance, (float(x), float(y))))
    return min(candidates)[1] if candidates else None
