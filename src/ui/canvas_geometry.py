"""Canvas geometry helpers — re-exports from core geometry.

This module provides geometry building functions for canvas interaction.
Implementation is in src.core.geometry for separation of concerns.
"""

from __future__ import annotations

from src.core.geometry import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.core.geometry.arc import arc_from_center_start_end, arc_from_three_points

__all__ = [
    "arc_from_center_start_end",
    "arc_from_three_points",
    "build_circle_poly",
    "build_ellipse_poly",
    "build_polygon_poly",
    "build_rect_poly",
]
