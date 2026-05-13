"""Phase 3: Shape-aware editing pipeline for arcs, circles, ellipses, splines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.backend.shapes import ArcShape, CircleShape, EllipseShape, SplineShape

if TYPE_CHECKING:
    from src.backend.shapes import Shape


@dataclass
class ControlPoint:
    """Represents an editable control point on a shape."""

    x: float
    y: float
    point_type: str  # "center", "radius", "start", "end", "control_point", etc.
    shape_id: int


class ShapeEditingPipeline:
    """Handles shape-aware editing with control points and curve parameters."""

    @staticmethod
    def get_control_points(shape: Shape) -> list[ControlPoint]:
        """Get all editable control points for a shape."""
        points: list[ControlPoint] = []

        if isinstance(shape, ArcShape):
            # Arc: center, start angle point, end angle point
            points.append(
                ControlPoint(shape.center[0], shape.center[1], "center", shape.id)
            )
            # Start point on circle
            start_rad = shape.start_angle * 3.14159 / 180
            start_x = shape.center[0] + shape.radius * __import__("math").cos(start_rad)
            start_y = shape.center[1] + shape.radius * __import__("math").sin(start_rad)
            points.append(ControlPoint(start_x, start_y, "start_angle", shape.id))
            # End point on circle
            end_rad = shape.end_angle * 3.14159 / 180
            end_x = shape.center[0] + shape.radius * __import__("math").cos(end_rad)
            end_y = shape.center[1] + shape.radius * __import__("math").sin(end_rad)
            points.append(ControlPoint(end_x, end_y, "end_angle", shape.id))

        elif isinstance(shape, CircleShape):
            # Circle: center + 4 cardinal points
            points.append(
                ControlPoint(shape.center[0], shape.center[1], "center", shape.id)
            )
            points.append(
                ControlPoint(
                    shape.center[0], shape.center[1] + shape.radius, "north", shape.id
                )
            )
            points.append(
                ControlPoint(
                    shape.center[0], shape.center[1] - shape.radius, "south", shape.id
                )
            )
            points.append(
                ControlPoint(
                    shape.center[0] + shape.radius, shape.center[1], "east", shape.id
                )
            )
            points.append(
                ControlPoint(
                    shape.center[0] - shape.radius, shape.center[1], "west", shape.id
                )
            )

        elif isinstance(shape, EllipseShape):
            # Ellipse: center + 4 cardinal points
            cos_r = __import__("math").cos(shape.rotation * 3.14159 / 180)
            sin_r = __import__("math").sin(shape.rotation * 3.14159 / 180)
            cx, cy = shape.center
            # Top
            points.append(
                ControlPoint(
                    cx + shape.ry * sin_r, cy + shape.ry * cos_r, "north", shape.id
                )
            )
            # Bottom
            points.append(
                ControlPoint(
                    cx - shape.ry * sin_r, cy - shape.ry * cos_r, "south", shape.id
                )
            )
            # Right
            points.append(
                ControlPoint(
                    cx + shape.rx * cos_r, cy + shape.rx * sin_r, "east", shape.id
                )
            )
            # Left
            points.append(
                ControlPoint(
                    cx - shape.rx * cos_r, cy - shape.rx * sin_r, "west", shape.id
                )
            )
            # Center
            points.append(ControlPoint(cx, cy, "center", shape.id))

        elif isinstance(shape, SplineShape):
            # Spline: all control points
            for i, pt in enumerate(shape.control_points):
                points.append(ControlPoint(pt[0], pt[1], f"control_{i}", shape.id))

        return points

    @staticmethod
    def apply_control_point_drag(
        shape: Shape,
        point_type: str,
        new_x: float,
        new_y: float,
    ) -> None:
        """Update shape based on control point drag.

        Modifies shape properties directly and invalidates tessellation cache.
        """
        import math

        if isinstance(shape, ArcShape):
            if point_type == "center":
                shape.center = (new_x, new_y)
            elif point_type == "start_angle":
                dx = new_x - shape.center[0]
                dy = new_y - shape.center[1]
                angle = math.atan2(dy, dx) * 180 / math.pi
                shape.start_angle = angle
            elif point_type == "end_angle":
                dx = new_x - shape.center[0]
                dy = new_y - shape.center[1]
                angle = math.atan2(dy, dx) * 180 / math.pi
                shape.end_angle = angle

        elif isinstance(shape, CircleShape):
            if point_type == "center":
                shape.center = (new_x, new_y)
            elif point_type in ("north", "south", "east", "west"):
                # Update radius from cardinal point
                dx = new_x - shape.center[0]
                dy = new_y - shape.center[1]
                shape.radius = math.sqrt(dx * dx + dy * dy)

        elif isinstance(shape, EllipseShape):
            if point_type == "center":
                shape.center = (new_x, new_y)
            # TODO: Implement cardinal point updates for ellipse

        elif isinstance(shape, SplineShape):
            if point_type.startswith("control_"):
                idx = int(point_type.split("_")[1])
                if 0 <= idx < len(shape.control_points):
                    shape.control_points[idx] = (new_x, new_y)

        # Invalidate tessellation cache after modification
        shape.invalidate_cache()

    @staticmethod
    def get_snap_points(shape: Shape) -> list[tuple[float, float]]:
        """Get snap points based on shape type (not tessellation grid)."""
        points: list[tuple[float, float]] = []

        if isinstance(shape, ArcShape):
            # Snap to center, start, end
            points.append(shape.center)
            start_rad = shape.start_angle * 3.14159 / 180
            start_x = shape.center[0] + shape.radius * __import__("math").cos(start_rad)
            start_y = shape.center[1] + shape.radius * __import__("math").sin(start_rad)
            points.append((start_x, start_y))

        elif isinstance(shape, CircleShape):
            # Snap to center and cardinal points
            points.append(shape.center)
            points.append((shape.center[0], shape.center[1] + shape.radius))
            points.append((shape.center[0], shape.center[1] - shape.radius))
            points.append((shape.center[0] + shape.radius, shape.center[1]))
            points.append((shape.center[0] - shape.radius, shape.center[1]))

        elif isinstance(shape, EllipseShape):
            points.append(shape.center)
            # Add cardinal points

        elif isinstance(shape, SplineShape):
            # Snap to control points
            points.extend(shape.control_points)

        return points
