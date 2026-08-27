"""Pure polyline translation, rotation, scaling, and mirroring."""

from __future__ import annotations

import math

Point = tuple[float, float]
Polyline = list[Point]


def translate(points: Polyline, dx: float, dy: float) -> Polyline:
    return [(x + dx, y + dy) for x, y in points]


def rotate(points: Polyline, center: Point, angle_deg: float) -> Polyline:
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    cx, cy = center
    return [
        (cx + (x - cx) * cosine - (y - cy) * sine, cy + (x - cx) * sine + (y - cy) * cosine)
        for x, y in points
    ]


def scale(points: Polyline, center: Point, factor: float) -> Polyline:
    if factor <= 0:
        raise ValueError("Scale factor must be positive")
    cx, cy = center
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points]


def mirror(points: Polyline, center: Point, axis: str) -> Polyline:
    cx, cy = center
    if axis == "horizontal":
        return [(2.0 * cx - x, y) for x, y in points]
    if axis == "vertical":
        return [(x, 2.0 * cy - y) for x, y in points]
    raise ValueError("Mirror axis must be 'horizontal' or 'vertical'")
