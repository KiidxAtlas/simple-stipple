"""Procedural drafting primitives kept independent of Qt and canvas state."""

from __future__ import annotations

import math

from shapely.geometry import Polygon

Point = tuple[float, float]


def radial_profile(radii: list[float], *, rotation: float = -90.0) -> list[Point]:
    count = len(radii)
    points = [
        (
            radius * math.cos(math.radians(rotation + 360.0 * index / count)),
            radius * math.sin(math.radians(rotation + 360.0 * index / count)),
        )
        for index, radius in enumerate(radii)
    ]
    return points + [points[0]]


def gear(teeth: int = 12, root_radius: float = 8.0, tip_radius: float = 10.0) -> list[Point]:
    teeth = max(3, min(256, int(teeth)))
    radii: list[float] = []
    for _ in range(teeth):
        radii.extend((root_radius, tip_radius, tip_radius, root_radius))
    return radial_profile(radii)


def spiral(
    start_radius: float = 1.0,
    end_radius: float = 12.0,
    turns: float = 3.0,
    segments_per_turn: int = 48,
) -> list[Point]:
    count = max(8, int(abs(turns) * max(8, segments_per_turn)))
    return [
        (
            (start_radius + (end_radius - start_radius) * i / count)
            * math.cos(-math.pi / 2 + turns * 2 * math.pi * i / count),
            (start_radius + (end_radius - start_radius) * i / count)
            * math.sin(-math.pi / 2 + turns * 2 * math.pi * i / count),
        )
        for i in range(count + 1)
    ]


def superellipse(rx: float = 10.0, ry: float = 8.0, exponent: float = 4.0) -> list[Point]:
    exponent = max(0.2, float(exponent))
    points = []
    for index in range(128):
        angle = 2 * math.pi * index / 128
        cosine, sine = math.cos(angle), math.sin(angle)
        points.append(
            (
                rx * math.copysign(abs(cosine) ** (2.0 / exponent), cosine),
                ry * math.copysign(abs(sine) ** (2.0 / exponent), sine),
            )
        )
    return points + [points[0]]


def teardrop(radius: float = 10.0) -> list[Point]:
    """Smooth symmetric teardrop sampled from two cubic Bézier halves."""
    top, bottom = (0.0, -1.55 * radius), (0.0, radius)

    def cubic(p0: Point, p1: Point, p2: Point, p3: Point, count: int = 40) -> list[Point]:
        values = []
        for index in range(count):
            t = index / count
            u = 1.0 - t
            values.append(
                (
                    u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
                )
            )
        return values

    right = cubic(top, (radius * 0.75, -radius * 0.6), (radius * 1.2, radius * 0.3), bottom)
    left = cubic(bottom, (-radius * 1.2, radius * 0.3), (-radius * 0.75, -radius * 0.6), top)
    return right + left + [top]


def keyhole(
    head_radius: float = 6.0, stem_width: float = 5.0, stem_length: float = 12.0
) -> list[Point]:
    head = Polygon(
        [
            (
                head_radius * math.cos(2 * math.pi * i / 96),
                head_radius * math.sin(2 * math.pi * i / 96),
            )
            for i in range(96)
        ]
    )
    stem = Polygon(
        [
            (-stem_width / 2, 0),
            (stem_width / 2, 0),
            (stem_width / 2, stem_length),
            (-stem_width / 2, stem_length),
        ]
    )
    merged = head.union(stem)
    if merged.geom_type != "Polygon":
        return []
    return [(float(x), float(y)) for x, y in merged.exterior.coords]


def ring(outer_radius: float = 10.0, inner_radius: float = 6.0) -> tuple[list[Point], list[Point]]:
    if not 0 < inner_radius < outer_radius:
        raise ValueError("Inner radius must be between zero and outer radius")
    outer = radial_profile([outer_radius] * 96)
    inner = list(reversed(radial_profile([inner_radius] * 96)))
    return outer, inner


def chamfered_star(
    points: int = 5,
    outer_radius: float = 10.0,
    inner_ratio: float = 0.45,
    chamfer: float = 0.16,
) -> list[Point]:
    base = radial_profile(
        [outer_radius if i % 2 == 0 else outer_radius * inner_ratio for i in range(points * 2)]
    )[:-1]
    result: list[Point] = []
    amount = max(0.01, min(0.45, chamfer))
    for index, point in enumerate(base):
        previous = base[index - 1]
        following = base[(index + 1) % len(base)]
        result.extend(
            [
                (
                    point[0] + (previous[0] - point[0]) * amount,
                    point[1] + (previous[1] - point[1]) * amount,
                ),
                (
                    point[0] + (following[0] - point[0]) * amount,
                    point[1] + (following[1] - point[1]) * amount,
                ),
            ]
        )
    return result + [result[0]]


