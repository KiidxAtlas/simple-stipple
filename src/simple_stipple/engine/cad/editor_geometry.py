"""Geometry behavior and shape adaptation for editor entities."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from simple_stipple.document.model import EntityRecord
from simple_stipple.engine.cad.shapes import Shape, ShapeFactory

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
        self.points = [(x + dx, y + dy) for x, y in self.points]

    def rotate(self, center: Point, angle_degrees: float) -> None:
        angle = math.radians(angle_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        cx, cy = center
        self.points = [
            (cx + (x - cx) * cosine - (y - cy) * sine, cy + (x - cx) * sine + (y - cy) * cosine)
            for x, y in self.points
        ]

    def scale(self, center: Point, factor: float) -> None:
        cx, cy = center
        self.points = [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in self.points]


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


def geometry_for_entity(entity: EntityRecord) -> CanvasGeometry:
    """Adapt a legacy record to the new geometry interface.

    Generic imported paths remain lightweight polylines. Parametric records
    use Shape subclasses, eliminating tessellation switches at call sites.
    """
    kind = str(getattr(entity, "kind", "polyline"))
    points = list(getattr(entity, "points", []))
    if kind not in {
        "line",
        "arc",
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
        in {"arc", "circle", "ellipse", "slot", "rounded_rectangle", "star"}
    )


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
    angle_degrees: float = 0.0,
    axis: str | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
) -> bool:
    """Transform canonical metadata using the entity's complete geometry."""
    geometry = geometry_for_entity(entity)
    if not isinstance(geometry, ShapeGeometry):
        return False
    shape = geometry.shape
    if transform == "translate":
        shape.translate(dx, dy)
    elif transform == "rotate":
        shape.rotate(center, angle_degrees)
    elif transform == "scale" and factor is not None:
        shape.scale(center, factor)
    elif transform == "mirror" and axis is not None:
        shape.mirror(center, axis)
    else:
        return False
    _, metadata = shape.to_meta_dict()
    if metadata is None:
        return False
    entity.meta = {**(getattr(entity, "meta", None) or {}), **metadata}
    return True
