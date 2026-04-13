"""Draft tab — interaction-first 2D drafting.

Design goals:
- Maximize canvas space; minimize persistent chrome
- Primary creation path is direct drag on canvas (no dialog/dropdown)
- Context menu and hotkeys provide secondary fast paths
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM
from src.core.document_actions import set_active_layer
from src.core.document_graph import DocumentGraph
from src.core.document_migration import graph_from_polylines
from src.core.dxf_io import load_dxf_polylines, write_polylines_dxf
from src.core.shapes import shape_circle, shape_polygon, shape_rect, shape_slot
from src.ui.action_maps import SHAPE_ACTION_MAP
from src.ui.canvas import DxfCanvas
from src.ui.canvas_graph_adapter import CanvasGraphAdapter
from src.ui.helpers import CanvasStatusStrip, _surface_frame

ACTION_MAP = SHAPE_ACTION_MAP


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    return sep


class EfficientDraftCanvas(DxfCanvas):
    """Canvas subclass with direct-drag shape creation and contextual shape actions."""

    quickShapeChanged = Signal(str)
    quickShapeEnabledChanged = Signal(bool)

    _VALID_SHAPES = {"rectangle", "circle", "slot", "hexagon"}

    def __init__(self, *args, **kwargs) -> None:
        self._send_selected_to_pattern_cb = kwargs.pop(
            "on_send_selected_to_pattern", None
        )
        super().__init__(*args, **kwargs)
        self._quick_shape_mode: str = "rectangle"
        self._quick_shape_enabled: bool = True
        self._shape_drag_active: bool = False
        self._shape_drag_mode: str = "rectangle"
        self._shape_start_w: tuple[float, float] | None = None
        self._shape_start_c: QPoint | None = None
        self._shape_end_c: QPoint | None = None
        self._shape_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())

        # Keep grid visible by default; snap off (faster free drawing by default)
        self.set_grid_visible(True)
        self.set_grid_snap(False)
        self.set_grid_spacing(1.0)

    @property
    def quick_shape_mode(self) -> str:
        return self._quick_shape_mode

    @property
    def quick_shape_enabled(self) -> bool:
        return self._quick_shape_enabled

    def set_quick_shape_enabled(self, enabled: bool) -> None:
        self._quick_shape_enabled = enabled
        self.quickShapeEnabledChanged.emit(enabled)
        self._redraw()

    def set_quick_shape_mode(self, mode: str, *, flash: bool = True) -> None:
        m = mode.strip().lower()
        if m not in self._VALID_SHAPES:
            return
        self._quick_shape_mode = m
        # Selecting a mode implicitly re-enables quick shape
        if not self._quick_shape_enabled:
            self._quick_shape_enabled = True
            self.quickShapeEnabledChanged.emit(True)
        self.quickShapeChanged.emit(m)
        if flash:
            self._show_flash(f"Drag shape: {m}", 900)
        self._redraw()

    # ── Event overrides ──────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._mode == "select"
            and not self._measure_mode
        ):
            # Preserve shift+drag rectangular selection from base class
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().mousePressEvent(event)
                return

            pos = event.position()
            hit = self._find_poly_at(pos.x(), pos.y())
            if hit is None:
                if self._quick_shape_enabled:
                    mode = self._shape_mode_from_modifiers(event.modifiers())
                    self._start_shape_drag(mode, pos)
                    return
                # Quick shape disabled — fall through to base (empty-canvas deselect)
                super().mousePressEvent(event)
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._shape_drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.position().toPoint()
            self._shape_end_c = pos
            if self._shape_start_c is not None:
                rect = QRect(self._shape_start_c, pos).normalized()
                self._shape_band.setGeometry(rect)
            wx, wy = self._c2w(event.position().x(), event.position().y())
            self._cursor_wx = wx
            self._cursor_wy = wy
            self._redraw()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._shape_drag_active and event.button() == Qt.MouseButton.LeftButton:
            self._shape_band.hide()
            self._finish_shape_drag(event.position().toPoint())
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._mode == "select":
            key = event.key()
            if key == Qt.Key.Key_R:
                self.set_quick_shape_mode("rectangle")
                return
            if key == Qt.Key.Key_C:
                self.set_quick_shape_mode("circle")
                return
            if key == Qt.Key.Key_S:
                self.set_quick_shape_mode("slot")
                return
            if key == Qt.Key.Key_P:
                self.set_quick_shape_mode("hexagon")
                return

        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        # Discoverability hint for new users (default path is direct drag)
        if self._mode == "select":
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(DIM), 1.0))
            hint = (
                f"Drag empty canvas to create {self._quick_shape_mode}. "
                "Alt+Drag: circle · Right-click: shape menu · R/C/S/P: quick mode"
                if self._quick_shape_enabled
                else "Quick shape is off. Use the toolbar toggle or R/C/S/P to re-enable it."
            )
            painter.drawText(
                10,
                18,
                hint,
            )
            painter.end()

    # ── Context menu override ────────────────────────────────────────────

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if self._mode in ("draw", "edit"):
            super()._rightclick_cb(cx, cy)
            return

        menu = QMenu(self)

        # Contextual primary shape actions
        shape_menu = menu.addMenu("Create shape")
        shape_menu.addAction(
            "Rectangle (drag)",
            lambda: self.set_quick_shape_mode("rectangle"),
        )
        shape_menu.addAction(
            "Circle (drag)",
            lambda: self.set_quick_shape_mode("circle"),
        )
        shape_menu.addAction(
            "Slot (drag)",
            lambda: self.set_quick_shape_mode("slot"),
        )
        shape_menu.addAction(
            "Hexagon (drag)",
            lambda: self.set_quick_shape_mode("hexagon"),
        )

        menu.addSeparator()

        # Selection-specific actions
        poly_hit = self._find_poly_at(cx, cy)
        if poly_hit is not None:
            idx = poly_hit
            if idx in self._sel:
                menu.addAction("Deselect", lambda: self._ctx_deselect(idx))
            else:
                menu.addAction("Select", lambda: self._ctx_select(idx))
            menu.addAction("Delete", lambda: self._ctx_delete_poly(idx))
            menu.addSeparator()

        context_idx = poly_hit

        def _ensure_context_selection() -> bool:
            if self._sel:
                return True
            if context_idx is None:
                return False
            self._sel = {context_idx}
            self._redraw()
            self._notify()
            return True

        def _run_transform(action) -> None:
            if _ensure_context_selection():
                action()
            else:
                self._show_flash("Select shape(s) first", 1000)

        if self._sel:
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            menu.addAction("Duplicate", self.duplicate_selected)
            menu.addAction("Fit selection", self.fit_selection)
        else:
            menu.addAction("Select all", self.select_all)

        menu.addAction(
            "Send selected to Pattern Fill",
            lambda: _run_transform(self._send_selected_to_pattern),
        )

        transform_menu = menu.addMenu("Transform")
        transform_menu.addAction(
            "Rotate +90°", lambda: _run_transform(lambda: self.rotate_selected(90.0))
        )
        transform_menu.addAction(
            "Rotate -90°", lambda: _run_transform(lambda: self.rotate_selected(-90.0))
        )
        transform_menu.addAction(
            "Mirror horizontal",
            lambda: _run_transform(lambda: self.mirror_selected("horizontal")),
        )
        transform_menu.addAction(
            "Mirror vertical",
            lambda: _run_transform(lambda: self.mirror_selected("vertical")),
        )

        dim_menu = transform_menu.addMenu("Dimensions / Spacing")
        dim_menu.addAction(
            "Edit width + height…  [⌘⇧D]",
            lambda: _run_transform(self._prompt_edit_dimensions),
        )
        dim_menu.addAction(
            "Set line length…",
            lambda: _run_transform(
                lambda: (
                    lambda v: self._set_selected_line_length(v[0]) if v[1] else None
                )(
                    QInputDialog.getDouble(
                        self,
                        "Set Line Length",
                        "Line length (mm):",
                        10.0,
                        0.001,
                        1_000_000.0,
                        3,
                    )
                )
            ),
        )
        dim_menu.addAction(
            "Distribute horizontal spacing…",
            lambda: _run_transform(
                lambda: (
                    lambda v: (
                        self._distribute_selected("horizontal", v[0]) if v[1] else None
                    )
                )(
                    QInputDialog.getDouble(
                        self,
                        "Distribute Horizontal",
                        "Spacing (mm):",
                        1.0,
                        0.0,
                        1_000_000.0,
                        3,
                    )
                )
            ),
        )
        dim_menu.addAction(
            "Distribute vertical spacing…",
            lambda: _run_transform(
                lambda: (
                    lambda v: (
                        self._distribute_selected("vertical", v[0]) if v[1] else None
                    )
                )(
                    QInputDialog.getDouble(
                        self,
                        "Distribute Vertical",
                        "Spacing (mm):",
                        1.0,
                        0.0,
                        1_000_000.0,
                        3,
                    )
                )
            ),
        )

        align_menu = transform_menu.addMenu("Align")
        align_menu.addAction(
            "Left", lambda: _run_transform(lambda: self.align_selected("left"))
        )
        align_menu.addAction(
            "Center X", lambda: _run_transform(lambda: self.align_selected("center-x"))
        )
        align_menu.addAction(
            "Right", lambda: _run_transform(lambda: self.align_selected("right"))
        )
        align_menu.addAction(
            "Top", lambda: _run_transform(lambda: self.align_selected("top"))
        )
        align_menu.addAction(
            "Center Y", lambda: _run_transform(lambda: self.align_selected("center-y"))
        )
        align_menu.addAction(
            "Bottom", lambda: _run_transform(lambda: self.align_selected("bottom"))
        )

        menu.addSeparator()

        # Canvas controls stay contextual (not persistent toolbar toggles)
        menu.addAction("Fit view [F]", self.fit)

        grid_action = menu.addAction("Show grid")
        grid_action.setCheckable(True)
        grid_action.setChecked(self._grid_visible)
        grid_action.triggered.connect(self.set_grid_visible)

        snap_action = menu.addAction("Snap to grid")
        snap_action.setCheckable(True)
        snap_action.setChecked(self._grid_snap)
        snap_action.triggered.connect(self.set_grid_snap)

        mode_menu = menu.addMenu("Mode")
        mode_menu.addAction("Select [Esc]", lambda: self.set_mode("select"))
        mode_menu.addAction("Draw [D]", lambda: self.set_mode("draw"))
        mode_menu.addAction("Edit [E]", lambda: self.set_mode("edit"))

        menu.popup(self.mapToGlobal(QPoint(int(cx), int(cy))))

    def _send_selected_to_pattern(self) -> None:
        if not callable(self._send_selected_to_pattern_cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        self._send_selected_to_pattern_cb(payload)
        self._show_flash("Sent to Pattern Fill", 900)

    # ── Shape-drag internals ─────────────────────────────────────────────

    def _shape_mode_from_modifiers(self, mods: Qt.KeyboardModifiers) -> str:
        if mods & Qt.KeyboardModifier.AltModifier:
            return "circle"
        if mods & Qt.KeyboardModifier.ControlModifier:
            return "slot"
        return self._quick_shape_mode

    def _start_shape_drag(self, mode: str, pos_f) -> None:
        pos = pos_f.toPoint()
        wx, wy = self._c2w(pos_f.x(), pos_f.y())
        self._shape_drag_active = True
        self._shape_drag_mode = mode
        self._shape_start_w = (wx, wy)
        self._shape_start_c = pos
        self._shape_end_c = pos
        self._shape_band.setGeometry(QRect(pos, pos))
        self._shape_band.show()

    def _finish_shape_drag(self, end_c: QPoint) -> None:
        if (
            not self._shape_drag_active
            or self._shape_start_w is None
            or self._shape_start_c is None
        ):
            self._clear_shape_drag()
            return

        start_c = self._shape_start_c
        drag_px = abs(end_c.x() - start_c.x()) + abs(end_c.y() - start_c.y())
        if drag_px < 8:
            # Tiny movement = treat as non-creation
            self._clear_shape_drag()
            self._redraw()
            return

        sx, sy = self._shape_start_w
        ex, ey = self._c2w(float(end_c.x()), float(end_c.y()))

        poly = self._build_drag_shape(self._shape_drag_mode, sx, sy, ex, ey)
        if poly:
            was_empty = len(self._polys) == 0
            self._push_undo()
            self._polys.append(poly)
            self._sel = {len(self._polys) - 1}
            if was_empty:
                self._fit()
            else:
                self._redraw()
            self._notify()
            self._fire_poly_change()
            self._show_flash(f"{self._shape_drag_mode.title()} created", 800)

        self._clear_shape_drag()

    @staticmethod
    def _translate(
        coords: list[tuple[float, float]],
        cx: float,
        cy: float,
    ) -> list[tuple[float, float]]:
        return [(x + cx, y + cy) for x, y in coords]

    def _build_drag_shape(
        self,
        mode: str,
        sx: float,
        sy: float,
        ex: float,
        ey: float,
    ) -> list[tuple[float, float]]:
        w = abs(ex - sx)
        h = abs(ey - sy)
        if w < 1e-6 or h < 1e-6:
            return []

        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0

        if mode == "rectangle":
            return self._translate(shape_rect(w, h), cx, cy)

        if mode == "circle":
            r = min(w, h) / 2.0
            return self._translate(shape_circle(r, 64), cx, cy)

        if mode == "slot":
            length = max(w, h)
            width = min(w, h)
            return self._translate(shape_slot(length, width), cx, cy)

        if mode == "hexagon":
            r = min(w, h) / 2.0
            return self._translate(shape_polygon(6, r), cx, cy)

        return []

    def _clear_shape_drag(self) -> None:
        self._shape_drag_active = False
        self._shape_start_w = None
        self._shape_start_c = None
        self._shape_end_c = None


class ShapeTab(QWidget):
    """Canvas-first drafting tab optimized for interaction speed."""

    stateChanged = Signal()
    sendSelectedToPatternRequested = Signal(object)

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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_canvas(), stretch=1)

        self._canvas_status = CanvasStatusStrip()
        root.addWidget(self._canvas_status)

        self.setAcceptDrops(True)

        self._refresh_status()

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setProperty("surface", "panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # Mode group
        self._mode_btns: dict[str, QPushButton] = {}
        for mode in ("Select", "Draw", "Edit"):
            btn = QPushButton(mode)
            btn.setMinimumHeight(28)
            btn.setProperty("active", mode == "Select")
            btn.clicked.connect(lambda checked=False, m=mode: self._on_toolbar_mode(m))
            lay.addWidget(btn)
            self._mode_btns[mode] = btn

        lay.addWidget(_toolbar_sep())

        fit_btn = QPushButton("Fit")
        fit_btn.setMinimumHeight(28)
        fit_btn.clicked.connect(lambda: self._canvas.fit())
        lay.addWidget(fit_btn)

        open_btn = QPushButton("Open DXF")
        open_btn.setMinimumHeight(28)
        open_btn.setToolTip("Open a DXF file into the draft canvas for editing")
        open_btn.clicked.connect(self._browse_dxf)
        lay.addWidget(open_btn)

        measure_btn = QPushButton("Measure")
        measure_btn.setMinimumHeight(28)
        measure_btn.clicked.connect(lambda: self._canvas.toggle_measure())
        lay.addWidget(measure_btn)

        lay.addWidget(_toolbar_sep())

        self._shape_mode_label = QLabel("Shape: Rectangle")
        self._shape_mode_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        lay.addWidget(self._shape_mode_label)

        lay.addWidget(_toolbar_sep())

        self._quick_shape_btn = QPushButton("Quick Shape")
        self._quick_shape_btn.setMinimumHeight(28)
        self._quick_shape_btn.setCheckable(True)
        self._quick_shape_btn.setChecked(True)
        self._quick_shape_btn.setProperty("active", True)
        self._quick_shape_btn.setToolTip(
            "Toggle drag-to-create shapes on empty canvas.\n"
            "When off, dragging on empty canvas does nothing."
        )
        self._quick_shape_btn.clicked.connect(self._on_quick_shape_toggle)
        lay.addWidget(self._quick_shape_btn)

        lay.addStretch()

        export_btn = QPushButton("Export DXF")
        export_btn.setFixedHeight(28)
        export_btn.setMinimumWidth(90)
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(self._export)
        lay.addWidget(export_btn)

        return bar

    # ── Canvas ────────────────────────────────────────────────────────────

    def _build_canvas(self) -> QWidget:
        w = _surface_frame("canvas")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = EfficientDraftCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_edit,
            on_action=self._on_canvas_action,
            on_send_selected_to_pattern=self._on_send_selected_to_pattern,
        )
        self._canvas.quickShapeChanged.connect(self._on_quick_shape_changed)
        self._canvas.quickShapeEnabledChanged.connect(
            self._on_quick_shape_enabled_changed
        )
        self._graph_adapter.load_to_canvas(self._canvas, fit=False)

        layout.addWidget(self._canvas, stretch=1)
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
        self._quick_shape_btn.setChecked(enabled)
        self._quick_shape_btn.setProperty("active", enabled)
        self._quick_shape_btn.style().unpolish(self._quick_shape_btn)
        self._quick_shape_btn.style().polish(self._quick_shape_btn)
        self._refresh_status()

    def _on_quick_shape_toggle(self, checked: bool) -> None:
        self._canvas.set_quick_shape_enabled(checked)

    def _on_sel_change(self, _count: int) -> None:
        self._refresh_status()

    def _on_canvas_edit(self) -> None:
        self._graph_adapter.capture_from_canvas(self._canvas)
        self._refresh_status()
        self._emit_state_changed()

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

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self) -> None:
        polys = self._canvas.get_polylines_state()
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
            str(Path(self._settings.get("shape_output_dir", "")) / "draft.dxf"),
            "DXF files (*.dxf);;All files (*)",
        )
        if not out_path:
            return

        try:
            write_polylines_dxf(polys, out_path, close=True)
            self._last_out_path = out_path
            self._canvas._show_flash(f"Exported: {Path(out_path).name}", 1200)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    # ── Status ────────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return

        summary = self._canvas.get_status_summary()
        n = self._canvas.poly_count
        mode = str(summary["mode"])

        if n:
            readiness = (
                f"Quick shape: {self._canvas.quick_shape_mode.title()}"
                if self._canvas.quick_shape_enabled
                else "Quick shape: Off"
            )
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
            readiness_text=readiness,
            readiness_tone=tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
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
        idir = self._settings.get("shape_input_dxf_dir", "")
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
            self._settings["shape_input_dxf_dir"] = str(Path(path).parent)
            self._canvas._show_flash(f"Loaded DXF: {Path(path).name}", 1200)
            self._refresh_status()
            self._emit_state_changed()
        except Exception as exc:
            QMessageBox.critical(self, "Open DXF Failed", str(exc))

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
        state = state or {}

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
        self._doc_graph = DocumentGraph()
        set_active_layer(self._doc_graph, "geometry")
        self._graph_adapter = CanvasGraphAdapter(
            self._doc_graph, display_layer="geometry"
        )
        self._graph_adapter.load_to_canvas(self._canvas, fit=False)
        self._canvas.set_mode("select")
        self._canvas.set_quick_shape_mode("rectangle", flash=False)
        self._canvas.set_quick_shape_enabled(True)
        self._last_in_path = None
        self._suspend_state = False
        self._refresh_status()

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets
