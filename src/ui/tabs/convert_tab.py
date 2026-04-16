"""Utilities tab — FVI → DXF | DXF Fixer | DXF → SVG | SVG → DXF."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.document.graph import DocumentGraph
from src.core.document.migration import graph_from_polylines, polylines_from_graph
from src.core.dxf.io import load_dxf_polylines
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components.action_maps import UTILITIES_ACTION_MAP
from src.ui.components.helpers import (
    CanvasPrecisionBar,
    CanvasStatusStrip,
    _content_splitter,
    _surface_frame,
)
from src.ui.tabs.convert_subtabs import (
    FixerSubTab,
    FviSubTab,
    SvgSubTab,
    SvgToDxfSubTab,
)

ACTION_MAP = UTILITIES_ACTION_MAP
LOGGER = logging.getLogger(__name__)


class UtilitiesTab(QWidget):
    """Utilities — conversion and repair helpers for vector workflows."""

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar (sub-tabs + small preview) ───────────────────────────
        left_w = _surface_frame("sidebar")
        left = QVBoxLayout(left_w)
        left.setContentsMargins(10, 10, 10, 10)
        left.setSpacing(6)

        tool_lbl = QLabel("Tool")
        tool_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        left.addWidget(tool_lbl)

        self._tool_combo = QComboBox()
        self._tool_combo.addItems([
            "FVI to DXF",
            "Repair DXF",
            "DXF to SVG",
            "SVG to DXF",
        ])
        self._tool_combo.setToolTip("Choose a conversion or repair utility")
        left.addWidget(self._tool_combo)

        self._tool_stack = QStackedWidget()

        self._fvi_subtab = FviSubTab(settings=self._settings)
        self._fix_subtab = FixerSubTab(settings=self._settings)
        self._svg_subtab = SvgSubTab(settings=self._settings)
        self._svg_dxf_subtab = SvgToDxfSubTab(settings=self._settings)
        self._tool_stack.addWidget(self._fvi_subtab)
        self._tool_stack.addWidget(self._fix_subtab)
        self._tool_stack.addWidget(self._svg_subtab)
        self._tool_stack.addWidget(self._svg_dxf_subtab)
        self._tool_combo.currentIndexChanged.connect(self._tool_stack.setCurrentIndex)
        left.addWidget(self._tool_stack, stretch=1)

        # ── Right: preview canvas + compact log ──────────────────────────────
        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        self._precision_bar = CanvasPrecisionBar(
            None, on_changed=self._refresh_preview_ui
        )
        right.addWidget(self._precision_bar)

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        right.addWidget(self._canvas_status)

        # Preview canvas — main content area
        self._preview_canvas = DxfCanvas(selectable=False)
        self._preview_canvas.set_grid_visible(True)
        self._preview_canvas.set_grid_snap(False)
        self._preview_canvas.set_grid_spacing(1.0)
        self._precision_bar.bind_canvas(self._preview_canvas)
        right.addWidget(self._preview_canvas, stretch=1)

        # Log — compact panel below preview
        log_lbl = QLabel("Log")
        log_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        right.addWidget(log_lbl)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setStyleSheet("font-family: Menlo, Courier; font-size: 11px;")
        self._log.setPlaceholderText(
            "Conversion output and repair details will appear here."
        )
        right.addWidget(self._log)

        left_w.setMinimumWidth(320)
        left_w.setMaximumWidth(400)
        self._left_panel = left_w
        self._splitter = _content_splitter(left_w, right_w, sizes=(320, 920))
        root.addWidget(self._splitter)

        # Connect sub-tab signals to shared log and preview
        for tab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            tab.log_line.connect(self._log.appendPlainText)
            tab.preview_path.connect(self._load_preview)

        self._refresh_preview_ui()

    def _refresh_preview_ui(self) -> None:
        if not hasattr(self, "_preview_canvas"):
            return
        summary = self._preview_canvas.get_status_summary()
        zoom = self._preview_canvas.get_zoom_percent()
        cursor = self._preview_canvas.get_cursor_world_pos()
        topo = self._preview_canvas.get_topology_summary()

        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._preview_canvas.sel_count,
            object_count=self._preview_canvas.poly_count,
            precision_text=str(summary["precision"]),
            topology_text=f"{topo['closed']} closed · {topo['open']} open",
            readiness_text=(
                "Preview ready" if self._preview_canvas.poly_count else "No preview"
            ),
            readiness_tone=("success" if self._preview_canvas.poly_count else "warn"),
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        self._precision_bar.refresh()

    def get_workspace_state(self) -> dict:
        doc_graph = graph_from_polylines(
            self._preview_canvas.get_polylines_state(),
            layer="convert_preview",
            as_segments=False,
        )
        return {
            "active_sub_tab": self._tool_stack.currentIndex(),
            "fvi_src": self._fvi_subtab._src_edit.text(),
            "fvi_out": self._fvi_subtab._out_edit.text(),
            "fvi_batch": self._fvi_subtab._is_batch(),
            "fix_src": self._fix_subtab._src_edit.text(),
            "fix_out": self._fix_subtab._out_edit.text(),
            "svg_src": self._svg_subtab._src_edit.text(),
            "svg_out": self._svg_subtab._out_edit.text(),
            "svg_dxf_src": self._svg_dxf_subtab._src_edit.text(),
            "svg_dxf_out": self._svg_dxf_subtab._out_edit.text(),
            "preview_polys": self._preview_canvas.get_polylines_state(),
            "preview_view": self._preview_canvas.get_view_state(),
            "document_graph": doc_graph.snapshot(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        if not isinstance(state, dict):
            state = {}
        try:
            index = int(state.get("active_sub_tab", 0))
        except (TypeError, ValueError):
            index = 0
        self._tool_combo.setCurrentIndex(index)
        self._tool_stack.setCurrentIndex(index)
        self._fvi_subtab._set_mode(
            "batch" if bool(state.get("fvi_batch")) else "single"
        )
        self._fvi_subtab._src_edit.setText(str(state.get("fvi_src", "")))
        self._fvi_subtab._out_edit.setText(str(state.get("fvi_out", "")))
        self._fix_subtab._src_edit.setText(str(state.get("fix_src", "")))
        self._fix_subtab._out_edit.setText(str(state.get("fix_out", "")))
        self._svg_subtab._src_edit.setText(str(state.get("svg_src", "")))
        self._svg_subtab._out_edit.setText(str(state.get("svg_out", "")))
        self._svg_dxf_subtab._src_edit.setText(str(state.get("svg_dxf_src", "")))
        self._svg_dxf_subtab._out_edit.setText(str(state.get("svg_dxf_out", "")))
        graph_state = state.get("document_graph")
        if isinstance(graph_state, dict):
            doc_graph = DocumentGraph()
            doc_graph.restore(graph_state)
            preview_polys = polylines_from_graph(doc_graph, layer="convert_preview")
            if not preview_polys:
                preview_polys = polylines_from_graph(doc_graph, layer="geometry")
        else:
            preview_polys = [list(poly) for poly in state.get("preview_polys", [])]
        self._preview_canvas.set_polylines_state(preview_polys, fit=bool(preview_polys))
        if preview_polys and state.get("preview_view"):
            self._preview_canvas.set_view_state(state["preview_view"])
        self._refresh_preview_ui()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._log.clear()

    def _load_preview(self, dxf_path: str) -> None:
        try:
            polys = load_dxf_polylines(dxf_path)
            if polys:
                self._preview_canvas.load(polys)
                self._refresh_preview_ui()
        except (OSError, ValueError) as exc:
            LOGGER.debug("Preview load failed for '%s': %s", dxf_path, exc)


# Keep old name as alias for backward compatibility
FviTab = UtilitiesTab
