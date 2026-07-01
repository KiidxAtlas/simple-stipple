"""Derived Shape objects for snap candidates — rebuilt from polyline state."""

from __future__ import annotations

from typing import Any

from src.backend.shapes import Shape, ShapeFactory


class ShapeStorage:
    """Ordered Shape objects derived from the canvas polyline state.

    Rebuilt by ``PolylineView._sync_shape_storage_from_entities`` after each
     edit; consumed by PolylineView for shape-aware snap candidates.
    """

    def __init__(self):
        self._shapes: list[Shape] = []

    def migrate_from_polylines(
        self,
        polys: list[list[tuple[float, float]]],
        kinds: list[str],
        meta_list: list[dict[str, Any] | None],
    ) -> None:
        """Rebuild shape objects from the polyline + metadata state."""
        ShapeFactory.reset_id_counter(0)
        self._shapes = [
            ShapeFactory.from_legacy(
                kind=kinds[idx] if idx < len(kinds) else "polyline",
                points=poly,
                metadata=meta_list[idx] if idx < len(meta_list) else None,
            )
            for idx, poly in enumerate(polys)
        ]

    def get_all_shapes(self) -> list[Shape]:
        return list(self._shapes)
