"""Shape data model + factory for the curve redesign system.

Two previously-separate modules merged here — ``factory.py`` was purely a
constructor/deserializer for the ``Shape`` subclasses this module defines,
with no independent reason to be a separate file.

All shapes inherit from ``Shape`` and manage their own state, metadata, and
tessellation.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

Point = tuple[float, float]


def _finite(*vals: object) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in vals)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _rotate_pt(p: Point, center: Point, angle_deg: float) -> Point:
    if abs(angle_deg) < 1e-9:
        return p
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    dx, dy = p[0] - center[0], p[1] - center[1]
    return (center[0] + dx * ca - dy * sa, center[1] + dx * sa + dy * ca)


def _scale_pt(p: Point, center: Point, factor: float) -> Point:
    if abs(factor - 1.0) < 1e-9:
        return p
    return (
        center[0] + (p[0] - center[0]) * factor,
        center[1] + (p[1] - center[1]) * factor,
    )


def _mirror_pt(p: Point, center: Point, axis: str) -> Point:
    if axis == "horizontal":
        return (2 * center[0] - p[0], p[1])
    if axis == "vertical":
        return (p[0], 2 * center[1] - p[1])
    return p


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
        "polyline",
        "line",
        "arc",
        "circle",
        "ellipse",
        "spline",
        "bezier",
        "rectangle",
        "polygon",
        "slot",
        "rounded_rectangle",
        "star",
    ]
    name: str = "Shape"
    visible: bool = True
    locked: bool = False
    layer: str = "default"
    construction: bool = False

    # Tessellation cache
    _tessellation_cache: list[Point] | None = field(default=None, init=False, repr=False)
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

    # ── Geometric operations ─────────────────────────────────────────────
    # Each shape transforms its own parametric fields; there is exactly one
    # implementation per shape kind (previously this logic was duplicated as
    # kind-string switches in view.py, dxf/io.py, and factory.py).

    def _map_points(self, fn) -> None:
        """Apply a point mapping to this shape's defining geometry.

        Default covers point-list shapes; parametric subclasses override.
        """
        raise NotImplementedError

    def translate(self, dx: float, dy: float) -> None:
        self._map_points(lambda p: (p[0] + dx, p[1] + dy))

    def rotate(self, center: Point, angle_deg: float) -> None:
        self._map_points(lambda p: _rotate_pt(p, center, angle_deg))

    def scale(self, center: Point, factor: float) -> None:
        self._map_points(lambda p: _scale_pt(p, center, factor))

    def mirror(self, center: Point, axis: str) -> None:
        self._map_points(lambda p: _mirror_pt(p, center, axis))

    def set_parameter(self, key: str, value: float) -> bool:
        """Update a user-editable defining parameter when supported."""
        return False

    def move_control_point(self, index: int, point: Point) -> bool:
        """Move one editable control point when this shape exposes it."""
        return False

    # ── Interop ──────────────────────────────────────────────────────────

    def to_meta_dict(self) -> tuple[str, dict | None]:
        """Return ``(kind, meta)`` in the polyline+metadata dict encoding used
        by ``EntityRecord`` (the canvas's live entity representation)."""
        return (self.shape_type, None)

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        """Emit this shape as a native DXF entity onto ``msp``.

        Returns ``False`` when the shape has no native DXF form or its
        parameters are degenerate (non-finite, zero radius, …); callers then
        fall back to writing the tessellated polyline, so no shape is ever
        dropped or written corrupt.
        """
        return False

    def copy(self, new_id: int) -> Shape:
        """Create a deep copy with a new ID."""
        copy = deepcopy(self)
        copy.id = new_id
        return copy


class PolylineShape(Shape):
    """A polyline made of point sequences (user-drawn or generated)."""

    def __init__(self, id: int, control_points: list[Point], closed: bool = False, **kwargs):
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

    def _map_points(self, fn) -> None:
        self.control_points = [fn(p) for p in self._control_points]

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True


class PolygonShape(Shape):
    """Regular polygon retaining center/radius/side-count parameters."""

    def __init__(
        self,
        id: int,
        center: Point,
        radius: float,
        sides: int = 6,
        rotation: float = 0.0,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="polygon", **kwargs)
        self.center = center
        self.radius = abs(radius)
        self.sides = max(3, min(64, int(sides)))
        self.rotation = rotation % 360.0

    @property
    def control_points(self) -> list[Point]:
        return [self.center]

    def _compute_tessellation(self) -> list[Point]:
        # Match backend.geometry.shape_polygon: rotation=0 starts at 12
        # o'clock. Keeping Shape and generator conventions aligned is vital
        # because Shape supplies snap candidates while the generator supplies
        # the stored/rendered points.
        offset = math.radians(self.rotation - 90.0)
        points = [
            (
                self.center[0] + self.radius * math.cos(offset + 2 * math.pi * i / self.sides),
                self.center[1] + self.radius * math.sin(offset + 2 * math.pi * i / self.sides),
            )
            for i in range(self.sides)
        ]
        return points + [points[0]]

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)
        self.rotation = (self.rotation + angle_deg) % 360.0
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.radius *= abs(factor)
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key == "radius" and value > 0:
            self.radius = float(value)
        elif key == "sides" and 3 <= int(value) <= 64:
            self.sides = int(value)
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "polygon",
            {
                "center": tuple(self.center),
                "radius": self.radius,
                "sides": self.sides,
                "rotation": self.rotation,
            },
        )


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

    def _map_points(self, fn) -> None:
        self.start = fn(self.start)
        self.end = fn(self.end)
        self.invalidate_cache()

    def move_control_point(self, index: int, point: Point) -> bool:
        if index == 0:
            self.start = point
        elif index in {1, -1}:
            self.end = point
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return ("line", {"start": tuple(self.start), "end": tuple(self.end)})

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        sx, sy = self.start
        ex, ey = self.end
        if not _finite(sx, sy, ex, ey) or math.hypot(ex - sx, ey - sy) <= 1e-9:
            return False
        msp.add_line((float(sx), float(sy)), (float(ex), float(ey)), dxfattribs=dxfattribs)
        return True


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

    def _map_points(self, fn) -> None:
        """Transform the arc by mapping its center/start/end points, then
        re-deriving radius and angles (matches legacy canvas behavior,
        including the sweep flip a mirror produces)."""
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
        c = fn(self.center)
        s = fn(start_pt)
        e = fn(end_pt)
        self.center = c
        self.radius = math.hypot(s[0] - c[0], s[1] - c[1])
        self.start_angle = math.degrees(math.atan2(s[1] - c[1], s[0] - c[0])) % 360.0
        self.end_angle = math.degrees(math.atan2(e[1] - c[1], e[0] - c[0])) % 360.0
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key != "radius" or value <= 0:
            return False
        self.radius = float(value)
        self.invalidate_cache()
        return True

    def move_control_point(self, index: int, point: Point) -> bool:
        if index == 0:
            self.center = point
        elif index == 1:
            self.radius = max(1e-3, math.dist(self.center, point))
            self.start_angle = (
                math.degrees(math.atan2(point[1] - self.center[1], point[0] - self.center[0]))
                % 360.0
            )
        elif index == 2:
            self.radius = max(1e-3, math.dist(self.center, point))
            self.end_angle = (
                math.degrees(math.atan2(point[1] - self.center[1], point[0] - self.center[0]))
                % 360.0
            )
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "arc",
            {
                "center": tuple(self.center),
                "radius": self.radius,
                "start_angle": self.start_angle,
                "end_angle": self.end_angle,
            },
        )

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cx, cy = self.center
        if (
            not _finite(cx, cy, self.radius, self.start_angle, self.end_angle)
            or self.radius <= 1e-9
        ):
            return False
        msp.add_arc(
            (float(cx), float(cy)),
            float(self.radius),
            float(self.start_angle),
            float(self.end_angle),
            dxfattribs=dxfattribs,
        )
        return True


