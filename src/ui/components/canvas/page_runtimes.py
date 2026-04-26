"""Canvas/layer runtime helpers for page UIs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.ui.components.layer_tree.helpers import (
    build_layer_row,
    build_shape_rows,
    describe_polyline,
)


class TraceCanvasPageRuntime:
    """Encapsulate Trace page canvas/layer/status behavior."""

    def __init__(
        self,
        *,
        canvas: Any,
        toolbar_module: Any,
        layer_sidebar: Any,
        canvas_status: Any,
        precision_bar: Any,
        is_running: Callable[[], bool],
        has_image: Callable[[], bool],
    ) -> None:
        self._canvas = canvas
        self._toolbar_module = toolbar_module
        self._layer_sidebar = layer_sidebar
        self._canvas_status = canvas_status
        self._precision_bar = precision_bar
        self._is_running = is_running
        self._has_image = has_image

    def on_toolbar_mode(self, value: str) -> None:
        self._toolbar_module.set_active_mode(value)
        self._canvas.set_mode(value.lower())

    def on_canvas_mode_change(self, mode: str) -> None:
        self._toolbar_module.set_active_mode(mode)

    def on_selection_change(self, count: int) -> None:
        self._toolbar_module.set_selection_count(count)

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)

    def fit_selection(self) -> bool:
        return bool(self._canvas.fit_selection())

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, object]]:
        hidden = layer_view_state.setdefault("trace_preview", {}).setdefault(
            "hidden", set()
        )
        return [
            build_layer_row(
                name="trace_preview",
                display_name="Trace Preview",
                active=True,
                visible=True,
                editable=False,
                shapes=[
                    {
                        "key": idx,
                        "label": f"Shape {idx + 1:02d}",
                        "visible": idx not in hidden,
                        "editable": False,
                        "draggable": False,
                    }
                    for idx in range(self._canvas.poly_count)
                ],
            )
        ]

    def refresh_canvas_panels(self) -> None:
        self._layer_sidebar.apply_current_visibility()
        summary = self._canvas.get_status_summary()
        if self._is_running():
            readiness_text = "Tracing"
            readiness_tone = "warn"
        elif self._canvas.poly_count:
            readiness_text = "Trace ready"
            readiness_tone = "success"
        elif self._has_image():
            readiness_text = "Ready to trace"
            readiness_tone = "accent"
        else:
            readiness_text = "No image"
            readiness_tone = "warn"
        zoom = (
            self._canvas.get_zoom_percent()
            if hasattr(self._canvas, "get_zoom_percent")
            else 100
        )
        cursor = (
            self._canvas.get_cursor_world_pos()
            if hasattr(self._canvas, "get_cursor_world_pos")
            else None
        )
        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._canvas.sel_count,
            object_count=self._canvas.poly_count,
            precision_text=str(summary["precision"]),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        self._precision_bar.refresh()
        self._layer_sidebar.refresh_tree()


class PatternCanvasPageRuntime:
    """Encapsulate Pattern page canvas/layer/status behavior."""

    def __init__(
        self,
        *,
        canvas: Any,
        toolbar_module: Any,
        layer_sidebar: Any,
        canvas_status: Any,
        precision_bar: Any,
        get_orig_polys: Callable[[], list[list[tuple[float, float]]]],
        get_showing_preview: Callable[[], bool],
        is_preview_running: Callable[[], bool],
        has_preview_cache: Callable[[], bool],
        has_zones: Callable[[], bool],
    ) -> None:
        self._canvas = canvas
        self._toolbar_module = toolbar_module
        self._layer_sidebar = layer_sidebar
        self._canvas_status = canvas_status
        self._precision_bar = precision_bar
        self._get_orig_polys = get_orig_polys
        self._get_showing_preview = get_showing_preview
        self._is_preview_running = is_preview_running
        self._has_preview_cache = has_preview_cache
        self._has_zones = has_zones

    def on_toolbar_mode(self, value: str) -> None:
        self._toolbar_module.set_active_mode(value)
        self._canvas.set_mode(value.lower())

    def on_canvas_mode_change(self, mode: str) -> None:
        self._toolbar_module.set_active_mode(mode)

    def on_selection_change(self, count: int) -> None:
        self._toolbar_module.set_selection_count(count)

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)

    def fit_selection(self) -> bool:
        return bool(self._canvas.fit_selection())

    def _active_tree_layer_name(self) -> str:
        return "pattern_preview" if self._get_showing_preview() else "pattern_active"

    def _build_tree_shape_rows(
        self,
        layer_name: str,
        polylines: list[list[tuple[float, float]]],
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        hidden = layer_view_state.setdefault(layer_name, {}).setdefault("hidden", set())
        return build_shape_rows(
            polylines,
            hidden,
            describe_polyline,
            editable=False,
            draggable=False,
        )

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        active_name = self._active_tree_layer_name()
        rows: list[dict[str, Any]] = []
        orig_polys = self._get_orig_polys()
        if orig_polys:
            rows.append(
                build_layer_row(
                    name="outline_source",
                    display_name="Outline",
                    active=False,
                    visible=True,
                    editable=False,
                    shapes=self._build_tree_shape_rows(
                        "outline_source",
                        orig_polys,
                        layer_view_state,
                    ),
                )
            )
        rows.append(
            build_layer_row(
                name=active_name,
                display_name="Preview" if self._get_showing_preview() else "Pattern",
                active=True,
                visible=True,
                editable=False,
                shapes=self._build_tree_shape_rows(
                    active_name,
                    self._canvas.get_polylines_state(),
                    layer_view_state,
                ),
            )
        )
        return rows

    def refresh_canvas_panels(self) -> None:
        self._layer_sidebar.apply_current_visibility()
        summary = self._canvas.get_status_summary()
        topo = self._canvas.get_topology_summary()
        if self._is_preview_running():
            readiness_text = "Previewing"
            readiness_tone = "warn"
        elif self._get_showing_preview():
            readiness_text = "Preview"
            readiness_tone = "success"
        elif topo["open"] > 0 and not self._has_zones():
            readiness_text = f"{topo['open']} open outline(s)"
            readiness_tone = "warn"
        elif self._has_preview_cache():
            readiness_text = "Preview ready"
            readiness_tone = "success"
        elif self._canvas.poly_count:
            readiness_text = "Outline ready"
            readiness_tone = "accent"
        else:
            readiness_text = "No outline"
            readiness_tone = "warn"
        zoom = (
            self._canvas.get_zoom_percent()
            if hasattr(self._canvas, "get_zoom_percent")
            else 100
        )
        cursor = (
            self._canvas.get_cursor_world_pos()
            if hasattr(self._canvas, "get_cursor_world_pos")
            else None
        )
        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._canvas.sel_count,
            object_count=self._canvas.poly_count,
            precision_text=str(summary["precision"]),
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        self._precision_bar.refresh()
        self._layer_sidebar.refresh_tree()
