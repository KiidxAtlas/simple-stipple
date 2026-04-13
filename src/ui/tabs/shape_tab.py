"""Shape Creator tab."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, SEL, SHAPES
from src.core.dxf_io import write_polylines_dxf
from src.core.shapes import (
    shape_circle,
    shape_ellipse,
    shape_polygon,
    shape_rect,
    shape_rect_rounded,
    shape_slot,
)
from src.settings import save_settings
from src.ui.canvas import DxfCanvas
from src.ui.helpers import (
    CanvasObjectBrowser,
    CanvasStatusStrip,
    CollapsibleSection,
    _canvas_toolbar,
    _content_splitter,
    _section_label,
    _sidebar_panel,
    _surface_frame,
    clear_line_edit_error,
    parse_float_field,
    set_line_edit_error,
)


def _param_row(grid: QGridLayout, row: int, label: str, default: str) -> QLineEdit:
    grid.addWidget(QLabel(label), row, 0)
    e = QLineEdit(default)
    e.setFixedWidth(80)
    grid.addWidget(e, row, 1)
    return e


class ShapeTab(QWidget):
    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._last_out_path: str | None = None
        self._canvas_dirty: bool = False
        self._suspend_state_changes: bool = False
        self._presets: dict[str, dict] = dict(self._settings.get("shape_presets", {}))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        left_content = QWidget()
        left = QVBoxLayout(left_content)
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)

        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        splitter = _content_splitter(
            _sidebar_panel(left_content, min_width=260, max_width=360),
            right_w,
            sizes=(280, 920),
        )
        root.addWidget(splitter)

        self._build_left(left)
        self._build_right(right)
        self._update_preview()

    def _build_left(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Shape")

        self._shape_combo = QComboBox()
        self._shape_combo.setToolTip("Choose the type of shape to create")
        self._shape_combo.addItems(SHAPES)
        self._shape_combo.currentTextChanged.connect(self._switch_shape)
        layout.addWidget(self._shape_combo)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setPlaceholderText("Preset")
        self._preset_combo.setToolTip("Saved parameter presets for the current shape")
        preset_row.addWidget(self._preset_combo, stretch=1)
        load_preset_btn = QPushButton("Load")
        load_preset_btn.setFixedWidth(56)
        load_preset_btn.setToolTip("Apply the selected preset to the parameter fields")
        load_preset_btn.clicked.connect(self._apply_selected_preset)
        preset_row.addWidget(load_preset_btn)
        save_preset_btn = QPushButton("Save")
        save_preset_btn.setFixedWidth(56)
        save_preset_btn.setToolTip("Save the current parameters as a named preset")
        save_preset_btn.clicked.connect(self._save_preset)
        preset_row.addWidget(save_preset_btn)
        delete_preset_btn = QPushButton("Delete")
        delete_preset_btn.setFixedWidth(62)
        delete_preset_btn.setToolTip("Remove the selected preset permanently")
        delete_preset_btn.clicked.connect(self._delete_selected_preset)
        preset_row.addWidget(delete_preset_btn)
        layout.addLayout(preset_row)
        self._refresh_preset_combo()

        # Stacked param panels
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._rect_page, self._rect_w, self._rect_h, self._rect_cr = self._make_rect()
        self._circle_page, self._circ_r, self._circ_n = self._make_circle()
        self._ellipse_page, self._ell_rx, self._ell_ry, self._ell_n = (
            self._make_ellipse()
        )
        self._polygon_page, self._poly_sides, self._poly_r = self._make_polygon()
        self._slot_page, self._slot_len, self._slot_w = self._make_slot()

        self._stack.addWidget(self._rect_page)
        self._stack.addWidget(self._circle_page)
        self._stack.addWidget(self._ellipse_page)
        self._stack.addWidget(self._polygon_page)
        self._stack.addWidget(self._slot_page)
        self._stack.setCurrentIndex(0)

        _section_label(layout, "Details")
        # Rotation
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotation (°)"))
        self._rotation = QLineEdit("0")
        self._rotation.setFixedWidth(80)
        self._rotation.setToolTip("Rotate the generated shape by this many degrees")
        self._rotation.textChanged.connect(self._schedule_preview)
        rot_row.addWidget(self._rotation)
        layout.addLayout(rot_row)

        aids_content = QWidget()
        aids_layout = QVBoxLayout(aids_content)
        aids_layout.setContentsMargins(0, 0, 0, 0)
        aids_layout.setSpacing(8)
        self._grid_visible_cb = QCheckBox("Show grid")
        self._grid_visible_cb.setChecked(True)
        self._grid_visible_cb.setToolTip("Toggle the background grid overlay")
        self._grid_visible_cb.stateChanged.connect(self._apply_canvas_aids)
        aids_layout.addWidget(self._grid_visible_cb)

        grid_row = QHBoxLayout()
        self._snap_grid_cb = QCheckBox("Snap to grid")
        self._snap_grid_cb.setToolTip("Snap points to the nearest grid intersection when editing")
        self._snap_grid_cb.stateChanged.connect(self._apply_canvas_aids)
        grid_row.addWidget(self._snap_grid_cb)
        grid_row.addStretch()
        grid_row.addWidget(QLabel("Grid (mm)"))
        self._grid_spacing = QLineEdit("1.0")
        self._grid_spacing.setFixedWidth(80)
        self._grid_spacing.setToolTip("Distance between grid lines in millimetres")
        self._grid_spacing.textChanged.connect(self._apply_canvas_aids)
        grid_row.addWidget(self._grid_spacing)
        aids_layout.addLayout(grid_row)

        aid_row = QHBoxLayout()
        self._fit_sel_btn = QPushButton("Fit Selection")
        self._fit_sel_btn.setToolTip("Zoom the canvas to fit the selected objects")
        self._fit_sel_btn.clicked.connect(self._fit_selection)
        aid_row.addWidget(self._fit_sel_btn)
        self._measure_btn = QPushButton("Measure")
        self._measure_btn.setToolTip("Toggle point-to-point measurement mode on the canvas")
        self._measure_btn.clicked.connect(self._toggle_measure)
        aid_row.addWidget(self._measure_btn)
        aids_layout.addLayout(aid_row)
        layout.addWidget(
            CollapsibleSection(
                "Canvas Aids & Precision",
                aids_content,
                expanded=True,
            )
        )

        transform_content = QWidget()
        transform_layout = QVBoxLayout(transform_content)
        transform_layout.setContentsMargins(0, 0, 0, 0)
        transform_layout.setSpacing(8)
        transform_row = QHBoxLayout()
        self._duplicate_btn = QPushButton("Duplicate")
        self._duplicate_btn.setToolTip("Create a copy of the selected objects offset slightly")
        self._duplicate_btn.clicked.connect(self._duplicate_selection)
        transform_row.addWidget(self._duplicate_btn)
        self._mirror_h_btn = QPushButton("Mirror H")
        self._mirror_h_btn.setToolTip("Flip the selection horizontally around its centre")
        self._mirror_h_btn.clicked.connect(lambda: self._mirror_selection("horizontal"))
        transform_row.addWidget(self._mirror_h_btn)
        self._mirror_v_btn = QPushButton("Mirror V")
        self._mirror_v_btn.setToolTip("Flip the selection vertically around its centre")
        self._mirror_v_btn.clicked.connect(lambda: self._mirror_selection("vertical"))
        transform_row.addWidget(self._mirror_v_btn)
        transform_layout.addLayout(transform_row)

        rotate_row = QHBoxLayout()
        rotate_row.addWidget(QLabel("Rotate (°)"))
        self._transform_rotate = QLineEdit("15")
        self._transform_rotate.setFixedWidth(70)
        self._transform_rotate.setToolTip("Rotation angle in degrees for the Apply button")
        rotate_row.addWidget(self._transform_rotate)
        rotate_apply_btn = QPushButton("Apply")
        rotate_apply_btn.setToolTip("Rotate the selection by the specified angle")
        rotate_apply_btn.clicked.connect(self._rotate_selection)
        rotate_row.addWidget(rotate_apply_btn)
        rotate_down_btn = QPushButton("-90")
        rotate_down_btn.setToolTip("Rotate the selection 90 degrees counter-clockwise")
        rotate_down_btn.clicked.connect(lambda: self._rotate_selection(-90.0))
        rotate_row.addWidget(rotate_down_btn)
        rotate_up_btn = QPushButton("+90")
        rotate_up_btn.setToolTip("Rotate the selection 90 degrees clockwise")
        rotate_up_btn.clicked.connect(lambda: self._rotate_selection(90.0))
        rotate_row.addWidget(rotate_up_btn)
        transform_layout.addLayout(rotate_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Scale (%)"))
        self._transform_scale = QLineEdit("100")
        self._transform_scale.setFixedWidth(70)
        self._transform_scale.setToolTip("Scale percentage (100 = no change, 50 = half size)")
        scale_row.addWidget(self._transform_scale)
        scale_apply_btn = QPushButton("Apply")
        scale_apply_btn.setToolTip("Scale the selection by the specified percentage")
        scale_apply_btn.clicked.connect(self._scale_selection)
        scale_row.addWidget(scale_apply_btn)
        transform_layout.addLayout(scale_row)

        align_row = QHBoxLayout()
        self._align_combo = QComboBox()
        self._align_combo.setToolTip("Choose an alignment edge or axis")
        self._align_combo.addItems([
            "Left",
            "Center X",
            "Right",
            "Top",
            "Center Y",
            "Bottom",
        ])
        align_row.addWidget(self._align_combo, stretch=1)
        align_apply_btn = QPushButton("Align")
        align_apply_btn.setToolTip("Align the selected objects to the chosen edge or axis")
        align_apply_btn.clicked.connect(self._align_selection)
        align_row.addWidget(align_apply_btn)
        transform_layout.addLayout(align_row)
        layout.addWidget(
            CollapsibleSection(
                "Transform Tools",
                transform_content,
                expanded=True,
            )
        )

        _section_label(layout, "Edit")
        # Regenerate
        self._regen_btn = QPushButton("↺ Regenerate")
        self._regen_btn.setMinimumHeight(30)
        self._regen_btn.setEnabled(False)
        self._regen_btn.setToolTip("Reset canvas to generated shape")
        self._regen_btn.clicked.connect(self._on_regenerate)
        layout.addWidget(self._regen_btn)

        _section_label(layout, "Export")
        # Export
        self._export_btn = QPushButton("Export DXF…")
        self._export_btn.setMinimumHeight(38)
        self._export_btn.setToolTip("Save the current canvas geometry as a DXF file")
        self._export_btn.setProperty("role", "primary")
        self._export_btn.clicked.connect(self._export)
        layout.addWidget(self._export_btn)

        self._shape_status = QLabel("")
        self._shape_status.setStyleSheet(f"color: {DIM};")
        self._shape_status.setWordWrap(True)
        layout.addWidget(self._shape_status)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the exported file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_in_finder)
        layout.addWidget(self._reveal_btn)

        layout.addStretch()

    def _make_rect(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        rw = _param_row(g, 0, "Width (mm)", "50.0")
        rw.setToolTip("Horizontal width of the rectangle")
        rh = _param_row(g, 1, "Height (mm)", "30.0")
        rh.setToolTip("Vertical height of the rectangle")
        cr = _param_row(g, 2, "Corner radius (mm)", "0")
        cr.setToolTip("Fillet radius for rounded corners (0 = sharp)")
        for e in (rw, rh, cr):
            e.textChanged.connect(self._schedule_preview)
        return w, rw, rh, cr

    def _make_circle(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        r = _param_row(g, 0, "Radius (mm)", "25.0")
        r.setToolTip("Radius of the circle")
        n = _param_row(g, 1, "Segments", "64")
        n.setToolTip("Number of line segments used to approximate the circle")
        for e in (r, n):
            e.textChanged.connect(self._schedule_preview)
        return w, r, n

    def _make_ellipse(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        rx = _param_row(g, 0, "X radius (mm)", "40.0")
        rx.setToolTip("Horizontal (X) radius of the ellipse")
        ry = _param_row(g, 1, "Y radius (mm)", "20.0")
        ry.setToolTip("Vertical (Y) radius of the ellipse")
        n = _param_row(g, 2, "Segments", "64")
        n.setToolTip("Number of line segments used to approximate the ellipse")
        for e in (rx, ry, n):
            e.textChanged.connect(self._schedule_preview)
        return w, rx, ry, n

    def _make_polygon(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        sides = _param_row(g, 0, "Sides", "6")
        sides.setToolTip("Number of sides (3 = triangle, 6 = hexagon, etc.)")
        r = _param_row(g, 1, "Radius (mm)", "25.0")
        r.setToolTip("Distance from centre to each vertex")
        for e in (sides, r):
            e.textChanged.connect(self._schedule_preview)
        return w, sides, r

    def _make_slot(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        length = _param_row(g, 0, "Length (mm)", "60.0")
        length.setToolTip("Overall length of the slot (end to end)")
        width = _param_row(g, 1, "Width (mm)", "16.0")
        width.setToolTip("Width of the slot (diameter of the rounded ends)")
        for e in (length, width):
            e.textChanged.connect(self._schedule_preview)
        return w, length, width

    def _switch_shape(self, value: str) -> None:
        idx = {
            "Rectangle": 0,
            "Circle": 1,
            "Ellipse": 2,
            "Regular Polygon": 3,
            "Slot": 4,
        }.get(value, 0)
        self._stack.setCurrentIndex(idx)
        self._schedule_preview()

    def _build_right(self, layout: QVBoxLayout) -> None:
        toolbar, self._mode_btns, self._sel_label = _canvas_toolbar(
            self._on_toolbar_mode,
            lambda: self._canvas.fit(),
        )
        layout.addWidget(toolbar)

        self._canvas_status = CanvasStatusStrip()
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_layout = QVBoxLayout(canvas_shell)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(8)

        caption = QLabel(
            "Edit the generated geometry directly or export it as a clean DXF."
        )
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        canvas_layout.addWidget(caption)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_edit,
        )
        canvas_layout.addWidget(self._canvas, stretch=1)

        self._object_browser = CanvasObjectBrowser("Sketch Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection_requested
        )
        self._object_browser.fitRequested.connect(self._fit_selection)

        splitter = _content_splitter(canvas_shell, self._object_browser, sizes=(860, 260))
        layout.addWidget(splitter, stretch=1)
        self._apply_canvas_aids()
        self._refresh_canvas_panels()

    def _emit_state_changed(self) -> None:
        if not self._suspend_state_changes:
            self.stateChanged.emit()

    def _refresh_preset_combo(self) -> None:
        current = (
            self._preset_combo.currentText() if hasattr(self, "_preset_combo") else ""
        )
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("Presets")
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)
        idx = self._preset_combo.findText(current)
        self._preset_combo.setCurrentIndex(max(idx, 0))
        self._preset_combo.blockSignals(False)

    def _current_param_payload(self) -> dict[str, object]:
        return {
            "shape": self._shape_combo.currentText(),
            "rect_w": self._rect_w.text(),
            "rect_h": self._rect_h.text(),
            "rect_cr": self._rect_cr.text(),
            "circ_r": self._circ_r.text(),
            "circ_n": self._circ_n.text(),
            "ell_rx": self._ell_rx.text(),
            "ell_ry": self._ell_ry.text(),
            "ell_n": self._ell_n.text(),
            "poly_sides": self._poly_sides.text(),
            "poly_r": self._poly_r.text(),
            "slot_len": self._slot_len.text(),
            "slot_w": self._slot_w.text(),
            "rotation": self._rotation.text(),
            "grid_visible": self._grid_visible_cb.isChecked(),
            "snap_grid": self._snap_grid_cb.isChecked(),
            "grid_spacing": self._grid_spacing.text(),
            "transform_rotate": self._transform_rotate.text(),
            "transform_scale": self._transform_scale.text(),
            "align_mode": self._align_combo.currentText(),
        }

    def _apply_param_payload(self, payload: dict) -> None:
        values = {
            "rect_w": "50.0",
            "rect_h": "30.0",
            "rect_cr": "0",
            "circ_r": "25.0",
            "circ_n": "64",
            "ell_rx": "40.0",
            "ell_ry": "20.0",
            "ell_n": "64",
            "poly_sides": "6",
            "poly_r": "25.0",
            "slot_len": "60.0",
            "slot_w": "16.0",
            "rotation": "0",
            "grid_spacing": "1.0",
            "transform_rotate": "15",
            "transform_scale": "100",
        }
        values.update({k: str(v) for k, v in payload.items() if k in values})
        self._shape_combo.setCurrentText(str(payload.get("shape", "Rectangle")))
        self._rect_w.setText(values["rect_w"])
        self._rect_h.setText(values["rect_h"])
        self._rect_cr.setText(values["rect_cr"])
        self._circ_r.setText(values["circ_r"])
        self._circ_n.setText(values["circ_n"])
        self._ell_rx.setText(values["ell_rx"])
        self._ell_ry.setText(values["ell_ry"])
        self._ell_n.setText(values["ell_n"])
        self._poly_sides.setText(values["poly_sides"])
        self._poly_r.setText(values["poly_r"])
        self._slot_len.setText(values["slot_len"])
        self._slot_w.setText(values["slot_w"])
        self._rotation.setText(values["rotation"])
        self._grid_visible_cb.setChecked(bool(payload.get("grid_visible", True)))
        self._snap_grid_cb.setChecked(bool(payload.get("snap_grid", False)))
        self._grid_spacing.setText(values["grid_spacing"])
        self._transform_rotate.setText(values["transform_rotate"])
        self._transform_scale.setText(values["transform_scale"])
        self._align_combo.setCurrentText(str(payload.get("align_mode", "Left")))
        self._apply_canvas_aids()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Shape Preset", "Preset name")
        name = name.strip()
        if not ok or not name:
            return
        self._presets[name] = self._current_param_payload()
        self._settings["shape_presets"] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._preset_combo.setCurrentText(name)
        self._shape_status.setText(f"Saved preset: {name}")
        self._shape_status.setStyleSheet("color: #3fb950;")
        self._emit_state_changed()

    def _apply_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets":
            return
        payload = self._presets.get(name)
        if not payload:
            return
        self._suspend_state_changes = True
        self._apply_param_payload(payload)
        self._suspend_state_changes = False
        self._canvas_dirty = False
        self._regen_btn.setEnabled(False)
        self._update_preview()
        self._shape_status.setText(f"Loaded preset: {name}")
        self._shape_status.setStyleSheet("color: #3fb950;")
        self._emit_state_changed()

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets" or name not in self._presets:
            return
        self._presets.pop(name, None)
        self._settings["shape_presets"] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._shape_status.setText(f"Deleted preset: {name}")
        self._shape_status.setStyleSheet(f"color: {DIM};")
        self._emit_state_changed()

    def get_preset_state(self) -> dict[str, dict]:
        return {name: dict(payload) for name, payload in self._presets.items()}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        self._presets = {name: dict(payload) for name, payload in presets.items()}
        self._refresh_preset_combo()

    def get_workspace_state(self) -> dict:
        return {
            "params": self._current_param_payload(),
            "canvas_dirty": self._canvas_dirty,
            "canvas_polys": self._canvas.get_polylines_state(),
            "canvas_view": self._canvas.get_view_state(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state_changes = True
        state = state or {}
        self._apply_param_payload(state.get("params", {}))
        self._canvas_dirty = bool(state.get("canvas_dirty", False))
        polys = state.get("canvas_polys") or []
        if self._canvas_dirty and polys:
            self._canvas.set_polylines_state(polys, fit=True)
        else:
            self._canvas_dirty = False
            self._update_preview()
        if polys and state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
        self._regen_btn.setEnabled(self._canvas_dirty)
        self._suspend_state_changes = False
        self._refresh_canvas_panels()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._shape_status.setText("")
        self._shape_status.setStyleSheet(f"color: {DIM};")
        self._refresh_canvas_panels()

    def _parse_float_field(
        self,
        entry: QLineEdit,
        label: str,
        **kw,
    ) -> float | None:
        try:
            value = parse_float_field(entry.text(), **kw)
        except ValueError as exc:
            message = f"{label} {exc}"
            set_line_edit_error(entry, message)
            self._shape_status.setText(message)
            self._shape_status.setStyleSheet("color: #f85149;")
            raise ValueError(message) from exc
        clear_line_edit_error(entry)
        return value

    # ── Preview ───────────────────────────────────────────────────────────────

    def _schedule_preview(self, *_) -> None:
        if self._suspend_state_changes:
            return
        self._preview_timer.stop()
        self._preview_timer.start(250)
        self._emit_state_changed()

    def _update_preview(self) -> None:
        if self._canvas_dirty:
            return
        coords = self._build_coords()
        if coords:
            self._canvas.load([coords])
            self._refresh_canvas_panels()

    def _build_coords(self) -> list[tuple[float, float]] | None:
        shape = self._shape_combo.currentText()
        try:
            coords: list[tuple[float, float]] | None = None
            if shape == "Rectangle":
                w = self._parse_float_field(self._rect_w, "Width", minimum=0.001)
                h = self._parse_float_field(self._rect_h, "Height", minimum=0.001)
                cr = self._parse_float_field(
                    self._rect_cr, "Corner radius", minimum=0.0
                )
                coords = shape_rect_rounded(w, h, cr) if cr > 0 else shape_rect(w, h)
            elif shape == "Circle":
                r = self._parse_float_field(self._circ_r, "Radius", minimum=0.001)
                n = max(
                    3,
                    int(self._parse_float_field(self._circ_n, "Segments", minimum=3.0)),
                )
                coords = shape_circle(r, n)
            elif shape == "Ellipse":
                rx = self._parse_float_field(self._ell_rx, "X radius", minimum=0.001)
                ry = self._parse_float_field(self._ell_ry, "Y radius", minimum=0.001)
                n = max(
                    3,
                    int(self._parse_float_field(self._ell_n, "Segments", minimum=3.0)),
                )
                coords = shape_ellipse(rx, ry, n)
            elif shape == "Slot":
                length = self._parse_float_field(self._slot_len, "Length", minimum=0.001)
                width = self._parse_float_field(self._slot_w, "Width", minimum=0.001)
                coords = shape_slot(length, width)
            else:
                sides = max(
                    3,
                    int(
                        self._parse_float_field(self._poly_sides, "Sides", minimum=3.0)
                    ),
                )
                r = self._parse_float_field(self._poly_r, "Radius", minimum=0.001)
                coords = shape_polygon(sides, r)
            if coords is not None:
                deg = self._parse_float_field(self._rotation, "Rotation")
                if abs(deg) > 1e-6:
                    a = math.radians(deg)
                    ca, sa = math.cos(a), math.sin(a)
                    coords = [(x * ca - y * sa, x * sa + y * ca) for x, y in coords]
                self._shape_status.setText("")
                self._shape_status.setStyleSheet(f"color: {DIM};")
            return coords
        except ValueError:
            pass
        return None

    def _on_canvas_edit(self) -> None:
        self._canvas_dirty = True
        self._regen_btn.setEnabled(True)
        self._refresh_canvas_panels()
        self._emit_state_changed()

    def _apply_canvas_aids(self, *_) -> None:
        if not hasattr(self, "_canvas"):
            return
        try:
            spacing = float(self._grid_spacing.text().strip() or "1.0")
        except ValueError:
            return
        if spacing <= 0:
            return
        self._canvas.set_grid_visible(self._grid_visible_cb.isChecked())
        self._canvas.set_grid_snap(self._snap_grid_cb.isChecked())
        self._canvas.set_grid_spacing(spacing)
        self._refresh_canvas_panels()
        self._emit_state_changed()

    def _fit_selection(self) -> None:
        if not self._canvas.fit_selection():
            self._shape_status.setText("Select geometry to frame it.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _toggle_measure(self) -> None:
        self._canvas.toggle_measure()

    def _duplicate_selection(self) -> None:
        if self._canvas.duplicate_selected():
            self._canvas_dirty = True
            self._regen_btn.setEnabled(True)
            self._shape_status.setText("Selection duplicated.")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._emit_state_changed()
        else:
            self._shape_status.setText("Select geometry to duplicate.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _rotate_selection(self, angle: float | None = None) -> None:
        try:
            value = angle if angle is not None else float(self._transform_rotate.text())
        except ValueError:
            self._shape_status.setText("Rotate value must be numeric.")
            self._shape_status.setStyleSheet("color: #f85149;")
            return
        if self._canvas.rotate_selected(value):
            self._canvas_dirty = True
            self._regen_btn.setEnabled(True)
            self._shape_status.setText(f"Rotated selection by {value:g}°.")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._emit_state_changed()
        else:
            self._shape_status.setText("Select geometry to rotate.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _scale_selection(self) -> None:
        try:
            percent = float(self._transform_scale.text())
        except ValueError:
            self._shape_status.setText("Scale must be numeric.")
            self._shape_status.setStyleSheet("color: #f85149;")
            return
        factor = percent / 100.0
        if factor <= 0:
            self._shape_status.setText("Scale must be greater than 0.")
            self._shape_status.setStyleSheet("color: #f85149;")
            return
        if self._canvas.scale_selected(factor):
            self._canvas_dirty = True
            self._regen_btn.setEnabled(True)
            self._shape_status.setText(f"Scaled selection to {percent:g}%.")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._emit_state_changed()
        else:
            self._shape_status.setText("Select geometry to scale.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _mirror_selection(self, axis: str) -> None:
        if self._canvas.mirror_selected(axis):
            self._canvas_dirty = True
            self._regen_btn.setEnabled(True)
            self._shape_status.setText(f"Mirrored selection {axis}.")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._emit_state_changed()
        else:
            self._shape_status.setText("Select geometry to mirror.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _align_selection(self) -> None:
        mode_map = {
            "Left": "left",
            "Center X": "center-x",
            "Right": "right",
            "Top": "top",
            "Center Y": "center-y",
            "Bottom": "bottom",
        }
        mode = mode_map.get(self._align_combo.currentText(), "left")
        if self._canvas.align_selected(mode):
            self._canvas_dirty = True
            self._regen_btn.setEnabled(True)
            self._shape_status.setText(f"Aligned selection: {self._align_combo.currentText()}.")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._emit_state_changed()
        else:
            self._shape_status.setText("Select two or more shapes to align.")
            self._shape_status.setStyleSheet(f"color: {DIM};")

    def _on_regenerate(self) -> None:
        self._canvas_dirty = False
        self._regen_btn.setEnabled(False)
        coords = self._build_coords()
        if coords:
            self._canvas.load([coords])
        self._refresh_canvas_panels()
        self._emit_state_changed()

    def _on_sel_change(self, count: int) -> None:
        if count:
            self._sel_label.setText(f"{count} selected")
            self._sel_label.setStyleSheet(f"color: {SEL};")
        else:
            self._sel_label.setText("")
            self._sel_label.setStyleSheet(f"color: {DIM};")
        self._refresh_canvas_panels()

    def _set_active_mode_btn(self, mode: str) -> None:
        v = mode.lower()
        for k, b in self._mode_btns.items():
            b.setProperty("active", k.lower() == v)
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_toolbar_mode(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._canvas.set_mode(mode.lower())
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._refresh_canvas_panels()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)
        self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        summary = self._canvas.get_status_summary()
        readiness_text = (
            "Canvas edited" if self._canvas_dirty and self._canvas.poly_count else "Parametric preview"
        )
        readiness_tone = "warn" if self._canvas_dirty and self._canvas.poly_count else "accent"
        if self._canvas.poly_count == 0:
            readiness_text = "No geometry"
            readiness_tone = "warn"
        zoom = self._canvas.get_zoom_percent() if hasattr(self._canvas, "get_zoom_percent") else 100
        cursor = self._canvas.get_cursor_world_pos() if hasattr(self._canvas, "get_cursor_world_pos") else None
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
        if hasattr(self, "_object_browser"):
            self._object_browser.set_objects(
                self._canvas.get_polylines_state(),
                self._canvas.get_selection_indices(),
            )

    def _export(self) -> None:
        if self._canvas_dirty:
            polys = self._canvas.get_active() + self._canvas.get_selected()
            if not polys:
                QMessageBox.critical(self, "Error", "Canvas is empty.")
                return
        else:
            coords = self._build_coords()
            if not coords:
                QMessageBox.critical(self, "Error", "Invalid shape parameters.")
                return
            polys = [coords]

        shape_name = self._shape_combo.currentText().lower().replace(" ", "_")
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save shape as DXF",
            str(Path(self._settings.get("shape_output_dir", "")) / f"{shape_name}.dxf"),
            "DXF files (*.dxf);;All files (*)",
        )
        if not out_path:
            return

        try:
            write_polylines_dxf(polys, out_path, close=True)
            self._shape_status.setText(f"Saved → {Path(out_path).name}")
            self._shape_status.setStyleSheet("color: #3fb950;")
            self._last_out_path = out_path
            self._reveal_btn.setEnabled(True)
        except Exception as exc:
            self._shape_status.setText(f"Error: {exc}")
            self._shape_status.setStyleSheet("color: #f85149;")

    def _reveal_in_finder(self) -> None:
        if self._last_out_path:
            p = Path(self._last_out_path)
            if not p.exists():
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"The file no longer exists:\n{self._last_out_path}",
                )
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))
