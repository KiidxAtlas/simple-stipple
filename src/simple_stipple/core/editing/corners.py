"""Corner rounding and chamfering as pure point-list operations.

These are the geometry kernels behind the canvas's ``_round_vertex`` /
``_chamfer_vertex`` commands, extracted so the HUD prompt can preview the
result live (``_set_operation_preview``) without touching the document, and
so the math is unit-testable without a canvas.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def rounded_corner_points(
    points: list[Point], vi: int, radius: float, *, closed: bool
) -> list[Point] | None:
    """Return the path with vertex ``vi`` replaced by a tangent arc.

    ``points`` may carry the closing duplicate of a closed path (pass
    ``closed=True``); the returned list mirrors that convention. Returns None
    when the corner can't take a round (endpoints of open paths, degenerate
    or straight corners, radius too large for the adjacent edges).
    """
    if radius <= 0:
        return None
    pts = points[:-1] if closed else list(points)
    n = len(pts)
    if n < 3:
        return None
    if closed and vi == n:
        vi = 0
    if not (0 <= vi < n):
        return None
    if not closed and (vi == 0 or vi == n - 1):
        return None

    prev_i = (vi - 1) % n
    next_i = (vi + 1) % n
    ax, ay = pts[prev_i]
    bx, by = pts[vi]
    cx, cy = pts[next_i]
    u1 = (ax - bx, ay - by)
    u2 = (cx - bx, cy - by)
    l1 = math.hypot(*u1)
    l2 = math.hypot(*u2)
    if l1 < 1e-9 or l2 < 1e-9:
        return None
    u1 = (u1[0] / l1, u1[1] / l1)
    u2 = (u2[0] / l2, u2[1] / l2)
    dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
    phi = math.acos(dot)
    if phi < 1e-3 or abs(math.pi - phi) < 1e-3:
        return None

    offset = radius / math.tan(phi / 2.0)
    offset = min(offset, l1 * 0.45, l2 * 0.45)
    if offset <= 1e-6:
        return None
    r = offset * math.tan(phi / 2.0)

    t1 = (bx + u1[0] * offset, by + u1[1] * offset)
    t2 = (bx + u2[0] * offset, by + u2[1] * offset)

    bis = (u1[0] + u2[0], u1[1] + u2[1])
    bl = math.hypot(*bis)
    if bl < 1e-9:
        return None
    bis = (bis[0] / bl, bis[1] / bl)
    center_dist = r / math.sin(phi / 2.0)
    center = (bx + bis[0] * center_dist, by + bis[1] * center_dist)

    a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
    a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
    # Use the minor arc between tangent points; choosing the major arc
    # produces loop-like rounding artifacts.
    span = a2 - a1
    while span <= -math.pi:
        span += 2 * math.pi
    while span > math.pi:
        span -= 2 * math.pi
    steps = max(4, min(24, int(abs(span) / (math.pi / 18.0))))
    arc_pts = [
        (
            center[0] + r * math.cos(a1 + span * (i / steps)),
            center[1] + r * math.sin(a1 + span * (i / steps)),
        )
        for i in range(steps + 1)
    ]

    new_pts = pts[:vi] + arc_pts + pts[vi + 1 :]
    if closed:
        return new_pts + [new_pts[0]]
    return new_pts


def chamfered_corner_points(
    points: list[Point], vi: int, dist: float, *, closed: bool
) -> list[Point] | None:
    """Return the path with vertex ``vi`` replaced by a bounded chamfer edge.

    Same eligibility rules and closed-path convention as
    :func:`rounded_corner_points`.
    """
    if dist <= 0:
        return None
    pts = points[:-1] if closed else list(points)
    n = len(pts)
    if n < 3:
        return None
    if closed and vi == n:
        vi = 0
    if not 0 <= vi < n or (not closed and vi in {0, n - 1}):
        return None
    previous, current, following = pts[(vi - 1) % n], pts[vi], pts[(vi + 1) % n]
    incoming = (previous[0] - current[0], previous[1] - current[1])
    outgoing = (following[0] - current[0], following[1] - current[1])
    incoming_length, outgoing_length = math.hypot(*incoming), math.hypot(*outgoing)
    if incoming_length < 1e-9 or outgoing_length < 1e-9:
        return None
    length = min(dist, incoming_length * 0.45, outgoing_length * 0.45)
    first = (
        current[0] + incoming[0] / incoming_length * length,
        current[1] + incoming[1] / incoming_length * length,
    )
    second = (
        current[0] + outgoing[0] / outgoing_length * length,
        current[1] + outgoing[1] / outgoing_length * length,
    )
    updated = pts[:vi] + [first, second] + pts[vi + 1 :]
    if closed:
        return updated + [updated[0]]
    return updated


__all__ = ["chamfered_corner_points", "rounded_corner_points"]
