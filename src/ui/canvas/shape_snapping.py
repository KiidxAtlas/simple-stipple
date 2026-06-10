"""Phase 5: Shape-aware snapping system."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from src.backend.shapes import ArcShape, CircleShape, EllipseShape, SplineShape

if TYPE_CHECKING:
    from src.backend.shapes import Shape


class ShapeSnapEngine:
    """Shape-aware snapping for precise alignment and positioning."""

    SNAP_RADIUS = 10.0  # Screen pixels

    @staticmethod
    def get_snap_candidates(shape: Shape) -> list[tuple[float, float, str]]:
        """Get snap points for a shape.

        Returns list of (x, y, snap_type) where snap_type describes the point.
        """
        points: list[tuple[float, float, str]] = []

        if isinstance(shape, ArcShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Start point
            start_rad = shape.start_angle * math.pi / 180
            start_x = shape.center[0] + shape.radius * math.cos(start_rad)
            start_y = shape.center[1] + shape.radius * math.sin(start_rad)
            points.append((start_x, start_y, "arc_start"))
            # End point
            end_rad = shape.end_angle * math.pi / 180
            end_x = shape.center[0] + shape.radius * math.cos(end_rad)
            end_y = shape.center[1] + shape.radius * math.sin(end_rad)
            points.append((end_x, end_y, "arc_end"))

        elif isinstance(shape, CircleShape):
            # Center (primary snap point)
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points
            points.append((
                shape.center[0],
                shape.center[1] + shape.radius,
                "circle_north",
            ))
            points.append((
                shape.center[0],
                shape.center[1] - shape.radius,
                "circle_south",
            ))
            points.append((
                shape.center[0] + shape.radius,
                shape.center[1],
                "circle_east",
            ))
            points.append((
                shape.center[0] - shape.radius,
                shape.center[1],
                "circle_west",
            ))

        elif isinstance(shape, EllipseShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points (approximate for rotation)
            cos_r = math.cos(shape.rotation * math.pi / 180)
            sin_r = math.sin(shape.rotation * math.pi / 180)
            # North
            points.append((
                shape.center[0] + shape.ry * sin_r,
                shape.center[1] + shape.ry * cos_r,
                "ellipse_north",
            ))
            # South
            points.append((
                shape.center[0] - shape.ry * sin_r,
                shape.center[1] - shape.ry * cos_r,
                "ellipse_south",
            ))

        elif isinstance(shape, SplineShape):
            # Control points are primary snap targets for splines
            for i, (x, y) in enumerate(shape.control_points):
                points.append((x, y, f"spline_control_{i}"))

        # Fallback: tessellation points (lower priority)
        if not points:
            for i, (x, y) in enumerate(shape.points):
                points.append((x, y, f"tessellation_{i}"))

        return points

