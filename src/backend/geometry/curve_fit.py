"""Fit a dense/jagged polyline (e.g. Trace's bitmap-tracing output) down to
a small set of bezier anchors + tangent handles — turning "a pile of
points" into an actual editable curve.

This is not a strict least-squares curve fit (á la Schneider's
FitCurve/Graphics Gems): it reduces the point count with the same
Douglas-Peucker simplification already used by "Simplify" (so ``tolerance``
bounds how much the *anchor positions* deviate from the original points),
then estimates a tangent handle per surviving anchor from its neighbors
(a Catmull-Rom-style central difference), zeroing the handle at points
where the polyline turns sharply so real corners stay sharp corners
instead of getting rounded off. The resulting curve can bow slightly
outside ``tolerance`` between anchors — this trades strict error-bound
guarantees for a simple, fast, robust fit that reuses already-tested
simplification code.
"""

from __future__ import annotations

import math

from shapely.errors import GEOSException
from shapely.geometry import LineString

Point = tuple[float, float]


def fit_polyline_to_bezier(
    points: list[Point],
    *,
    tolerance: float = 0.3,
    corner_angle_deg: float = 55.0,
    closed: bool = False,
    tension: float = 0.35,
) -> tuple[list[Point], list[Point]] | None:
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
    tangents: list[Point] = []
    for i in range(n):
        prev_pt = anchors[(i - 1) % n] if closed or i > 0 else None
        next_pt = anchors[(i + 1) % n] if closed or i < n - 1 else None
        tangents.append(
            _anchor_tangent(prev_pt, anchors[i], next_pt, corner_angle_deg, tension)
        )
    return anchors, tangents


def _collapse_close_anchors(
    anchors: list[Point], *, min_gap: float, closed: bool
) -> list[Point]:
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
    prev_pt: Point | None,
    curr: Point,
    next_pt: Point | None,
    corner_angle_deg: float,
    tension: float,
) -> Point:
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
    prev_pt: Point, curr: Point, next_pt: Point, corner_angle_deg: float
) -> bool:
    v1x, v1y = curr[0] - prev_pt[0], curr[1] - prev_pt[1]
    v2x, v2y = next_pt[0] - curr[0], next_pt[1] - curr[1]
    len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if len1 < 1e-9 or len2 < 1e-9:
        return True
    cos_a = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (len1 * len2)))
    turn_deg = math.degrees(math.acos(cos_a))
    return turn_deg > corner_angle_deg
