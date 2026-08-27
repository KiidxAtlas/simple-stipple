"""Pattern-specific presentation of reusable canvas controls."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from simple_stipple.canvas.layers.logic import build_layer_row, build_shape_rows, describe_polyline
from simple_stipple.canvas.runtime import CanvasPageRuntimeBase

LOGGER = logging.getLogger(__name__)


class PatternCanvasPageRuntime(CanvasPageRuntimeBase):
    """Present Pattern outlines and generated results in the canvas sidebar."""

    def __init__(
        self,
        *,
        canvas: Any,
        toolbar_module: Any,
        layer_sidebar: Any,
        canvas_status: Any,
        precision_bar: Any,
        get_orig_polys: Callable[[], list[list[tuple[float, float]]]],
        is_preview_running: Callable[[], bool],
        has_preview_cache: Callable[[], bool],
        has_zones: Callable[[], bool],
        get_preview_categories: Callable[[], dict[str, list[list[tuple[float, float]]]]]
        | None = None,
    ) -> None:
        super().__init__(canvas=canvas, toolbar_module=toolbar_module)
        self._layer_sidebar, self._canvas_status, self._precision_bar = (
            layer_sidebar,
            canvas_status,
            precision_bar,
        )
        self._get_orig_polys, self._is_preview_running = get_orig_polys, is_preview_running
        self._has_preview_cache, self._has_zones = has_preview_cache, has_zones
        self._get_preview_categories = get_preview_categories
        self._shape_labels: dict[str, dict[str, str]] = {}

    def rename_shape(self, layer_name: str, shape_key: object, new_label: str) -> None:
        if isinstance(shape_key, (tuple, list)) and shape_key:
            first = shape_key[0]
            group_id = self._canvas._grouping_service.group_of(first) if isinstance(first, str) else None
            if group_id is not None:
                self._canvas.set_group_label(group_id, new_label)
            return
        if isinstance(shape_key, str):
            self._shape_labels.setdefault(layer_name, {})[shape_key] = new_label

    def _shape_label(
        self, layer_name: str, entity_id: str, poly: list[tuple[float, float]], ordinal: int
    ) -> str:
        custom = self._shape_labels.get(layer_name, {}).get(entity_id)
        if custom:
            geometry = describe_polyline(entity_id, poly)
            return f"{custom}  ·  {geometry.split('  ', 1)[-1]}"
        count = len(poly) - int(len(poly) > 1 and poly[0] == poly[-1])
        return f"Outline {ordinal}  ·  {count} pts"

    def build_layer_tree_rows(
        self, layer_view_state: dict[str, dict[str, set[str]]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for layer_name in self._canvas.layer_names() or ["Outline"]:
            pairs = [
                (entity.id, self._canvas._flattened_points_by_id(entity.id))
                for entity in self._canvas._entities_by_id.values()
                if (entity.layer or "Outline") == layer_name
            ]
            hidden = layer_view_state.setdefault(layer_name, {}).setdefault("hidden", set())
            ordinal = 0

            def label(
                entity_id: str,
                poly: list[tuple[float, float]],
                layer: str = layer_name,
            ) -> str:
                nonlocal ordinal
                ordinal += 1
                return self._shape_label(layer, entity_id, poly, ordinal)

            rows.append(
                build_layer_row(
                    name=layer_name,
                    display_name=layer_name,
                    active=layer_name == self._canvas.active_layer,
                    visible=True,
                    editable=True,
                    shapes=build_shape_rows(
                        [entity_id for entity_id, _ in pairs],
                        [poly for _, poly in pairs],
                        hidden,
                        label,
                        editable=True,
                        draggable=True,
                        groups=self._canvas._grouping_service.group_map(),
                        group_labels=dict(getattr(self._canvas, "_group_labels", {})),
                    ),
                    color=self._canvas.layer_color(layer_name),
                )
            )
        if (result := self._result_layer_row()) is not None:
            rows.append(result)
        return rows

    def _result_layer_row(self) -> dict[str, Any] | None:
        if self._get_preview_categories is None:
            return None
        try:
            categories = self._get_preview_categories() or {}
        except Exception:
            LOGGER.exception("Preview-category callback failed")
            return None
        count = len(categories.get("pattern") or []) + len(categories.get("fill") or [])
        return (
            build_layer_row(
                name="pattern_result",
                display_name=f"Pattern result  ·  {count} paths",
                active=False,
                visible=self._canvas.result_visible(),
                editable=False,
                shapes=[],
            )
            if count
            else None
        )

    def refresh_canvas_panels(self) -> None:
        self._layer_sidebar.apply_current_visibility()
        summary, topology = self._canvas.get_status_summary(), self._canvas.get_topology_summary()
        if self._is_preview_running():
            readiness_text, readiness_tone = "Solving", "warn"
        elif topology["open"] > 0 and not self._has_zones():
            readiness_text, readiness_tone = f"{topology['open']} open outline(s)", "warn"
        elif self._has_preview_cache():
            readiness_text, readiness_tone = "Pattern solved", "success"
        elif self._canvas.poly_count:
            readiness_text, readiness_tone = "Outline ready", "accent"
        else:
            readiness_text, readiness_tone = "No outline", "warn"
        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._canvas.sel_count,
            object_count=self._canvas.poly_count,
            precision_text=str(summary["precision"]),
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=self._canvas.get_zoom_percent(),
            cursor_pos=self._canvas.get_cursor_world_pos(),
            unit=str(getattr(self._canvas, "_unit_system", "mm")),
        )
        self._precision_bar.refresh()
        self._layer_sidebar.refresh_tree()
