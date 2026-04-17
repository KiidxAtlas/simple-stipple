"""Draft tab — interaction-first 2D drafting.

Design goals:
- Maximize canvas space; minimize persistent chrome
- Primary creation path is direct drag on canvas (no dialog/dropdown)
- Context menu and hotkeys provide secondary fast paths
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPoint, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.document.actions import set_active_layer
from src.core.document.graph import DocumentGraph
from src.core.document.migration import graph_from_polylines
from src.core.dxf.io import load_dxf_polylines, write_polylines_dxf
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.canvas.graph_adapter import CanvasGraphAdapter
from src.ui.components.containers import (
    CanvasObjectBrowser,
    CanvasPrecisionBar,
    CanvasStatusStrip,
    DxfLayersTree,
)
from src.ui.components.factories import _content_splitter, _surface_frame
from src.ui.panels.properties import PropertiesPanel


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    return sep


class EfficientDraftCanvas(DxfCanvas):
    """Backward-compatible alias; Draft now uses DxfCanvas directly."""


class ShapeTab(QWidget):
    """Canvas-first drafting tab optimized for interaction speed."""

    stateChanged = Signal()
    sendSelectedToPatternRequested = Signal(object)
    useSelectedAsFillPatternRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._suspend_state: bool = False
        self._last_out_path: str | None = None
        self._last_in_path: str | None = None
        self._doc_graph = DocumentGraph()
        set_active_layer(self._doc_graph, "geometry")
        self._graph_adapter = CanvasGraphAdapter(
            self._doc_graph, display_layer="geometry"
        )
        self._hidden_indices: set[int] = set()
        self._locked_indices: set[int] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_canvas(), stretch=1)

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        root.addWidget(self._canvas_status)

        self.setAcceptDrops(True)

        self._refresh_status()

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setProperty("surface", "panel")
        container = QVBoxLayout(bar)
        container.setContentsMargins(6, 6, 6, 6)
        container.setSpacing(4)

        lay = QHBoxLayout()
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(4)

        # Mode group
        self._mode_btns: dict[str, QPushButton] = {}
        for mode in ("Select", "Draw", "Edit"):
            btn = QPushButton(mode)
            btn.setMinimumHeight(28)
            btn.setProperty("active", mode == "Select")
            mode_shortcut = {"Select": "S", "Draw": "D", "Edit": "E"}[mode]
            btn.setToolTip(f"{mode} mode ({mode_shortcut})")
            btn.clicked.connect(lambda checked=False, m=mode: self._on_toolbar_mode(m))
            lay.addWidget(btn)
            self._mode_btns[mode] = btn

        open_btn = QPushButton("Open DXF")
        open_btn.setMinimumHeight(28)
        open_btn.setToolTip("Open a DXF file into the draft canvas for editing")
        open_btn.clicked.connect(self._browse_dxf)
        lay.addWidget(open_btn)

        lay.addWidget(_toolbar_sep())
        self._shape_mode_label = QLabel("Shape: Rectangle")
        self._shape_mode_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        lay.addWidget(self._shape_mode_label)

        self._actions_btn = QPushButton("Actions ▾")
        self._actions_btn.setMinimumHeight(28)
        self._actions_btn.setToolTip("Context actions for current selection")
        self._actions_btn.clicked.connect(self._show_context_actions_menu)
        lay.addWidget(self._actions_btn)

        lay.addStretch()

        export_btn = QPushButton("Export DXF")
        export_btn.setFixedHeight(28)
        export_btn.setMinimumWidth(90)
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(self._export)
        lay.addWidget(export_btn)

        container.addLayout(lay)

        self._precision_bar = CanvasPrecisionBar(None, on_changed=self._refresh_status)
        container.addWidget(self._precision_bar)

        return bar

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
        if hasattr(self, "_precision_bar"):
            self._precision_bar.bind_canvas(self._canvas)
        self._graph_adapter.load_to_canvas(self._canvas, fit=False)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._object_browser = CanvasObjectBrowser("Draft Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection_requested
        )
        self._object_browser.visibilityChanged.connect(
            self._on_browser_visibility_changed
        )
        self._object_browser.lockChanged.connect(self._on_browser_lock_changed)
        self._object_browser.fitRequested.connect(self._fit_selection)
        side_layout.addWidget(self._object_browser, stretch=3)

        self._layers_tree = DxfLayersTree("DXF Layers")
        side_layout.addWidget(self._layers_tree, stretch=2)

        self._properties_panel = PropertiesPanel()
        side_layout.addWidget(self._properties_panel, stretch=2)

        splitter = _content_splitter(self._canvas, side_panel, sizes=(860, 280))
        layout.addWidget(splitter, stretch=1)
        return w

    # ── Mode / callbacks ──────────────────────────────────────────────────

    def _set_active_mode_btn(self, mode: str) -> None:
        v = mode.lower()
        for k, b in self._mode_btns.items():
            b.setProperty("active", k.lower() == v)
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_toolbar_mode(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._canvas.set_mode(mode.lower())
        self._refresh_status()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._refresh_status()

    def _on_quick_shape_changed(self, mode: str) -> None:
        self._shape_mode_label.setText(f"Shape: {mode.title()}")
        self._refresh_status()

    def _on_quick_shape_enabled_changed(self, enabled: bool) -> None:
        _ = enabled
        self._refresh_status()

    def _on_sel_change(self, _count: int) -> None:
        self._refresh_status()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)
        self._refresh_status()

    def _on_browser_visibility_changed(self, idx: int, visible: bool) -> None:
        if visible:
            self._hidden_indices.discard(idx)
        else:
            self._hidden_indices.add(idx)
        self._sync_browser_interaction_state()
        self._refresh_status()

    def _on_browser_lock_changed(self, idx: int, locked: bool) -> None:
        if locked:
            self._locked_indices.add(idx)
        else:
            self._locked_indices.discard(idx)
        self._sync_browser_interaction_state()
        self._refresh_status()

    def _sync_browser_interaction_state(self) -> None:
        max_idx = self._canvas.poly_count
        valid = set(range(max_idx))
        self._hidden_indices &= valid
        self._locked_indices &= valid
        self._canvas.set_hidden_indices(sorted(self._hidden_indices))
        self._canvas.set_locked_indices(sorted(self._locked_indices))

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

    def _apply_live_geometry(
        self,
        width_mm: float | None,
        height_mm: float | None,
        length_mm: float | None,
    ) -> None:
        changed = False
        if width_mm is not None:
            changed = self._canvas._set_selected_width(width_mm) or changed
        if height_mm is not None:
            changed = self._canvas._set_selected_height(height_mm) or changed
        if length_mm is not None:
            changed = self._canvas._set_selected_line_length(length_mm) or changed
        if changed:
            self._graph_adapter.capture_from_canvas(self._canvas)
            self._refresh_status()
            self._emit_state_changed()

    def _fit_selection(self) -> None:
        if self._canvas.fit_selection():
            self._refresh_status()

    def _on_canvas_edit(self) -> None:
        self._graph_adapter.capture_from_canvas(self._canvas)
        self._sync_browser_interaction_state()
        self._refresh_status()
        self._emit_state_changed()

    def _close_selected_polylines(self) -> None:
        changed = self._canvas.close_selected_polylines()
        if changed:
            self._graph_adapter.capture_from_canvas(self._canvas)
            self._canvas._show_flash(f"Closed {changed} polyline(s)", 900)
            self._refresh_status()
            self._emit_state_changed()
        else:
            self._canvas._show_flash("No open polyline selected", 900)

    def _open_selected_polylines(self) -> None:
        changed = self._canvas.open_selected_polylines()
        if changed:
            self._graph_adapter.capture_from_canvas(self._canvas)
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
            self._graph_adapter.capture_from_canvas(self._canvas)
            self._refresh_status()
            self._emit_state_changed()

    def _duplicate_selected(self) -> None:
        if self._canvas.duplicate_selected():
            self._graph_adapter.capture_from_canvas(self._canvas)
            self._refresh_status()
            self._emit_state_changed()

    def _select_all(self) -> None:
        self._canvas.select_all()
        self._refresh_status()

    def _deselect_all(self) -> None:
        self._canvas.deselect_all()
        self._refresh_status()

    def _on_canvas_action(self, action_type: str, payload: dict | None = None) -> None:
        self._doc_graph.record_action(
            f"canvas:{action_type}",
            payload or {},
            touched=[("layer", "geometry")],
            invalidated_layers=sorted(
                self._doc_graph.reachable_dependents({"geometry"})
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
        polys = self._canvas.get_export_polylines_state()
        if not polys:
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
            write_polylines_dxf(polys, out_path, close=True)
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

        if hasattr(self, "_object_browser"):
            self._sync_browser_interaction_state()
            self._object_browser.set_objects(
                self._canvas.get_polylines_state(),
                self._canvas.get_selection_indices(),
                hidden_indices=sorted(self._hidden_indices),
                locked_indices=sorted(self._locked_indices),
            )

        if hasattr(self, "_layers_tree"):
            active = getattr(self._doc_graph, "active_layer", "geometry")
            rows = [
                (
                    name,
                    len(layer.polylines),
                    bool(layer.dirty),
                    name == active,
                )
                for name, layer in sorted(self._doc_graph.layers.items())
            ]
            if not rows:
                rows = [("geometry", self._canvas.poly_count, False, True)]
            self._layers_tree.set_layers(rows)

        if hasattr(self, "_properties_panel"):
            sel_count = self._canvas.sel_count
            mode = self._canvas.get_mode()
            selection_indices = self._canvas.get_selection_indices()
            locked_selected = [
                i for i in selection_indices if i in self._locked_indices
            ]
            if sel_count:
                summary_text = (
                    f"{sel_count} object{'s' if sel_count != 1 else ''} selected"
                )
                next_step = "Drag to move, right-click for transform options"
                details_text = (
                    "Double-click selects connected object. Right-click always opens context actions. "
                    + self._selection_relationship_summary()
                ).strip()
                actions = [
                    (
                        "Fit Selection",
                        "Zoom to selected objects",
                        self._fit_selection,
                        True,
                    ),
                    (
                        "Close Polyline",
                        "Close selected open polylines (Shift+C)",
                        self._close_selected_polylines,
                        True,
                    ),
                    (
                        "Open Polyline",
                        "Open selected closed polylines (Shift+O)",
                        self._open_selected_polylines,
                        True,
                    ),
                    (
                        "Delete Selected",
                        "Delete selected geometry (Delete)",
                        self._delete_selected,
                        bool(
                            selection_indices
                            and len(locked_selected) < len(selection_indices)
                        ),
                    ),
                ]
            elif mode == "draw":
                summary_text = "Draw mode active"
                next_step = "Click to place points, double-click to close polygon"
                details_text = "Smart snap and preview are active before commit."
                actions = [
                    (
                        "Back to Select",
                        "Exit draw mode (D)",
                        lambda: self._canvas.set_mode("select"),
                        True,
                    ),
                    ("Fit View", "Fit all geometry (F)", self._fit_view, True),
                ]
            else:
                summary_text = "No active selection"
                next_step = "Canvas ready"
                details_text = (
                    "Use Q for radial menu, or Cmd/Ctrl+K for command palette."
                )
                actions = [
                    (
                        "Enable Quick Rectangle",
                        "Set quick-shape drag mode to rectangle (Shift+R)",
                        lambda: self._canvas.set_quick_shape_mode("rectangle"),
                        True,
                    ),
                    ("Fit View", "Fit all geometry (F)", self._canvas.fit, True),
                    (
                        "Select All",
                        "Select all objects (Ctrl+A)",
                        self._select_all,
                        self._canvas.poly_count > 0,
                    ),
                ]

            self._properties_panel.set_context(
                mode=mode,
                selected_count=sel_count,
                object_count=n,
                summary=summary_text,
                next_step=next_step,
                details=details_text,
            )
            self._properties_panel.set_actions(actions)

            width_mm: float | None = None
            height_mm: float | None = None
            length_mm: float | None = None
            editor_enabled = False
            if selection_indices:
                bounds = self._canvas._selection_bounds(selection_indices)
                if bounds is not None:
                    width_mm = max(0.0, bounds[2] - bounds[0])
                    height_mm = max(0.0, bounds[3] - bounds[1])
                    editor_enabled = True
                if len(selection_indices) == 1:
                    poly = self._canvas.get_polylines_state()[selection_indices[0]]
                    if len(poly) == 2:
                        ax, ay = poly[0]
                        bx, by = poly[1]
                        length_mm = math.hypot(bx - ax, by - ay)
            self._properties_panel.set_geometry_editor(
                width_mm,
                height_mm,
                length_mm,
                self._apply_live_geometry,
                enabled=editor_enabled and not locked_selected,
            )

        self._shape_mode_label.setText(
            f"Shape: {self._canvas.quick_shape_mode.title()}"
        )

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
            polys = load_dxf_polylines(path)
            self._last_in_path = path
            self._canvas.set_polylines_state(polys, fit=bool(polys))
            self._canvas.set_mode("select")
            self._doc_graph = graph_from_polylines(
                polys,
                layer="geometry",
                as_segments=True,
            )
            self._graph_adapter = CanvasGraphAdapter(
                self._doc_graph, display_layer="geometry"
            )
            self._canvas._show_flash(f"Loaded DXF: {Path(path).name}", 1200)
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
        self._canvas.set_polylines_state(incoming, fit=True)
        self._canvas.set_mode("select")
        self._doc_graph = graph_from_polylines(
            incoming,
            layer="geometry",
            as_segments=True,
        )
        self._graph_adapter = CanvasGraphAdapter(
            self._doc_graph, display_layer="geometry"
        )
        self._canvas._show_flash(f"Loaded {len(incoming)} from {source_label}", 1200)
        self._refresh_status()
        self._emit_state_changed()

    # ── Workspace persistence ─────────────────────────────────────────────

    def get_workspace_state(self) -> dict:
        self._graph_adapter.capture_from_canvas(self._canvas)
        return {
            "canvas_polys": self._canvas.get_polylines_state(),
            "canvas_view": self._canvas.get_view_state(),
            "quick_shape_mode": self._canvas.quick_shape_mode,
            "quick_shape_enabled": self._canvas.quick_shape_enabled,
            "last_input_dxf": self._last_in_path,
            "document_graph": self._doc_graph.snapshot(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state = True
        if not isinstance(state, dict):
            state = {}
        self._hidden_indices.clear()
        self._locked_indices.clear()

        graph_state = state.get("document_graph")
        if isinstance(graph_state, dict):
            self._doc_graph.restore(graph_state)
            self._graph_adapter = CanvasGraphAdapter(
                self._doc_graph, display_layer="geometry"
            )
            self._graph_adapter.load_to_canvas(
                self._canvas, fit=bool(self._canvas.poly_count == 0)
            )

        polys = state.get("canvas_polys", [])
        if polys and not isinstance(graph_state, dict):
            self._canvas.set_polylines_state(polys, fit=True)
            self._doc_graph = graph_from_polylines(
                polys, layer="geometry", as_segments=True
            )
            self._graph_adapter = CanvasGraphAdapter(
                self._doc_graph, display_layer="geometry"
            )
        else:
            if not isinstance(graph_state, dict):
                self._canvas.load([])
                self._doc_graph = DocumentGraph()
                set_active_layer(self._doc_graph, "geometry")
                self._graph_adapter = CanvasGraphAdapter(
                    self._doc_graph, display_layer="geometry"
                )

        if state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
            view_state = state["canvas_view"]
            if isinstance(view_state, dict):
                self._hidden_indices = {
                    int(i)
                    for i in view_state.get("hidden_indices", [])
                    if isinstance(i, int)
                }
                self._locked_indices = {
                    int(i)
                    for i in view_state.get("locked_indices", [])
                    if isinstance(i, int)
                }
        self._sync_browser_interaction_state()

        if state.get("quick_shape_mode"):
            self._canvas.set_quick_shape_mode(
                str(state["quick_shape_mode"]), flash=False
            )
        self._canvas.set_quick_shape_enabled(
            bool(state.get("quick_shape_enabled", True))
        )
        self._last_in_path = str(state.get("last_input_dxf", "") or "") or None

        self._suspend_state = False
        self._refresh_status()

    def clear_workspace_state(self) -> None:
        self._suspend_state = True
        self._hidden_indices.clear()
        self._locked_indices.clear()
        self._doc_graph = DocumentGraph()
        set_active_layer(self._doc_graph, "geometry")
        self._graph_adapter = CanvasGraphAdapter(
            self._doc_graph, display_layer="geometry"
        )
        self._graph_adapter.load_to_canvas(self._canvas, fit=False)
        self._canvas.set_mode("select")
        self._sync_browser_interaction_state()
        self._canvas.set_quick_shape_mode("rectangle", flash=False)
        self._canvas.set_quick_shape_enabled(True)
        self._last_in_path = None
        self._suspend_state = False
        self._refresh_status()

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets
