"""Canvas shape manipulation actions — break apart, combine, simplify shapes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.backend.shapes.composition import ShapeComposition
from src.backend.shapes.shape import PolylineShape

if TYPE_CHECKING:
    from src.ui.canvas.view import PolylineView


class ShapeManipulationActions:
    """Actions for combining and decomposing shapes on the canvas."""

    @staticmethod
    def break_apart(canvas: PolylineView) -> None:
        """Break apart selected shapes into simpler components.

        For polylines with multiple segments, creates individual segment shapes.
        For other shapes, attempts decomposition if applicable.
        """
        selected_indices = canvas.get_selection_indices()
        if not selected_indices:
            return

        storage = canvas._shape_storage
        new_shapes: list = []

        for idx in selected_indices:
            shape = storage.get_shape_at_index(idx)
            if shape is None:
                continue

            # Get decomposed shapes
            decomposed = ShapeComposition.break_apart_shape(shape)

            if len(decomposed) > 1:
                # If shape was decomposed into multiple pieces, add them
                new_shapes.extend(decomposed)
                # Remove original
                storage.remove_shape(shape.id)
            # If decomposed is just the original (no decomposition), leave it

        # Add new shapes to storage
        for new_shape in new_shapes:
            storage.add_shape(new_shape)

        # Update canvas
        canvas.set_selection([])
        canvas._redraw()

    @staticmethod
    def combine(canvas: PolylineView) -> None:
        """Combine selected shapes using boolean union.

        Merges overlapping or adjacent shapes into a single unified shape.
        """
        selected_indices = canvas.get_selection_indices()
        if not selected_indices or len(selected_indices) < 2:
            return

        storage = canvas._shape_storage
        selected_shapes = storage.get_selected_shapes(set(selected_indices))

        if not selected_shapes:
            return

        # Perform union operation
        combined = ShapeComposition.combine_shapes(selected_shapes, operation="union")

        if not combined or combined == selected_shapes:
            return

        # Remove original shapes
        for shape in selected_shapes:
            storage.remove_shape(shape.id)

        # Add combined shape(s)
        for new_shape in combined:
            storage.add_shape(new_shape)

        # Update canvas
        canvas.set_selection([])
        canvas._redraw()

    @staticmethod
    def simplify(canvas: PolylineView, tolerance: float = 0.1) -> None:
        """Simplify selected shapes by reducing control points.

        Args:
            canvas: The canvas instance
            tolerance: Simplification tolerance in drawing units (smaller = more detail)
        """
        selected_indices = canvas.get_selection_indices()
        if not selected_indices:
            return

        storage = canvas._shape_storage
        changed = False

        for idx in selected_indices:
            shape = storage.get_shape_at_index(idx)
            if shape is None or not isinstance(shape, PolylineShape):
                continue

            original_pt_count = len(shape.control_points)
            simplified = ShapeComposition.simplify_shape(shape, tolerance)

            if len(simplified.control_points) < original_pt_count:
                # Replace in storage
                storage._shapes[shape.id] = simplified
                changed = True

        if changed:
            canvas._redraw()