class CircleShape(Shape):
    """A circle defined by center and radius."""

    def __init__(self, id: int, center: Point, radius: float, segments: int = 64, **kwargs):
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

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.radius = self.radius * abs(factor)
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key != "radius" or value <= 0:
            return False
        self.radius = float(value)
        self.invalidate_cache()
        return True

    def move_control_point(self, index: int, point: Point) -> bool:
        if index == 0:
            self.center = point
        elif index == 1:
            self.radius = max(1e-3, math.dist(self.center, point))
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return ("circle", {"center": tuple(self.center), "radius": self.radius})

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cx, cy = self.center
        if not _finite(cx, cy, self.radius) or self.radius <= 1e-9:
            return False
        msp.add_circle((float(cx), float(cy)), float(self.radius), dxfattribs=dxfattribs)
        return True


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
        pts.append(
            (
                self.center[0] + self.rx * cos_r,
                self.center[1] + self.rx * sin_r,
            )
        )

        # Point on y-axis (semi-minor)
        pts.append(
            (
                self.center[0] - self.ry * sin_r,
                self.center[1] + self.ry * cos_r,
            )
        )

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

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)
        self.rotation = (self.rotation + angle_deg) % 360.0
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.rx = self.rx * abs(factor)
        self.ry = self.ry * abs(factor)
        self.invalidate_cache()

    def mirror(self, center: Point, axis: str) -> None:
        self.center = _mirror_pt(self.center, center, axis)
        if axis == "horizontal":
            self.rotation = (180.0 - self.rotation) % 360.0
        elif axis == "vertical":
            self.rotation = (-self.rotation) % 360.0
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key not in {"rx", "ry"} or value <= 0:
            return False
        setattr(self, key, float(value))
        self.invalidate_cache()
        return True

    def move_control_point(self, index: int, point: Point) -> bool:
        if index == 0:
            self.center = point
            self.invalidate_cache()
            return True
        if index not in {1, 2, 3, 4}:
            return False
        angle = math.radians(-self.rotation)
        dx, dy = point[0] - self.center[0], point[1] - self.center[1]
        local_x = dx * math.cos(angle) - dy * math.sin(angle)
        local_y = dx * math.sin(angle) + dy * math.cos(angle)
        if index in {1, 3}:
            self.rx = max(1e-3, abs(local_x))
        else:
            self.ry = max(1e-3, abs(local_y))
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "ellipse",
            {
                "center": tuple(self.center),
                "rx": self.rx,
                "ry": self.ry,
                "rotation": self.rotation,
            },
        )

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cx, cy = self.center
        rx, ry, rot_deg = self.rx, self.ry, self.rotation
        if not _finite(cx, cy, rx, ry, rot_deg) or rx <= 0 or ry <= 0:
            return False
        # DXF requires ratio ≤ 1 (minor/major). If ry > rx, the major axis
        # is the y one — swap and rotate 90°.
        if ry > rx:
            rx, ry = ry, rx
            rot_deg += 90.0
        rot = math.radians(rot_deg)
        msp.add_ellipse(
            (float(cx), float(cy)),
            (rx * math.cos(rot), rx * math.sin(rot)),
            ratio=min(ry / rx, 1.0),
            dxfattribs=dxfattribs,
        )
        return True


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

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)
        self.rotation = (self.rotation + angle_deg) % 360.0
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.width = self.width * abs(factor)
        self.height = self.height * abs(factor)
        self.invalidate_cache()

    def mirror(self, center: Point, axis: str) -> None:
        self.center = _mirror_pt(self.center, center, axis)
        if axis == "horizontal":
            self.rotation = (180.0 - self.rotation) % 360.0
        elif axis == "vertical":
            self.rotation = (-self.rotation) % 360.0
        self.invalidate_cache()

    def move_control_point(self, index: int, point: Point) -> bool:
        if index not in {0, 1, 2, 3, 4}:
            return False
        angle = math.radians(-self.rotation)
        dx, dy = point[0] - self.center[0], point[1] - self.center[1]
        local_x = dx * math.cos(angle) - dy * math.sin(angle)
        local_y = dx * math.sin(angle) + dy * math.cos(angle)
        self.width = max(1e-3, 2.0 * abs(local_x))
        self.height = max(1e-3, 2.0 * abs(local_y))
        self.invalidate_cache()
        return True

    def set_parameter(self, key: str, value: float) -> bool:
        if key == "width" and value > 0:
            self.width = float(value)
        elif key == "height" and value > 0:
            self.height = float(value)
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "rectangle",
            {
                "center": tuple(self.center),
                "width": self.width,
                "height": self.height,
                "rotation": self.rotation,
            },
        )

    # Rectangles have no native DXF entity — base to_dxf() returns False and
    # the caller emits the tessellated LWPOLYLINE (matches legacy writer).


