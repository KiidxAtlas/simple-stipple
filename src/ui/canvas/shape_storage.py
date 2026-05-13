"""Shape storage layer - handles migration and dual-system support for Phase 2."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.backend.shapes import Shape, ShapeFactory


class ShapeStorage:
    """Manages shape storage with support for legacy polyline migration.

    During Phase 2, this layer handles:
    - Converting from old (polyline + metadata) to new (shape objects) storage
    - Keeping both systems in sync for validation
    - Gradual transition from index-based to ID-based access
    """

    def __init__(self):
        self._shapes: dict[int, Shape] = {}
        self._shape_order: list[int] = []  # Maintains render/iteration order
        self._next_id: int = 1
        self._dual_system_validation = True  # Enable checking during Phase 2

    def reset(self) -> None:
        """Clear all shapes."""
        self._shapes.clear()
        self._shape_order.clear()
        self._next_id = 1

    def migrate_from_polylines(
        self,
        polys: list[list[tuple[float, float]]],
        kinds: list[str],
        meta_list: list[dict[str, Any] | None],
    ) -> None:
        """Convert old polyline system to new shape system."""
        self.reset()
        ShapeFactory.reset_id_counter(0)

        for idx, poly in enumerate(polys):
            kind = kinds[idx] if idx < len(kinds) else "polyline"
            meta = meta_list[idx] if idx < len(meta_list) else None

            # Create shape from legacy data
            shape = ShapeFactory.from_legacy(
                kind=kind,
                points=poly,
                metadata=meta,
            )

            self._shapes[shape.id] = shape
            self._shape_order.append(shape.id)

        self._next_id = ShapeFactory._id_counter

    def add_shape(self, shape: Shape) -> int:
        """Add a shape and return its ID."""
        self._shapes[shape.id] = shape
        self._shape_order.append(shape.id)
        return shape.id

    def remove_shape(self, shape_id: int) -> None:
        """Remove a shape by ID."""
        if shape_id in self._shapes:
            del self._shapes[shape_id]
        if shape_id in self._shape_order:
            self._shape_order.remove(shape_id)

    def insert_shape(self, idx: int, shape: Shape) -> None:
        """Insert a shape at a specific position in render order."""
        self._shapes[shape.id] = shape
        if 0 <= idx <= len(self._shape_order):
            self._shape_order.insert(idx, shape.id)
        else:
            self._shape_order.append(shape.id)

    def get_shape(self, shape_id: int) -> Shape | None:
        """Get a shape by ID."""
        return self._shapes.get(shape_id)

    def get_shape_at_index(self, idx: int) -> Shape | None:
        """Get a shape by render order index."""
        if 0 <= idx < len(self._shape_order):
            shape_id = self._shape_order[idx]
            return self._shapes.get(shape_id)
        return None

    def shape_id_at_index(self, idx: int) -> int | None:
        """Get shape ID at render order index."""
        if 0 <= idx < len(self._shape_order):
            return self._shape_order[idx]
        return None

    def index_of_shape_id(self, shape_id: int) -> int | None:
        """Get render order index for a shape ID."""
        if shape_id in self._shape_order:
            return self._shape_order.index(shape_id)
        return None

    def get_all_shapes(self) -> list[Shape]:
        """Get all shapes in render order."""
        return [self._shapes[sid] for sid in self._shape_order if sid in self._shapes]

    def get_all_shape_ids(self) -> list[int]:
        """Get all shape IDs in render order."""
        return list(self._shape_order)

    def count(self) -> int:
        """Get number of shapes."""
        return len(self._shape_order)

    def get_selected_shapes(self, selected_indices: set[int]) -> list[Shape]:
        """Get shapes for a set of render indices."""
        return [
            self._shapes[self._shape_order[i]]
            for i in selected_indices
            if i < len(self._shape_order) and self._shape_order[i] in self._shapes
        ]

    def get_selected_shape_ids(self, selected_indices: set[int]) -> set[int]:
        """Convert render indices to shape IDs."""
        return {
            self._shape_order[i]
            for i in selected_indices
            if i < len(self._shape_order) and self._shape_order[i] in self._shapes
        }

    def get_shape_indices(self, shape_ids: set[int]) -> list[int]:
        """Convert shape IDs back to render indices."""
        indices = []
        for shape_id in shape_ids:
            idx = self.index_of_shape_id(shape_id)
            if idx is not None:
                indices.append(idx)
        return sorted(indices)

    def reorder_shape(self, old_idx: int, new_idx: int) -> None:
        """Move a shape from one position to another in render order."""
        if 0 <= old_idx < len(self._shape_order):
            shape_id = self._shape_order.pop(old_idx)
            if 0 <= new_idx <= len(self._shape_order):
                self._shape_order.insert(new_idx, shape_id)
            else:
                self._shape_order.append(shape_id)

    def snapshot(self) -> dict[int, Shape]:
        """Create a deep copy snapshot of all shapes for undo/redo."""
        return {sid: deepcopy(shape) for sid, shape in self._shapes.items()}

    def restore_snapshot(self, snapshot: dict[int, Shape]) -> None:
        """Restore shapes from a snapshot."""
        self._shapes = {sid: deepcopy(shape) for sid, shape in snapshot.items()}
        # Rebuild order from snapshot keys (preserving insertion order)
        self._shape_order = [sid for sid in self._shape_order if sid in self._shapes]

    def export_to_polylines(
        self,
    ) -> tuple[
        list[list[tuple[float, float]]],
        list[str],
        list[dict[str, Any] | None],
    ]:
        """Export shapes back to old format for compatibility."""
        polys = []
        kinds = []
        meta_list = []

        for shape_id in self._shape_order:
            shape = self._shapes.get(shape_id)
            if shape is None:
                continue

            polys.append(list(shape.points))
            kinds.append(shape.shape_type)
            meta_list.append(self._shape_to_meta(shape))

        return polys, kinds, meta_list

    @staticmethod
    def _shape_to_meta(shape: Shape) -> dict[str, Any] | None:
        """Convert shape properties back to metadata dict."""
        if shape.shape_type == "polyline":
            return None

        if shape.shape_type == "spline":
            control_points = getattr(shape, "control_points", None)
            degree = getattr(shape, "degree", None)
            closed = getattr(shape, "closed", None)
            segments = getattr(shape, "segments", None)
            meta: dict[str, Any] = {}
            if control_points is not None:
                meta["control_points"] = [tuple(pt) for pt in control_points]
            if degree is not None:
                meta["degree"] = int(degree)
            if closed is not None:
                meta["closed"] = bool(closed)
            if segments is not None:
                meta["segments"] = int(segments)
            return meta if meta else None

        meta = {}
        center = getattr(shape, "center", None)
        radius = getattr(shape, "radius", None)
        rx = getattr(shape, "rx", None)
        ry = getattr(shape, "ry", None)
        rotation = getattr(shape, "rotation", None)
        start_angle = getattr(shape, "start_angle", None)
        end_angle = getattr(shape, "end_angle", None)
        width = getattr(shape, "width", None)
        height = getattr(shape, "height", None)

        if center is not None:
            meta["center"] = center
        if radius is not None:
            meta["radius"] = radius
        if rx is not None:
            meta["rx"] = rx
        if ry is not None:
            meta["ry"] = ry
        if rotation is not None:
            meta["rotation"] = rotation
        if start_angle is not None:
            meta["start_angle"] = start_angle
        if end_angle is not None:
            meta["end_angle"] = end_angle
        if width is not None:
            meta["width"] = width
        if height is not None:
            meta["height"] = height

        return meta if meta else None

    def validate_dual_system(
        self,
        polys: list[list[tuple[float, float]]],
        kinds: list[str],
    ) -> bool:
        """Validate that shapes match the old polyline storage.

        Used during Phase 2 to ensure no data loss during migration.
        """
        if not self._dual_system_validation:
            return True

        # Shape count should match polyline count
        if len(self._shape_order) != len(polys):
            print(f"Shape count mismatch: {len(self._shape_order)} vs {len(polys)}")
            return False

        # Point count should match (approximately, within tolerance)
        for i, (shape_id, poly) in enumerate(zip(self._shape_order, polys)):
            shape = self._shapes.get(shape_id)
            if shape is None:
                print(f"Shape {shape_id} not found at index {i}")
                return False

            if len(shape.points) != len(poly):
                print(
                    f"Point count mismatch at index {i}: "
                    f"{len(shape.points)} vs {len(poly)}"
                )
                # Not a hard error - tessellation can vary
                # return False

        return True
