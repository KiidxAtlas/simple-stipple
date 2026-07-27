"""Pure offset operations for open and closed polylines."""

from __future__ import annotations

import math

from simple_stipple.engine.editing.clipper_engine import clipper_offset

Point = tuple[float, float]


def is_closed(points: list[Point], tolerance: float = 0.01) -> bool:
    return len(points) >= 3 and math.dist(points[0], points[-1]) < tolerance


def offset_polyline(points: list[Point], distance: float) -> list[Point] | None:
    if len(points) < 2:
        return None
    try:
        results = clipper_offset(points, distance, closed=is_closed(points))
        return max(results, key=len) if results else None
    except (RuntimeError, TypeError, ValueError):
        return None
