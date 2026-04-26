"""Separately placeable canvas modules with auto-wiring defaults."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from src.ui.components.canvas.widgets import CanvasPrecisionBar, DxfLayersTree
from src.ui.components.common.factories import _canvas_toolbar
from src.ui.components.layer_tree.controller import CanvasLayerSidebarController
from src.ui.components.layer_tree.helpers import (
    build_layer_row,
    build_shape_rows,
    describe_polyline,
    hidden_bucket,
)

LayerTreeState = dict[str, dict[str, set[int]]]
LayerRowsBuilder = Callable[[LayerTreeState], list[dict[str, Any]]]


class CanvasToolbarModule(QWidget):
    """Toolbar module that can auto-control a bound canvas."""

    def __init__(
        self,
        *,
        canvas: Any | None = None,
        on_mode: Callable[[str], None] | None = None,
        on_fit: Callable[[], None] | None = None,
        modes: tuple[str, ...] = ("Select", "Draw", "Edit"),
        extra_widgets: Sequence[QWidget] | None = None,
    ) -> None:
        super().__init__()
        self._canvas = canvas
        self._on_mode = on_mode
        self._on_fit = on_fit

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar, mode_buttons, selection_label = _canvas_toolbar(
            self._handle_mode,
            self._handle_fit,
            modes=modes,
        )
        toolbar_layout = toolbar.layout()
        if isinstance(toolbar_layout, QHBoxLayout) and extra_widgets:
            for widget in extra_widgets:
                toolbar_layout.insertWidget(toolbar_layout.count() - 1, widget)

        root.addWidget(toolbar)

        self.toolbar = toolbar
        self.mode_buttons = mode_buttons
        self.selection_label = selection_label
        self.sync_from_canvas()

    def bind_canvas(self, canvas: Any | None) -> None:
        self._canvas = canvas
        self.sync_from_canvas()

    def _handle_mode(self, mode: str) -> None:
        if callable(self._on_mode):
            self._on_mode(mode)
            return
        if self._canvas is not None and hasattr(self._canvas, "set_mode"):
            self._canvas.set_mode(mode.lower())
        self.set_active_mode(mode)

    def _handle_fit(self) -> None:
        if callable(self._on_fit):
            self._on_fit()
            return
        if self._canvas is not None and hasattr(self._canvas, "fit"):
            self._canvas.fit()

    def set_active_mode(self, mode: str) -> None:
        value = mode.lower()
        for name, button in self.mode_buttons.items():
            button.setProperty("active", name.lower() == value)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_selection_count(self, count: int) -> None:
        if count > 0:
            self.selection_label.setText(f"{count} selected")
            self.selection_label.setStyleSheet("color: #79c0ff;")
            return
        self.selection_label.setText("")
        self.selection_label.setStyleSheet("color: #8b949e;")

    def sync_from_canvas(self) -> None:
        if self._canvas is None:
            return
        if hasattr(self._canvas, "get_mode"):
            self.set_active_mode(str(self._canvas.get_mode()))
        self.set_selection_count(int(getattr(self._canvas, "sel_count", 0)))


class CanvasGridModule(CanvasPrecisionBar):
    """Grid/precision module (separate from toolbar and layer tree)."""

    def __init__(
        self,
        *,
        canvas: Any | None = None,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(canvas, on_changed=on_changed)


class CanvasLayerTreeModule(QWidget):
    """Layer-tree module that auto-wires to a bound canvas by default."""

    def __init__(
        self,
        *,
        canvas: Any,
        title: str = "Layers",
        editable: bool = False,
        get_active_layer_name: Callable[[], str] | None = None,
        build_layer_rows: LayerRowsBuilder | None = None,
        on_selection_requested: Callable[[list[int]], None] | None = None,
        on_fit_requested: Callable[[], None] | None = None,
        on_visibility_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._canvas = canvas
        self._active_layer_name = get_active_layer_name
        self._build_layer_rows = build_layer_rows

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tree = DxfLayersTree(title, editable=editable)
        root.addWidget(tree, stretch=1)

        selection_handler = on_selection_requested or self._default_select
        fit_handler = on_fit_requested or self._default_fit
        visibility_handler = on_visibility_changed or (lambda: None)

        controller = CanvasLayerSidebarController(
            canvas=canvas,
            layers_tree=tree,
            get_active_layer_name=self._resolve_active_layer_name,
            build_rows=self._build_rows,
            on_selection_requested=selection_handler,
            on_fit_requested=fit_handler,
            on_visibility_changed=visibility_handler,
        )

        self.tree = tree
        self.controller = controller

    @property
    def state(self) -> LayerTreeState:
        return self.controller.state

    def refresh_tree(self) -> None:
        self.controller.refresh_tree()

    def apply_current_visibility(self) -> None:
        self.controller.apply_current_visibility()

    def _default_select(self, indices: list[int]) -> None:
        if hasattr(self._canvas, "set_selection"):
            self._canvas.set_selection(indices)

    def _default_fit(self) -> None:
        if hasattr(self._canvas, "fit_selection") and self._canvas.fit_selection():
            return
        if hasattr(self._canvas, "fit"):
            self._canvas.fit()

    def _resolve_active_layer_name(self) -> str:
        if callable(self._active_layer_name):
            return str(self._active_layer_name())
        return "active"

    def _build_rows(self, layer_view_state: LayerTreeState) -> list[dict[str, Any]]:
        if callable(self._build_layer_rows):
            return self._build_layer_rows(layer_view_state)

        layer_name = self._resolve_active_layer_name()
        hidden = hidden_bucket(layer_view_state, layer_name)
        polylines = (
            self._canvas.get_polylines_state()
            if hasattr(self._canvas, "get_polylines_state")
            else []
        )
        return [
            build_layer_row(
                name=layer_name,
                display_name="Layer",
                active=True,
                visible=True,
                editable=False,
                shapes=build_shape_rows(
                    polylines,
                    hidden,
                    describe_polyline,
                    editable=False,
                    draggable=False,
                ),
            )
        ]
