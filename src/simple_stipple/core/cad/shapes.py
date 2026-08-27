"""Concrete shape classes. Each inherits :class:`Shape` and owns its own
state, metadata, and tessellation."""

from __future__ import annotations

import math

from simple_stipple.core.cad.shape_base import (
    Point,
    Shape,
    _anisotropic_scale_fn,
    _CenterBasedShapeMixin,
    _finite,
    _mirror_pt,
    _ParametricRotateMixin,
    _rotate_pt,
    _scale_pt,
)


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

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        self._map_points(_anisotropic_scale_fn(center, factor_x, factor_y))
        return self

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True


class PolygonShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
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

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        self._map_points(_anisotropic_scale_fn(center, factor_x, factor_y))
        return self

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

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        """A non-uniform scale traces an ellipse, not a circle.

        Since the factors apply along world axes and a circle has no
        preferred direction, the image is always an axis-aligned ellipse
        (rotation 0) with rx = radius * factor_x, ry = radius * factor_y,
        and the *same* start/end angle in that unrotated parameter space —
        this holds regardless of where ``center`` (the scale pivot) sits
        relative to the arc's own center. Declines (returns None) for a
        zero or negative factor: that also mirrors the arc, which additionally
        reverses its sweep direction — a case ``EllipticalArcShape`` doesn't
        need to handle since nothing else produces one that way.
        """
        if factor_x <= 0 or factor_y <= 0:
            return None
        new_center = _anisotropic_scale_fn(center, factor_x, factor_y)(self.center)
        return EllipticalArcShape(
            id=self.id,
            center=new_center,
            rx=self.radius * factor_x,
            ry=self.radius * factor_y,
            start_angle=self.start_angle,
            end_angle=self.end_angle,
            segments=self.segments,
            name=self.name,
            visible=self.visible,
            locked=self.locked,
            layer=self.layer,
            construction=self.construction,
        )

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


class EllipticalArcShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
    """An elliptical arc — what a circular ``ArcShape`` becomes under a
    non-uniform scale. No draw tool creates one directly.

    ``rx``/``ry`` are the semi-axes before ``rotation`` is applied;
    ``start_angle``/``end_angle`` are in the ellipse's own (unrotated,
    pre-``rotation``) parameter space, the same convention ``EllipseShape``
    uses for its cardinal points.
    """

    def __init__(
        self,
        id: int,
        center: Point,
        rx: float,
        ry: float,
        start_angle: float,
        end_angle: float,
        rotation: float = 0.0,
        segments: int = 48,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="elliptical_arc", **kwargs)
        self.center = center
        self.rx = abs(rx)
        self.ry = abs(ry)
        self.rotation = rotation % 360.0
        self.start_angle = start_angle % 360.0
        self.end_angle = end_angle % 360.0
        self.segments = max(2, min(segments, 256))

    def _point_at(self, angle_deg: float) -> Point:
        t = math.radians(angle_deg)
        rot = math.radians(self.rotation)
        lx, ly = self.rx * math.cos(t), self.ry * math.sin(t)
        return (
            self.center[0] + lx * math.cos(rot) - ly * math.sin(rot),
            self.center[1] + lx * math.sin(rot) + ly * math.cos(rot),
        )

    @property
    def control_points(self) -> list[Point]:
        return [self.center, self._point_at(self.start_angle), self._point_at(self.end_angle)]

    def _compute_tessellation(self) -> list[Point]:
        start = math.radians(self.start_angle)
        end = math.radians(self.end_angle)
        if end < start:
            end += 2 * math.pi
        step = (end - start) / self.segments
        return [self._point_at(math.degrees(start + i * step)) for i in range(self.segments + 1)]

    def scale(self, center: Point, factor: float) -> None:
        self.center = _scale_pt(self.center, center, factor)
        self.rx *= abs(factor)
        self.ry *= abs(factor)
        self.invalidate_cache()

    def mirror(self, center: Point, axis: str) -> None:
        self.center = _mirror_pt(self.center, center, axis)
        if axis == "horizontal":
            self.rotation = (180.0 - self.rotation) % 360.0
        elif axis == "vertical":
            self.rotation = (-self.rotation) % 360.0
        # Mirroring reverses travel direction; swap start/end (each mapped
        # through the same reflection) to keep the "sweep CCW from start to
        # end" convention _compute_tessellation relies on.
        self.start_angle, self.end_angle = (
            (-self.end_angle) % 360.0,
            (-self.start_angle) % 360.0,
        )
        self.invalidate_cache()

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "elliptical_arc",
            {
                "center": tuple(self.center),
                "rx": self.rx,
                "ry": self.ry,
                "rotation": self.rotation,
                "start_angle": self.start_angle,
                "end_angle": self.end_angle,
            },
        )

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cx, cy = self.center
        if (
            not _finite(cx, cy, self.rx, self.ry, self.rotation, self.start_angle, self.end_angle)
            or self.rx <= 1e-9
            or self.ry <= 1e-9
        ):
            return False
        from ezdxf.math import ConstructionEllipse  # type: ignore[attr-defined]

        rot = math.radians(self.rotation)
        ellipse = ConstructionEllipse(
            center=(float(cx), float(cy)),
            major_axis=(self.rx * math.cos(rot), self.rx * math.sin(rot)),
            ratio=self.ry / self.rx,
            start_param=math.radians(self.start_angle),
            end_param=math.radians(self.end_angle),
        )
        if ellipse.ratio > 1.0:
            # ezdxf's ELLIPSE requires ratio (minor/major) <= 1.0 — swap
            # which axis is "major" rather than emit an invalid entity.
            ellipse.swap_axis()
        msp.add_ellipse(
            ellipse.center,
            major_axis=ellipse.major_axis,
            ratio=ellipse.ratio,
            start_param=ellipse.start_param,
            end_param=ellipse.end_param,
            dxfattribs=dxfattribs,
        )
        return True


class CircleShape(_CenterBasedShapeMixin, Shape):
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


class EllipseShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
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


class RectangleShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
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
        from simple_stipple.core.cad.geometry import build_rounded_rect_poly

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


class StarShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
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
        from simple_stipple.core.cad.geometry import build_star_poly

        return build_star_poly(
            self.center[0],
            self.center[1],
            self.radius,
            self.point_count,
            inner_ratio=self.inner_ratio,
            rotation=self.rotation,
        )

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


class SlotShape(_CenterBasedShapeMixin, _ParametricRotateMixin, Shape):
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
        from simple_stipple.core.cad.geometry import shape_slot

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


# ════════════════════════════════════════════════════════════════════════════
# Factory — construct/deserialize Shape instances
# ════════════════════════════════════════════════════════════════════════════


