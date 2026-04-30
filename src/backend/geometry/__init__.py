"""Public geometry API."""

from src.backend.geometry.arc import arc_from_center_start_end, arc_from_three_points
from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
    clip_line_to_outline,
    clip_polygon_to_outline,
)
from src.backend.geometry.spline import build_spline_poly

# ── Geometry tolerances ─────────────────────────────────────────────────────
# EPS is the canonical "two world coordinates are the same" tolerance, in
# the same units as the canvas (millimetres). Use it for closure detection,
# duplicate-point culling, near-equality comparisons.
#
# EPS_SQ_DEGENERATE is for degenerate-segment detection (squared distance);
# segments with length^2 below this are treated as zero-length.
EPS = 1e-6
EPS_SQ_DEGENERATE = 1e-12
MAX_ARC_SEGMENTS = 2048


def approx_equal(a: float, b: float, *, eps: float = EPS) -> bool:
    return abs(a - b) <= eps


def points_close(
    p: tuple[float, float],
    q: tuple[float, float],
    *,
    eps: float = EPS,
) -> bool:
    return abs(p[0] - q[0]) <= eps and abs(p[1] - q[1]) <= eps


__all__ = [
    "EPS",
    "EPS_SQ_DEGENERATE",
    "MAX_ARC_SEGMENTS",
    "approx_equal",
    "arc_from_center_start_end",
    "arc_from_three_points",
    "build_circle_poly",
    "build_ellipse_poly",
    "build_polygon_poly",
    "build_rect_poly",
    "build_spline_poly",
    "clip_line_to_outline",
    "clip_polygon_to_outline",
    "points_close",
]
