"""Canvas-to-page runtime wiring: layer adapter, per-page status/toolbar
sync, and separately-placeable canvas modules.

Three previously-separate modules merged here — all are "how a canvas gets
wired into a page's UI" and are consumed exclusively by the
``ui/pages/*/tab.py`` files, a real import-graph-backed cohesion rather
than a superficial one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from src.ui.widgets.canvas.precision_bar import CanvasPrecisionBar
from src.ui.widgets.canvas.toolbar import canvas_toolbar
from src.ui.widgets.layer_tree.logic import (
    CanvasLayerSidebarController,
    LayerRowsBuilder,
    LayerTreeState,
    build_layer_row,
    build_shape_rows,
    describe_polyline,
    flatten_shape_keys,
    hidden_bucket,
)
from src.ui.widgets.layer_tree.widget import DxfLayersTree

LOGGER = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# CanvasRuntime — layer tree <-> per-entity layer model adapter
# ══════════════════════════════════════════════════════════════════════════

# Entities carry their layer, hidden, and locked state on ``EntityRecord``,
# so there is no separate document graph to capture into or reload from:
# switching layers is a repaint, and moving shapes between layers is a field
# assignment. (This replaced a DocumentGraph + adapter + per-layer view-state
# bucket stack that kept three copies of the same facts.)


class CanvasRuntime:
    """Coordinate layer state between a multi-layer canvas and its page UI."""

    def __init__(self, *, canvas: Any, default_layer: str = "Layer 1") -> None:
        self._canvas = canvas
        self.default_layer = default_layer
        canvas.set_layer_model([default_layer], default_layer)

    # ── Layer queries ─────────────────────────────────────────────────────

    def current_layer_name(self) -> str:
        return self._canvas.active_layer or self.default_layer

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        idxs = flatten_shape_keys(indices)
        canvas = self._canvas
        # Clicking a shape row that lives on a non-active layer activates
        # that layer first, so tree selection always works.
        for i in idxs:
            if 0 <= i < len(canvas._entities):
                layer = canvas._entities[i].layer
                if layer and layer != canvas.active_layer:
                    canvas.set_active_layer(layer)
                break
        canvas.set_selection(idxs)

    def reload_active_layer(self, *, fit: bool = False) -> None:
        """Kept for API compatibility — entities never leave the canvas."""
        if fit:
            self._canvas.fit()
        else:
            self._canvas._redraw()

    # ── Layer operations ──────────────────────────────────────────────────

    def switch_active_layer(self, layer: str, *, fit: bool = False) -> bool:
        changed = self.current_layer_name() != layer
        self._canvas.set_active_layer(layer)
        if fit:
            self._canvas.fit()
        return changed

    def add_layer_and_activate(self, layer: str) -> None:
        self._canvas.add_layer(layer, activate=True)

    def shape_move_requested(
        self,
        source_layer: str,
        shape_key: object,
        target_layer: str,
    ) -> bool:
        return self.shapes_move_requested(source_layer, [shape_key], target_layer)

    def shapes_move_requested(
        self,
        source_layer: str,
        shape_keys: list,
        target_layer: str,
    ) -> bool:
        if source_layer == target_layer:
            return False
        indices = sorted(set(flatten_shape_keys(shape_keys)))
        if not indices:
            return False
        return self._canvas.move_indices_to_layer(indices, target_layer) > 0

    def move_selected_to_layer(self, target_layer: str) -> bool:
        indices = self._canvas.get_selection_indices()
        if not indices:
            return False
        return self._canvas.move_indices_to_layer(indices, target_layer) > 0

    def layer_renamed(self, old_name: str, new_name: str) -> None:
        self._canvas.rename_layer(old_name, new_name)
        if self.default_layer == old_name:
            self.default_layer = new_name

    def layer_deleted(self, layer: str) -> None:
        self._canvas.delete_layer(layer)

    def layer_moved(self, layer: str, new_index: int) -> None:
        self._canvas.move_layer(layer, new_index)

    def on_canvas_edit(self) -> None:
        """Kept for API compatibility — entity state is already the truth."""

    # ── Visibility (entity-native) ────────────────────────────────────────

    def _commit_visibility(self, apply) -> None:
        canvas = self._canvas

        def mutate(document) -> None:
            apply(document.entities)
            document.drop_inactive_selection()

        result = canvas._canvas_service.update_document(mutate)
        if not result.changed:
            return
        canvas._reset_edit_interaction_state()
        canvas._redraw()
        canvas._notify()
        canvas._fire_poly_change()

    def set_shapes_hidden(self, keys: object, hidden: bool) -> None:
        canvas = self._canvas
        entity_ids = {
            canvas._entities[idx].id
            for idx in flatten_shape_keys(keys)
            if 0 <= idx < len(canvas._entities)
        }

        def apply(entities) -> None:
            for entity in entities:
                if entity.id in entity_ids:
                    entity.hidden = hidden

        self._commit_visibility(apply)

    def set_layer_hidden(self, layer: str, hidden: bool) -> None:
        def apply(entities) -> None:
            for entity in entities:
                if entity.layer == layer:
                    entity.hidden = hidden

        self._commit_visibility(apply)

    def solo_layer(self, target_layer: str) -> None:
        def apply(entities) -> None:
            for entity in entities:
                entity.hidden = entity.layer != target_layer

        self._commit_visibility(apply)

    def set_all_hidden(self, hidden: bool) -> None:
        def apply(entities) -> None:
            for entity in entities:
                entity.hidden = hidden

        self._commit_visibility(apply)

    # ── Shape labels ──────────────────────────────────────────────────────

    def rename_shape(self, layer_name: str, shape_key: object, new_label: str) -> None:
        """Persist a custom display label for a shape (or name a group)."""
        if isinstance(shape_key, (tuple, list)) and shape_key:
            first = shape_key[0]
            gid = self._canvas._group_of(first) if isinstance(first, int) else None
            if gid is not None:
                self._canvas.set_group_label(gid, new_label)
            return
        if not isinstance(shape_key, int):
            return
        ents = self._canvas._entities
        if not (0 <= shape_key < len(ents)):
            return
        e = ents[shape_key]
        label = str(new_label).strip()
        if label:
            e.meta = {**(e.meta or {}), "label": label}
        elif e.meta:
            e.meta.pop("label", None)

    # ── Layer tree rows ───────────────────────────────────────────────────

    def _shape_label(self, ordinal: int, e: Any) -> str:
        """Row label. ``ordinal`` is the shape's position within its layer
        (stable, human-friendly numbering — not the entity index)."""
        geo = describe_polyline(ordinal, e.points)
        custom = (e.meta or {}).get("label")
        if not custom:
            return geo
        parts = geo.split("  ", 1)
        geo_suffix = parts[1] if len(parts) > 1 else geo
        return f"{custom}  ·  {geo_suffix}"

    def build_layer_shape_rows(self, layer_name: str) -> list[dict[str, Any]]:
        """One row per shape (grouped shapes collapse into one row per
        group). Row keys are canvas entity indices."""
        canvas = self._canvas
        pairs = [(i, e) for i, e in enumerate(canvas._entities) if e.layer == layer_name]
        group_labels = dict(getattr(canvas, "_group_labels", {}))
        members_by_gid: dict[int, list[int]] = {}
        for i, e in pairs:
            if e.group is not None:
                members_by_gid.setdefault(e.group, []).append(i)

        rows: list[dict[str, Any]] = []
        emitted: set[int] = set()
        ordinal = 0
        for i, e in pairs:
            gid = e.group
            if gid is None or len(members_by_gid.get(gid, [])) < 2:
                rows.append(
                    {
                        "key": i,
                        "label": self._shape_label(ordinal, e),
                        "visible": not e.hidden,
                        "editable": True,
                        "draggable": True,
                    }
                )
                ordinal += 1
                continue
            if gid in emitted:
                continue
            emitted.add(gid)
            members = members_by_gid[gid]
            title = group_labels.get(gid) or "Group"
            rows.append(
                {
                    "key": tuple(members),
                    "label": (f"{ordinal + 1:02d}  {title}  ·  {len(members)} shapes"),
                    "visible": any(not canvas._entities[m].hidden for m in members),
                    "editable": True,
                    "draggable": True,
                }
            )
            ordinal += 1
        return rows

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]] | None = None,
    ) -> list[dict[str, Any]]:
        canvas = self._canvas
        active = self.current_layer_name()
        rows: list[dict[str, Any]] = []
        for name in canvas.layer_names():
            shapes = self.build_layer_shape_rows(name)
            visible = not shapes or any(row["visible"] for row in shapes)
            rows.append(
                build_layer_row(
                    name=name,
                    display_name=name,
                    active=name == active,
                    visible=visible,
                    editable=True,
                    shapes=shapes,
                    color=canvas.layer_color(name),
                )
            )
        if not rows:
            rows = [
                build_layer_row(
                    name=self.default_layer,
                    display_name=self.default_layer,
                    active=True,
                    visible=True,
                    editable=True,
                    shapes=[],
                )
            ]
        return rows

    # ── Loading ───────────────────────────────────────────────────────────

    def load_polys(self, polys: list[list[tuple[float, float]]], *, fit: bool) -> None:
        self._canvas.set_layer_model([self.default_layer], self.default_layer)
        self._canvas.set_polylines_state(polys, fit=fit)
        self._canvas.set_mode("select")

    def add_polys(self, polys: list[list[tuple[float, float]]], *, fit: bool) -> None:
        """Append polys as new entities without touching whatever's already
        on the canvas (used for cross-tab "send selection here" actions,
        as opposed to load_polys which replaces the whole canvas)."""
        self._canvas.add_polylines_state(polys, fit=fit)
        self._canvas.set_mode("select")

    def load_polys_by_layer(
        self,
        by_layer: dict[str, list[list[tuple[float, float]]]],
        *,
        fit: bool,
    ) -> None:
        """Load polylines into named layers; first layer becomes active."""
        if not by_layer:
            self.load_polys([], fit=fit)
            return
        names = list(by_layer)
        self._canvas.set_layer_model(names, names[0])
        flat: list[list[tuple[float, float]]] = []
        layers: list[str] = []
        for name, polys in by_layer.items():
            for p in polys:
                flat.append(p)
                layers.append(name)
        self._canvas.set_polylines_state(flat, fit=fit)
        for e, layer in zip(self._canvas._entities, layers):
            e.layer = layer
        self._canvas.set_mode("select")
        self._canvas._redraw()

    def reset_empty(self) -> None:
        self._canvas.set_layer_model([self.default_layer], self.default_layer)
        self._canvas.set_polylines_state([])
        self._canvas.set_mode("select")


# ══════════════════════════════════════════════════════════════════════════
# Per-page canvas runtimes — toolbar/status sync for Trace and Pattern
# ══════════════════════════════════════════════════════════════════════════


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
            gid = self._canvas._group_of(first) if isinstance(first, int) else None
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
        label_fn = self._shape_label_builder(layer_name) if is_outline else describe_polyline
        groups = self._canvas._group_map() if is_outline else None
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
                    LOGGER.exception("Preview-category callback failed")
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
                        pattern_polys if categories else self._canvas.get_polylines_state(),
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
                    poly for idx, poly in enumerate(orig_polys) if idx not in outline_hidden
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
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        self._precision_bar.refresh()
        self._layer_sidebar.refresh_tree()


# ══════════════════════════════════════════════════════════════════════════
# Separately-placeable canvas modules with auto-wiring defaults
# ══════════════════════════════════════════════════════════════════════════


class CanvasToolbarModule(QWidget):
    """Toolbar module that can auto-control a bound canvas."""

    def __init__(
        self,
        *,
        canvas: Any | None = None,
        on_mode: Callable[[str], None] | None = None,
        on_fit: Callable[[], None] | None = None,
        modes: tuple[str, ...] = ("Select", "Draw", "Edit"),
        show_fit: bool = True,
        extra_widgets: Sequence[QWidget] | None = None,
    ) -> None:
        super().__init__()
        self._canvas = canvas
        self._on_mode = on_mode
        self._on_fit = on_fit

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar, mode_buttons, selection_label, guidance_label = canvas_toolbar(
            self._handle_mode,
            self._handle_fit,
            modes=modes,
            show_fit=show_fit,
        )
        toolbar_layout = toolbar.layout()
        self.state_buttons: dict[str, QPushButton] = {}
        if isinstance(toolbar_layout, QHBoxLayout) and canvas is not None:
            for text, shortcut, method_name, state_name in (
                ("Scale", "M", "toggle_measure", "_measure_mode"),
                ("Dimension", "Shift+M", "toggle_dimension_mode", "_dimension_mode"),
            ):
                if not hasattr(canvas, method_name):
                    continue
                button = QPushButton(text)
                button.setCheckable(True)
                button.setMinimumHeight(28)
                button.setToolTip(f"Toggle {text.lower()} tool ({shortcut})")
                button.setAccessibleName(f"{text} tool")
                self.state_buttons[state_name] = button

                def _toggle(_checked=False, *, b=button, method=method_name, state=state_name):
                    getattr(canvas, method)()
                    b.setChecked(bool(getattr(canvas, state, False)))

                button.clicked.connect(_toggle)
                toolbar_layout.insertWidget(toolbar_layout.count() - 1, button)
        if isinstance(toolbar_layout, QHBoxLayout) and extra_widgets:
            for widget in extra_widgets:
                toolbar_layout.insertWidget(toolbar_layout.count() - 1, widget)

        root.addWidget(toolbar)

        self.toolbar = toolbar
        self.mode_buttons = mode_buttons
        self.selection_label = selection_label
        self.guidance_label = guidance_label
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
        self.selection_label.setText(f"{count} selected" if count > 0 else "")
        self.selection_label.setProperty("active", count > 0)
        self.selection_label.style().unpolish(self.selection_label)
        self.selection_label.style().polish(self.selection_label)

    def sync_from_canvas(self) -> None:
        if self._canvas is None:
            return
        if hasattr(self._canvas, "get_mode"):
            self.set_active_mode(str(self._canvas.get_mode()))
        self.set_selection_count(int(getattr(self._canvas, "sel_count", 0)))
        for state_name, button in self.state_buttons.items():
            button.setChecked(bool(getattr(self._canvas, state_name, False)))
        if hasattr(self._canvas, "get_command_guidance"):
            guidance, _tone = self._canvas.get_command_guidance()
            active_tool = "Select"
            if bool(getattr(self._canvas, "_dimension_mode", False)):
                active_tool = "Dimension"
            elif bool(getattr(self._canvas, "_measure_mode", False)):
                active_tool = "Scale"
            elif hasattr(self._canvas, "get_mode"):
                active_tool = str(self._canvas.get_mode()).replace("_", " ").title()
            self.guidance_label.setText(f"{active_tool} · {guidance}")
            self.guidance_label.setAccessibleName(f"Active tool: {active_tool}")
            self.guidance_label.setAccessibleDescription(str(guidance))


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
        visibility_adapter: Any | None = None,
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
            visibility_adapter=visibility_adapter,
        )

        self.tree = tree
        self.controller = controller

        # Canvas -> tree sync: highlight the tree rows for whatever is
        # currently selected on the canvas (click a shape, group, etc.).
        if hasattr(canvas, "selectionChanged"):
            canvas.selectionChanged.connect(self._sync_tree_selection)

    @property
    def state(self) -> LayerTreeState:
        return self.controller.state

    def refresh_tree(self) -> None:
        self.controller.refresh_tree()
        self._sync_tree_selection()

    def apply_current_visibility(self) -> None:
        self.controller.apply_current_visibility()

    def _sync_tree_selection(self, _count: int = 0) -> None:
        if hasattr(self._canvas, "get_selection_indices"):
            self.tree.select_shape_keys(self._canvas.get_selection_indices())

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
