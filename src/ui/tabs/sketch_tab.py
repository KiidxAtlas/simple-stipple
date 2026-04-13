"""Sketch tab — constraint-based 2D drawing with graph data model."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM
from src.core.gcs import ConstraintSolver
from src.core.sketch import (
    ConstraintKind,
    HorizontalConstraint,
    VerticalConstraint,
)
from src.ui.action_maps import SKETCH_ACTION_MAP
from src.ui.helpers import CanvasStatusStrip, _section_label, _surface_frame
from src.ui.sketch_canvas import SketchCanvas

ACTION_MAP = SKETCH_ACTION_MAP


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    return sep


class SketchTab(QWidget):
    """Constraint-based 2D sketch — points, segments, objects, constraints."""

    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._solver = ConstraintSolver()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        canvas_w = self._build_canvas()
        panel_w = self._build_panel()

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.addWidget(canvas_w)
        self._splitter.addWidget(panel_w)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([920, 240])
        root.addWidget(self._splitter, stretch=1)

        self._status = CanvasStatusStrip()
        root.addWidget(self._status)

        self._refresh_panel()
        self._refresh_status()

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setProperty("surface", "panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # Mode buttons
        self._mode_btns: dict[str, QPushButton] = {}
        for mode in ("Select", "Draw"):
            btn = QPushButton(mode)
            btn.setMinimumHeight(28)
            btn.setProperty("active", mode == "Select")
            btn.clicked.connect(lambda checked=False, m=mode: self._on_toolbar_mode(m))
            lay.addWidget(btn)
            self._mode_btns[mode] = btn

        lay.addWidget(_toolbar_sep())

        # View
        fit_btn = QPushButton("Fit")
        fit_btn.setMinimumHeight(28)
        fit_btn.setToolTip("Fit all geometry in view")
        fit_btn.clicked.connect(lambda: self._canvas.fit())
        lay.addWidget(fit_btn)

        lay.addWidget(_toolbar_sep())

        # Grid / snap
        self._grid_btn = QPushButton("Grid")
        self._grid_btn.setMinimumHeight(28)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setChecked(True)
        self._grid_btn.setProperty("active", True)
        self._grid_btn.toggled.connect(self._on_grid_toggled)
        lay.addWidget(self._grid_btn)

        self._snap_btn = QPushButton("Snap")
        self._snap_btn.setMinimumHeight(28)
        self._snap_btn.setCheckable(True)
        self._snap_btn.setChecked(False)
        self._snap_btn.setProperty("active", False)
        self._snap_btn.toggled.connect(self._on_snap_toggled)
        lay.addWidget(self._snap_btn)

        lay.addWidget(_toolbar_sep())

        # Constraint shortcuts
        h_btn = QPushButton("H")
        h_btn.setMinimumHeight(28)
        h_btn.setFixedWidth(30)
        h_btn.setToolTip("Constrain selected segment horizontal")
        h_btn.clicked.connect(
            lambda: self._add_segment_constraint(ConstraintKind.HORIZONTAL)
        )
        lay.addWidget(h_btn)

        v_btn = QPushButton("V")
        v_btn.setMinimumHeight(28)
        v_btn.setFixedWidth(30)
        v_btn.setToolTip("Constrain selected segment vertical")
        v_btn.clicked.connect(
            lambda: self._add_segment_constraint(ConstraintKind.VERTICAL)
        )
        lay.addWidget(v_btn)

        lay.addStretch()

        # Clear
        clear_btn = QPushButton("Clear All")
        clear_btn.setMinimumHeight(28)
        clear_btn.setToolTip("Remove all geometry")
        clear_btn.clicked.connect(self._clear_all)
        lay.addWidget(clear_btn)

        return bar

    # ── Canvas ────────────────────────────────────────────────────────────

    def _build_canvas(self) -> QWidget:
        w = _surface_frame("canvas")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = SketchCanvas()
        self._canvas.selectionChanged.connect(self._on_sel_change)
        self._canvas.modeChanged.connect(self._on_canvas_mode_change)
        self._canvas.graphChanged.connect(self._on_graph_change)
        layout.addWidget(self._canvas, stretch=1)
        return w

    # ── Right panel ───────────────────────────────────────────────────────

    def _build_panel(self) -> QWidget:
        panel = _surface_frame("sidebar")
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Graph info
        _section_label(layout, "Sketch Info")

        self._info_points = QLabel("Points: 0")
        self._info_points.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        layout.addWidget(self._info_points)

        self._info_segments = QLabel("Segments: 0")
        self._info_segments.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        layout.addWidget(self._info_segments)

        self._info_objects = QLabel("Objects: 0")
        self._info_objects.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        layout.addWidget(self._info_objects)

        self._info_dof = QLabel("DOF: 0")
        self._info_dof.setStyleSheet(
            "color: #3fb950; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self._info_dof)

        # Constraints
        _section_label(layout, "Constraints")

        self._constraint_list = QLabel("None")
        self._constraint_list.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self._constraint_list.setWordWrap(True)
        layout.addWidget(self._constraint_list)

        # Selection info
        _section_label(layout, "Selection")

        self._sel_info = QLabel("Nothing selected")
        self._sel_info.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self._sel_info.setWordWrap(True)
        layout.addWidget(self._sel_info)

        layout.addStretch()
        return panel

    # ── Callbacks ─────────────────────────────────────────────────────────

    def _on_toolbar_mode(self, mode: str) -> None:
        self._canvas.set_mode(mode.lower())

    def _on_canvas_mode_change(self, mode: str) -> None:
        for name, btn in self._mode_btns.items():
            active = name.lower() == mode
            btn.setProperty("active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._refresh_status()

    def _on_sel_change(self, count: int) -> None:
        self._refresh_panel()
        self._refresh_status()

    def _on_graph_change(self) -> None:
        self._refresh_panel()
        self._refresh_status()
        self.stateChanged.emit()

    def _on_grid_toggled(self, checked: bool) -> None:
        self._grid_btn.setProperty("active", checked)
        self._grid_btn.style().unpolish(self._grid_btn)
        self._grid_btn.style().polish(self._grid_btn)
        self._canvas.set_grid_visible(checked)

    def _on_snap_toggled(self, checked: bool) -> None:
        self._snap_btn.setProperty("active", checked)
        self._snap_btn.style().unpolish(self._snap_btn)
        self._snap_btn.style().polish(self._snap_btn)
        self._canvas.set_grid_snap(checked)

    # ── Constraint actions ────────────────────────────────────────────────

    def _add_segment_constraint(self, kind: str) -> None:
        graph = self._canvas.graph
        for idx in sorted(self._canvas._sel):
            seg = graph.segment_at_index(idx)
            if seg is None:
                continue
            cid = graph._next_id()
            if kind == ConstraintKind.HORIZONTAL:
                graph.add_constraint(
                    HorizontalConstraint(id=cid, kind=kind, segment=seg)
                )
            elif kind == ConstraintKind.VERTICAL:
                graph.add_constraint(VerticalConstraint(id=cid, kind=kind, segment=seg))
        # Solve and sync
        result = self._solver.solve(graph)
        if result.success:
            self._canvas._sync_from_graph()
        self._refresh_panel()

    def _clear_all(self) -> None:
        self._canvas.clear_graph()
        self._refresh_panel()
        self._refresh_status()

    # ── Panel refresh ─────────────────────────────────────────────────────

    def _refresh_panel(self) -> None:
        graph = self._canvas.graph
        n_pts = len(graph.points)
        n_segs = len(graph.segments)
        n_objs = len(graph.geometries)
        n_constraints = len(graph.constraints)
        dof = graph.total_dof()

        self._info_points.setText(f"Points: {n_pts}")
        self._info_segments.setText(f"Segments: {n_segs}")
        self._info_objects.setText(f"Objects: {n_objs}")
        self._info_dof.setText(f"DOF: {dof}")

        # Color DOF: green if fully constrained, yellow if under, red if over
        if dof == 0 and n_pts > 0:
            self._info_dof.setStyleSheet(
                "color: #3fb950; font-size: 11px; font-weight: 600;"
            )
        elif n_pts == 0:
            self._info_dof.setStyleSheet(
                f"color: {DIM}; font-size: 11px; font-weight: 600;"
            )
        else:
            self._info_dof.setStyleSheet(
                "color: #d29922; font-size: 11px; font-weight: 600;"
            )

        # Constraints summary
        if n_constraints == 0:
            self._constraint_list.setText("None")
        else:
            lines = []
            for c in graph.constraints.values():
                lines.append(f"• {c.kind} (id {c.id})")
            self._constraint_list.setText("\n".join(lines))

        # Selection info
        sel_count = len(self._canvas._sel)
        if sel_count == 0:
            self._sel_info.setText("Nothing selected")
        else:
            parts = [f"{sel_count} segment{'s' if sel_count != 1 else ''}"]
            # Check if selection is an object / connected chain
            for idx in self._canvas._sel:
                seg = graph.segment_at_index(idx)
                if seg:
                    geo = graph.geometry_for_segment(seg)
                    if geo and geo.is_closed:
                        indices = graph.segments_in_geometry(geo)
                        if indices == self._canvas._sel:
                            parts.append(
                                f"Object (id {geo.id}, {len(geo.segments)} sides)"
                            )
                            break
                    component = graph.segments_in_component(seg)
                    if component == self._canvas._sel and len(component) > 1:
                        parts.append(f"Chain ({len(component)} connected segments)")
                        break
            self._sel_info.setText(" · ".join(parts))

    def _refresh_status(self) -> None:
        graph = self._canvas.graph
        n_segs = len(graph.segments)
        n_objs = len(graph.geometries)
        sel_count = len(self._canvas._sel)
        mode = self._canvas._mode

        if n_segs == 0:
            readiness = "Empty sketch"
            tone = "warn"
        elif n_objs > 0:
            readiness = f"{n_objs} object{'s' if n_objs != 1 else ''}"
            tone = "ok"
        else:
            readiness = f"{n_segs} segment{'s' if n_segs != 1 else ''}"
            tone = "neutral"

        zoom_pct = int(self._canvas._scale / max(self._canvas._fit_scale, 1e-9) * 100)
        cursor = None
        if self._canvas._cursor_wx is not None and self._canvas._cursor_wy is not None:
            cursor = (self._canvas._cursor_wx, self._canvas._cursor_wy)

        self._status.set_snapshot(
            mode=mode,
            selected_count=sel_count,
            object_count=n_segs,
            precision_text="",
            readiness_text=readiness,
            readiness_tone=tone,
            zoom_percent=zoom_pct,
            cursor_pos=cursor,
        )

    # ── Workspace state ───────────────────────────────────────────────────

    def get_workspace_state(self) -> dict:
        return {
            "graph": self._canvas.graph.snapshot(),
            "canvas_view": self._canvas.get_view_state(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        state = state or {}
        if state.get("graph"):
            self._canvas.graph.restore(state["graph"])
            self._canvas._sync_from_graph()
        if state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
        self._refresh_panel()
        self._refresh_status()

    def clear_workspace_state(self) -> None:
        self._canvas.clear_graph()
        self._refresh_panel()
        self._refresh_status()

    def get_preset_state(self) -> dict:
        return {}

    def apply_preset_state(self, presets: dict) -> None:
        pass
