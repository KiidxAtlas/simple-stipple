"""Geometry clipping/intersection operations (pure core logic)."""

from __future__ import annotations

from typing import TypeAlias

from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

Point: TypeAlias = tuple[float, float]


def clip_polygon_to_outline(shape: Polygon, outline: Polygon) -> BaseGeometry:
    """Return ``shape`` clipped to ``outline``."""
    return outline.intersection(shape)


def clip_line_to_outline(points: list[Point], outline: Polygon) -> BaseGeometry:
    """Return line geometry clipped to ``outline``."""
    return outline.intersection(LineString(points))


__all__ = ["clip_line_to_outline", "clip_polygon_to_outline"]
