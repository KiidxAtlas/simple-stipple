"""Shape builders — generate polyline point lists centred at origin.

Shapes are built using Shapely geometry primitives and buffers rather than
manual trigonometry loops, giving mathematically exact results and correct
handling of degenerate inputs.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from shapely.affinity import scale as shapely_scale  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point, box  # type: ignore[import-untyped]

PointTuple = tuple[float, float]


def _to_coords(geom: Any) -> list[PointTuple]:
    """Extract exterior coordinates from a Shapely geometry as a closed list."""
    return list(geom.exterior.coords)


def shape_rect(w: float, h: float) -> list[tuple[float, float]]:
    """Return an axis-aligned rectangle polyline centered at origin."""
    if w <= 0 or h <= 0:
        return []
    hw, hh = w / 2, h / 2
    return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]


def shape_rect_rounded(
    w: float, h: float, r: float, n_corner: int = 8
) -> list[tuple[float, float]]:
    """Rectangle with rounded corners, centred at origin."""
    if w <= 0 or h <= 0:
        return []
    r = min(r, w / 2, h / 2)
    hw, hh = w / 2, h / 2
    # Shrink inner box by radius, then buffer outward with round joins
    inner = box(-hw + r, -hh + r, hw - r, hh - r)
    rounded = inner.buffer(r, resolution=n_corner, join_style="round")
    return _to_coords(rounded)


@lru_cache(maxsize=256)
def _shape_circle_cached(r: float, n: int) -> tuple[PointTuple, ...]:
    return tuple(_to_coords(Point(0, 0).buffer(r, resolution=max(3, n // 4))))


def shape_circle(r: float, n: int = 64) -> list[PointTuple]:
    """Return a circle polyline centered at origin."""
    if r <= 0:
        return []
    return list(_shape_circle_cached(float(r), int(n)))


@lru_cache(maxsize=256)
def _shape_ellipse_cached(rx: float, ry: float, n: int) -> tuple[PointTuple, ...]:
    circle = Point(0, 0).buffer(1.0, resolution=max(3, n // 4))
    ellipse = shapely_scale(circle, xfact=rx, yfact=ry, origin=(0, 0))
    return tuple(_to_coords(ellipse))


def shape_ellipse(rx: float, ry: float, n: int = 64) -> list[PointTuple]:
    """Return an ellipse polyline centered at origin."""
    if rx <= 0 or ry <= 0:
        return []
    return list(_shape_ellipse_cached(float(rx), float(ry), int(n)))


@lru_cache(maxsize=256)
def _shape_polygon_cached(sides: int, r: float) -> tuple[PointTuple, ...]:
    points = [
        (
            r * math.cos(2 * math.pi * i / sides - math.pi / 2),
            r * math.sin(2 * math.pi * i / sides - math.pi / 2),
        )
        for i in range(sides)
    ]
    return tuple(points + [points[0]])


def shape_polygon(sides: int, r: float) -> list[PointTuple]:
    """Return a regular polygon polyline centered at origin."""
    if r <= 0:
        return []
    sides = max(3, sides)
    return list(_shape_polygon_cached(int(sides), float(r)))


def shape_slot(
    length: float, width: float, n_end: int = 24
) -> list[tuple[float, float]]:
    """Obround / slot profile centred at origin."""
    if width <= 0 or length <= 0:
        return []
    radius = width / 2.0
    half_straight = max(0.0, length / 2.0 - radius)
    line = LineString([(-half_straight, 0.0), (half_straight, 0.0)])
    slot = line.buffer(radius, resolution=max(4, n_end // 2), cap_style="round")
    return _to_coords(slot)
