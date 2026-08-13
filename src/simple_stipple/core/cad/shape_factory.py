"""Construct, deserialize, and transform Shape instances."""

from __future__ import annotations

from typing import Any

from simple_stipple.core.cad.primitives import BezierShape, SplineShape
from simple_stipple.core.cad.shape_base import Point, Shape
from simple_stipple.core.cad.shapes import (
    ArcShape,
    CircleShape,
    EllipseShape,
    EllipticalArcShape,
    LineShape,
    PolygonShape,
    PolylineShape,
    RectangleShape,
    RoundedRectangleShape,
    SlotShape,
    StarShape,
)


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
    def elliptical_arc(
        cls,
        center: Point,
        rx: float,
        ry: float,
        start_angle: float,
        end_angle: float,
        rotation: float = 0.0,
        segments: int = 48,
        **kwargs,
    ) -> EllipticalArcShape:
        """Create an elliptical arc shape."""
        return EllipticalArcShape(
            id=cls.next_id(),
            center=center,
            rx=rx,
            ry=ry,
            start_angle=start_angle,
            end_angle=end_angle,
            rotation=rotation,
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

        elif kind == "elliptical_arc" and metadata.get("center"):
            return cls.elliptical_arc(
                center=metadata["center"],
                rx=metadata.get("rx", 0),
                ry=metadata.get("ry", 0),
                rotation=metadata.get("rotation", 0),
                start_angle=metadata.get("start_angle", 0),
                end_angle=metadata.get("end_angle", 180),
                segments=metadata.get("segments", 48),
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

        elif shape_type == "elliptical_arc":
            return EllipticalArcShape(
                id=shape_id or cls.next_id(),
                center=_as_point(data.get("center", (0, 0))),
                rx=data.get("rx", 0),
                ry=data.get("ry", 0),
                rotation=data.get("rotation", 0),
                start_angle=data.get("start_angle", 0),
                end_angle=data.get("end_angle", 180),
                segments=data.get("segments", 48),
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
            from simple_stipple.core.cad.primitives import SplineShape

            return SplineShape(
                id=shape_id or cls.next_id(),
                control_points=data.get("control_points", []),
                degree=data.get("degree", 3),
                closed=data.get("closed", False),
                segments=data.get("segments", 24),
                **kwargs,
            )

        elif shape_type == "bezier":
            from simple_stipple.core.cad.primitives import BezierShape

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
    factor_y: float | None = None,
    angle_deg: float = 0.0,
    axis: str | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    points: list[Point] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Apply a geometric transform to ``kind`` + ``meta`` metadata.

    Reconstructs the shape, delegates to its transform method, and returns
    the resulting ``(kind, meta)`` — kind included because a non-uniform
    scale can change it (``Shape.scale_xy``: arc -> elliptical_arc), not
    just the metadata values. Returns ``None`` when the transform does not
    apply — callers keep the original kind/metadata in that case.

    ``points`` supplies the control points for shape kinds — line, polyline,
    spline, bezier — whose geometry isn't fully captured by ``meta`` alone.
    Bezier in particular stores its anchors on the entity's own ``points``,
    not in ``meta``; reconstructing it from ``meta`` alone (as ``kind+meta``
    encoding normally allows) would silently yield a shape with no control
    points, and scaling/rotating that drops its tangents to empty.
    """
    if not meta or kind == "polyline":
        return None
    try:
        shape = ShapeFactory.from_meta_dict(kind=kind, points=points or [], metadata=meta)
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    if transform == "translate":
        shape.translate(dx, dy)
    elif transform == "rotate":
        shape.rotate(center, angle_deg)
    elif transform == "scale":
        if factor is None:
            return None
        if factor_y is not None and abs(factor_y - factor) > 1e-9:
            scaled = shape.scale_xy(center, factor, factor_y)
            if scaled is None:
                return None
            shape = scaled
        else:
            shape.scale(center, factor)
    elif transform == "mirror":
        if axis is None:
            return None
        shape.mirror(center, axis)
    else:
        return None
    new_kind, new_meta = shape.to_meta_dict()
    if new_meta is None:
        return None
    return new_kind, ({**meta, **new_meta} if new_kind == kind else new_meta)