class RoundedRectangleShape(RectangleShape):
    """Rectangle with a retained, editable corner radius."""

    def __init__(
        self,
        id: int,
        center: Point,
        width: float,
        height: float,
        radius: float,
        rotation: float = 0.0,
        **kwargs,
    ):
        super().__init__(id, center, width, height, rotation, **kwargs)
        self.shape_type = "rounded_rectangle"
        self.radius = max(0.0, min(abs(float(radius)), self.width / 2, self.height / 2))

    def _compute_tessellation(self) -> list[Point]:
        from src.backend.geometry import build_rounded_rect_poly

        points = build_rounded_rect_poly(
            self.center[0], self.center[1], self.width, self.height, self.radius
        )
        if self.rotation:
            points = [_rotate_pt(point, self.center, self.rotation) for point in points]
        return points

    def scale(self, center: Point, factor: float) -> None:
        super().scale(center, factor)
        self.radius *= abs(factor)
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key == "width" and value > 0:
            self.width = float(value)
        elif key == "height" and value > 0:
            self.height = float(value)
        elif key == "radius" and value >= 0:
            self.radius = min(float(value), self.width / 2, self.height / 2)
        else:
            return False
        self.radius = min(self.radius, self.width / 2, self.height / 2)
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "rounded_rectangle",
            {
                "center": tuple(self.center),
                "width": self.width,
                "height": self.height,
                "radius": self.radius,
                "rotation": self.rotation,
            },
        )


