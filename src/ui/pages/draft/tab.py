"""Draft page — interaction-first 2D drafting.

Design goals:
- Maximize canvas space; minimize persistent chrome
- Primary creation path is direct drag on canvas (no dialog/dropdown)
- Context menu and hotkeys provide secondary fast paths
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components.canvas.modules import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
)
from src.ui.components.canvas.runtime import CanvasRuntime
from src.ui.components.canvas.widgets import (
    CanvasStatusStrip,
)
from src.ui.components.common.factories import _content_splitter, _surface_frame
from src.ui.pages.draft.session import (
    apply_draft_workspace_state,
    clear_draft_workspace_state,
    get_draft_workspace_state,
)


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    return sep


class DraftPage(QWidget):
    """Canvas-first drafting page optimized for interaction speed."""

    DEFAULT_LAYER = "Layer 1"

    stateChanged = Signal()
    sendSelectedToPatternRequested = Signal(object)
    useSelectedAsFillPatternRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._suspend_state: bool = False
        self._last_out_path: str | None = None
        self._last_in_path: str | None = None
        self._imported_dxf_layers: list[tuple[str, int, bool, bool]] = []
        self._runtime: CanvasRuntime | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        canvas_host = self._build_canvas()
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_grid())
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

        self._shape_mode_label = QLabel("Shape: Rectangle")
        self._shape_mode_label.setStyleSheet("color: #8b949e; font-size: 11px;")

        self._actions_btn = QPushButton("Actions ▾")
        self._actions_btn.setMinimumHeight(28)
        self._actions_btn.setToolTip("Context actions for current selection")
        self._actions_btn.clicked.connect(self._show_context_actions_menu)

        export_btn = QPushButton("Export DXF")
        export_btn.setFixedHeight(28)
        export_btn.setMinimumWidth(90)
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(self._export)

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._fit_view,
            extra_widgets=[
                open_btn,
                _toolbar_sep(),
                self._shape_mode_label,
                self._actions_btn,
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
        w = _surface_frame("canvas")
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
        self._layers_tree.moveSelectedRequested.connect(self._on_move_selected_to_layer)
        side_layout.addWidget(self._layer_module, stretch=1)

        splitter = _content_splitter(self._canvas, side_panel, sizes=(860, 280))
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
        self._shape_mode_label.setText(f"Shape: {mode.title()}")
        self._refresh_status()

    def _on_quick_shape_enabled_changed(self, enabled: bool) -> None:
        _ = enabled
        self._refresh_status()

    def _on_sel_change(self, count: int) -> None:
        if hasattr(self, "_toolbar_module"):
            self._toolbar_module.set_selection_count(count)
        self._refresh_status()

    def _current_layer_name(self) -> str:
        return self._rt().current_layer_name()

    def _on_tree_selection_requested(self, indices: list[int]) -> None:
        self._rt().on_tree_selection_requested(indices)
        self._refresh_status()

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

    def _show_context_actions_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Close selected [Shift+C]", self._close_selected_polylines)
        menu.addAction("Open selected [Shift+O]", self._open_selected_polylines)
        menu.addSeparator()
        menu.addAction("Delete selected [Delete]", self._delete_selected)
        menu.addAction("Duplicate selected [Ctrl+D]", self._duplicate_selected)
        menu.addAction("Select all [Ctrl+A]", self._select_all)
        menu.addAction("Deselect all [Ctrl+Shift+A]", self._deselect_all)
        menu.addSeparator()
        menu.addAction("Toggle measure [M]", self._toggle_measure_mode)
        menu.addAction("Fit view [F]", self._fit_view)
        menu.addAction("Fit selection", self._fit_selection)
        menu.popup(self._actions_btn.mapToGlobal(QPoint(0, self._actions_btn.height())))

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

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export DXF",
            str(Path(self._settings.get("draft_output_dir", "")) / "draft.dxf"),
            "DXF files (*.dxf);;All files (*)",
        )
        if not out_path:
            return

        try:
            write_polylines_dxf(
                [list(r["polyline"]) for r in records],
                out_path,
                close=True,
                entity_kinds=[str(r.get("kind", "polyline")) for r in records],
                entity_meta=[r.get("meta") for r in records],
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

    def _emit_state_changed(self) -> None:
        if not self._suspend_state:
            self.stateChanged.emit()

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
        idir = self._settings.get("draft_input_dxf_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open DXF for Draft Editing",
            idir,
            "DXF files (*.dxf *.Dxf *.DXF);;All files (*)",
        )
        if path:
            self._load_dxf(path)

    def _load_dxf(self, path: str) -> None:
        try:
            polys, report = load_dxf_polylines_with_report(path)
            self._last_in_path = path
            self._imported_dxf_layers = [
                (name, count, False, False)
                for name, count in report.layer_counts.items()
            ]
            self._rt().load_polys(polys, fit=bool(polys))
            self._canvas._show_flash(f"Loaded DXF: {Path(path).name}", 1200)
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

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets
