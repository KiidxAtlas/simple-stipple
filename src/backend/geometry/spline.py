"""Spline sampling helpers for canvas rendering and draw previews."""

from __future__ import annotations

Point = tuple[float, float]

_CLOSURE_EPS = 1e-6


def _points_close(a: Point, b: Point, eps: float = _CLOSURE_EPS) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _catmull_rom(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    t2 = t * t
    t3 = t2 * t
    x = 0.5 * (
        2 * p1[0]
        + (-p0[0] + p2[0]) * t
        + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
        + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
    )
    y = 0.5 * (
        2 * p1[1]
        + (-p0[1] + p2[1]) * t
        + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
        + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
    )
    return (x, y)


def build_spline_poly(
    points: list[Point],
    segments: int = 24,
    *,
    closed: bool = False,
) -> list[Point]:
    """Sample a smooth Catmull-Rom spline through the provided control points."""
    if len(points) < 2:
        return list(points)

    steps = max(4, int(segments))
    pts: list[Point] = [(float(pt[0]), float(pt[1])) for pt in points]
    if closed and len(pts) >= 3 and _points_close(pts[0], pts[-1]):
        pts = pts[:-1]

    if len(pts) == 2:
        return [(pts[0][0], pts[0][1]), (pts[1][0], pts[1][1])]

    result: list[Point] = []
    if closed:
        count = len(pts)
        for i in range(count):
            p0 = pts[(i - 1) % count]
            p1 = pts[i]
            p2 = pts[(i + 1) % count]
            p3 = pts[(i + 2) % count]
            for j in range(steps):
                t = j / steps
                result.append(_catmull_rom(p0, p1, p2, p3, t))
        result.append(pts[0])
        return result

    padded = [pts[0], *pts, pts[-1]]
    for i in range(len(pts) - 1):
        p0 = padded[i]
        p1 = padded[i + 1]
        p2 = padded[i + 2]
        p3 = padded[i + 3]
        for j in range(steps):
            t = j / steps
            result.append(_catmull_rom(p0, p1, p2, p3, t))
    result.append(pts[-1])
    return result


__all__ = ["build_spline_poly"]