class StarShape(Shape):
    """Regular star retaining point count and inner-radius ratio."""

    def __init__(
        self,
        id: int,
        center: Point,
        radius: float,
        points: int = 5,
        inner_ratio: float = 0.45,
        rotation: float = -90.0,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="star", **kwargs)
        self.center = center
        self.radius = abs(float(radius))
        self.point_count = max(3, min(64, int(points)))
        self.inner_ratio = max(0.05, min(0.95, float(inner_ratio)))
        self.rotation = float(rotation) % 360.0

    @property
    def control_points(self) -> list[Point]:
        return [self.center]

    def _compute_tessellation(self) -> list[Point]:
        from src.backend.geometry import build_star_poly

        return build_star_poly(
            self.center[0],
            self.center[1],
            self.radius,
            self.point_count,
            inner_ratio=self.inner_ratio,
            rotation=self.rotation,
        )

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)
        self.rotation = (self.rotation + angle_deg) % 360.0
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.radius *= abs(factor)
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key == "radius" and value > 0:
            self.radius = float(value)
        elif key == "points" and 3 <= int(value) <= 64:
            self.point_count = int(value)
        elif key == "inner_ratio" and 0.05 <= value <= 0.95:
            self.inner_ratio = float(value)
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "star",
            {
                "center": tuple(self.center),
                "radius": self.radius,
                "points": self.point_count,
                "inner_ratio": self.inner_ratio,
                "rotation": self.rotation,
            },
        )


class SlotShape(Shape):
    """An obround/stadium slot: a straight-sided rectangle capped by two
    semicircles, defined by center, length, width, and rotation.

    Unrotated, a slot's bounding box is exactly ``length`` x ``width`` (the
    rounded ends are inscribed within it), same footprint as a rectangle.
    """

    def __init__(
        self,
        id: int,
        center: Point,
        length: float,
        width: float,
        rotation: float = 0,  # degrees
        **kwargs,
    ):
        super().__init__(id=id, shape_type="slot", **kwargs)
        self.center = center
        self.length = abs(length)
        self.width = abs(width)
        self.rotation = rotation % 360.0

    @property
    def control_points(self) -> list[Point]:
        """Return the slot's bounding-box corners, rotated about the center."""
        hw = self.length / 2
        hh = self.width / 2
        corners = [
            (-hw, -hh),
            (hw, -hh),
            (hw, hh),
            (-hw, hh),
        ]
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
        from src.backend.geometry import shape_slot

        pts = shape_slot(self.length, self.width)
        if not pts:
            return []
        rot_rad = math.radians(self.rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)
        return [
            (
                self.center[0] + x * cos_r - y * sin_r,
                self.center[1] + x * sin_r + y * cos_r,
            )
            for x, y in pts
        ]

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)
        self.invalidate_cache()

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)
        self.rotation = (self.rotation + angle_deg) % 360.0
        self.invalidate_cache()

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.length *= abs(factor)
        self.width *= abs(factor)
        self.invalidate_cache()

    def mirror(self, center: Point, axis: str) -> None:
        self.center = _mirror_pt(self.center, center, axis)
        if axis == "horizontal":
            self.rotation = (180.0 - self.rotation) % 360.0
        elif axis == "vertical":
            self.rotation = (-self.rotation) % 360.0
        self.invalidate_cache()

    def set_parameter(self, key: str, value: float) -> bool:
        if key == "length" and value > 0:
            self.length = float(value)
        elif key == "width" and value > 0:
            self.width = float(value)
        else:
            return False
        self.invalidate_cache()
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "slot",
            {
                "center": tuple(self.center),
                "length": self.length,
                "width": self.width,
                "rotation": self.rotation,
            },
        )

    # Slots have no native DXF entity — base to_dxf() returns False and the
    # caller emits the tessellated polyline (matches rectangle).


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
        from src.backend.geometry import build_spline_poly

        try:
            return build_spline_poly(
                self._control_points,
                segments=self.segments,
                closed=self.closed,
            )
        except (ValueError, TypeError, IndexError, ZeroDivisionError):
            # Fallback to polyline if spline fails (malformed control points)
            return list(self._control_points)

    def _map_points(self, fn) -> None:
        self.control_points = [fn(p) for p in self._control_points]

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "spline",
            {
                "control_points": [tuple(p) for p in self._control_points],
                "degree": self.degree,
                "closed": self.closed,
                "segments": self.segments,
            },
        )

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cps = [
            (float(p[0]), float(p[1]))
            for p in self._control_points
            if len(p) >= 2 and _finite(p[0], p[1])
        ]
        if len(cps) < 2:
            return False
        # ezdxf needs at least degree+1 control points; clamp the degree
        # rather than emitting an invalid spline.
        try:
            degree = int(self.degree)
        except (TypeError, ValueError):
            degree = 3
        degree = max(1, min(degree, len(cps) - 1))
        msp.add_spline(cps, degree=degree, dxfattribs=dxfattribs)
        return True


