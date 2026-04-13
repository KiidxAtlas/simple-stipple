"""Adapter between DocumentGraph layers and PolylineView canvas."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.document_actions import replace_layer_polylines
from src.core.document_graph import DocumentGraph, EntityRef


@dataclass
class CanvasGraphAdapter:
    graph: DocumentGraph
    display_layer: str = "geometry"
    index_to_ref: list[EntityRef] = field(default_factory=list)

    def _build_index_mapping(self) -> None:
        if self.display_layer == "geometry":
            self.index_to_ref = [
                ("segment", sid) for sid in sorted(self.graph.segments.keys())
            ]
            return

        polys = self.graph.get_layer_polylines(
            self.display_layer, fallback_geometry=False
        )
        self.index_to_ref = [
            ("layer-polyline", idx)  # type: ignore[list-item]
            for idx in range(len(polys))
        ]

    def load_to_canvas(self, canvas, *, fit: bool = False) -> None:
        polys = self.graph.get_layer_polylines(self.display_layer)
        canvas.set_polylines_state(polys, fit=fit)
        self._build_index_mapping()

    def capture_from_canvas(self, canvas) -> None:
        polylines = canvas.get_polylines_state()
        if self.display_layer == "geometry":
            # Rebuild canonical primitives from current canvas state.
            self.graph.points.clear()
            self.graph.segments.clear()
            self.graph.constraints.clear()
            for poly in polylines:
                self.graph.add_polyline_as_segments(
                    poly,
                    layer="geometry",
                    merge_points=False,
                )
            self.graph.set_layer_polylines("geometry", [], entity_refs=[])
            self.graph.record_action(
                "replace_geometry_from_canvas",
                {"count": len(polylines)},
                touched=[("layer", "geometry")],
                invalidated_layers=sorted(
                    self.graph.reachable_dependents({"geometry"})
                ),
            )
        else:
            replace_layer_polylines(self.graph, self.display_layer, polylines)
        self._build_index_mapping()

    def selected_refs(self, canvas) -> list[EntityRef]:
        selected_indices = canvas.get_selection_indices()
        refs: list[EntityRef] = []
        for idx in selected_indices:
            if 0 <= idx < len(self.index_to_ref):
                refs.append(self.index_to_ref[idx])
        return refs

    def select_refs(self, canvas, refs: list[EntityRef]) -> None:
        ref_set = set(refs)
        indices = [idx for idx, ref in enumerate(self.index_to_ref) if ref in ref_set]
        canvas.set_selection(indices)
