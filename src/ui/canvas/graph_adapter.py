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
        polys = self.graph.get_layer_polylines(self.display_layer)
        canvas.set_polylines_state(polys, fit=fit)
        self._build_index_mapping()

    def capture_from_canvas(self, canvas: PolylineView) -> None:
        self.graph.set_layer_polylines(
            self.display_layer, canvas.get_polylines_state()
        )
        self._build_index_mapping()

    def selected_refs(self, canvas: PolylineView) -> list[EntityRef]:
        selected_indices = canvas.get_selection_indices()
        return [
            self.index_to_ref[idx]
            for idx in selected_indices
            if 0 <= idx < len(self.index_to_ref)
        ]