class BezierShape(Shape):
    """Bezier path with independent incoming/outgoing anchor handles."""

    def __init__(
        self,
        id: int,
        control_points: list[Point],
        tangents: list[Point] | None = None,
        handles_in: list[Point] | None = None,
        handles_out: list[Point] | None = None,
        node_types: list[str] | None = None,
        closed: bool = False,
        segments: int = 16,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="bezier", **kwargs)
        self._control_points = list(control_points)
        self.tangents = list(tangents or [])
        self.handles_out = list(handles_out if handles_out is not None else self.tangents)
        self.handles_in = list(
            handles_in if handles_in is not None else [(-x, -y) for x, y in self.tangents]
        )
        self.node_types = list(node_types or [])
        self.node_types.extend(["symmetric"] * (len(control_points) - len(self.node_types)))
        self.closed = closed
        self.segments = max(4, int(segments))

    @property
    def control_points(self) -> list[Point]:
        return self._control_points

    @control_points.setter
    def control_points(self, value: list[Point]) -> None:
        self._control_points = list(value)
        self.invalidate_cache()

    def _compute_tessellation(self) -> list[Point]:
        from src.backend.geometry import build_bezier_poly

        return build_bezier_poly(
            self._control_points,
            self.tangents,
            segments=self.segments,
            closed=self.closed,
            handles_in=self.handles_in,
            handles_out=self.handles_out,
        )

    def _map_points(self, fn) -> None:
        old_points = list(self._control_points)
        mapped_points = [fn(point) for point in old_points]

        def _map_vectors(vectors: list[Point]) -> list[Point]:
            mapped: list[Point] = []
            for index, vector in enumerate(vectors):
                if index >= len(old_points):
                    break
                anchor = old_points[index]
                tip = fn((anchor[0] + vector[0], anchor[1] + vector[1]))
                mapped.append((tip[0] - mapped_points[index][0], tip[1] - mapped_points[index][1]))
            return mapped

        mapped_tangents: list[Point] = []
        for index, tangent in enumerate(self.tangents):
            if index >= len(old_points):
                break
            anchor = old_points[index]
            tip = fn((anchor[0] + tangent[0], anchor[1] + tangent[1]))
            mapped_tangents.append(
                (tip[0] - mapped_points[index][0], tip[1] - mapped_points[index][1])
            )
        self._control_points = mapped_points
        self.tangents = mapped_tangents
        self.handles_in = _map_vectors(self.handles_in)
        self.handles_out = _map_vectors(self.handles_out)
        self.invalidate_cache()

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "bezier",
            {
                "tangents": [tuple(tangent) for tangent in self.tangents],
                "handles_in": [tuple(handle) for handle in self.handles_in],
                "handles_out": [tuple(handle) for handle in self.handles_out],
                "node_types": list(self.node_types),
                "closed": self.closed,
                "segments": self.segments,
            },
        )


# ════════════════════════════════════════════════════════════════════════════
# Factory — construct/deserialize Shape instances
# ════════════════════════════════════════════════════════════════════════════


def _as_point(value: Any) -> Point:
    """Coerce a JSON-decoded [x, y] list (or tuple) into a Point."""
    x, y = value
    return (float(x), float(y))


