"""Pure offset operations for open and closed polylines."""

from __future__ import annotations

import math

from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

Point = tuple[float, float]


def is_closed(points: list[Point], tolerance: float = 0.01) -> bool:
    return len(points) >= 3 and math.dist(points[0], points[-1]) < tolerance


def offset_polyline(points: list[Point], distance: float) -> list[Point] | None:
    if len(points) < 2:
        return None
    try:
        if is_closed(points):
            geometry = Polygon(points)
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            buffered = geometry.buffer(distance, join_style="round")
            if isinstance(buffered, MultiPolygon):
                buffered = max(buffered.geoms, key=lambda item: item.area)
            if buffered.is_empty or not isinstance(buffered, Polygon):
                return None
            return [(float(x), float(y)) for x, y in buffered.exterior.coords]
        geometry = LineString(points).parallel_offset(
            abs(distance), "left" if distance >= 0 else "right", join_style="mitre", mitre_limit=2.0
        )
        if isinstance(geometry, MultiLineString):
            geometry = max(geometry.geoms, key=lambda item: item.length)
        if geometry.is_empty or not isinstance(geometry, LineString):
            return None
        return [(float(x), float(y)) for x, y in geometry.coords]
    except (GEOSException, TypeError, ValueError):
        return None
