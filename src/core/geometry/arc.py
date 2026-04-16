"""Arc calculations for core geometry workflows."""

from __future__ import annotations

import math

Point = tuple[float, float]


def arc_from_three_points(
    p0: Point,
    p1: Point,
    p2: Point,
    segments: int = 24,
) -> list[Point]:
    """Build arc points passing through three points."""
    x1, y1 = p0
    x2, y2 = p1
    x3, y3 = p2
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [p0, p1, p2]
    ux = (
        (x1 * x1 + y1 * y1) * (y2 - y3)
        + (x2 * x2 + y2 * y2) * (y3 - y1)
        + (x3 * x3 + y3 * y3) * (y1 - y2)
    ) / d
    uy = (
        (x1 * x1 + y1 * y1) * (x3 - x2)
        + (x2 * x2 + y2 * y2) * (x1 - x3)
        + (x3 * x3 + y3 * y3) * (x2 - x1)
    ) / d
    radius = math.hypot(x1 - ux, y1 - uy)
    if radius < 1e-9:
        return [p0, p1, p2]

    a0 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y3 - uy, x3 - ux)
    ccw = ((x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)) > 0

    def _norm(angle: float) -> float:
        while angle < 0:
            angle += 2 * math.pi
        while angle >= 2 * math.pi:
            angle -= 2 * math.pi
        return angle

    a0 = _norm(a0)
    a2 = _norm(a2)
    if ccw:
        if a2 <= a0:
            a2 += 2 * math.pi
    elif a2 >= a0:
        a2 -= 2 * math.pi

    steps = max(2, segments)
    step = (a2 - a0) / steps
    return [
        (ux + radius * math.cos(a0 + i * step), uy + radius * math.sin(a0 + i * step))
        for i in range(steps + 1)
    ]


def arc_from_center_start_end(
    center: Point,
    start: Point,
    end: Point,
    segments: int = 24,
) -> list[Point]:
    """Build arc points from center, start point, and end point."""
    cx, cy = center
    sx, sy = start
    ex, ey = end
    radius = math.hypot(sx - cx, sy - cy)
    end_radius = math.hypot(ex - cx, ey - cy)
    if radius < 1e-9 or end_radius < 1e-9:
        return [center, start, end]

    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    ccw = ((sx - cx) * (ey - cy) - (sy - cy) * (ex - cx)) > 0

    def _norm(angle: float) -> float:
        while angle < 0:
            angle += 2 * math.pi
        while angle >= 2 * math.pi:
            angle -= 2 * math.pi
        return angle

    a0 = _norm(a0)
    a1 = _norm(a1)
    if ccw:
        if a1 <= a0:
            a1 += 2 * math.pi
    elif a1 >= a0:
        a1 -= 2 * math.pi

    steps = max(2, segments)
    step = (a1 - a0) / steps
    return [
        (cx + radius * math.cos(a0 + i * step), cy + radius * math.sin(a0 + i * step))
        for i in range(steps + 1)
    ]


__all__ = ["arc_from_center_start_end", "arc_from_three_points"]
