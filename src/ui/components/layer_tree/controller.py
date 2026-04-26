"""Shared controller for wiring a DXF layer tree sidebar to a canvas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.ui.components.layer_tree.helpers import (
    apply_layer_visibility,
    apply_shape_visibility,
    hidden_bucket,
)

LayerTreeState = dict[str, dict[str, set[int]]]
LayerRowsBuilder = Callable[[LayerTreeState], list[dict[str, Any]]]


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
    ) -> None:
        self._canvas = canvas
        self._layers_tree = layers_tree
        self._get_active_layer_name = get_active_layer_name
        self._build_rows = build_rows
        self._on_visibility_changed = on_visibility_changed
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

    @property
    def state(self) -> LayerTreeState:
        return self._layer_view_state

    def clear(self) -> None:
        self._layer_view_state = {}
        self._shape_counts = {}

    def hidden_for(self, layer_name: str) -> set[int]:
        return hidden_bucket(self._layer_view_state, layer_name)

    def apply_current_visibility(self) -> None:
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

    def on_layer_visibility_changed(self, layer: str, visible: bool) -> None:
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
        if not isinstance(shape_key, int):
            return
        hidden = self.hidden_for(layer)
        apply_shape_visibility(hidden, shape_key, visible)
        self.apply_current_visibility()
        self._on_visibility_changed()
