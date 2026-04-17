"""Public geometry API."""

from src.core.geometry.arc import arc_from_center_start_end, arc_from_three_points
from src.core.geometry.primitives import (
    clip_line_to_outline,
    clip_polygon_to_outline,
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)

__all__ = [
    "arc_from_center_start_end",
    "arc_from_three_points",
    "build_circle_poly",
    "build_ellipse_poly",
    "build_polygon_poly",
    "build_rect_poly",
    "clip_line_to_outline",
    "clip_polygon_to_outline",
]
