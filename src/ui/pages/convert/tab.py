"""Convert page — FVI → DXF | DXF Fixer | DXF → SVG | SVG → DXF."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import graph_from_polylines, polylines_from_graph
from src.backend.dxf.io import load_dxf_polylines
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.canvas.modules import CanvasGridModule
from src.ui.core.base_page import BasePage
from src.ui.core.factories import content_splitter, surface_frame
from src.ui.pages.convert.subtabs import (
    FixerSubTab,
    FviSubTab,
    SvgSubTab,
    SvgToDxfSubTab,
)
from src.ui.widgets.status_strip import CanvasStatusStrip

LOGGER = logging.getLogger(__name__)


class ConvertPage(BasePage):
    """Convert page — conversion and repair helpers for vector workflows."""

    _TOOL_DESCS = (
        "Convert FVI vector files to DXF. Supports single file or folder batch mode.",
        "Clean up malformed DXF files — close open polylines, simplify, and remove degenerate geometry.",
        "Export DXF as an SVG vector graphic for web or print workflows.",
        "Import an SVG and convert its paths to DXF polylines.",
    )
    _BTN_LABELS = ("Convert", "Fix DXF", "Convert to SVG", "Convert to DXF")

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar content ──────────────────────────────────────────────
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(10, 10, 10, 4)
        left.setSpacing(4)

        # Vertical tool selector
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        _tool_labels = [
            "→  FVI Export",
            "⚙  Repair DXF",
            "→  DXF to SVG",
            "→  SVG to DXF",
        ]
        _tool_tips = [
            "Convert FVI files to DXF format",
            "Repair and clean up DXF files",
            "Export DXF as SVG vector graphics",
            "Import SVG files as DXF outlines",
        ]
        for i, (lbl, tip) in enumerate(zip(_tool_labels, _tool_tips)):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setProperty("active", i == 0)
            btn.setProperty("role", "tool-item")
            btn.setMinimumHeight(40)
            btn.setToolTip(tip)
            self._tool_group.addButton(btn, i)
            left.addWidget(btn)

        left.addSpacing(4)

        self._subtab_desc = QLabel(self._TOOL_DESCS[0])
        self._subtab_desc.setProperty("role", "hint")
        self._subtab_desc.setWordWrap(True)
        left.addWidget(self._subtab_desc)

        left.addSpacing(2)

        self._tool_stack = QStackedWidget()
        self._fvi_subtab = FviSubTab(settings=self._settings)
        self._fix_subtab = FixerSubTab(settings=self._settings)
        self._svg_subtab = SvgSubTab(settings=self._settings)
        self._svg_dxf_subtab = SvgToDxfSubTab(settings=self._settings)
        self._tool_stack.addWidget(self._fvi_subtab)
        self._tool_stack.addWidget(self._fix_subtab)
        self._tool_stack.addWidget(self._svg_subtab)
        self._tool_stack.addWidget(self._svg_dxf_subtab)
        left.addWidget(self._tool_stack, stretch=1)

        # ── Manual sidebar: scroll area + sticky footer ───────────────────────
        sidebar_frame = surface_frame("sidebar")
        sidebar_frame.setMinimumWidth(320)
        sidebar_frame.setMaximumWidth(400)
        sidebar_outer = QVBoxLayout(sidebar_frame)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(0)

        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QFrame.Shape.NoFrame)
        _scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _scroll.setWidget(left_w)
        sidebar_outer.addWidget(_scroll, stretch=1)

        _sep = QFrame()
        _sep.setFrameShape(QFrame.Shape.HLine)
        _sep.setMaximumHeight(1)
        _sep.setProperty("role", "hsep")
        sidebar_outer.addWidget(_sep)

        # Sticky CTA footer
        footer_w = QWidget()
        footer_layout = QVBoxLayout(footer_w)
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(6)

        self._footer_btn = QPushButton(self._BTN_LABELS[0])
        self._footer_btn.setMinimumHeight(38)
        self._footer_btn.setProperty("role", "primary")
        self._footer_btn.clicked.connect(self._trigger_active_subtab)

        self._footer_overflow = QToolButton()
        self._footer_overflow.setText("⋯")
        self._footer_overflow.setFixedWidth(32)
        self._footer_overflow.setFixedHeight(38)
        self._footer_overflow.setToolTip("More actions")
        self._footer_overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._footer_overflow_menu = QMenu(self._footer_overflow)
        self._footer_overflow.setMenu(self._footer_overflow_menu)

        footer_cta = QHBoxLayout()
        footer_cta.setSpacing(4)
        footer_cta.addWidget(self._footer_btn, stretch=1)
        footer_cta.addWidget(self._footer_overflow)
        footer_layout.addLayout(footer_cta)

        self._footer_status = QLabel("")
        self._footer_status.setWordWrap(True)
        self._footer_status.setVisible(False)
        footer_layout.addWidget(self._footer_status)

        sidebar_outer.addWidget(footer_w)
        self._left_panel = sidebar_frame

        # ── Right panel: empty state → canvas preview ─────────────────────────
        right_w = surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        self._right_stack = QStackedWidget()
        right.addWidget(self._right_stack)

        # Page 0 — empty state
        _empty_w = QWidget()
        _ev = QVBoxLayout(_empty_w)
        _ev.setContentsMargins(24, 24, 24, 24)
        _ev_icon = QLabel("↗")
        _ev_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_icon.setProperty("role", "empty-icon")
        _ev_title = QLabel("No preview")
        _ev_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_title.setProperty("role", "empty-title")
        _ev_hint = QLabel("Load a file and run a conversion\nto see the preview here.")
        _ev_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_hint.setWordWrap(True)
        _ev_hint.setProperty("role", "empty-hint")
        _ev.addStretch()
        _ev.addWidget(_ev_icon)
        _ev.addSpacing(8)
        _ev.addWidget(_ev_title)
        _ev.addSpacing(4)
        _ev.addWidget(_ev_hint)
        _ev.addStretch()
        self._right_stack.addWidget(_empty_w)

        # Page 1 — canvas + log
        _canvas_w = QWidget()
        _cl = QVBoxLayout(_canvas_w)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(6)

        self._precision_bar = CanvasGridModule(
            canvas=None, on_changed=self._refresh_preview_ui
        )
        _cl.addWidget(self._precision_bar)

        self._canvas_status = CanvasStatusStrip()
        _cl.addWidget(self._canvas_status)

        self._preview_canvas = DxfCanvas(selectable=False)
        self._preview_canvas.set_grid_visible(True)
        self._preview_canvas.set_grid_snap(False)
        self._preview_canvas.set_grid_spacing(1.0)
        self._precision_bar.bind_canvas(self._preview_canvas)
        _cl.addWidget(self._preview_canvas, stretch=1)

        log_lbl = QLabel("LOG")
        log_lbl.setProperty("role", "eyebrow")
        _cl.addWidget(log_lbl)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setProperty("role", "log")
        self._log.setPlaceholderText(
            "Conversion output and repair details will appear here."
        )
        _cl.addWidget(self._log)

        self._right_stack.addWidget(_canvas_w)
        self._right_stack.setCurrentIndex(0)

        # ── Splitter ──────────────────────────────────────────────────────────
        self._splitter = content_splitter(self._left_panel, right_w, sizes=(320, 920))
        root.addWidget(self._splitter)

        # ── Connect signals ───────────────────────────────────────────────────
        for tab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            tab.log_line.connect(self._log.appendPlainText)
            tab.preview_path.connect(self._load_preview)

        # Secondary overflow action enabled state (guarded by current tab)
        self._fvi_subtab._out_dir_sig.connect(
            lambda _: self._update_sec_action_if_active(0, True)
        )
        self._fix_subtab._reveal_state.connect(
            lambda b: self._update_sec_action_if_active(1, b)
        )
        self._svg_subtab._reveal_state.connect(
            lambda b: self._update_sec_action_if_active(2, b)
        )
        self._svg_dxf_subtab._reveal_state.connect(
            lambda b: self._update_sec_action_if_active(3, b)
        )

        self._tool_group.idClicked.connect(self._on_tool_changed)
        self._on_tool_changed(0)
        self._refresh_preview_ui()

    def _on_tool_changed(self, idx: int) -> None:
        self._tool_stack.setCurrentIndex(idx)
        self._subtab_desc.setText(self._TOOL_DESCS[idx])
        for btn in self._tool_group.buttons():
            active = self._tool_group.id(btn) == idx
            btn.setProperty("active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._footer_btn.setText(self._BTN_LABELS[idx])
        self._footer_btn.setEnabled(True)
        self._footer_status.setVisible(False)

        _all = (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )
        if hasattr(self, "_active_tab_idx"):
            prev = _all[self._active_tab_idx]
            prev._btn_state.disconnect(self._footer_btn.setEnabled)
            prev._status_sig.disconnect(self._set_footer_status)
        self._active_tab_idx = idx
        subtab = _all[idx]
        subtab._btn_state.connect(self._footer_btn.setEnabled)
        subtab._status_sig.connect(self._set_footer_status)

        self._footer_overflow_menu.clear()
        if idx == 0:
            sec = self._footer_overflow_menu.addAction(
                "Open Output Folder", self._fvi_subtab._open_output_folder
            )
            sec.setEnabled(bool(self._fvi_subtab._last_out_dir))
        else:
            sec = self._footer_overflow_menu.addAction(
                "Show in Finder",
                subtab._reveal,  # type: ignore[union-attr]
            )
            sec.setEnabled(bool(subtab._last_out))  # type: ignore[union-attr]

    def _trigger_active_subtab(self) -> None:
        """Disable footer CTA, show working status, then invoke the active subtab."""
        self._footer_btn.setEnabled(False)
        self._set_footer_status("Working…", "#8b949e")
        _all = (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )
        _all[self._tool_stack.currentIndex()].run()

    def _update_sec_action_if_active(self, tab_idx: int, enabled: bool) -> None:
        """Update the secondary overflow action when its tab is active."""
        if self._tool_stack.currentIndex() == tab_idx:
            actions = self._footer_overflow_menu.actions()
            if actions:
                actions[0].setEnabled(enabled)

    def _set_footer_status(self, text: str, color: str = "#8b949e") -> None:
        if not text:
            self._footer_status.setVisible(False)
            return
        self._footer_status.setVisible(True)
        self._footer_status.setText(text)
        if color == "#3fb950":
            role = "status-ok"
        elif color == "#f85149":
            role = "status-err"
        else:
            role = "status-neutral"
        self._footer_status.setProperty("role", role)
        self._footer_status.style().unpolish(self._footer_status)
        self._footer_status.style().polish(self._footer_status)

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
        btn = self._tool_group.button(index)
        if btn is not None:
            btn.setChecked(True)
        self._on_tool_changed(index)
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
        if preview_polys:
            self._right_stack.setCurrentIndex(1)
            if state.get("preview_view"):
                self._preview_canvas.set_view_state(state["preview_view"])
        self._refresh_preview_ui()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._log.clear()
        self._right_stack.setCurrentIndex(0)
        self._footer_status.setVisible(False)

    def _load_preview(self, dxf_path: str) -> None:
        try:
            polys = load_dxf_polylines(dxf_path)
            if polys:
                self._right_stack.setCurrentIndex(1)
                self._preview_canvas.load(polys)
                self._refresh_preview_ui()
        except (OSError, ValueError) as exc:
            LOGGER.debug("Preview load failed for '%s': %s", dxf_path, exc)
