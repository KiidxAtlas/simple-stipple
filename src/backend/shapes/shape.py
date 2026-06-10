"""Base shape class and common types for the curve redesign system.

All shapes inherit from Shape and manage their own state, metadata, and tessellation.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal

Point = tuple[float, float]


@dataclass
class Shape(ABC):
    """Base class for all shape types.

    Shapes are the primary storage unit, replacing the old polyline + metadata system.
    Each shape manages:
    - Its own geometric state (points, parameters, etc.)
    - Metadata (visibility, lock status, layer, etc.)
    - Tessellation cache (lazy-computed for rendering)
    - DXF export capability
    """

    id: int
    shape_type: Literal[
        "polyline", "line", "arc", "circle", "ellipse", "spline", "rectangle"
    ]
    name: str = "Shape"
    visible: bool = True
    locked: bool = False
    layer: str = "default"
    construction: bool = False

    # Tessellation cache
    _tessellation_cache: list[Point] | None = field(
        default=None, init=False, repr=False
    )
    _cache_dirty: bool = field(default=True, init=False, repr=False)

    @property
    @abstractmethod
    def control_points(self) -> list[Point]:
        """Return the primary control points for this shape."""
        ...

    @property
    def points(self) -> list[Point]:
        """Get tessellated points for rendering.

        Returns cached tessellation if available, otherwise computes and caches.
        """
        if self._cache_dirty or self._tessellation_cache is None:
            self._tessellation_cache = self._compute_tessellation()
            self._cache_dirty = False
        return self._tessellation_cache

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Get bounding box as (x0, y0, x1, y1)."""
        if not self.control_points:
            return (0, 0, 0, 0)
        xs = [pt[0] for pt in self.control_points]
        ys = [pt[1] for pt in self.control_points]
        return (min(xs), min(ys), max(xs), max(ys))

    def invalidate_cache(self) -> None:
        """Mark tessellation cache as dirty."""
        self._cache_dirty = True

    @abstractmethod
    def _compute_tessellation(self) -> list[Point]:
        """Compute tessellated point sequence. Implemented by subclasses."""
        ...

    def transform(self, matrix: tuple[tuple[float, float, float], ...]) -> None:
        """Apply 2D affine transformation to shape.

        Default implementation transforms control points. Subclasses may override.
        """
        # Simple 2D affine: apply to each control point
        new_points = []
        for pt in self.control_points:
            x, y = pt
            # Assume 2x3 matrix: [[a, b, tx], [c, d, ty]]
            if len(matrix) == 2 and len(matrix[0]) == 3:
                a, b, tx = matrix[0]
                c, d, ty = matrix[1]
                new_x = a * x + b * y + tx
                new_y = c * x + d * y + ty
                new_points.append((new_x, new_y))
            else:
                # Fallback: just translate
                new_points.append(pt)

        # Subclasses should override this to properly transform their properties
        self.invalidate_cache()

    def copy(self, new_id: int) -> Shape:
        """Create a deep copy with a new ID."""
        copy = deepcopy(self)
        copy.id = new_id
        return copy


class PolylineShape(Shape):
    """A polyline made of point sequences (user-drawn or generated)."""

    def __init__(
        self, id: int, control_points: list[Point], closed: bool = False, **kwargs
    ):
        super().__init__(id=id, shape_type="polyline", **kwargs)
        self._control_points = list(control_points)
        self.closed = closed

    @property
    def control_points(self) -> list[Point]:
        return self._control_points

    @control_points.setter
    def control_points(self, value: list[Point]):
        self._control_points = list(value)
        self.invalidate_cache()

    def _compute_tessellation(self) -> list[Point]:
        """Polylines don't need tessellation — return points as-is."""
        return list(self._control_points)

class LineShape(Shape):
    """A simple line segment (two points)."""

    def __init__(self, id: int, start: Point, end: Point, **kwargs):
        super().__init__(id=id, shape_type="line", **kwargs)
        self.start = start
        self.end = end

    @property
    def control_points(self) -> list[Point]:
        return [self.start, self.end]

    def _compute_tessellation(self) -> list[Point]:
        """Lines don't need tessellation."""
        return [self.start, self.end]

class ArcShape(Shape):
    """A circular arc defined by center, radius, and angle range."""

    def __init__(
        self,
        id: int,
        center: Point,
        radius: float,
        start_angle: float,  # degrees
        end_angle: float,  # degrees
        segments: int = 24,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="arc", **kwargs)
        self.center = center
        self.radius = abs(radius)
        self.start_angle = start_angle % 360.0
        self.end_angle = end_angle % 360.0
        self.segments = max(2, min(segments, 256))

    @property
    def control_points(self) -> list[Point]:
        """Return arc's geometric points (center, start, end)."""
        start_rad = math.radians(self.start_angle)
        end_rad = math.radians(self.end_angle)
        start_pt = (
            self.center[0] + self.radius * math.cos(start_rad),
            self.center[1] + self.radius * math.sin(start_rad),
        )
        end_pt = (
            self.center[0] + self.radius * math.cos(end_rad),
            self.center[1] + self.radius * math.sin(end_rad),
        )
        return [self.center, start_pt, end_pt]

    def _compute_tessellation(self) -> list[Point]:
        """Tessellate arc into point sequence."""
        points = []
        start = math.radians(self.start_angle)
        end = math.radians(self.end_angle)

        # Handle wrap-around (e.g., 350° to 10° = 20° arc)
        if end < start:
            end += 2 * math.pi

        step = (end - start) / self.segments
        for i in range(self.segments + 1):
            angle = start + i * step
            x = self.center[0] + self.radius * math.cos(angle)
            y = self.center[1] + self.radius * math.sin(angle)
            points.append((x, y))

        return points

