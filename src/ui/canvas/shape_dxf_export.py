"""Phase 6: True DXF export with proper curve entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.backend.shapes import Shape


class DXFExporter:
    """Exports shapes to DXF with proper curve entities (not just polylines)."""

    @staticmethod
    def shape_to_dxf_entity(shape: Shape) -> dict[str, Any]:
        """Convert a shape to DXF entity representation.

        Returns a dict suitable for ezdxf or similar DXF library.
        """
        entity_type = shape.shape_type.upper()

        if shape.shape_type == "polyline":
            return DXFExporter._polyline_to_dxf(shape)
        elif shape.shape_type == "line":
            return DXFExporter._line_to_dxf(shape)
        elif shape.shape_type == "arc":
            return DXFExporter._arc_to_dxf(shape)
        elif shape.shape_type == "circle":
            return DXFExporter._circle_to_dxf(shape)
        elif shape.shape_type == "ellipse":
            return DXFExporter._ellipse_to_dxf(shape)
        elif shape.shape_type == "rectangle":
            return DXFExporter._rectangle_to_dxf(shape)
        elif shape.shape_type == "spline":
            return DXFExporter._spline_to_dxf(shape)
        else:
            # Fallback: export as polyline
            return DXFExporter._polyline_to_dxf(shape)

    @staticmethod
    def _polyline_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export polyline to DXF LWPOLYLINE."""
        return {
            "type": "LWPOLYLINE",
            "dxftype": "LWPOLYLINE",
            "points": [(pt[0], pt[1]) for pt in shape.points],
            "flags": 1 if (hasattr(shape, "closed") and shape.closed) else 0,
        }

    @staticmethod
    def _line_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export line to DXF LINE."""
        if hasattr(shape, "start") and hasattr(shape, "end"):
            return {
                "type": "LINE",
                "dxftype": "LINE",
                "start": shape.start,
                "end": shape.end,
            }
        return {"type": "LINE", "dxftype": "LINE", "start": (0, 0), "end": (0, 0)}

    @staticmethod
    def _arc_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export arc to DXF ARC entity."""
        if hasattr(shape, "center") and hasattr(shape, "radius"):
            return {
                "type": "ARC",
                "dxftype": "ARC",
                "center": shape.center,
                "radius": shape.radius,
                "start_angle": shape.start_angle
                if hasattr(shape, "start_angle")
                else 0,
                "end_angle": shape.end_angle if hasattr(shape, "end_angle") else 360,
            }
        return {
            "type": "ARC",
            "dxftype": "ARC",
            "center": (0, 0),
            "radius": 1,
            "start_angle": 0,
            "end_angle": 360,
        }

    @staticmethod
    def _circle_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export circle to DXF CIRCLE entity."""
        if hasattr(shape, "center") and hasattr(shape, "radius"):
            return {
                "type": "CIRCLE",
                "dxftype": "CIRCLE",
                "center": shape.center,
                "radius": shape.radius,
            }
        return {
            "type": "CIRCLE",
            "dxftype": "CIRCLE",
            "center": (0, 0),
            "radius": 1,
        }

    @staticmethod
    def _ellipse_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export ellipse to DXF ELLIPSE entity."""
        if all(hasattr(shape, attr) for attr in ("center", "rx", "ry", "rotation")):
            return {
                "type": "ELLIPSE",
                "dxftype": "ELLIPSE",
                "center": shape.center,
                "major_axis": (shape.rx, 0),
                "ratio": shape.ry / shape.rx if shape.rx != 0 else 1,
                "rotation": shape.rotation,
            }
        return {
            "type": "ELLIPSE",
            "dxftype": "ELLIPSE",
            "center": (0, 0),
            "major_axis": (1, 0),
            "ratio": 1,
            "rotation": 0,
        }

    @staticmethod
    def _rectangle_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export rectangle to DXF LWPOLYLINE (or RECTANGLE if supported)."""
        if hasattr(shape, "control_points") and len(shape.control_points) >= 4:
            # Export as polyline with 4 corners
            return {
                "type": "LWPOLYLINE",
                "dxftype": "LWPOLYLINE",
                "points": [pt for pt in shape.control_points[:4]],
                "flags": 1,  # Closed
            }
        # Fallback: tessellated
        return DXFExporter._polyline_to_dxf(shape)

    @staticmethod
    def _spline_to_dxf(shape: Shape) -> dict[str, Any]:
        """Export spline to DXF SPLINE entity."""
        if hasattr(shape, "control_points") and hasattr(shape, "degree"):
            return {
                "type": "SPLINE",
                "dxftype": "SPLINE",
                "control_points": shape.control_points,
                "degree": shape.degree if hasattr(shape, "degree") else 3,
                "closed": 1 if (hasattr(shape, "closed") and shape.closed) else 0,
            }
        return {
            "type": "SPLINE",
            "dxftype": "SPLINE",
            "control_points": [(0, 0)],
            "degree": 3,
            "closed": 0,
        }

    @staticmethod
    def export_shapes_to_dxf(
        shapes: list[Shape], filepath: str, layer_mapping: dict[int, str] | None = None
    ) -> bool:
        """Export a list of shapes to a DXF file.

        Args:
            shapes: List of shapes to export
            filepath: Output DXF file path
            layer_mapping: Optional dict mapping shape IDs to layer names

        Returns:
            True if successful, False otherwise
        """
        try:
            import ezdxf
        except ImportError:
            print("ERROR: ezdxf not installed. Install with: pip install ezdxf")
            return False

        try:
            doc = ezdxf.new()
            msp = doc.modelspace()

            for shape in shapes:
                entity_dict = DXFExporter.shape_to_dxf_entity(shape)
                entity_type = entity_dict.get("type", "LINE")

                # Determine layer
                layer = "0"
                if layer_mapping and hasattr(shape, "id"):
                    layer = layer_mapping.get(
                        shape.id, shape.layer if hasattr(shape, "layer") else "0"
                    )

                # Add entity to DXF
                if entity_type == "LINE":
                    msp.add_line(
                        entity_dict["start"],
                        entity_dict["end"],
                        dxfattribs={"layer": layer},
                    )
                elif entity_type == "ARC":
                    msp.add_arc(
                        entity_dict["center"],
                        entity_dict["radius"],
                        entity_dict["start_angle"],
                        entity_dict["end_angle"],
                        dxfattribs={"layer": layer},
                    )
                elif entity_type == "CIRCLE":
                    msp.add_circle(
                        entity_dict["center"],
                        entity_dict["radius"],
                        dxfattribs={"layer": layer},
                    )
                elif entity_type == "ELLIPSE":
                    msp.add_ellipse(
                        entity_dict["center"],
                        entity_dict["major_axis"],
                        entity_dict["ratio"],
                        dxfattribs={"layer": layer},
                    )
                elif entity_type == "LWPOLYLINE":
                    msp.add_lwpolyline(
                        entity_dict["points"],
                        dxfattribs={"layer": layer},
                        close=bool(entity_dict.get("flags")),
                    )
                elif entity_type == "SPLINE":
                    msp.add_spline(
                        entity_dict["control_points"],
                        degree=entity_dict.get("degree", 3),
                        dxfattribs={"layer": layer},
                    )

            # Save the file
            doc.saveas(filepath)
            return True

        except Exception as e:
            print(f"ERROR exporting DXF: {e}")
            return False