class ShapeFactory:
    """Factory for creating shapes from various inputs."""

    _id_counter = 0

    @classmethod
    def next_id(cls) -> int:
        """Get next unique shape ID."""
        cls._id_counter += 1
        return cls._id_counter

    @classmethod
    def reset_id_counter(cls, start: int = 0) -> None:
        """Reset ID counter (useful for testing/migration)."""
        cls._id_counter = start

    @classmethod
    def polyline(cls, points: list[Point], closed: bool = False, **kwargs) -> PolylineShape:
        """Create a polyline shape."""
        return PolylineShape(id=cls.next_id(), control_points=points, closed=closed, **kwargs)

    @classmethod
    def line(cls, start: Point, end: Point, **kwargs) -> LineShape:
        """Create a line shape."""
        return LineShape(id=cls.next_id(), start=start, end=end, **kwargs)

    @classmethod
    def arc(
        cls,
        center: Point,
        radius: float,
        start_angle: float,
        end_angle: float,
        segments: int = 24,
        **kwargs,
    ) -> ArcShape:
        """Create an arc shape."""
        return ArcShape(
            id=cls.next_id(),
            center=center,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            segments=segments,
            **kwargs,
        )

    @classmethod
    def circle(cls, center: Point, radius: float, segments: int = 64, **kwargs) -> CircleShape:
        """Create a circle shape."""
        return CircleShape(
            id=cls.next_id(), center=center, radius=radius, segments=segments, **kwargs
        )

    @classmethod
    def ellipse(
        cls,
        center: Point,
        rx: float,
        ry: float,
        rotation: float = 0,
        segments: int = 64,
        **kwargs,
    ) -> EllipseShape:
        """Create an ellipse shape."""
        return EllipseShape(
            id=cls.next_id(),
            center=center,
            rx=rx,
            ry=ry,
            rotation=rotation,
            segments=segments,
            **kwargs,
        )

    @classmethod
    def rectangle(
        cls, center: Point, width: float, height: float, rotation: float = 0, **kwargs
    ) -> RectangleShape:
        """Create a rectangle shape."""
        return RectangleShape(
            id=cls.next_id(),
            center=center,
            width=width,
            height=height,
            rotation=rotation,
            **kwargs,
        )

    @classmethod
    def slot(
        cls, center: Point, length: float, width: float, rotation: float = 0, **kwargs
    ) -> SlotShape:
        """Create a slot (obround) shape."""
        return SlotShape(
            id=cls.next_id(),
            center=center,
            length=length,
            width=width,
            rotation=rotation,
            **kwargs,
        )

    @classmethod
    def rounded_rectangle(
        cls,
        center: Point,
        width: float,
        height: float,
        radius: float,
        rotation: float = 0.0,
        **kwargs,
    ) -> RoundedRectangleShape:
        return RoundedRectangleShape(
            id=cls.next_id(),
            center=center,
            width=width,
            height=height,
            radius=radius,
            rotation=rotation,
            **kwargs,
        )

    @classmethod
    def star(
        cls,
        center: Point,
        radius: float,
        points: int = 5,
        inner_ratio: float = 0.45,
        rotation: float = -90.0,
        **kwargs,
    ) -> StarShape:
        return StarShape(
            id=cls.next_id(),
            center=center,
            radius=radius,
            points=points,
            inner_ratio=inner_ratio,
            rotation=rotation,
            **kwargs,
        )

    @classmethod
    def polygon(
        cls,
        center: Point,
        radius: float,
        sides: int = 6,
        rotation: float = 0.0,
        **kwargs,
    ) -> PolygonShape:
        return PolygonShape(
            id=cls.next_id(),
            center=center,
            radius=radius,
            sides=sides,
            rotation=rotation,
            **kwargs,
        )

    @classmethod
    def spline(
        cls,
        control_points: list[Point],
        degree: int = 3,
        closed: bool = False,
        segments: int = 24,
        **kwargs,
    ) -> SplineShape:
        """Create a spline shape."""
        return SplineShape(
            id=cls.next_id(),
            control_points=control_points,
            degree=degree,
            closed=closed,
            segments=segments,
            **kwargs,
        )

    @classmethod
    def bezier(
        cls,
        control_points: list[Point],
        tangents: list[Point] | None = None,
        handles_in: list[Point] | None = None,
        handles_out: list[Point] | None = None,
        node_types: list[str] | None = None,
        closed: bool = False,
        segments: int = 16,
        **kwargs,
    ) -> BezierShape:
        return BezierShape(
            id=cls.next_id(),
            control_points=control_points,
            tangents=tangents,
            handles_in=handles_in,
            handles_out=handles_out,
            node_types=node_types,
            closed=closed,
            segments=segments,
            **kwargs,
        )

    @classmethod
    def from_meta_dict(
        cls,
        kind: str,
        points: list[Point],
        metadata: dict[str, Any] | None = None,
        is_construction: bool = False,
        is_hidden: bool = False,
        is_locked: bool = False,
        name: str = "Imported Shape",
    ) -> Shape:
        """Create a shape from the polyline + metadata dict encoding.

        This is the live bridge between ``EntityRecord``'s ``kind``/``meta``
        storage (the canvas's actual entity representation) and the
        ``Shape`` class hierarchy's transform methods.
        """
        metadata = metadata or {}

        # Common kwargs
        kwargs = {
            "name": name,
            "construction": is_construction,
            "visible": not is_hidden,
            "locked": is_locked,
        }

        # Convert based on kind and metadata
        if kind == "arc" and metadata.get("center"):
            # Arc with full metadata
            return cls.arc(
                center=metadata["center"],
                radius=metadata.get("radius", 0),
                start_angle=metadata.get("start_angle", 0),
                end_angle=metadata.get("end_angle", 180),
                segments=metadata.get("segments", 24),
                **kwargs,
            )

        elif kind == "circle" and metadata.get("center"):
            # Circle with metadata
            return cls.circle(
                center=metadata["center"],
                radius=metadata.get("radius", 0),
                segments=metadata.get("segments", 64),
                **kwargs,
            )

        elif kind == "ellipse" and metadata.get("center"):
            # Ellipse with metadata
            return cls.ellipse(
                center=metadata["center"],
                rx=metadata.get("rx", 0),
                ry=metadata.get("ry", 0),
                rotation=metadata.get("rotation", 0),
                segments=metadata.get("segments", 64),
                **kwargs,
            )

        elif kind == "rectangle" and len(points) >= 4:
            # Rectangle: reconstruct center and dimensions from points
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            return cls.rectangle(
                center=_as_point(metadata.get("center", center)),
                width=float(metadata.get("width", width)),
                height=float(metadata.get("height", height)),
                rotation=float(metadata.get("rotation", 0)),
                **kwargs,
            )

        elif kind == "slot" and metadata.get("center"):
            return cls.slot(
                center=_as_point(metadata["center"]),
                length=float(metadata.get("length", 0.0)),
                width=float(metadata.get("width", 0.0)),
                rotation=float(metadata.get("rotation", 0.0)),
                **kwargs,
            )

        elif kind == "rounded_rectangle" and metadata.get("center"):
            return cls.rounded_rectangle(
                center=_as_point(metadata["center"]),
                width=float(metadata.get("width", 0.0)),
                height=float(metadata.get("height", 0.0)),
                radius=float(metadata.get("radius", 0.0)),
                rotation=float(metadata.get("rotation", 0.0)),
                **kwargs,
            )

        elif kind == "star" and metadata.get("center"):
            return cls.star(
                center=_as_point(metadata["center"]),
                radius=float(metadata.get("radius", 0.0)),
                points=int(metadata.get("points", 5)),
                inner_ratio=float(metadata.get("inner_ratio", 0.45)),
                rotation=float(metadata.get("rotation", -90.0)),
                **kwargs,
            )

        elif kind == "line" and len(points) >= 2:
            # Line
            return cls.line(start=points[0], end=points[-1], **kwargs)

        elif kind == "spline":
            return cls.spline(
                control_points=[
                    _as_point(point) for point in metadata.get("control_points", points)
                ],
                degree=int(metadata.get("degree", 3)),
                segments=int(metadata.get("segments", 24)),
                closed=bool(metadata.get("closed", False)),
                **kwargs,
            )

        elif kind == "bezier":
            return cls.bezier(
                control_points=points,
                tangents=[_as_point(point) for point in metadata.get("tangents", [])],
                handles_in=[_as_point(point) for point in metadata.get("handles_in", [])]
                if "handles_in" in metadata
                else None,
                handles_out=[_as_point(point) for point in metadata.get("handles_out", [])]
                if "handles_out" in metadata
                else None,
                node_types=[str(value) for value in metadata.get("node_types", [])],
                segments=int(metadata.get("segments", 16)),
                closed=bool(metadata.get("closed", False)),
                **kwargs,
            )

        elif kind == "polygon" and metadata.get("center"):
            return cls.polygon(
                center=_as_point(metadata["center"]),
                radius=float(metadata.get("radius", 0.0)),
                sides=int(metadata.get("sides", 6)),
                rotation=float(metadata.get("rotation", 0.0)),
                **kwargs,
            )

        else:
            # Default to polyline
            closed = kind in {"rectangle", "polygon"} or (
                len(points) >= 2
                and abs(points[0][0] - points[-1][0]) < 1e-6
                and abs(points[0][1] - points[-1][1]) < 1e-6
            )
            return cls.polyline(points=points, closed=closed, **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Shape:
        """Create a shape from a serialized dictionary."""
        shape_type = data.get("type", "polyline")
        shape_id = data.get("id")

        # Common properties
        kwargs = {
            "name": data.get("name", "Shape"),
            "visible": data.get("visible", True),
            "locked": data.get("locked", False),
            "layer": data.get("layer", "default"),
            "construction": data.get("construction", False),
        }

        # Create based on type
        if shape_type == "polyline":
            return PolylineShape(
                id=shape_id or cls.next_id(),
                control_points=data.get("points", []),
                closed=data.get("closed", False),
                **kwargs,
            )

        elif shape_type == "line":
            return LineShape(
                id=shape_id or cls.next_id(),
                start=_as_point(data.get("start", (0, 0))),
                end=_as_point(data.get("end", (0, 0))),
                **kwargs,
            )

        elif shape_type == "arc":
            return ArcShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                start_angle=data.get("start_angle", 0),
                end_angle=data.get("end_angle", 180),
                segments=data.get("segments", 24),
                **kwargs,
            )

        elif shape_type == "circle":
            return CircleShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                segments=data.get("segments", 64),
                **kwargs,
            )

        elif shape_type == "ellipse":
            return EllipseShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                rx=data.get("rx", 0),
                ry=data.get("ry", 0),
                rotation=data.get("rotation", 0),
                segments=data.get("segments", 64),
                **kwargs,
            )

        elif shape_type == "rectangle":
            return RectangleShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                width=data.get("width", 0),
                height=data.get("height", 0),
                rotation=data.get("rotation", 0),
                **kwargs,
            )

        elif shape_type == "slot":
            return SlotShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                length=data.get("length", 0),
                width=data.get("width", 0),
                rotation=data.get("rotation", 0),
                **kwargs,
            )

        elif shape_type == "rounded_rectangle":
            return RoundedRectangleShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                width=data.get("width", 0),
                height=data.get("height", 0),
                radius=data.get("radius", 0),
                rotation=data.get("rotation", 0),
                **kwargs,
            )

        elif shape_type == "star":
            return StarShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                points=data.get("points", 5),
                inner_ratio=data.get("inner_ratio", 0.45),
                rotation=data.get("rotation", -90),
                **kwargs,
            )

        elif shape_type == "spline":
            return SplineShape(
                id=shape_id or cls.next_id(),
                control_points=data.get("control_points", []),
                degree=data.get("degree", 3),
                closed=data.get("closed", False),
                segments=data.get("segments", 24),
                **kwargs,
            )

        elif shape_type == "bezier":
            return BezierShape(
                id=shape_id or cls.next_id(),
                control_points=[_as_point(point) for point in data.get("control_points", [])],
                tangents=[_as_point(point) for point in data.get("tangents", [])],
                handles_in=[_as_point(point) for point in data.get("handles_in", [])]
                if "handles_in" in data
                else None,
                handles_out=[_as_point(point) for point in data.get("handles_out", [])]
                if "handles_out" in data
                else None,
                node_types=[str(value) for value in data.get("node_types", [])],
                closed=data.get("closed", False),
                segments=data.get("segments", 16),
                **kwargs,
            )

        elif shape_type == "polygon":
            return PolygonShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                sides=data.get("sides", 6),
                rotation=data.get("rotation", 0),
                **kwargs,
            )

        else:
            # Unknown type, default to polyline
            return PolylineShape(
                id=shape_id or cls.next_id(),
                control_points=data.get("points", []),
                closed=data.get("closed", False),
                **kwargs,
            )


