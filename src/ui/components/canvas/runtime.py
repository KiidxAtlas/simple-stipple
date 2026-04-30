"""Canvas runtime logic shared by page-level canvas UIs."""

from __future__ import annotations

from typing import Any

from src.backend.document.actions import (
    create_layer,
    delete_layer,
    move_entities_to_layer,
    rename_layer,
    reorder_layer,
    set_active_layer,
)
from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import graph_from_polylines
from src.ui.canvas.graph_adapter import CanvasGraphAdapter
from src.ui.components.layer_tree.helpers import (
    build_layer_row,
    build_shape_rows,
    describe_polyline,
    hidden_bucket,
)


class CanvasRuntime:
    """Own graph/layer state and coordinate it with a canvas surface."""

    def __init__(self, *, canvas: Any, default_layer: str = "Layer 1") -> None:
        self._canvas = canvas
        self.default_layer = default_layer

        self.doc_graph = DocumentGraph()
        set_active_layer(self.doc_graph, self.default_layer)
        self.graph_adapter = CanvasGraphAdapter(
            self.doc_graph,
            display_layer=self.default_layer,
        )
        self.layer_view_state: dict[str, dict[str, set[int]]] = {}
        self._hidden_indices: set[int] = set()
        self._locked_indices: set[int] = set()
        # Maps ghost overlay poly index → (layer_name, local_poly_index_in_layer).
        self._ghost_layer_map: list[tuple[str, int]] = []
        # Custom shape labels: layer_name → {poly_index: label}
        self._shape_labels: dict[str, dict[int, str]] = {}
        self.set_current_layer_view(self.default_layer)

    def current_layer_name(self) -> str:
        return getattr(self.doc_graph, "active_layer", self.default_layer)

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection([int(i) for i in indices if isinstance(i, int)])

    def layer_view_bucket(self, layer: str | None = None) -> dict[str, set[int]]:
        layer_name = layer or self.current_layer_name()
        bucket = self.layer_view_state.setdefault(layer_name, {"locked": set()})
        hidden_bucket(self.layer_view_state, layer_name)
        if "locked" not in bucket:
            bucket["locked"] = set()
        return bucket

    def set_current_layer_view(self, layer: str | None = None) -> None:
        bucket = self.layer_view_bucket(layer)
        self._hidden_indices = bucket["hidden"]
        self._locked_indices = bucket["locked"]

    def rename_layer_view_state(self, old: str, new: str) -> None:
        if old == new:
            return
        if old in self.layer_view_state:
            self.layer_view_state[new] = self.layer_view_state.pop(old)
        if self.current_layer_name() == new:
            self.set_current_layer_view(new)

    def delete_layer_view_state(self, name: str) -> None:
        self.layer_view_state.pop(name, None)
        if self.current_layer_name() == name:
            self.set_current_layer_view(self.current_layer_name())

    def normalize_graph_for_ui(self) -> None:
        """Hide internal geometry layer and keep a user-facing active layer."""
        if self.default_layer not in self.doc_graph.layers:
            self.doc_graph.ensure_layer(self.default_layer)
        if self.doc_graph.active_layer == "geometry":
            geometry_polys = self.doc_graph.get_layer_polylines(
                "geometry", fallback_geometry=True
            )
            current_default = self.doc_graph.get_layer_polylines(
                self.default_layer,
                fallback_geometry=False,
            )
            if geometry_polys and not current_default:
                self.doc_graph.set_layer_polylines(self.default_layer, geometry_polys)
            self.doc_graph.set_active_layer(self.default_layer)

    def reload_active_layer(self, *, fit: bool = False) -> None:
        active = self.current_layer_name()
        self.graph_adapter = CanvasGraphAdapter(self.doc_graph, display_layer=active)
        self.graph_adapter.load_to_canvas(self._canvas, fit=fit)
        self._canvas.set_mode("select")
        self._canvas.deselect_all()
        self.set_current_layer_view(active)
        self.sync_browser_interaction_state()
        self._update_ghost_layers()

    def _update_ghost_layers(self) -> None:
        """Render all visible non-active layers as ghost overlays."""
        if not hasattr(self._canvas, "set_ghost_polylines"):
            return
        active = self.current_layer_name()
        ghost_polys: list[list[tuple[float, float]]] = []
        ghost_map: list[tuple[str, int]] = []
        for name, layer in self.doc_graph.iter_layers():
            if name == "geometry" or name == active:
                continue
            layer_polys = list(layer.polylines)
            if not layer_polys:
                continue
            hidden = hidden_bucket(self.layer_view_state, name)
            n = len(layer_polys)
            # Skip entirely hidden layers.
            if n > 0 and len(hidden) >= n:
                continue
            for idx, poly in enumerate(layer_polys):
                if idx not in hidden:
                    ghost_map.append((name, idx))
                    ghost_polys.append(poly)
        self._ghost_layer_map = ghost_map
        self._canvas.set_ghost_polylines(ghost_polys if ghost_polys else None)

    def layer_for_ghost_index(self, ghost_idx: int) -> tuple[str, int] | None:
        """Return (layer_name, local_poly_index) for a ghost overlay poly index."""
        if 0 <= ghost_idx < len(self._ghost_layer_map):
            return self._ghost_layer_map[ghost_idx]
        return None

    def switch_active_layer(self, layer: str, *, fit: bool = False) -> bool:
        current = self.current_layer_name()
        if current == layer:
            self.set_current_layer_view(layer)
            self.sync_browser_interaction_state()
            return False
        self.graph_adapter.capture_from_canvas(self._canvas)
        set_active_layer(self.doc_graph, layer)
        self.reload_active_layer(fit=fit)
        return True

    def add_layer_and_activate(self, layer: str) -> None:
        self.graph_adapter.capture_from_canvas(self._canvas)
        create_layer(self.doc_graph, layer, activate=True)
        self.reload_active_layer(fit=False)

    def shape_move_requested(
        self,
        source_layer: str,
        shape_key: object,
        target_layer: str,
    ) -> bool:
        if not isinstance(shape_key, int):
            return False
        return self.shapes_move_requested(source_layer, [shape_key], target_layer)

    def shapes_move_requested(
        self,
        source_layer: str,
        shape_keys: list,
        target_layer: str,
    ) -> bool:
        """Move multiple shapes from *source_layer* to *target_layer* atomically.

        Captures the current canvas state once, performs every move in a single
        graph operation, then reloads the source layer. The active layer is
        intentionally left alone — moving a shape *out* of the layer the user
        is editing should not yank them away from it.
        """
        if source_layer == target_layer:
            return False
        indices = sorted({int(k) for k in shape_keys if isinstance(k, int)})
        if not indices:
            return False
        self.graph_adapter.capture_from_canvas(self._canvas)
        move_entities_to_layer(
            self.doc_graph,
            [("layer-polyline", idx) for idx in indices],
            source_layer=source_layer,
            target_layer=target_layer,
        )
        # Stay on the source layer so the canvas keeps the user's context.
        # The tree refresh below will show the source list shrunken and the
        # target row's badge incremented.
        self.reload_active_layer(fit=False)
        return True

    def layer_renamed(self, old_name: str, new_name: str) -> None:
        was_active = self.current_layer_name() == old_name
        if was_active:
            self.graph_adapter.capture_from_canvas(self._canvas)
        rename_layer(self.doc_graph, old_name, new_name)
        self.rename_layer_view_state(old_name, new_name)
        if was_active:
            self.reload_active_layer(fit=False)

    def layer_deleted(self, layer: str) -> None:
        was_active = self.current_layer_name() == layer
        if was_active:
            self.graph_adapter.capture_from_canvas(self._canvas)
        delete_layer(self.doc_graph, layer)
        self.normalize_graph_for_ui()
        self.delete_layer_view_state(layer)
        self.reload_active_layer(fit=False)

    def layer_moved(self, layer: str, new_index: int) -> None:
        reorder_layer(self.doc_graph, layer, new_index + 1)

    def move_selected_to_layer(self, target_layer: str) -> bool:
        source_layer = self.current_layer_name()
        if source_layer == target_layer:
            return False
        refs = self.graph_adapter.selected_refs(self._canvas)
        if not refs:
            return False

        self.graph_adapter.capture_from_canvas(self._canvas)
        move_entities_to_layer(
            self.doc_graph,
            refs,
            source_layer=source_layer,
            target_layer=target_layer,
        )
        # Keep the user on the source layer; the moved shapes vanish from
        # the canvas and reappear under the target layer's tree row.
        self.reload_active_layer(fit=False)
        return True

    def sync_browser_interaction_state(self) -> None:
        max_idx = self._canvas.poly_count
        valid = set(range(max_idx))
        self._hidden_indices &= valid
        self._locked_indices &= valid
        self._canvas.set_hidden_indices(sorted(self._hidden_indices))
        self._canvas.set_locked_indices(sorted(self._locked_indices))

    def on_canvas_edit(self) -> None:
        self.graph_adapter.capture_from_canvas(self._canvas)
        self.sync_browser_interaction_state()
        self._update_ghost_layers()

    def rename_shape(self, layer_name: str, shape_key: object, new_label: str) -> None:
        """Persist a custom display label for a shape."""
        if not isinstance(shape_key, int):
            return
        self._shape_labels.setdefault(layer_name, {})[int(shape_key)] = new_label

    def _shape_label_builder(
        self, layer_name: str
    ):  # returns a Callable[[int, list], str]
        labels = self._shape_labels.get(layer_name, {})

        def _label(idx: int, poly: list[tuple[float, float]]) -> str:
            custom = labels.get(idx)
            geo = describe_polyline(idx, poly)
            if not custom:
                return geo
            # Append the geometry description so topology info is preserved.
            # geo = "01  Closed  ·  4 pts  ·  10.7 × 6.1 mm"
            # Strip the "01  " index prefix before appending as suffix.
            parts = geo.split("  ", 1)
            geo_suffix = parts[1] if len(parts) > 1 else geo
            return f"{custom}  ·  {geo_suffix}"

        return _label

    def build_layer_shape_rows(
        self,
        layer_name: str,
        polylines: list[list[tuple[float, float]]],
    ) -> list[dict[str, Any]]:
        hidden = hidden_bucket(self.layer_view_state, layer_name)
        return build_shape_rows(
            polylines,
            hidden,
            self._shape_label_builder(layer_name),
            editable=True,
            draggable=True,
        )

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        active = self.current_layer_name()
        rows: list[dict[str, Any]] = []
        for name, layer in self.doc_graph.iter_layers():
            if name == "geometry":
                continue
            layer_polys = list(layer.polylines)
            if name == active:
                layer_polys = self._canvas.get_polylines_state()
            # A layer is considered hidden if all its shapes are in the hidden set.
            hidden = hidden_bucket(layer_view_state, name)
            n = len(layer_polys)
            visible = n == 0 or len(hidden) < n
            rows.append(
                build_layer_row(
                    name=name,
                    display_name=name,
                    active=name == active,
                    visible=visible,
                    editable=True,
                    shapes=self.build_layer_shape_rows(name, layer_polys),
                )
            )
        if not rows:
            rows = [
                {
                    "name": self.default_layer,
                    "internal_name": self.default_layer,
                    "display_name": self.default_layer,
                    "visible": True,
                    "active": True,
                    "editable": True,
                    "shapes": [],
                }
            ]
        self.layer_view_state = layer_view_state
        return rows

    def load_polys(self, polys: list[list[tuple[float, float]]], *, fit: bool) -> None:
        self._canvas.set_polylines_state(polys, fit=fit)
        self._canvas.set_mode("select")
        self.doc_graph = graph_from_polylines(
            polys,
            layer=self.default_layer,
            as_segments=False,
        )
        self.graph_adapter = CanvasGraphAdapter(
            self.doc_graph,
            display_layer=self.default_layer,
        )
        self.layer_view_state = {}
        self.normalize_graph_for_ui()
        set_active_layer(self.doc_graph, self.default_layer)
        self.set_current_layer_view(self.default_layer)

    def load_polys_by_layer(
        self,
        by_layer: dict[str, list[list[tuple[float, float]]]],
        *,
        fit: bool,
    ) -> None:
        """Load polylines into separate document-graph layers.

        The first non-empty layer becomes the active layer; its polylines are
        also pushed onto the canvas. Remaining layers are stored on the graph
        and surface in the layer tree / ghost overlays.
        """
        if not by_layer:
            self.load_polys([], fit=fit)
            return
        first_name = next(iter(by_layer))
        first_polys = by_layer[first_name]
        # Seed canvas + graph using the first layer as the active one so we
        # reuse the existing setup pipeline (graph_adapter, view state, etc.).
        self._canvas.set_polylines_state(first_polys, fit=fit)
        self._canvas.set_mode("select")
        self.doc_graph = graph_from_polylines(
            first_polys,
            layer=first_name,
            as_segments=False,
        )
        # Drop into all remaining layers verbatim.
        for name, polys in by_layer.items():
            if name == first_name:
                continue
            self.doc_graph.set_layer_polylines(name, polys)
        self.graph_adapter = CanvasGraphAdapter(
            self.doc_graph,
            display_layer=first_name,
        )
        self.layer_view_state = {}
        self.normalize_graph_for_ui()
        set_active_layer(self.doc_graph, first_name)
        self.set_current_layer_view(first_name)
        self._update_ghost_layers()

    def restore_graph_state(self, graph_state: dict) -> None:
        self.doc_graph.restore(graph_state)
        self.normalize_graph_for_ui()
        self.graph_adapter = CanvasGraphAdapter(
            self.doc_graph,
            display_layer=self.doc_graph.active_layer,
        )

    def reset_empty(self) -> None:
        self.doc_graph = DocumentGraph()
        set_active_layer(self.doc_graph, self.default_layer)
        self.graph_adapter = CanvasGraphAdapter(
            self.doc_graph,
            display_layer=self.doc_graph.active_layer,
        )
        self.graph_adapter.load_to_canvas(self._canvas, fit=False)
        self.layer_view_state = {}
        self.set_current_layer_view(self.default_layer)
        self.sync_browser_interaction_state()
