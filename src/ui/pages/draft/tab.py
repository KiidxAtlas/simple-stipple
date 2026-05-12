"""Draft page — interaction-first 2D drafting.

Design goals:
- Maximize canvas space; minimize persistent chrome
- Primary creation path is direct drag on canvas (no dialog/dropdown)
- Context menu and hotkeys provide secondary fast paths
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sized

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.core.base_page import BasePage

from src.backend.dxf.io import (
    load_dxf_polylines_by_layer_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.canvas.modules import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
)
from src.ui.canvas.runtime import CanvasRuntime
from src.ui.core.factories import content_splitter, surface_frame
from src.ui.widgets.recent_files_button import RecentFilesButton
from src.ui.widgets.status_strip import CanvasStatusStrip
from src.ui.pages.draft.session import (
    apply_draft_workspace_state,
    clear_draft_workspace_state,
    get_draft_workspace_state,
)
from src.ui.util.dialog_paths import pick_open_file, pick_save_file
from src.ui.util.recent_files import KIND_DXF, record_recent


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setProperty("role", "toolbar-sep")
    return sep


class DraftPage(BasePage):
    """Canvas-first drafting page optimized for interaction speed."""

    DEFAULT_LAYER = "Layer 1"

    sendSelectedToPatternRequested = Signal(object)
    useSelectedAsFillPatternRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._last_out_path: str | None = None
        self._last_in_path: str | None = None
        self._imported_dxf_layers: list[tuple[str, int, bool, bool]] = []
        self._runtime: CanvasRuntime | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        canvas_host = self._build_canvas()

        _toolbar_panel = surface_frame("panel")
        _tp_layout = QVBoxLayout(_toolbar_panel)
        _tp_layout.setContentsMargins(8, 4, 8, 4)
        _tp_layout.setSpacing(2)
        _tp_layout.addWidget(self._build_toolbar())
        _tp_layout.addWidget(self._build_grid())
        root.addWidget(_toolbar_panel)
        root.addWidget(canvas_host, stretch=1)

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        root.addWidget(self._canvas_status)

        self.setAcceptDrops(True)

        self._refresh_status()

    def _rt(self) -> CanvasRuntime:
        assert self._runtime is not None
        return self._runtime

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        open_btn = QPushButton("Open DXF")
        open_btn.setMinimumHeight(28)
        open_btn.setToolTip("Open a DXF file into the draft canvas for editing")
        open_btn.clicked.connect(self._browse_dxf)

        self._recent_btn = RecentFilesButton(
            self._settings,
            KIND_DXF,
            empty_message="No recent DXF files.",
        )
        self._recent_btn.setMinimumHeight(28)
        self._recent_btn.setToolTip("Pick from recently opened DXF files")
        self._recent_btn.fileSelected.connect(self._load_dxf)

        # Shape mode — interactive dropdown to switch quick-draw shape type
        self._shape_mode_btn = QToolButton()
        self._shape_mode_btn.setText("▸ Rectangle")
        self._shape_mode_btn.setToolTip("Change the quick-draw shape mode")
        self._shape_mode_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        _shape_menu = QMenu(self._shape_mode_btn)
        for _shape in ("Rectangle", "Circle", "Slot", "Hexagon"):
            _shape_menu.addAction(
                _shape,
                lambda s=_shape.lower(): self._canvas.set_quick_shape_mode(s),
            )
        self._shape_mode_btn.setMenu(_shape_menu)

        # Close Poly — most common shape-editing action, surfaced directly
        self._close_poly_btn = QPushButton("Close Poly")
        self._close_poly_btn.setMinimumHeight(28)
        self._close_poly_btn.setToolTip("Close selected open polylines [Shift+C]")
        self._close_poly_btn.clicked.connect(self._close_selected_polylines)

        # Select All — second most common action
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setMinimumHeight(28)
        self._select_all_btn.setToolTip(
            "Select all shapes on the active layer [Ctrl+A]"
        )
        self._select_all_btn.clicked.connect(self._select_all)

        # Overflow (⋯) — remaining actions
        overflow_btn = QToolButton()
        overflow_btn.setText("⋯")
        overflow_btn.setFixedWidth(32)
        overflow_btn.setToolTip("More actions")
        overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        overflow_menu = QMenu(overflow_btn)
        overflow_menu.addAction(
            "Open selected [Shift+O]", self._open_selected_polylines
        )
        overflow_menu.addSeparator()
        overflow_menu.addAction("Delete selected [Delete]", self._delete_selected)
        overflow_menu.addAction("Duplicate [Ctrl+D]", self._duplicate_selected)
        overflow_menu.addAction("Deselect all [Ctrl+Shift+A]", self._deselect_all)
        overflow_menu.addSeparator()
        overflow_menu.addAction("Toggle measure [M]", self._toggle_measure_mode)
        overflow_menu.addAction("Fit view [F]", self._fit_view)
        overflow_menu.addAction("Fit selection", self._fit_selection)
        overflow_btn.setMenu(overflow_menu)

        export_btn = QPushButton("Export DXF")
        export_btn.setMinimumHeight(28)
        export_btn.setMinimumWidth(90)
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(self._export)

        _spacer = QWidget()
        _spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._fit_view,
            extra_widgets=[
                _toolbar_sep(),
                self._shape_mode_btn,
                self._close_poly_btn,
                self._select_all_btn,
                overflow_btn,
                _spacer,
                _toolbar_sep(),
                open_btn,
                self._recent_btn,
                export_btn,
            ],
        )
        return self._toolbar_module

    def _build_grid(self) -> QWidget:
        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_status,
        )
        self._precision_bar = self._grid_module
        return self._grid_module

    # ── Canvas ────────────────────────────────────────────────────────────

    def _build_canvas(self) -> QWidget:
        w = surface_frame("canvas")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_edit,
            on_action=self._on_canvas_action,
            on_send_selected_to_pattern=self._on_send_selected_to_pattern,
            on_use_selected_as_fill_pattern=self._on_use_selected_as_fill_pattern,
            on_ghost_click=self._on_ghost_poly_click,
            draft_profile=True,
        )
        self._canvas.quickShapeChanged.connect(self._on_quick_shape_changed)
        self._canvas.quickShapeEnabledChanged.connect(
            self._on_quick_shape_enabled_changed
        )
        self._runtime = CanvasRuntime(
            canvas=self._canvas,
            default_layer=self.DEFAULT_LAYER,
        )
        self._runtime.graph_adapter.load_to_canvas(self._canvas, fit=False)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._layer_module = CanvasLayerTreeModule(
            canvas=self._canvas,
            title="Layers",
            editable=True,
            get_active_layer_name=self._current_layer_name,
            build_layer_rows=self._build_layer_tree_rows,
            on_selection_requested=self._on_tree_selection_requested,
            on_fit_requested=self._fit_selection,
            on_visibility_changed=self._refresh_status,
        )
        self._layers_tree = self._layer_module.tree
        self._layer_sidebar = self._layer_module.controller

        self._layers_tree.layerActivated.connect(self._on_layer_activated)
        self._layers_tree.layerAdded.connect(self._on_layer_added)
        self._layers_tree.layerRenamed.connect(self._on_layer_renamed)
        self._layers_tree.layerDeleted.connect(self._on_layer_deleted)
        self._layers_tree.layerMoved.connect(self._on_layer_moved)
        self._layers_tree.shapeMoveRequested.connect(self._on_shape_move_requested)
        self._layers_tree.shapesMoveRequested.connect(self._on_shapes_move_requested)
        self._layers_tree.moveSelectedRequested.connect(self._on_move_selected_to_layer)
        self._layers_tree.shapeRenamed.connect(self._on_shape_renamed)
        side_layout.addWidget(self._layer_module, stretch=1)

        splitter = content_splitter(self._canvas, side_panel, sizes=(860, 280))
        layout.addWidget(splitter, stretch=1)
        return w

    # ── Mode / callbacks ──────────────────────────────────────────────────

    def _on_toolbar_mode(self, mode: str) -> None:
        self._toolbar_module.set_active_mode(mode)
        self._canvas.set_mode(mode.lower())
        self._refresh_status()

    def _on_canvas_mode_change(self, mode: str) -> None:
        if hasattr(self, "_toolbar_module"):
            self._toolbar_module.set_active_mode(mode)
        self._refresh_status()

    def _on_quick_shape_changed(self, mode: str) -> None:
        self._shape_mode_btn.setText(f"▸ {mode.title()}")
        self._refresh_status()

    def _on_quick_shape_enabled_changed(self, enabled: bool) -> None:
        self._shape_mode_btn.setEnabled(bool(enabled))
        self._refresh_status()

    def _on_sel_change(self, count: int) -> None:
        if hasattr(self, "_toolbar_module"):
            self._toolbar_module.set_selection_count(count)
        # Update only the selection count in the status strip — do NOT call
        # _refresh_status() here because that rebuilds the layer tree and
        # immediately erases the visual selection the user just made.
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _current_layer_name(self) -> str:
        return self._rt().current_layer_name()

    def _on_tree_selection_requested(self, indices: list[int]) -> None:
        self._rt().on_tree_selection_requested(indices)
        # Do NOT call _refresh_status() — that rebuilds the tree and clears
        # the selection highlight the user just created.

    def _layer_view_bucket(self, layer: str | None = None) -> dict[str, set[int]]:
        return self._rt().layer_view_bucket(layer)

    def _set_current_layer_view(self, layer: str | None = None) -> None:
        self._rt().set_current_layer_view(layer)

    def _rename_layer_view_state(self, old: str, new: str) -> None:
        self._rt().rename_layer_view_state(old, new)

    def _delete_layer_view_state(self, name: str) -> None:
        self._rt().delete_layer_view_state(name)

    def _normalize_graph_for_ui(self) -> None:
        self._rt().normalize_graph_for_ui()

    def _reload_active_layer(self, *, fit: bool = False) -> None:
        self._rt().reload_active_layer(fit=fit)

    def _switch_active_layer(self, layer: str, *, fit: bool = False) -> None:
        if self._rt().switch_active_layer(layer, fit=fit):
            self._refresh_status()
            self._emit_state_changed()

    def _add_layer_and_activate(self, layer: str) -> None:
        self._rt().add_layer_and_activate(layer)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_activated(self, layer: str) -> None:
        self._switch_active_layer(layer, fit=False)

    def _on_shape_move_requested(
        self, source_layer: str, shape_key: object, target_layer: str
    ) -> None:
        if self._rt().shape_move_requested(source_layer, shape_key, target_layer):
            self._refresh_status()
            self._emit_state_changed()

    def _on_shape_renamed(
        self, layer_name: str, shape_key: object, new_label: str
    ) -> None:
        self._rt().rename_shape(layer_name, shape_key, new_label)
        self._refresh_status()

    def _on_shapes_move_requested(
        self, source_layer: str, shape_keys: list, target_layer: str
    ) -> None:
        if self._rt().shapes_move_requested(source_layer, shape_keys, target_layer):
            count = len([k for k in shape_keys if isinstance(k, int)])
            try:
                self._canvas._show_flash(
                    f"Moved {count} shape{'s' if count != 1 else ''} to {target_layer}",
                    1200,
                )
            except Exception:
                pass
            self._refresh_status()
            self._emit_state_changed()

    def _on_layer_added(self, layer: str) -> None:
        self._add_layer_and_activate(layer)

    def _on_layer_renamed(self, old_name: str, new_name: str) -> None:
        self._rt().layer_renamed(old_name, new_name)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_deleted(self, layer: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Layer",
            f"Delete layer '{layer}' and all of its contents?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._rt().layer_deleted(layer)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_moved(self, layer: str, new_index: int) -> None:
        self._rt().layer_moved(layer, new_index)
        self._refresh_status()
        self._emit_state_changed()

    def _on_move_selected_to_layer(self, target_layer: str) -> None:
        if not self._rt().move_selected_to_layer(target_layer):
            self._canvas._show_flash("Select shape(s) first", 1000)
            return
        try:
            self._canvas._show_flash(f"Moved selection to {target_layer}", 1200)
        except Exception:
            pass
        self._refresh_status()
        self._emit_state_changed()

    def _sync_browser_interaction_state(self) -> None:
        self._rt().sync_browser_interaction_state()

    def _selection_relationship_summary(self) -> str:
        sel = self._canvas.get_selection_indices()
        if not sel:
            return ""
        if len(sel) == 1:
            idx = sel[0]
            connected_fn = getattr(self._canvas, "_connected_poly_indices", None)
            if callable(connected_fn):
                connected = connected_fn(idx)
                if isinstance(connected, (set, list, tuple)):
                    return f"Connected group size: {len(connected)}"
            return ""
        return f"Multi-selection across {len(sel)} objects"

    def _fit_selection(self) -> None:
        if self._canvas.fit_selection():
            self._refresh_status()

    def _on_canvas_edit(self) -> None:
        self._rt().on_canvas_edit()
        self._refresh_status()
        self._emit_state_changed()

    def _close_selected_polylines(self) -> None:
        changed = self._canvas.close_selected_polylines()
        if changed:
            self._rt().graph_adapter.capture_from_canvas(self._canvas)
            self._canvas._show_flash(f"Closed {changed} polyline(s)", 900)
            self._refresh_status()
            self._emit_state_changed()
        else:
            self._canvas._show_flash("No open polyline selected", 900)

    def _open_selected_polylines(self) -> None:
        changed = self._canvas.open_selected_polylines()
        if changed:
            self._rt().graph_adapter.capture_from_canvas(self._canvas)
            self._canvas._show_flash(f"Opened {changed} polyline(s)", 900)
            self._refresh_status()
            self._emit_state_changed()
        else:
            self._canvas._show_flash("No closed polyline selected", 900)

    def _fit_view(self) -> None:
        self._canvas.fit()
        self._refresh_status()

    def _toggle_measure_mode(self) -> None:
        self._canvas.toggle_measure()
        self._refresh_status()

    def _delete_selected(self) -> None:
        deleted = self._canvas.delete_selected()
        if deleted:
            self._rt().graph_adapter.capture_from_canvas(self._canvas)
            self._refresh_status()
            self._emit_state_changed()

    def _duplicate_selected(self) -> None:
        if self._canvas.duplicate_selected():
            self._rt().graph_adapter.capture_from_canvas(self._canvas)
            self._refresh_status()
            self._emit_state_changed()

    def _select_all(self) -> None:
        self._canvas.select_all()
        self._refresh_status()

    def _deselect_all(self) -> None:
        self._canvas.deselect_all()
        self._refresh_status()

    def _on_canvas_action(self, action_type: str, payload: dict | None = None) -> None:
        active = self._rt().current_layer_name()
        self._rt().doc_graph.record_action(
            f"canvas:{action_type}",
            payload or {},
            touched=[("layer", active)],
            invalidated_layers=sorted(
                self._rt().doc_graph.reachable_dependents({active})
            ),
            user_initiated=True,
        )

    def _on_ghost_poly_click(self, ghost_idx: int) -> None:
        """Clicking a shape from another layer activates that layer and selects the shape."""
        result = self._rt().layer_for_ghost_index(ghost_idx)
        if result is None:
            return
        layer_name, local_idx = result
        # Switch to the target layer (loads its polys into the canvas).
        self._switch_active_layer(layer_name, fit=False)
        # Now select the shape by its local index within the newly active layer.
        self._canvas.set_selection([local_idx])
        self._refresh_status()

    def _on_send_selected_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self.sendSelectedToPatternRequested.emit(polys)

    def _on_use_selected_as_fill_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self.useSelectedAsFillPatternRequested.emit(polys)

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self) -> None:
        records = self._canvas.get_export_dxf_state()
        if not records:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "The canvas is empty — draw or drag-create shapes first.",
            )
            return

        out_path = pick_save_file(
            self,
            self._settings,
            "draft_output",
            "Export DXF",
            "draft.dxf",
            "DXF files (*.dxf);;All files (*)",
            fallback_dir=self._settings.get("draft_output_dir", ""),
        )
        if not out_path:
            return

        try:
            # Capture in-canvas edits back into the active layer of the
            # document graph so the export sees the latest state of every
            # layer rather than just whatever happens to live in self._polys.
            rt = self._rt()
            try:
                rt.graph_adapter.capture_from_canvas(self._canvas)
            except (AttributeError, ValueError, TypeError):
                pass

            active_name = rt.current_layer_name()
            active_polys = [list(r["polyline"]) for r in records]
            active_kinds = [str(r.get("kind", "polyline")) for r in records]
            active_metas = [r.get("meta") for r in records]

            # Build {layer_name: polys} preserving layer_order. Skip the
            # "geometry" sentinel (it's the legacy single-layer fallback).
            layered: dict[str, list[list[tuple[float, float]]]] = {}
            for name, layer in rt.doc_graph.iter_layers():
                if name == "geometry":
                    continue
                if name == active_name:
                    polys = active_polys
                else:
                    polys = [list(p) for p in layer.polylines]
                if polys:
                    layered[name] = polys

            if not layered:
                # No graph layers populated — fall back to flat export so
                # we never lose the user's work. Emit onto a named layer
                # (instead of the AutoCAD default "0") so downstream CAM
                # tools assign it a real color and can fill it; layer "0"
                # is locked to color 7 which many laser apps treat as
                # "BYBLOCK / no color set" and refuse to fill.
                fallback_layer = active_name or "Layer"
                write_polylines_dxf(
                    active_polys,
                    out_path,
                    close=True,
                    pattern_layer=fallback_layer,
                    entity_kinds=active_kinds,
                    entity_meta=active_metas,
                )
            else:
                # First layer in iter order becomes the "main" entity stream
                # so it can carry kind/meta info (lines, circles, ellipses,
                # arcs). Remaining layers are emitted as additional DXF
                # layers via extra_layers.
                first_name = next(iter(layered))
                first_polys = layered.pop(first_name)
                if first_name == active_name:
                    main_kinds: list[str] | None = active_kinds
                    main_metas: list[dict[str, Any] | None] | None = active_metas
                else:
                    main_kinds = None
                    main_metas = None
                write_polylines_dxf(
                    first_polys,
                    out_path,
                    close=True,
                    pattern_layer=first_name,
                    entity_kinds=main_kinds,
                    entity_meta=main_metas,
                    extra_layers=layered or None,
                )
            self._last_out_path = out_path
            self._canvas._show_flash(f"Exported: {Path(out_path).name}", 1200)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    # ── Status ────────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return

        summary = self._canvas.get_status_summary()
        topo = self._canvas.get_topology_summary()
        n = self._canvas.poly_count
        mode = str(summary["mode"])

        if n:
            quick_mode = (
                f"Quick shape: {self._canvas.quick_shape_mode.title()}"
                if self._canvas.quick_shape_enabled
                else "Quick shape: Off"
            )
            readiness = f"{quick_mode} · {topo['closed']} closed/{topo['open']} open"
            tone = "accent"
        else:
            readiness = (
                "Drag on canvas to create shape"
                if self._canvas.quick_shape_enabled
                else "Quick shape disabled"
            )
            tone = "warn"

        zoom = self._canvas.get_zoom_percent()
        cursor = self._canvas.get_cursor_world_pos()

        self._canvas_status.set_snapshot(
            mode=mode,
            selected_count=self._canvas.sel_count,
            object_count=n,
            precision_text=str(summary["precision"]),
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness,
            readiness_tone=tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )

        if hasattr(self, "_precision_bar"):
            self._precision_bar.refresh()

        if hasattr(self, "_layers_tree"):
            self._layer_sidebar.refresh_tree()
            self._rt()._update_ghost_layers()

    def _build_layer_shape_rows(
        self,
        layer_name: str,
        polylines: list[list[tuple[float, float]]],
    ) -> list[dict[str, Any]]:
        return self._rt().build_layer_shape_rows(layer_name, polylines)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        return self._rt().build_layer_tree_rows(layer_view_state)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".dxf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".dxf"):
                self._load_dxf(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _browse_dxf(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "draft_input_dxf",
            "Open DXF for Draft Editing",
            "DXF files (*.dxf *.Dxf *.DXF);;All files (*)",
            fallback_dir=self._settings.get("draft_input_dxf_dir", ""),
        )
        if path:
            self._load_dxf(path)

    def _load_dxf(self, path: str) -> None:
        try:
            by_layer, report = load_dxf_polylines_by_layer_with_report(path)
            self._last_in_path = path
            self._imported_dxf_layers = [
                (name, count, False, False)
                for name, count in report.layer_counts.items()
            ]
            # Flatten for fit-bounds / fallback consumers.
            flat: list[list[tuple[float, float]]] = []
            for polys in by_layer.values():
                flat.extend(polys)
            rt = self._rt()
            if len(by_layer) > 1:
                rt.load_polys_by_layer(by_layer, fit=bool(flat))
            else:
                rt.load_polys(flat, fit=bool(flat))
            self._canvas._show_flash(f"Loaded DXF: {Path(path).name}", 1200)
            record_recent(self._settings, KIND_DXF, path)
            if report.has_issues:
                detail = summarize_dxf_import_report(report)
                if detail:
                    QMessageBox.warning(
                        self,
                        "DXF Import Notice",
                        f"{Path(path).name} loaded, but some DXF content could not be preserved.\n\n{detail}",
                    )
            self._refresh_status()
            self._emit_state_changed()
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Open DXF Failed", str(exc))

    def load_outline_polys(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Pattern selection",
    ) -> None:
        """Load polylines from another tab directly into Draft."""
        if not polys:
            return
        incoming = [[(x, y) for x, y in poly] for poly in polys]
        self._rt().load_polys(incoming, fit=True)
        self._canvas._show_flash(f"Loaded {len(incoming)} from {source_label}", 1200)
        self._refresh_status()
        self._emit_state_changed()

    # ── Workspace persistence ─────────────────────────────────────────────

    def get_workspace_state(self) -> dict:
        return get_draft_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_draft_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_draft_workspace_state(self)

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, state: dict | None) -> None:
        pass