def shape_from_meta(kind: str, meta: dict[str, Any]) -> Shape | None:
    """Build a Shape from the ``kind`` + metadata dict encoding, or None."""
    try:
        return ShapeFactory.from_dict({"type": kind, "id": 0, **meta})
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def transform_meta(
    kind: str,
    meta: dict[str, Any] | None,
    *,
    transform: str,
    center: Point = (0.0, 0.0),
    factor: float | None = None,
    angle_deg: float = 0.0,
    axis: str | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
) -> dict[str, Any] | None:
    """Apply a geometric transform to ``kind`` + ``meta`` metadata.

    Reconstructs the shape, delegates to its transform method, and returns
    the updated metadata (preserving any extra keys such as ``name``).
    Returns ``None`` when the transform does not apply — callers keep the
    original metadata in that case.
    """
    if not meta or kind == "polyline":
        return None
    shape = shape_from_meta(kind, meta)
    if shape is None:
        return None
    if transform == "translate":
        shape.translate(dx, dy)
    elif transform == "rotate":
        shape.rotate(center, angle_deg)
    elif transform == "scale":
        if factor is None:
            return None
        shape.scale(center, factor)
    elif transform == "mirror":
        if axis is None:
            return None
        shape.mirror(center, axis)
    else:
        return None
    _, new_meta = shape.to_meta_dict()
    if new_meta is None:
        return None
    return {**meta, **new_meta}
