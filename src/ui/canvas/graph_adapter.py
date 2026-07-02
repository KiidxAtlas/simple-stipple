"""Adapter between DocumentGraph layers and PolylineView canvas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.backend.document.graph import DocumentGraph, EntityRef

if TYPE_CHECKING:
    from src.ui.canvas.view import PolylineView


@dataclass
class CanvasGraphAdapter:
    graph: DocumentGraph
    display_layer: str = "geometry"
    index_to_ref: list[EntityRef] = field(default_factory=list)

    def _build_index_mapping(self) -> None:
        polys = self.graph.get_layer_polylines(self.display_layer)
        self.index_to_ref = [("layer-polyline", idx) for idx in range(len(polys))]

    def load_to_canvas(self, canvas: PolylineView, *, fit: bool = False) -> None:
        layer = self.graph.ensure_layer(self.display_layer)
        if layer.records is not None:
            canvas.set_entity_records(layer.records, fit=fit)
        else:
            canvas.set_polylines_state(layer.polylines, fit=fit)
        self._build_index_mapping()

    def capture_from_canvas(self, canvas: PolylineView) -> None:
        records = canvas.get_entity_records()
        self.graph.set_layer_polylines(
            self.display_layer,
            [[(p[0], p[1]) for p in r["points"]] for r in records],
            records=records,
        )
        self._build_index_mapping()

    def selected_refs(self, canvas: PolylineView) -> list[EntityRef]:
        selected_indices = canvas.get_selection_indices()
        return [
            self.index_to_ref[idx]
            for idx in selected_indices
            if 0 <= idx < len(self.index_to_ref)
        ]
