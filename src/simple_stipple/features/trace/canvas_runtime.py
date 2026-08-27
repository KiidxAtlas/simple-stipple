"""Trace-specific wiring for the reusable canvas controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from simple_stipple.canvas.layers.logic import build_layer_row
from simple_stipple.canvas.runtime import CanvasPageRuntimeBase


class TraceCanvasPageRuntime(CanvasPageRuntimeBase):
    """Keep Trace readiness and preview-layer presentation in the Trace feature."""

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
        super().__init__(canvas=canvas, toolbar_module=toolbar_module)
        self._layer_sidebar = layer_sidebar
        self._canvas_status = canvas_status
        self._precision_bar = precision_bar
        self._is_running = is_running
        self._has_image = has_image

    def build_layer_tree_rows(
        self, layer_view_state: dict[str, dict[str, set[str]]]
    ) -> list[dict[str, object]]:
        hidden = layer_view_state.setdefault("trace_preview", {}).setdefault("hidden", set())
        return [
            build_layer_row(
                name="trace_preview",
                display_name="Trace Preview",
                active=True,
                visible=True,
                editable=False,
                shapes=[
                    {
                        "key": index,
                        "label": f"Shape {index + 1:02d}",
                        "visible": index not in hidden,
                        "editable": False,
                        "draggable": False,
                    }
                    for index in range(self._canvas.poly_count)
                ],
            )
        ]

    def refresh_canvas_panels(self) -> None:
        self._layer_sidebar.apply_current_visibility()
        summary = self._canvas.get_status_summary()
        if self._is_running():
            readiness_text, readiness_tone = "Tracing", "warn"
        elif self._canvas.poly_count:
            readiness_text, readiness_tone = "Trace ready", "success"
        elif self._has_image():
            readiness_text, readiness_tone = "Ready to trace", "accent"
        else:
            readiness_text, readiness_tone = "No image", "warn"
        zoom = self._canvas.get_zoom_percent() if hasattr(self._canvas, "get_zoom_percent") else 100
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
            unit=str(getattr(self._canvas, "_unit_system", "mm")),
        )
        self._precision_bar.refresh()
        self._layer_sidebar.refresh_tree()
