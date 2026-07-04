"""Shared controller for wiring a DXF layer tree sidebar to a canvas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import (
    LayerRowsBuilder,
    LayerTreeState,
    apply_layer_visibility,
    apply_shape_visibility,
    flatten_shape_keys,
    hidden_bucket,
)


class CanvasLayerSidebarController:
    """Own layer visibility state and keep tree/canvas interaction in sync."""

    def __init__(
        self,
        *,
        canvas: Any,
        layers_tree: Any,
        get_active_layer_name: Callable[[], str],
        build_rows: LayerRowsBuilder,
        on_selection_requested: Callable[[list[int]], None],
        on_fit_requested: Callable[[], None],
        on_visibility_changed: Callable[[], None],
        visibility_adapter: Any | None = None,
    ) -> None:
        self._canvas = canvas
        self._layers_tree = layers_tree
        self._get_active_layer_name = get_active_layer_name
        self._build_rows = build_rows
        self._on_visibility_changed = on_visibility_changed
        # Entity-native visibility: when set, eye toggles write hidden flags
        # straight onto canvas entities (via set_shapes_hidden /
        # set_layer_hidden / solo_layer / set_all_hidden) instead of being
        # tracked in per-layer index buckets here.
        self._visibility_adapter = visibility_adapter
        self._layer_view_state: LayerTreeState = {}
        self._shape_counts: dict[str, int] = {}

        self._layers_tree.selectionRequested.connect(on_selection_requested)
        self._layers_tree.fitRequested.connect(on_fit_requested)
        self._layers_tree.layerVisibilityChanged.connect(
            self.on_layer_visibility_changed
        )
        self._layers_tree.shapeVisibilityChanged.connect(
            self.on_shape_visibility_changed
        )
        # New optional signals — guarded with hasattr so older trees still work.
        if hasattr(self._layers_tree, "bulkVisibilityRequested"):
            self._layers_tree.bulkVisibilityRequested.connect(
                self.on_bulk_visibility_requested
            )
        if hasattr(self._layers_tree, "layerSoloRequested"):
            self._layers_tree.layerSoloRequested.connect(self.on_solo_requested)
        # Shape operation signals — group, ungroup, merge, copy.
        if hasattr(self._layers_tree, "shapesGroupRequested"):
            self._layers_tree.shapesGroupRequested.connect(
                lambda layer, keys: self._handle_shape_operation("group", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesUngroupRequested"):
            self._layers_tree.shapesUngroupRequested.connect(
                lambda layer, keys: self._handle_shape_operation("ungroup", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesMergeRequested"):
            self._layers_tree.shapesMergeRequested.connect(
                lambda layer, keys: self._handle_shape_operation("merge", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesCopyRequested"):
            self._layers_tree.shapesCopyRequested.connect(
                lambda layer, keys: self._handle_shape_operation("copy", layer, keys)
            )

    def _handle_shape_operation(self, op: str, layer: str, keys: list) -> None:
        """Dispatch shape operations from the layer tree to the canvas."""
        from .model import flatten_shape_keys

        indices = flatten_shape_keys(keys)
        if not indices:
            return
        canvas = self._canvas
        if op == "group":
            if hasattr(canvas, "group_indices"):
                canvas.group_indices(indices)
        elif op == "ungroup":
            if hasattr(canvas, "ungroup_indices"):
                canvas.ungroup_indices(indices)
        elif op == "merge":
            if hasattr(canvas, "merge_selected_segments_to_objects"):
                # Temporarily select, merge, then restore selection.
                canvas.set_selection(indices)
                canvas.merge_selected_segments_to_objects()
        elif op == "copy" and hasattr(canvas, "_clipboard"):
            ents = canvas._entities
            clip_items = [
                {
                    "points": list(ents[i].points),
                    "kind": ents[i].kind,
                    "meta": ents[i].meta,
                }
                for i in indices
                if 0 <= i < len(ents)
            ]
            canvas._clipboard = clip_items

    @property
    def state(self) -> LayerTreeState:
        return self._layer_view_state

    def clear(self) -> None:
        self._layer_view_state = {}
        self._shape_counts = {}

    def hidden_for(self, layer_name: str) -> set[int]:
        return hidden_bucket(self._layer_view_state, layer_name)

    def apply_current_visibility(self) -> None:
        if self._visibility_adapter is not None:
            return  # hidden flags live on the entities themselves
        active_name = self._get_active_layer_name()
        hidden = sorted(self.hidden_for(active_name))
        self._canvas.set_hidden_indices(hidden)

    def refresh_tree(self) -> None:
        rows = self._build_rows(self._layer_view_state)
        self._shape_counts = {
            str(row.get("name", "")): len(list(row.get("shapes", [])))
            for row in rows
            if isinstance(row, dict)
        }
        self._layers_tree.set_layers(rows)
        # Rebuilding the tree wipes its row selection — reapply whatever is
        # currently selected on the canvas so the highlight survives status
        # refreshes (e.g. clicking a shape on a non-active layer switches
        # the active layer, which triggers a refresh right after the shape
        # selection was set; without this, the tree would show only the
        # newly-active LAYER highlighted, not the actual selected shape).
        get_indices = getattr(self._canvas, "get_selection_indices", None)
        select_keys = getattr(self._layers_tree, "select_shape_keys", None)
        if callable(get_indices) and callable(select_keys):
            select_keys(get_indices())

    def on_layer_visibility_changed(self, layer: str, visible: bool) -> None:
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_layer_hidden(layer, not visible)
            self._on_visibility_changed()
            return
        hidden = self.hidden_for(layer)
        apply_layer_visibility(hidden, self._shape_counts.get(layer, 0), visible)
        self.apply_current_visibility()
        self._on_visibility_changed()

    def on_shape_visibility_changed(
        self,
        layer: str,
        shape_key: object,
        visible: bool,
    ) -> None:
        indices = flatten_shape_keys(shape_key)
        if not indices:
            return
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_shapes_hidden(indices, not visible)
            self._on_visibility_changed()
            return
        hidden = self.hidden_for(layer)
        for idx in indices:
            apply_shape_visibility(hidden, idx, visible)
        self.apply_current_visibility()
        self._on_visibility_changed()

    def on_bulk_visibility_requested(self, visible: bool) -> None:
        """Toggle every known layer's visibility at once."""
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_all_hidden(not visible)
            self.refresh_tree()
            self._on_visibility_changed()
            return
        if not self._shape_counts:
            return
        for layer_name, count in self._shape_counts.items():
            hidden = self.hidden_for(layer_name)
            apply_layer_visibility(hidden, count, visible)
        self.apply_current_visibility()
        self.refresh_tree()
        self._on_visibility_changed()

    def on_solo_requested(self, target_layer: str) -> None:
        """Show only *target_layer*; hide every other known layer."""
        if self._visibility_adapter is not None:
            self._visibility_adapter.solo_layer(target_layer)
            self.refresh_tree()
            self._on_visibility_changed()
            return
        if not self._shape_counts:
            return
        for layer_name, count in self._shape_counts.items():
            hidden = self.hidden_for(layer_name)
            apply_layer_visibility(hidden, count, layer_name == target_layer)
        self.apply_current_visibility()
        self.refresh_tree()
        self._on_visibility_changed()
