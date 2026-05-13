"""Factory for creating and migrating shapes."""

from __future__ import annotations

from typing import Any

from src.backend.shapes.shape import (
    ArcShape,
    CircleShape,
    EllipseShape,
    LineShape,
    Point,
    PolylineShape,
    RectangleShape,
    Shape,
    SplineShape,
)


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
    def polyline(
        cls, points: list[Point], closed: bool = False, **kwargs
    ) -> PolylineShape:
        """Create a polyline shape."""
        return PolylineShape(
            id=cls.next_id(), control_points=points, closed=closed, **kwargs
        )

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
    def circle(
        cls, center: Point, radius: float, segments: int = 64, **kwargs
    ) -> CircleShape:
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
    def from_legacy(
        cls,
        kind: str,
        points: list[Point],
        metadata: dict[str, Any] | None = None,
        is_construction: bool = False,
        is_hidden: bool = False,
        is_locked: bool = False,
        name: str = "Imported Shape",
    ) -> Shape:
        """Create a shape from legacy polyline + metadata.

        This is used during migration from the old polyline system.
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
            return cls.rectangle(center=center, width=width, height=height, **kwargs)

        elif kind == "line" and len(points) >= 2:
            # Line
            return cls.line(start=points[0], end=points[-1], **kwargs)

        elif kind == "spline" and metadata.get("closed") is not None:
            # Spline (control points lost in old system, use tessellation)
            return cls.spline(
                control_points=points,
                segments=metadata.get("segments", 24),
                closed=metadata.get("closed", False),
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
                start=tuple(data.get("start", (0, 0))),
                end=tuple(data.get("end", (0, 0))),
                **kwargs,
            )

        elif shape_type == "arc":
            return ArcShape(
                id=shape_id or cls.next_id(),
                center=tuple(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                start_angle=data.get("start_angle", 0),
                end_angle=data.get("end_angle", 180),
                segments=data.get("segments", 24),
                **kwargs,
            )

        elif shape_type == "circle":
            return CircleShape(
                id=shape_id or cls.next_id(),
                center=tuple(data.get("center", (0, 0))),
                radius=data.get("radius", 0),
                segments=data.get("segments", 64),
                **kwargs,
            )

        elif shape_type == "ellipse":
            return EllipseShape(
                id=shape_id or cls.next_id(),
                center=tuple(data.get("center", (0, 0))),
                rx=data.get("rx", 0),
                ry=data.get("ry", 0),
                rotation=data.get("rotation", 0),
                segments=data.get("segments", 64),
                **kwargs,
            )

        elif shape_type == "rectangle":
            return RectangleShape(
                id=shape_id or cls.next_id(),
                center=tuple(data.get("center", (0, 0))),
                width=data.get("width", 0),
                height=data.get("height", 0),
                rotation=data.get("rotation", 0),
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

        else:
            # Unknown type, default to polyline
            return PolylineShape(
                id=shape_id or cls.next_id(),
                control_points=data.get("points", []),
                closed=data.get("closed", False),
                **kwargs,
            )