class CircleShape(Shape):
    """A circle defined by center and radius."""

    def __init__(
        self, id: int, center: Point, radius: float, segments: int = 64, **kwargs
    ):
        super().__init__(id=id, shape_type="circle", **kwargs)
        self.center = center
        self.radius = abs(radius)
        self.segments = max(4, min(segments, 512))

    @property
    def control_points(self) -> list[Point]:
        """Return circle's control points (center + cardinal points)."""
        r = self.radius
        return [
            self.center,
            (self.center[0] + r, self.center[1]),  # East
            (self.center[0] - r, self.center[1]),  # West
            (self.center[0], self.center[1] + r),  # North
            (self.center[0], self.center[1] - r),  # South
        ]

    def _compute_tessellation(self) -> list[Point]:
        """Tessellate circle into point sequence."""
        points = []
        for i in range(self.segments):
            angle = 2 * math.pi * i / self.segments
            x = self.center[0] + self.radius * math.cos(angle)
            y = self.center[1] + self.radius * math.sin(angle)
            points.append((x, y))
        # Close the circle
        points.append(points[0])
        return points

class EllipseShape(Shape):
    """An ellipse defined by center, radii, and rotation."""

    def __init__(
        self,
        id: int,
        center: Point,
        rx: float,  # semi-major or semi-minor
        ry: float,  # semi-major or semi-minor
        rotation: float = 0,  # degrees
        segments: int = 64,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="ellipse", **kwargs)
        self.center = center
        self.rx = abs(rx)
        self.ry = abs(ry)
        self.rotation = rotation % 360.0
        self.segments = max(4, min(segments, 512))

    @property
    def control_points(self) -> list[Point]:
        """Return ellipse's control points (center + cardinal points)."""
        # For simplicity, return cardinal points along major/minor axes
        rot_rad = math.radians(self.rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)

        # Rotated cardinal points
        pts = [self.center]  # Center

        # Point on x-axis (semi-major)
        pts.append((
            self.center[0] + self.rx * cos_r,
            self.center[1] + self.rx * sin_r,
        ))

        # Point on y-axis (semi-minor)
        pts.append((
            self.center[0] - self.ry * sin_r,
            self.center[1] + self.ry * cos_r,
        ))

        return pts

    def _compute_tessellation(self) -> list[Point]:
        """Tessellate ellipse into point sequence."""
        points = []
        rot_rad = math.radians(self.rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)

        for i in range(self.segments):
            angle = 2 * math.pi * i / self.segments

            # Unrotated point on ellipse
            x_unrot = self.rx * math.cos(angle)
            y_unrot = self.ry * math.sin(angle)

            # Apply rotation
            x = self.center[0] + x_unrot * cos_r - y_unrot * sin_r
            y = self.center[1] + x_unrot * sin_r + y_unrot * cos_r

            points.append((x, y))

        # Close the ellipse
        points.append(points[0])
        return points

class RectangleShape(Shape):
    """A rectangle defined by center, width, and height."""

    def __init__(
        self,
        id: int,
        center: Point,
        width: float,
        height: float,
        rotation: float = 0,  # degrees
        **kwargs,
    ):
        super().__init__(id=id, shape_type="rectangle", **kwargs)
        self.center = center
        self.width = abs(width)
        self.height = abs(height)
        self.rotation = rotation % 360.0

    @property
    def control_points(self) -> list[Point]:
        """Return rectangle's corner points."""
        # Unrotated corners
        hw = self.width / 2
        hh = self.height / 2
        corners = [
            (-hw, -hh),
            (hw, -hh),
            (hw, hh),
            (-hw, hh),
        ]

        # Apply rotation
        rot_rad = math.radians(self.rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)

        rotated = []
        for x, y in corners:
            rx = x * cos_r - y * sin_r
            ry = x * sin_r + y * cos_r
            rotated.append((self.center[0] + rx, self.center[1] + ry))

        return rotated

    def _compute_tessellation(self) -> list[Point]:
        """Rectangle as polyline (4 corners + close)."""
        pts = self.control_points
        return list(pts) + [pts[0]]  # Close the shape

class SplineShape(Shape):
    """A B-spline curve defined by control points."""

    def __init__(
        self,
        id: int,
        control_points: list[Point],
        degree: int = 3,
        closed: bool = False,
        segments: int = 24,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="spline", **kwargs)
        self._control_points = list(control_points)
        self.degree = min(degree, len(control_points) - 1)
        self.closed = closed
        self.segments = max(4, segments)

    @property
    def control_points(self) -> list[Point]:
        return self._control_points

    @control_points.setter
    def control_points(self, value: list[Point]):
        self._control_points = list(value)
        self.degree = min(self.degree, len(value) - 1)
        self.invalidate_cache()

    def _compute_tessellation(self) -> list[Point]:
        """Tessellate spline using B-spline interpolation."""
        if len(self._control_points) < 2:
            return []

        if len(self._control_points) == 2:
            # Just a line
            return list(self._control_points)

        # Simple cubic interpolation (not full B-spline, but good enough)
        # For full B-spline, use scipy or implement proper knot vector
        from src.backend.geometry.spline import build_spline_poly

        try:
            return build_spline_poly(
                self._control_points,
                segments=self.segments,
                closed=self.closed,
            )
        except Exception:
            # Fallback to polyline if spline fails
            return list(self._control_points)

