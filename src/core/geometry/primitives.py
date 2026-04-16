"""Geometry primitive builders (pure core logic)."""

from __future__ import annotations

from src.core.shapes import shape_circle, shape_ellipse, shape_polygon, shape_rect

Point = tuple[float, float]
Polyline = list[Point]


def _translate(poly: Polyline, cx: float, cy: float) -> Polyline:
    return [(x + cx, y + cy) for x, y in poly]


def build_circle_poly(cx: float, cy: float, r: float, segments: int = 64) -> Polyline:
    """Build a circle polyline centered at ``(cx, cy)``."""
    if r <= 0:
        return []
    return _translate(shape_circle(r, segments), cx, cy)


def build_rect_poly(cx: float, cy: float, w: float, h: float) -> Polyline:
    """Build a rectangle polyline centered at ``(cx, cy)``."""
    if w <= 0 or h <= 0:
        return []
    return _translate(shape_rect(w, h), cx, cy)


def build_ellipse_poly(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    segments: int = 64,
) -> Polyline:
    """Build an ellipse polyline centered at ``(cx, cy)``."""
    if rx <= 0 or ry <= 0:
        return []
    return _translate(shape_ellipse(rx, ry, segments), cx, cy)


def build_polygon_poly(cx: float, cy: float, r: float, sides: int = 6) -> Polyline:
    """Build a regular polygon polyline centered at ``(cx, cy)``."""
    if r <= 0:
        return []
    return _translate(shape_polygon(sides, r), cx, cy)


__all__ = [
    "build_circle_poly",
    "build_ellipse_poly",
    "build_polygon_poly",
    "build_rect_poly",
]
