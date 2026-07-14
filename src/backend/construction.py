"""Pure derived-geometry helpers for construction entities."""

from __future__ import annotations

import math

Point = tuple[float, float]
Line = tuple[Point, Point]


def circumcircle(a: Point, b: Point, c: Point) -> tuple[Point, float] | None:
    determinant = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(determinant) <= 1e-12:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    center = (
        (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / determinant,
        (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / determinant,
    )
    return center, math.dist(center, a)


def angle_bisector(first: Line, second: Line) -> tuple[Point, Point] | None:
    """Return intersection + unit direction for the internal line bisector."""
    a, b = first
    c, d = second
    ab = (b[0] - a[0], b[1] - a[1])
    cd = (d[0] - c[0], d[1] - c[1])
    cross = ab[0] * cd[1] - ab[1] * cd[0]
    if abs(cross) <= 1e-12:
        return None
    ac = (c[0] - a[0], c[1] - a[1])
    t = (ac[0] * cd[1] - ac[1] * cd[0]) / cross
    origin = (a[0] + t * ab[0], a[1] + t * ab[1])
    len_ab, len_cd = math.hypot(*ab), math.hypot(*cd)
    if min(len_ab, len_cd) <= 1e-12:
        return None
    u = (ab[0] / len_ab, ab[1] / len_ab)
    v = (cd[0] / len_cd, cd[1] / len_cd)
    direction = (u[0] + v[0], u[1] + v[1])
    length = math.hypot(*direction)
    if length <= 1e-9:
        direction = (-u[1], u[0])
        length = 1.0
    return origin, (direction[0] / length, direction[1] / length)


def centerline(first: Line, second: Line) -> Line:
    """Midline between corresponding endpoints, correcting reversed input order."""
    a, b = first
    c, d = second
    if math.dist(a, d) + math.dist(b, c) < math.dist(a, c) + math.dist(b, d):
        c, d = d, c
    return (
        ((a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0),
        ((b[0] + d[0]) / 2.0, (b[1] + d[1]) / 2.0),
    )


def tangents_from_point(point: Point, center: Point, radius: float) -> list[Line]:
    dx, dy = point[0] - center[0], point[1] - center[1]
    distance_sq = dx * dx + dy * dy
    radius_sq = radius * radius
    if radius <= 0 or distance_sq <= radius_sq + 1e-12:
        return []
    base = radius_sq / distance_sq
    turn = radius * math.sqrt(distance_sq - radius_sq) / distance_sq
    return [
        (
            point,
            (
                center[0] + base * dx - sign * turn * dy,
                center[1] + base * dy + sign * turn * dx,
            ),
        )
        for sign in (-1.0, 1.0)
    ]


def common_circle_tangents(
    first_center: Point, first_radius: float, second_center: Point, second_radius: float
) -> list[Line]:
    """Return all real external and internal common tangents."""
    dx, dy = second_center[0] - first_center[0], second_center[1] - first_center[1]
    distance_sq = dx * dx + dy * dy
    if min(first_radius, second_radius) <= 0 or distance_sq <= 1e-12:
        return []
    lines: list[Line] = []
    for family in (1.0, -1.0):
        delta_radius = first_radius - family * second_radius
        height_sq = distance_sq - delta_radius * delta_radius
        if height_sq < -1e-12:
            continue
        height = math.sqrt(max(0.0, height_sq))
        for side in (-1.0, 1.0):
            vx = (dx * delta_radius - dy * height * side) / distance_sq
            vy = (dy * delta_radius + dx * height * side) / distance_sq
            lines.append(
                (
                    (first_center[0] + vx * first_radius, first_center[1] + vy * first_radius),
                    (
                        second_center[0] + vx * family * second_radius,
                        second_center[1] + vy * family * second_radius,
                    ),
                )
            )
    return lines


__all__ = [
    "angle_bisector",
    "centerline",
    "circumcircle",
    "common_circle_tangents",
    "tangents_from_point",
]
