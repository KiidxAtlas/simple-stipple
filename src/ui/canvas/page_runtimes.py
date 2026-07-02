"""Canvas/layer runtime helpers for page UIs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.ui.widgets.layer_tree import (
    flatten_shape_keys,
    build_layer_row,
    build_shape_rows,
    describe_polyline,
)


class CanvasPageRuntimeBase:
    """Shared toolbar / selection sync helpers for canvas-backed pages."""

    def __init__(self, *, canvas: Any, toolbar_module: Any) -> None:
        self._canvas = canvas
        self._toolbar_module = toolbar_module

    def on_toolbar_mode(self, value: str) -> None:
        self._toolbar_module.set_active_mode(value)
        self._canvas.set_mode(value.lower())

    def on_canvas_mode_change(self, mode: str) -> None:
        self._toolbar_module.set_active_mode(mode)

    def on_selection_change(self, count: int) -> None:
        self._toolbar_module.set_selection_count(count)

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(flatten_shape_keys(indices))

    def fit_selection(self) -> bool:
        return bool(self._canvas.fit_selection())


class TraceCanvasPageRuntime(CanvasPageRuntimeBase):
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
        super().__init__(canvas=canvas, toolbar_module=toolbar_module)
        self._layer_sidebar = layer_sidebar
        self._canvas_status = canvas_status
        self._precision_bar = precision_bar
        self._is_running = is_running
        self._has_image = has_image

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


class PatternCanvasPageRuntime(CanvasPageRuntimeBase):
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
        get_preview_categories: Callable[[], dict[str, list[list[tuple[float, float]]]]]
        | None = None,
    ) -> None:
        super().__init__(canvas=canvas, toolbar_module=toolbar_module)
        self._layer_sidebar = layer_sidebar
        self._canvas_status = canvas_status
        self._precision_bar = precision_bar
        self._get_orig_polys = get_orig_polys
        self._get_showing_preview = get_showing_preview
        self._is_preview_running = is_preview_running
        self._has_preview_cache = has_preview_cache
        self._has_zones = has_zones
        self._get_preview_categories = get_preview_categories
        # Custom shape labels for outline mode: layer_name → {poly_index: label}
        self._shape_labels: dict[str, dict[int, str]] = {}

    def rename_shape(self, layer_name: str, shape_key: object, new_label: str) -> None:
        """Persist a custom display label for an outline shape."""
        if isinstance(shape_key, (tuple, list)) and shape_key:
            first = shape_key[0]
            gid = self._canvas._groups.get(first) if isinstance(first, int) else None
            if gid is not None:
                self._canvas.set_group_label(gid, new_label)
            return
        if not isinstance(shape_key, int):
            return
        self._shape_labels.setdefault(layer_name, {})[int(shape_key)] = new_label

    def _shape_label_builder(self, layer_name: str):
        """Return a label function that uses custom names if set."""
        labels = self._shape_labels.get(layer_name, {})

        def _label(idx: int, poly: list[tuple[float, float]]) -> str:
            custom = labels.get(idx)
            geo = describe_polyline(idx, poly)
            if not custom:
                return geo
            parts = geo.split("  ", 1)
            geo_suffix = parts[1] if len(parts) > 1 else geo
            return f"{custom}  ·  {geo_suffix}"

        return _label

    def _build_tree_shape_rows(
        self,
        layer_name: str,
        polylines: list[list[tuple[float, float]]],
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        hidden = layer_view_state.setdefault(layer_name, {}).setdefault("hidden", set())
        # Outline layer (not preview) is editable/draggable so users get the
        # same rename and selection features as the Draft tab.
        is_outline = layer_name == "pattern_active"
        label_fn = (
            self._shape_label_builder(layer_name) if is_outline else describe_polyline
        )
        groups = dict(self._canvas._groups.items()) if is_outline else None
        return build_shape_rows(
            polylines,
            hidden,
            label_fn,
            editable=is_outline,
            draggable=is_outline,
            groups=groups,
            group_labels=dict(getattr(self._canvas, "_group_labels", {})),
        )

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        showing_preview = self._get_showing_preview()
        rows: list[dict[str, Any]] = []
        # While previewing the pattern fill, split the layer tree into the
        # three DXF export categories (outline / pattern / fill) so the user
        # sees exactly what each layer of the export will contain.
        if showing_preview:
            categories: dict[str, list[list[tuple[float, float]]]] = {}
            if self._get_preview_categories is not None:
                try:
                    categories = self._get_preview_categories() or {}
                except Exception:
                    categories = {}

            outline_polys = categories.get("outline") or self._get_orig_polys()
            pattern_polys = categories.get("pattern", [])
            fill_polys = categories.get("fill", [])

            if outline_polys:
                rows.append(
                    build_layer_row(
                        name="pattern_outline",
                        display_name="Outline",
                        active=False,
                        visible=True,
                        editable=False,
                        shapes=self._build_tree_shape_rows(
                            "pattern_outline",
                            outline_polys,
                            layer_view_state,
                        ),
                    )
                )
            rows.append(
                build_layer_row(
                    name="pattern_preview",
                    display_name="Pattern",
                    active=True,
                    visible=True,
                    editable=False,
                    shapes=self._build_tree_shape_rows(
                        "pattern_preview",
                        pattern_polys
                        if categories
                        else self._canvas.get_polylines_state(),
                        layer_view_state,
                    ),
                )
            )
            if fill_polys:
                rows.append(
                    build_layer_row(
                        name="pattern_fill",
                        display_name="Fill",
                        active=False,
                        visible=True,
                        editable=False,
                        shapes=self._build_tree_shape_rows(
                            "pattern_fill",
                            fill_polys,
                            layer_view_state,
                        ),
                    )
                )
            return rows

        # Edit mode: the canvas state IS the outline. Show a single row
        # so the tree is honest about what is actually on the canvas.
        rows.append(
            build_layer_row(
                name="pattern_active",
                display_name="Outline",
                active=True,
                visible=True,
                editable=False,
                shapes=self._build_tree_shape_rows(
                    "pattern_active",
                    self._canvas.get_polylines_state(),
                    layer_view_state,
                ),
            )
        )
        return rows

    def refresh_canvas_panels(self) -> None:
        self._layer_sidebar.apply_current_visibility()
        # Sync ghost-overlay visibility with the outline_source row in the
        # layer tree (only present while previewing the pattern). The ghost
        # respects both per-shape toggles and the layer-level eye.
        if hasattr(self._canvas, "set_ghost_polylines") and self._get_showing_preview():
            orig_polys = self._get_orig_polys()
            if orig_polys:
                outline_hidden = self._layer_sidebar.hidden_for("pattern_outline")
                visible_polys = [
                    poly
                    for idx, poly in enumerate(orig_polys)
                    if idx not in outline_hidden
                ]
                self._canvas.set_ghost_polylines(visible_polys, visible=True)
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
