"""Geometry behavior and shape adaptation for document entity records."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from simple_stipple.core.cad.shape_base import Shape
from simple_stipple.core.cad.shape_factory import ShapeFactory
from simple_stipple.core.cad.snapping import polygon_centroid
from simple_stipple.core.editing import transform as editing_transform

Point = tuple[float, float]


@runtime_checkable
class CanvasGeometry(Protocol):
    """Geometry behavior required by rendering, snapping, and export."""

    @property
    def control_points(self) -> list[Point]: ...

    def tessellate(self) -> list[Point]: ...

    def translate(self, dx: float, dy: float) -> None: ...

    def rotate(self, center: Point, angle_degrees: float) -> None: ...

    def scale(self, center: Point, factor: float) -> None: ...


@dataclass
class PolylineGeometry:
    points: list[Point]

    @property
    def control_points(self) -> list[Point]:
        return self.points

    def tessellate(self) -> list[Point]:
        return list(self.points)

    def translate(self, dx: float, dy: float) -> None:
        self.points = editing_transform.translate(self.points, dx, dy)

    def rotate(self, center: Point, angle_degrees: float) -> None:
        self.points = editing_transform.rotate(self.points, center, angle_degrees)

    def scale(self, center: Point, factor: float) -> None:
        self.points = editing_transform.scale(self.points, center, factor)


@dataclass
class ShapeGeometry:
    shape: Shape

    @property
    def control_points(self) -> list[Point]:
        return list(self.shape.control_points)

    def tessellate(self) -> list[Point]:
        return list(self.shape.points)

    def translate(self, dx: float, dy: float) -> None:
        self.shape.translate(dx, dy)

    def rotate(self, center: Point, angle_degrees: float) -> None:
        self.shape.rotate(center, angle_degrees)

    def scale(self, center: Point, factor: float) -> None:
        self.shape.scale(center, factor)


def geometry_for_entity(entity: Any) -> CanvasGeometry:
    """Adapt a legacy record to the new geometry interface.

    Generic imported paths remain lightweight polylines. Parametric records
    use Shape subclasses, eliminating tessellation switches at call sites.
    """
    kind = str(getattr(entity, "kind", "polyline"))
    points = list(getattr(entity, "points", []))
    if kind not in {
        "line",
        "arc",
        "elliptical_arc",
        "circle",
        "ellipse",
        "rectangle",
        "rounded_rectangle",
        "polygon",
        "star",
        "spline",
        "bezier",
        "slot",
    }:
        return PolylineGeometry(points)
    metadata = deepcopy(getattr(entity, "meta", None)) or {}
    if kind == "arc" and "radius" in metadata:
        radius = abs(float(metadata.get("radius", 0.0) or 0.0))
        start = math.radians(float(metadata.get("start_angle", 0.0) or 0.0))
        end = math.radians(float(metadata.get("end_angle", 180.0) or 180.0))
        metadata["segments"] = max(24, min(720, int(radius * abs(end - start) * 2)))
    elif kind == "elliptical_arc" and "rx" in metadata:
        radius = max(
            abs(float(metadata.get("rx", 0.0) or 0.0)), abs(float(metadata.get("ry", 0.0) or 0.0))
        )
        start = math.radians(float(metadata.get("start_angle", 0.0) or 0.0))
        end = math.radians(float(metadata.get("end_angle", 180.0) or 180.0))
        metadata["segments"] = max(24, min(720, int(radius * abs(end - start) * 2)))
    shape = ShapeFactory.from_meta_dict(
        kind=kind,
        points=points,
        metadata=metadata,
        is_construction=bool(getattr(entity, "construction", False)),
        is_hidden=bool(getattr(entity, "hidden", False)),
        is_locked=bool(getattr(entity, "locked", False)),
    )
    return ShapeGeometry(shape)


def shape_for_entity(entity: Any) -> Shape:
    """Return a Shape for snapping/native export, including generic paths."""
    geometry = geometry_for_entity(entity)
    if isinstance(geometry, ShapeGeometry):
        return geometry.shape
    return ShapeFactory.polyline(
        geometry.control_points,
        closed=(
            len(geometry.control_points) >= 2
            and geometry.control_points[0] == geometry.control_points[-1]
        ),
        construction=bool(getattr(entity, "construction", False)),
        visible=not bool(getattr(entity, "hidden", False)),
        locked=bool(getattr(entity, "locked", False)),
    )


def entity_shows_point_handles(entity: Any) -> bool:
    """Whether the entity exposes its stored points as draggable handles."""
    geometry = geometry_for_entity(entity)
    return not (
        isinstance(geometry, ShapeGeometry)
        and geometry.shape.shape_type
        in {"arc", "elliptical_arc", "circle", "ellipse", "slot", "rounded_rectangle", "star"}
    )


def entity_center(entity: Any) -> Point | None:
    """Return an entity's exact parametric or closed-outline center.

    Parametric metadata takes precedence so coarse tessellation and open arcs
    retain their mathematically defined centers. Generic paths only expose a
    center when they are geometrically closed.
    """
    metadata = getattr(entity, "meta", None)
    if isinstance(metadata, dict):
        center = metadata.get("center")
        if isinstance(center, (tuple, list)) and len(center) == 2:
            return float(center[0]), float(center[1])
    points = geometry_for_entity(entity).tessellate()
    if len(points) < 3 or math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) >= 0.01:
        return None
    return polygon_centroid(points)


def _write_shape_to_entity(entity: Any, shape: Shape) -> None:
    kind, metadata = shape.to_meta_dict()
    entity.kind = kind
    entity.meta = metadata
    if kind in {"line", "spline", "bezier", "polyline"}:
        entity.points = list(shape.control_points)
    else:
        entity.points = list(shape.points)


def update_entity_parameter(entity: Any, key: str, value: float) -> bool:
    """Update a parametric record without exposing kind switches to the view."""
    geometry = geometry_for_entity(entity)
    if not isinstance(geometry, ShapeGeometry) or not geometry.shape.set_parameter(key, value):
        return False
    _write_shape_to_entity(entity, geometry.shape)
    return True


def move_entity_control_point(
    entity: Any,
    index: int,
    point: Point,
    *,
    displayed_point_count: int | None = None,
) -> bool:
    """Move a shape control point and persist its canonical metadata."""
    geometry = geometry_for_entity(entity)
    if not isinstance(geometry, ShapeGeometry):
        return False
    shape = geometry.shape
    if shape.shape_type not in {"arc", "circle", "ellipse", "rectangle"}:
        return False
    control_index = index
    if shape.shape_type == "arc":
        if index <= 1:
            control_index = 1
        elif displayed_point_count is not None and index >= displayed_point_count - 2:
            control_index = 2
        else:
            return False
    elif shape.shape_type == "circle":
        control_index = 1
    elif shape.shape_type == "ellipse":
        if displayed_point_count is None or displayed_point_count < 5:
            return False
        fraction = index / max(1, displayed_point_count - 1)
        control_index = int(round(fraction * 4.0)) % 4 + 1
    if not shape.move_control_point(control_index, point):
        return False
    _write_shape_to_entity(entity, shape)
    return True


def synchronize_entity_control_points(entity: Any) -> None:
    """Refresh metadata after generic point editing at one adapter boundary."""
    points = list(getattr(entity, "points", []))
    metadata = dict(getattr(entity, "meta", None) or {})
    kind = str(getattr(entity, "kind", "polyline"))
    if kind == "line" and len(points) >= 2:
        metadata["start"] = tuple(points[0])
        metadata["end"] = tuple(points[-1])
    elif kind == "spline":
        metadata["control_points"] = [tuple(point) for point in points]
    else:
        return
    entity.meta = metadata


def transform_entity_metadata(
    entity: Any,
    *,
    transform: str,
    center: Point = (0.0, 0.0),
    factor: float | None = None,
    factor_y: float | None = None,
    angle_degrees: float = 0.0,
    axis: str | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
) -> bool:
    """Transform canonical metadata using the entity's complete geometry.

    A non-uniform scale (``factor_y`` different from ``factor``) only
    applies when the shape kind supports it (see ``Shape.scale_xy``) —
    returns False otherwise, so the caller falls back instead of trusting
    metadata a non-uniform scale would have made incorrect.
    """
    geometry = geometry_for_entity(entity)
    if not isinstance(geometry, ShapeGeometry):
        return False
    shape = geometry.shape
    original_kind = shape.shape_type
    if transform == "translate":
        shape.translate(dx, dy)
    elif transform == "rotate":
        shape.rotate(center, angle_degrees)
    elif transform == "scale" and factor is not None:
        if factor_y is not None and abs(factor_y - factor) > 1e-9:
            scaled = shape.scale_xy(center, factor, factor_y)
            if scaled is None:
                return False
            shape = scaled
        else:
            shape.scale(center, factor)
    elif transform == "mirror" and axis is not None:
        shape.mirror(center, axis)
    else:
        return False
    new_kind, metadata = shape.to_meta_dict()
    if metadata is None:
        return False
    entity.kind = new_kind
    # A kind change (e.g. arc -> elliptical_arc) makes the old kind's
    # fields stale — start clean rather than merging them into the new
    # shape's own schema.
    entity.meta = (
        metadata
        if new_kind != original_kind
        else {**(getattr(entity, "meta", None) or {}), **metadata}
    )
    return True


def polyline_is_closed(poly: list[Point], threshold: float = 0.5) -> bool:
    """Return True when the polyline's endpoints are within *threshold* units."""
    if len(poly) < 2:
        return False
    dx = poly[-1][0] - poly[0][0]
    dy = poly[-1][1] - poly[0][1]
    return math.hypot(dx, dy) < threshold


def angle_between_rays(vertex: Point, p1: Point, p3: Point) -> float:
    """Unsigned angle in degrees between two rays emanating from *vertex*.

    Computes the minor angle (0–180°) between the ray *vertex*→*p1* and
    the ray *vertex*→*p3*, using the same formula that appears in the
    renderer and tools for dimension calculations.
    """
    first = math.atan2(p1[1] - vertex[1], p1[0] - vertex[0])
    second = math.atan2(p3[1] - vertex[1], p3[0] - vertex[0])
    return abs(math.degrees((second - first + math.pi) % math.tau - math.pi))