def rounded_star(
    points: int = 5, outer_radius: float = 10.0, inner_ratio: float = 0.45
) -> list[Point]:
    base = radial_profile(
        [outer_radius if i % 2 == 0 else outer_radius * inner_ratio for i in range(points * 2)]
    )
    polygon = Polygon(base).buffer(-outer_radius * 0.06).buffer(outer_radius * 0.06, quad_segs=6)
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return base
    return [(float(x), float(y)) for x, y in polygon.exterior.coords]


def finger_joint_box(
    width: float = 30.0, height: float = 20.0, fingers: int = 4, depth: float = 2.0
) -> list[Point]:
    """Closed rectangular outline with alternating outward finger tabs."""
    corners = [
        (-width / 2, -height / 2),
        (width / 2, -height / 2),
        (width / 2, height / 2),
        (-width / 2, height / 2),
    ]
    result: list[Point] = []
    fingers = max(1, min(64, int(fingers)))
    for edge_index, (start, end) in enumerate(zip(corners, corners[1:] + corners[:1])):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        nx, ny = uy, -ux
        steps = fingers * 2
        for step in range(steps):
            a = step / steps
            b = (step + 1) / steps
            offset = depth if step % 2 == 0 else 0.0
            result.append((start[0] + dx * a + nx * offset, start[1] + dy * a + ny * offset))
            result.append((start[0] + dx * b + nx * offset, start[1] + dy * b + ny * offset))
    return result + [result[0]]


def regular_polygon_from_edge(start: Point, end: Point, sides: int = 6) -> list[Point]:
    sides = max(3, min(64, int(sides)))
    edge = math.dist(start, end)
    if edge <= 1e-12:
        raise ValueError("Polygon edge must have non-zero length")
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    dx, dy = (end[0] - start[0]) / edge, (end[1] - start[1]) / edge
    apothem = edge / (2.0 * math.tan(math.pi / sides))
    center = (midpoint[0] - dy * apothem, midpoint[1] + dx * apothem)
    angle = math.atan2(start[1] - center[1], start[0] - center[0])
    radius = math.dist(center, start)
    points = [
        (
            center[0] + radius * math.cos(angle + 2 * math.pi * index / sides),
            center[1] + radius * math.sin(angle + 2 * math.pi * index / sides),
        )
        for index in range(sides)
    ]
    return points + [points[0]]


def dovetail_box(
    width: float = 30.0, height: float = 20.0, tails: int = 3, depth: float = 3.0
) -> list[Point]:
    """Rectangular panel with trapezoidal dovetail tabs on top and bottom."""
    tails = max(1, min(32, int(tails)))
    half_w, half_h = width / 2.0, height / 2.0
    points: list[Point] = [(-half_w, -half_h)]
    segment = width / tails
    shoulder = segment * 0.22
    for index in range(tails):
        left = -half_w + index * segment
        right = left + segment
        points.extend(
            [
                (left + shoulder, -half_h),
                (left + shoulder * 0.45, -half_h - depth),
                (right - shoulder * 0.45, -half_h - depth),
                (right - shoulder, -half_h),
                (right, -half_h),
            ]
        )
    points.append((half_w, half_h))
    for index in reversed(range(tails)):
        left = -half_w + index * segment
        right = left + segment
        points.extend(
            [
                (right - shoulder, half_h),
                (right - shoulder * 0.45, half_h + depth),
                (left + shoulder * 0.45, half_h + depth),
                (left + shoulder, half_h),
                (left, half_h),
            ]
        )
    return points + [points[0]]


def tabbed_panel(
    width: float = 30.0, height: float = 20.0, tab: float = 6.0, depth: float = 3.0
) -> list[Point]:
    """Rectangle with a centered outward tab on each edge."""
    hw, hh, ht = width / 2.0, height / 2.0, tab / 2.0
    return [
        (-hw, -hh),
        (-ht, -hh),
        (-ht, -hh - depth),
        (ht, -hh - depth),
        (ht, -hh),
        (hw, -hh),
        (hw, -ht),
        (hw + depth, -ht),
        (hw + depth, ht),
        (hw, ht),
        (hw, hh),
        (ht, hh),
        (ht, hh + depth),
        (-ht, hh + depth),
        (-ht, hh),
        (-hw, hh),
        (-hw, ht),
        (-hw - depth, ht),
        (-hw - depth, -ht),
        (-hw, -ht),
        (-hw, -hh),
    ]


__all__ = [
    "chamfered_star",
    "finger_joint_box",
    "gear",
    "keyhole",
    "radial_profile",
    "ring",
    "rounded_star",
    "spiral",
    "superellipse",
    "teardrop",
    "regular_polygon_from_edge",
    "dovetail_box",
    "tabbed_panel",
]
