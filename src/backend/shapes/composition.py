"""Shape composition and decomposition operations.

Provides utilities for breaking apart and combining shapes using geometric operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from src.backend.shapes import (
    PolylineShape,
    ShapeFactory,
)

if TYPE_CHECKING:
    from src.backend.shapes import Shape


class ShapeComposition:
    """Utilities for combining and decomposing shapes."""

    @staticmethod
    def combine_shapes(
        shapes: list[Shape],
        operation: str = "union",
    ) -> list[Shape]:
        """Combine multiple shapes using a geometric operation.

        Args:
            shapes: List of shapes to combine
            operation: "union" (boolean merge) or "group" (keep separate)

        Returns:
            List of resulting shapes (usually one combined shape or separate)
        """
        if not shapes:
            return []

        if len(shapes) == 1:
            return shapes

        # Convert all to polyline shapes for geometry operations
        geometries = []
        for shape in shapes:
            if isinstance(shape, PolylineShape):
                pts = shape.control_points
                if len(pts) < 2:
                    continue
                # Create geometry based on closed state
                if shape.closed:
                    if len(pts) >= 3:
                        geometries.append(Polygon(pts))
                else:
                    geometries.append(LineString(pts))

        if not geometries:
            return []

        if operation == "union":
            # Boolean union all shapes
            try:
                merged = unary_union(geometries)
                result_shapes: list[Shape] = []

                if merged.is_empty:
                    return []

                # Convert back to shape(s)
                if hasattr(merged, "geoms"):  # MultiPolygon or MultiLineString
                    for geom in merged.geoms:  # type: ignore
                        shape = ShapeComposition._geometry_to_shape(geom, shapes[0])
                        if shape:
                            result_shapes.append(shape)
                else:  # Single geometry
                    shape = ShapeComposition._geometry_to_shape(merged, shapes[0])
                    if shape:
                        result_shapes.append(shape)

                return result_shapes if result_shapes else shapes

            except (ValueError, TypeError):
                # Fallback: return original shapes if union fails
                return shapes

        elif operation == "group":
            # Keep shapes as is but mark them as grouped
            return shapes

        return shapes

    @staticmethod
    def break_apart_shape(shape: Shape) -> list[Shape]:
        """Decompose a shape into constituent parts.

        For polylines: if multi-segment, split into individual segments.
        For complex geometries: extract rings and linestrings.

        Returns:
            List of simpler shapes (may include segments or rings)
        """
        if not isinstance(shape, PolylineShape):
            return [shape]

        result: list[Shape] = []
        pts = shape.control_points

        if len(pts) < 2:
            return [shape]

        # If shape has more than 2 segments, split into line segments
        if len(pts) > 2 and not shape.closed:
            # Create a separate shape for each segment
            for i in range(len(pts) - 1):
                segment_shape = ShapeFactory.polyline(
                    [pts[i], pts[i + 1]],
                    closed=False,
                    name=f"{shape.name}_segment_{i}",
                    layer=shape.layer,
                    visible=shape.visible,
                    locked=shape.locked,
                )
                result.append(segment_shape)
            return result

        # If closed polyline with multiple vertices, extract rings/segments
        if shape.closed and len(pts) > 3:
            # For now, keep as is (could extract rings if it's a multi-ring polygon)
            # This could be enhanced to support complex polygon decomposition
            return [shape]

        # Single segment or special case: return as is
        return [shape]

    @staticmethod
    def simplify_shape(shape: Shape, tolerance: float = 0.01) -> Shape:
        """Simplify a shape by reducing control points.

        Args:
            shape: Shape to simplify
            tolerance: Simplification tolerance in drawing units

        Returns:
            Simplified shape or original if not applicable
        """
        if not isinstance(shape, PolylineShape):
            return shape

        try:
            from shapely.geometry import LineString, Polygon

            pts = shape.control_points
            if len(pts) < 3:
                return shape

            if shape.closed and len(pts) >= 3:
                geom = Polygon(pts)
            else:
                geom = LineString(pts)

            simplified = geom.simplify(tolerance, preserve_topology=True)

            # Extract simplified coordinates
            if hasattr(simplified, "exterior"):  # Polygon
                coords = list(simplified.exterior.coords)  # type: ignore
                new_pts = [
                    (pt[0], pt[1]) for pt in coords[:-1]
                ]  # Remove duplicate end point
            elif hasattr(simplified, "coords"):  # LineString
                coords = list(simplified.coords)  # type: ignore
                new_pts = [(pt[0], pt[1]) for pt in coords]
            else:
                return shape

            simplified_shape = ShapeFactory.polyline(
                new_pts,
                closed=shape.closed,
                name=shape.name,
                layer=shape.layer,
                visible=shape.visible,
                locked=shape.locked,
            )
            simplified_shape.id = shape.id  # Preserve ID for in-place replacement
            return simplified_shape

        except (ValueError, TypeError):
            return shape

    @staticmethod
    def _geometry_to_shape(geom, template_shape: Shape | None = None) -> Shape | None:
        """Convert a shapely geometry back to a Shape object."""
        try:
            if geom.is_empty:
                return None

            pts: list[tuple[float, float]] = []
            closed = False

            if hasattr(geom, "exterior"):  # Polygon
                coords = list(geom.exterior.coords)  # type: ignore
                pts = [(pt[0], pt[1]) for pt in coords[:-1]]  # Remove duplicate end
                closed = True
            elif hasattr(geom, "coords"):  # LineString/LinearRing
                coords = list(geom.coords)  # type: ignore
                pts = [(pt[0], pt[1]) for pt in coords]
                closed = False
            else:
                return None

            if len(pts) < 2:
                return None

            kwargs = {}
            if template_shape:
                kwargs = {
                    "name": template_shape.name,
                    "layer": template_shape.layer,
                    "visible": template_shape.visible,
                    "locked": template_shape.locked,
                }

            return ShapeFactory.polyline(pts, closed=closed, **kwargs)

        except (ValueError, TypeError):
            return None
