"""Backend geometry utilities: arcs, curve fitting, shape builders, splines.

Four previously-separate modules merged here — all pure-math helpers with
no cross-dependencies on each other, none individually large enough to
argue for staying split.

Constants (EPS, SNAP_DIST, MIN_SCALE, etc.) have been moved to
``simple_stipple.engine.cad.constants`` as the single source of truth.  This module
re-exports the core constants for backwards compatibility.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Any, NamedTuple

from shapely.affinity import scale as shapely_scale  # type: ignore[import-untyped]
from shapely.errors import GEOSException
from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

from simple_stipple.engine.cad.constants import (
    EPS,
    EPS_SQ_DEGENERATE,
    MIN_SCALE,
    SNAP_DIST,
)

PointTuple = tuple[float, float]

# Re-export for backwards compatibility — callers may import from geometry.
__all__ = [
    "EPS",
    "EPS_SQ_DEGENERATE",
    "MIN_SCALE",
    "SNAP_DIST",
]


def distance(first: PointTuple, second: PointTuple) -> float:
    """Return the Euclidean distance between two drawing points."""
    return math.dist(first, second)


def angle(first: PointTuple, vertex: PointTuple, third: PointTuple) -> float:
    """Return the smaller angle in degrees formed at *vertex*."""
    a = (first[0] - vertex[0], first[1] - vertex[1])
    b = (third[0] - vertex[0], third[1] - vertex[1])
    lengths = math.hypot(*a) * math.hypot(*b)
    if lengths <= EPS_SQ_DEGENERATE:
        raise ValueError("Angle points must be distinct")
    cosine = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / lengths))
    return math.degrees(math.acos(cosine))


def diameter(radius: float) -> float:
    """Return a circle diameter, rejecting an invalid negative radius."""
    if radius < 0:
        raise ValueError("Radius must be non-negative")
    return radius * 2.0


def approx_equal(a: float, b: float, *, eps: float = EPS) -> bool:
    return abs(a - b) <= eps


def points_close(
    p: tuple[float, float],
    q: tuple[float, float],
    *,
    eps: float = EPS,
) -> bool:
    return abs(p[0] - q[0]) <= eps and abs(p[1] - q[1]) <= eps


# ══════════════════════════════════════════════════════════════════════════
# Arc calculations
# ══════════════════════════════════════════════════════════════════════════

_LOG = logging.getLogger(__name__)

# Hard cap on arc tessellation segments to bound memory/CPU on bad inputs.
MAX_ARC_SEGMENTS = 2048


def _clamp_segments(n: int) -> int:
    return max(2, min(int(n), MAX_ARC_SEGMENTS))


class ArcSpec(NamedTuple):
    center: PointTuple
    radius: float
    start_angle: float
    end_angle: float


def _straight_line_pts(p0: PointTuple, p2: PointTuple, steps: int) -> list[PointTuple]:
    """Return *steps+1* linearly interpolated points from p0 to p2."""
    return [
        (
            p0[0] + (p2[0] - p0[0]) * i / steps,
            p0[1] + (p2[1] - p0[1]) * i / steps,
        )
        for i in range(steps + 1)
    ]


def arc_from_three_points(
    p0: PointTuple,
    p1: PointTuple,
    p2: PointTuple,
    segments: int = 24,
) -> list[PointTuple]:
    """Build arc points passing through three points."""
    x1, y1 = p0
    x2, y2 = p1
    x3, y3 = p2
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        # Points are collinear — no circle exists.  Return a straight-line
        # approximation with the same point count so callers always receive
        # segments+1 points and never mistake 3 raw points for a dense arc.
        _LOG.debug("arc_from_three_points: collinear input, returning straight line")
        return _straight_line_pts(p0, p2, _clamp_segments(segments))
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
        _LOG.debug("arc_from_three_points: degenerate radius, returning straight line")
        return _straight_line_pts(p0, p2, _clamp_segments(segments))

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

    steps = _clamp_segments(segments)
    step = (a2 - a0) / steps
    return [
        (ux + radius * math.cos(a0 + i * step), uy + radius * math.sin(a0 + i * step))
        for i in range(steps + 1)
    ]


def arc_from_center_start_end(
    center: PointTuple,
    start: PointTuple,
    end: PointTuple,
    segments: int = 24,
) -> list[PointTuple]:
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

    steps = _clamp_segments(segments)
    step = (a1 - a0) / steps
    return [
        (cx + radius * math.cos(a0 + i * step), cy + radius * math.sin(a0 + i * step))
        for i in range(steps + 1)
    ]


def arc_spec_from_center_start_end(
    center: PointTuple,
    start: PointTuple,
    end: PointTuple,
) -> ArcSpec | None:
    cx, cy = center
    sx, sy = start
    ex, ey = end
    radius = math.hypot(sx - cx, sy - cy)
    end_radius = math.hypot(ex - cx, ey - cy)
    if radius < 1e-9 or end_radius < 1e-9:
        return None

    start_angle = math.degrees(math.atan2(sy - cy, sx - cx)) % 360.0
    end_angle = math.degrees(math.atan2(ey - cy, ex - cx)) % 360.0
    return ArcSpec((cx, cy), radius, start_angle, end_angle)


def arc_spec_from_three_points(
    p0: PointTuple,
    p1: PointTuple,
    p2: PointTuple,
) -> ArcSpec | None:
    x1, y1 = p0
    x2, y2 = p1
    x3, y3 = p2
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
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
        return None
    a0 = math.degrees(math.atan2(y1 - uy, x1 - ux)) % 360.0
    a2 = math.degrees(math.atan2(y3 - uy, x3 - ux)) % 360.0
    return ArcSpec((ux, uy), radius, a0, a2)


# ════════════════════════════════════════════════════════════════════════════
# Curve fitting (dense polyline -> bezier anchors/tangents)
# ════════════════════════════════════════════════════════════════════════════


def fit_polyline_to_bezier(
    points: list[PointTuple],
    *,
    tolerance: float = 0.3,
    corner_angle_deg: float = 55.0,
    closed: bool = False,
    tension: float = 0.35,
) -> tuple[list[PointTuple], list[PointTuple]] | None:
    """Return ``(anchors, tangents)`` for a ``kind="bezier"`` entity, or
    ``None`` if the input is degenerate (fewer than 3 usable anchors after
    simplification).

    ``tangents[i]`` is the single symmetric handle vector for ``anchors[i]``
    — matching the Pen tool's data model (see ``build_bezier_poly``): the
    outgoing control point is ``anchors[i] + tangents[i]`` and the incoming
    one (for the segment ending here) is ``anchors[i] - tangents[i]``. A
    zero vector marks a corner anchor.
    """
    if len(points) < 3:
        return None
    pts = points[:-1] if closed and points[0] == points[-1] else list(points)
    if len(pts) < 3:
        return None

    try:
        simplified = LineString(pts).simplify(tolerance, preserve_topology=False)
    except (GEOSException, ValueError):
        return None
    anchors = [(float(x), float(y)) for x, y in simplified.coords]
    if closed and len(anchors) >= 2 and anchors[0] == anchors[-1]:
        anchors = anchors[:-1]
    if len(anchors) < 3:
        return None

    # Noisy input can make Douglas-Peucker keep two closely-spaced vertices
    # right around a single sharp corner instead of one — each half of that
    # turn then falls under corner_angle_deg on its own, and the "corner"
    # renders as a small rounded fillet instead of a sharp point. Collapsing
    # anchors that survived simplification but sit implausibly close
    # together (relative to tolerance) fixes the turn back into one vertex.
    anchors = _collapse_close_anchors(anchors, min_gap=tolerance * 3.0, closed=closed)
    if len(anchors) < 3:
        return None

    n = len(anchors)
    tangents: list[PointTuple] = []
    for i in range(n):
        prev_pt = anchors[(i - 1) % n] if closed or i > 0 else None
        next_pt = anchors[(i + 1) % n] if closed or i < n - 1 else None
        tangents.append(_anchor_tangent(prev_pt, anchors[i], next_pt, corner_angle_deg, tension))
    return anchors, tangents


def _collapse_close_anchors(
    anchors: list[PointTuple], *, min_gap: float, closed: bool
) -> list[PointTuple]:
    """Drop interior anchors closer than ``min_gap`` to the last surviving
    one — always keeps the first anchor (and, for an open curve, the true
    last point). Returns the input unchanged if collapsing would leave
    fewer than 3 anchors (better a cluttered corner than a degenerate fit).
    """
    if len(anchors) <= 3 or min_gap <= 0.0:
        return anchors
    kept = [anchors[0]]
    end = len(anchors) if closed else len(anchors) - 1
    for i in range(1, end):
        px, py = kept[-1]
        x, y = anchors[i]
        if math.hypot(x - px, y - py) >= min_gap:
            kept.append(anchors[i])
    if not closed:
        kept.append(anchors[-1])
    return kept if len(kept) >= 3 else anchors


def _anchor_tangent(
    prev_pt: PointTuple | None,
    curr: PointTuple,
    next_pt: PointTuple | None,
    corner_angle_deg: float,
    tension: float,
) -> PointTuple:
    if prev_pt is not None and next_pt is not None:
        if _is_corner(prev_pt, curr, next_pt, corner_angle_deg):
            return (0.0, 0.0)
        dx, dy = next_pt[0] - prev_pt[0], next_pt[1] - prev_pt[1]
        seg_a = math.hypot(curr[0] - prev_pt[0], curr[1] - prev_pt[1])
        seg_b = math.hypot(next_pt[0] - curr[0], next_pt[1] - curr[1])
        handle_len = tension * min(seg_a, seg_b)
    elif next_pt is not None:  # open start: one-sided estimate
        dx, dy = next_pt[0] - curr[0], next_pt[1] - curr[1]
        handle_len = tension * math.hypot(dx, dy)
    elif prev_pt is not None:  # open end: one-sided estimate
        dx, dy = curr[0] - prev_pt[0], curr[1] - prev_pt[1]
        handle_len = tension * math.hypot(dx, dy)
    else:
        return (0.0, 0.0)

    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 0.0)
    return (dx / length * handle_len, dy / length * handle_len)


def _is_corner(
    prev_pt: PointTuple, curr: PointTuple, next_pt: PointTuple, corner_angle_deg: float
) -> bool:
    v1x, v1y = curr[0] - prev_pt[0], curr[1] - prev_pt[1]
    v2x, v2y = next_pt[0] - curr[0], next_pt[1] - curr[1]
    len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if len1 < 1e-9 or len2 < 1e-9:
        return True
    cos_a = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (len1 * len2)))
    turn_deg = math.degrees(math.acos(cos_a))
    return turn_deg > corner_angle_deg


# ════════════════════════════════════════════════════════════════════════════
# Shape builders (polylines centred at origin + translate/clip helpers)
# ════════════════════════════════════════════════════════════════════════════


Polyline = list[PointTuple]


def _to_coords(geom: Any) -> list[PointTuple]:
    """Extract exterior coordinates from a Shapely geometry as a closed list."""
    return list(geom.exterior.coords)


def shape_rect(w: float, h: float) -> list[tuple[float, float]]:
    """Return an axis-aligned rectangle polyline centered at origin."""
    if w <= 0 or h <= 0:
        return []
    hw, hh = w / 2, h / 2
    return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]


@lru_cache(maxsize=256)
def _shape_circle_cached(r: float, n: int) -> tuple[PointTuple, ...]:
    from simple_stipple.engine.geometry.jit import tessellate_arc

    segments = max(12, int(n))
    return tuple(
        (float(x), float(y)) for x, y in tessellate_arc(0.0, 0.0, r, 0.0, math.tau, segments)
    )


def shape_circle(r: float, n: int = 64) -> list[PointTuple]:
    """Return a circle polyline centered at origin."""
    if r <= 0:
        return []
    return list(_shape_circle_cached(float(r), int(n)))


@lru_cache(maxsize=256)
def _shape_ellipse_cached(rx: float, ry: float, n: int) -> tuple[PointTuple, ...]:
    circle = Point(0, 0).buffer(1.0, quad_segs=max(3, n // 4))
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


def shape_slot(length: float, width: float, n_end: int = 24) -> list[tuple[float, float]]:
    """Obround / slot profile centred at origin."""
    if width <= 0 or length <= 0:
        return []
    radius = width / 2.0
    half_straight = max(0.0, length / 2.0 - radius)
    line = LineString([(-half_straight, 0.0), (half_straight, 0.0)])
    slot = line.buffer(radius, quad_segs=max(4, n_end // 2), cap_style="round")
    return _to_coords(slot)


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


def build_rounded_rect_poly(
    cx: float,
    cy: float,
    width: float,
    height: float,
    radius: float | None = None,
    corner_segments: int = 6,
) -> Polyline:
    """Build a closed rounded rectangle with a clamped corner radius."""
    half_w, half_h = abs(width) / 2.0, abs(height) / 2.0
    if half_w <= 0 or half_h <= 0:
        return []
    r = min(radius if radius is not None else min(half_w, half_h) * 0.2, half_w, half_h)
    r = max(0.0, float(r))
    if r <= 1e-9:
        return build_rect_poly(cx, cy, width, height)
    steps = max(2, int(corner_segments))
    result: Polyline = []
    for corner_x, corner_y, start_angle in (
        (cx + half_w - r, cy + half_h - r, 0.0),
        (cx - half_w + r, cy + half_h - r, 90.0),
        (cx - half_w + r, cy - half_h + r, 180.0),
        (cx + half_w - r, cy - half_h + r, 270.0),
    ):
        for step in range(steps + 1):
            angle = math.radians(start_angle + step * 90.0 / steps)
            result.append((corner_x + r * math.cos(angle), corner_y + r * math.sin(angle)))
    result.append(result[0])
    return result


def build_star_poly(
    cx: float,
    cy: float,
    outer_radius: float,
    points: int = 5,
    inner_ratio: float = 0.45,
    rotation: float = -90.0,
) -> Polyline:
    """Build a closed alternating-radius star polygon."""
    count = max(3, min(64, int(points)))
    outer = abs(float(outer_radius))
    if outer <= 0:
        return []
    inner = outer * max(0.05, min(0.95, float(inner_ratio)))
    start = math.radians(rotation)
    result = [
        (
            cx + (outer if index % 2 == 0 else inner) * math.cos(start + math.pi * index / count),
            cy + (outer if index % 2 == 0 else inner) * math.sin(start + math.pi * index / count),
        )
        for index in range(count * 2)
    ]
    return result + [result[0]]


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


def clip_polygon_to_outline(shape: Polygon, outline: Polygon) -> BaseGeometry:
    """Return ``shape`` clipped to ``outline``."""
    return outline.intersection(shape)


def clip_line_to_outline(points: list[PointTuple], outline: Polygon) -> BaseGeometry:
    """Return line geometry clipped to ``outline``."""
    return outline.intersection(LineString(points))


# ════════════════════════════════════════════════════════════════════════════
# Spline sampling (Catmull-Rom, cubic bezier tessellation)
# ════════════════════════════════════════════════════════════════════════════


def _points_close(a: PointTuple, b: PointTuple, eps: float = EPS) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _catmull_rom(
    p0: PointTuple, p1: PointTuple, p2: PointTuple, p3: PointTuple, t: float
) -> PointTuple:
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
    points: list[PointTuple],
    segments: int = 24,
    *,
    closed: bool = False,
) -> list[PointTuple]:
    """Sample a smooth Catmull-Rom spline through the provided control points."""
    if len(points) < 2:
        return list(points)

    steps = max(4, int(segments))
    pts: list[PointTuple] = [(float(pt[0]), float(pt[1])) for pt in points]
    if closed and len(pts) >= 3 and _points_close(pts[0], pts[-1]):
        pts = pts[:-1]

    if len(pts) == 2:
        return [(pts[0][0], pts[0][1]), (pts[1][0], pts[1][1])]

    result: list[PointTuple] = []
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


def _cubic_bezier(
    p0: PointTuple, c1: PointTuple, c2: PointTuple, p1: PointTuple, t: float
) -> PointTuple:
    mt = 1.0 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    x = a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0]
    y = a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1]
    return (x, y)


def build_bezier_poly(
    anchors: list[PointTuple],
    tangents: list[PointTuple],
    segments: int = 16,
    *,
    closed: bool = False,
    handles_in: list[PointTuple] | None = None,
    handles_out: list[PointTuple] | None = None,
) -> list[PointTuple]:
    """Tessellate a piecewise cubic bezier through ``anchors``.

    ``tangents`` is the legacy symmetric-handle representation. When
    ``handles_in`` / ``handles_out`` are provided they independently control
    each side of an anchor. The segment
    from anchor i to i+1 uses control points ``anchor[i] + tangent[i]`` and
    ``anchor[i+1] - tangent[i+1]`` (the mirrored, "incoming" side of the next
    anchor's handle), matching the single-handle-drag convention most pen
    tools use for smooth anchors. A zero tangent yields a straight segment.
    """
    n = len(anchors)
    if n < 2:
        return list(anchors)
    steps = max(2, int(segments))
    pts = [(float(p[0]), float(p[1])) for p in anchors]
    legacy = [
        (float(t[0]), float(t[1])) if i < len(tangents) else (0.0, 0.0)
        for i, t in enumerate(tangents[:n])
    ]
    while len(legacy) < n:
        legacy.append((0.0, 0.0))

    def _vectors(values: list[PointTuple] | None, fallback, *, negate=False):
        if values is None:
            return [(-x, -y) if negate else (x, y) for x, y in fallback]
        vectors = [(float(x), float(y)) for x, y in values[:n]]
        vectors.extend([(0.0, 0.0)] * (n - len(vectors)))
        return vectors

    outgoing = _vectors(handles_out, legacy)
    incoming = _vectors(handles_in, legacy, negate=True)

    segment_pairs = list(zip(range(n - 1), range(1, n)))
    if closed:
        segment_pairs.append((n - 1, 0))

    result: list[PointTuple] = [pts[0]]
    for i, j in segment_pairs:
        p0, p1 = pts[i], pts[j]
        c1 = (p0[0] + outgoing[i][0], p0[1] + outgoing[i][1])
        c2 = (p1[0] + incoming[j][0], p1[1] + incoming[j][1])
        for step in range(1, steps + 1):
            t = step / steps
            result.append(_cubic_bezier(p0, c1, c2, p1, t))
    return result


__all__ = [
    "ArcSpec",
    "EPS",
    "EPS_SQ_DEGENERATE",
    "MAX_ARC_SEGMENTS",
    "MIN_SCALE",
    "SNAP_DIST",
    "approx_equal",
    "arc_from_center_start_end",
    "arc_from_three_points",
    "arc_spec_from_center_start_end",
    "arc_spec_from_three_points",
    "build_bezier_poly",
    "build_circle_poly",
    "build_ellipse_poly",
    "build_polygon_poly",
    "build_rect_poly",
    "build_rounded_rect_poly",
    "build_star_poly",
    "build_spline_poly",
    "clip_line_to_outline",
    "clip_polygon_to_outline",
    "fit_polyline_to_bezier",
    "points_close",
    "shape_circle",
    "shape_ellipse",
    "shape_polygon",
    "shape_rect",
    "shape_slot",
]


def minimum_clearance(paths: list[list[tuple[float, float]]]) -> float | None:
    """Return the minimum distance between valid path geometries."""
    geometries = []
    for points in paths:
        if len(points) < 2:
            continue
        try:
            closed = len(points) >= 4 and math.dist(points[0], points[-1]) < 0.01
            geometry = Polygon(points) if closed else LineString(points)
            if not geometry.is_empty:
                geometries.append(geometry)
        except (TypeError, ValueError, GEOSException):
            continue
    if len(geometries) < 2:
        return None
    return min(
        geometries[first].distance(geometries[second])
        for first in range(len(geometries))
        for second in range(first + 1, len(geometries))
    )
