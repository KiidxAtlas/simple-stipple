"""Draft tab — freeform 2D canvas for drawing, editing, and exporting geometry."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, SHAPES
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
    _section_label,
    _surface_frame,
    clear_line_edit_error,
    parse_float_field,
    set_line_edit_error,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _param_row(grid: QGridLayout, row: int, label: str, default: str) -> QLineEdit:
    """Label + entry pair placed in a grid row."""
    grid.addWidget(QLabel(label), row, 0)
    entry = QLineEdit(default)
    entry.setFixedWidth(90)
    grid.addWidget(entry, row, 1)
    return entry


def _toolbar_sep() -> QLabel:
    """Thin vertical separator for toolbar grouping."""
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    return sep


# ── Insert Shape Dialog ──────────────────────────────────────────────────────


class InsertShapeDialog(QDialog):
    """Modal for configuring and inserting a parametric shape onto the canvas."""

    def __init__(self, parent: QWidget | None = None, presets: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Insert Shape")
        self.setMinimumWidth(360)
        self._presets: dict[str, dict] = dict(presets or {})
        self._result_coords: list[tuple[float, float]] | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Shape type
        _section_label(layout, "Shape")
        self._shape_combo = QComboBox()
        self._shape_combo.addItems(SHAPES)
        self._shape_combo.currentTextChanged.connect(self._switch_shape)
        layout.addWidget(self._shape_combo)

        # Parameter panels (stacked by shape type)
        self._stack = QStackedWidget()
        self._rect_page, self._rect_w, self._rect_h, self._rect_cr = self._make_rect()
        self._circle_page, self._circ_r, self._circ_n = self._make_circle()
        self._ellipse_page, self._ell_rx, self._ell_ry, self._ell_n = (
            self._make_ellipse()
        )
        self._polygon_page, self._poly_sides, self._poly_r = self._make_polygon()
        self._slot_page, self._slot_len, self._slot_w = self._make_slot()
        for page in (
            self._rect_page,
            self._circle_page,
            self._ellipse_page,
            self._polygon_page,
            self._slot_page,
        ):
            self._stack.addWidget(page)
        layout.addWidget(self._stack)

        # Rotation
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotation (°)"))
        self._rotation = QLineEdit("0")
        self._rotation.setFixedWidth(90)
        rot_row.addWidget(self._rotation)
        rot_row.addStretch()
        layout.addLayout(rot_row)

        # Presets
        _section_label(layout, "Presets")
        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setPlaceholderText("Saved presets")
        preset_row.addWidget(self._preset_combo, stretch=1)
        for label, slot, w in [
            ("Load", self._load_preset, 50),
            ("Save", self._save_preset, 50),
            ("Del", self._delete_preset, 40),
        ]:
            btn = QPushButton(label)
            btn.setFixedWidth(w)
            btn.clicked.connect(slot)
            preset_row.addWidget(btn)
        layout.addLayout(preset_row)
        self._refresh_presets()

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {DIM};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        insert_btn = QPushButton("Insert")
        insert_btn.setProperty("role", "primary")
        insert_btn.setMinimumWidth(80)
        insert_btn.clicked.connect(self._do_insert)
        btn_row.addWidget(insert_btn)
        layout.addLayout(btn_row)

    # ── Shape parameter panels ────────────────────────────────────────────

    def _make_rect(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        rw = _param_row(g, 0, "Width (mm)", "50.0")
        rh = _param_row(g, 1, "Height (mm)", "30.0")
        cr = _param_row(g, 2, "Corner radius (mm)", "0")
        return w, rw, rh, cr

    def _make_circle(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        r = _param_row(g, 0, "Radius (mm)", "25.0")
        n = _param_row(g, 1, "Segments", "64")
        return w, r, n

    def _make_ellipse(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        rx = _param_row(g, 0, "X radius (mm)", "40.0")
        ry = _param_row(g, 1, "Y radius (mm)", "20.0")
        n = _param_row(g, 2, "Segments", "64")
        return w, rx, ry, n

    def _make_polygon(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        sides = _param_row(g, 0, "Sides", "6")
        r = _param_row(g, 1, "Radius (mm)", "25.0")
        return w, sides, r

    def _make_slot(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        length = _param_row(g, 0, "Length (mm)", "60.0")
        width = _param_row(g, 1, "Width (mm)", "16.0")
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

    # ── Presets ───────────────────────────────────────────────────────────

    def _refresh_presets(self) -> None:
        self._preset_combo.clear()
        self._preset_combo.addItem("—")
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)

    def _current_payload(self) -> dict:
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
        }

    def _apply_payload(self, payload: dict) -> None:
        self._shape_combo.setCurrentText(str(payload.get("shape", "Rectangle")))
        field_map = [
            ("rect_w", self._rect_w),
            ("rect_h", self._rect_h),
            ("rect_cr", self._rect_cr),
            ("circ_r", self._circ_r),
            ("circ_n", self._circ_n),
            ("ell_rx", self._ell_rx),
            ("ell_ry", self._ell_ry),
            ("ell_n", self._ell_n),
            ("poly_sides", self._poly_sides),
            ("poly_r", self._poly_r),
            ("slot_len", self._slot_len),
            ("slot_w", self._slot_w),
            ("rotation", self._rotation),
        ]
        for key, widget in field_map:
            if key in payload:
                widget.setText(str(payload[key]))

    def _load_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "—" or name not in self._presets:
            return
        self._apply_payload(self._presets[name])
        self._status.setText(f"Loaded: {name}")
        self._status.setStyleSheet("color: #3fb950;")

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self._presets[name] = self._current_payload()
        self._refresh_presets()
        self._preset_combo.setCurrentText(name)
        self._status.setText(f"Saved: {name}")
        self._status.setStyleSheet("color: #3fb950;")

    def _delete_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "—" or name not in self._presets:
            return
        self._presets.pop(name, None)
        self._refresh_presets()
        self._status.setText(f"Deleted: {name}")
        self._status.setStyleSheet(f"color: {DIM};")

    # ── Build coordinates ─────────────────────────────────────────────────

    def _parse(self, entry: QLineEdit, label: str, **kw) -> float:
        try:
            value = parse_float_field(entry.text(), **kw)
        except ValueError as exc:
            set_line_edit_error(entry, f"{label}: {exc}")
            raise
        clear_line_edit_error(entry)
        return value

    def _build_coords(self) -> list[tuple[float, float]] | None:
        shape = self._shape_combo.currentText()
        try:
            coords: list[tuple[float, float]] | None = None
            if shape == "Rectangle":
                w = self._parse(self._rect_w, "Width", minimum=0.001)
                h = self._parse(self._rect_h, "Height", minimum=0.001)
                cr = self._parse(self._rect_cr, "Corner radius", minimum=0.0)
                coords = shape_rect_rounded(w, h, cr) if cr > 0 else shape_rect(w, h)
            elif shape == "Circle":
                r = self._parse(self._circ_r, "Radius", minimum=0.001)
                n = max(3, int(self._parse(self._circ_n, "Segments", minimum=3.0)))
                coords = shape_circle(r, n)
            elif shape == "Ellipse":
                rx = self._parse(self._ell_rx, "X radius", minimum=0.001)
                ry = self._parse(self._ell_ry, "Y radius", minimum=0.001)
                n = max(3, int(self._parse(self._ell_n, "Segments", minimum=3.0)))
                coords = shape_ellipse(rx, ry, n)
            elif shape == "Slot":
                length = self._parse(self._slot_len, "Length", minimum=0.001)
                width = self._parse(self._slot_w, "Width", minimum=0.001)
                coords = shape_slot(length, width)
            else:
                sides = max(
                    3,
                    int(self._parse(self._poly_sides, "Sides", minimum=3.0)),
                )
                r = self._parse(self._poly_r, "Radius", minimum=0.001)
                coords = shape_polygon(sides, r)

            if coords is not None:
                deg = self._parse(self._rotation, "Rotation")
                if abs(deg) > 1e-6:
                    a = math.radians(deg)
                    ca, sa = math.cos(a), math.sin(a)
                    coords = [
                        (x * ca - y * sa, x * sa + y * ca) for x, y in coords
                    ]
            return coords
        except ValueError:
            return None

    def _do_insert(self) -> None:
        coords = self._build_coords()
        if coords:
            self._result_coords = coords
            self.accept()
        else:
            self._status.setText("Fix parameter errors before inserting.")
            self._status.setStyleSheet("color: #f85149;")

    # ── Public API ────────────────────────────────────────────────────────

    def get_coords(self) -> list[tuple[float, float]] | None:
        return self._result_coords

    def get_presets(self) -> dict[str, dict]:
        return dict(self._presets)


# ── Draft Tab ─────────────────────────────────────────────────────────────────
#
# Layout:
#
#   ┌─────────────────────────────────────────────────────────────────────┐
#   │ TOOLBAR                                                             │
#   │ [Select][Draw][Edit] │ [Fit] │ [Grid][Snap][Measure]               │
#   │ [+ Insert Shape…]  ─stretch─  [Dup][⬌][⬍][-90°][+90°] │ [Export] │
#   ├─────────────────────────────────────────────┬───────────────────────┤
#   │                                             │ OBJECTS               │
#   │                                             │ [browser list]        │
#   │                CANVAS                       │                       │
#   │                                             │ TRANSFORM             │
#   │                                             │ rotate / scale / align│
#   │                                             │                       │
#   │                                             │ GRID                  │
#   │                                             │ spacing               │
#   ├─────────────────────────────────────────────┴───────────────────────┤
#   │ STATUS: mode · obj · sel              cursor · zoom · readiness    │
#   └─────────────────────────────────────────────────────────────────────┘


class ShapeTab(QWidget):
    """Freeform 2D drafting canvas — draw, edit, and export polyline geometry."""

    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._presets: dict[str, dict] = dict(self._settings.get("shape_presets", {}))
        self._last_out_path: str | None = None
        self._suspend_state: bool = False
        self._quick_btns: list[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        canvas_w = self._build_canvas()
        panel_w = self._build_panel()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(canvas_w)
        splitter.addWidget(panel_w)
        splitter.setStretchFactor(0, 1)  # canvas stretches
        splitter.setStretchFactor(1, 0)  # panel fixed
        splitter.setSizes([920, 240])
        root.addWidget(splitter, stretch=1)

        self._canvas_status = CanvasStatusStrip()
        root.addWidget(self._canvas_status)

        self._apply_grid_settings()
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
            btn.setFixedHeight(26)
            btn.setProperty("active", mode == "Select")
            btn.clicked.connect(
                lambda checked=False, m=mode: self._on_toolbar_mode(m)
            )
            lay.addWidget(btn)
            self._mode_btns[mode] = btn

        lay.addWidget(_toolbar_sep())

        # View
        fit_btn = QPushButton("Fit")
        fit_btn.setFixedHeight(26)
        fit_btn.setToolTip("Fit all geometry in view")
        fit_btn.clicked.connect(lambda: self._canvas.fit())
        lay.addWidget(fit_btn)

        lay.addWidget(_toolbar_sep())

        # Canvas aid toggles
        self._grid_btn = QPushButton("Grid")
        self._grid_btn.setFixedHeight(26)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setChecked(True)
        self._grid_btn.setProperty("active", True)
        self._grid_btn.setToolTip("Toggle background grid")
        self._grid_btn.toggled.connect(self._on_grid_toggled)
        lay.addWidget(self._grid_btn)

        self._snap_btn = QPushButton("Snap")
        self._snap_btn.setFixedHeight(26)
        self._snap_btn.setCheckable(True)
        self._snap_btn.setChecked(False)
        self._snap_btn.setProperty("active", False)
        self._snap_btn.setToolTip("Toggle snap to grid")
        self._snap_btn.toggled.connect(self._on_snap_toggled)
        lay.addWidget(self._snap_btn)

        measure_btn = QPushButton("Measure")
        measure_btn.setFixedHeight(26)
        measure_btn.setToolTip("Toggle point-to-point measurement")
        measure_btn.clicked.connect(lambda: self._canvas.toggle_measure())
        lay.addWidget(measure_btn)

        lay.addWidget(_toolbar_sep())

        # Insert shape
        insert_btn = QPushButton("+ Insert Shape…")
        insert_btn.setFixedHeight(26)
        insert_btn.setToolTip("Insert a parametric shape onto the canvas")
        insert_btn.clicked.connect(self._insert_shape)
        lay.addWidget(insert_btn)

        lay.addStretch()

        # Quick transforms (disabled until something is selected)
        self._add_quick_btn(lay, "Dup", "Duplicate selection",
                            self._duplicate_selection)
        self._add_quick_btn(lay, "⬌", "Mirror horizontally",
                            lambda: self._mirror_selection("horizontal"), w=30)
        self._add_quick_btn(lay, "⬍", "Mirror vertically",
                            lambda: self._mirror_selection("vertical"), w=30)
        self._add_quick_btn(lay, "−90°", "Rotate 90° CCW",
                            lambda: self._rotate_selection(-90.0))
        self._add_quick_btn(lay, "+90°", "Rotate 90° CW",
                            lambda: self._rotate_selection(90.0))

        lay.addWidget(_toolbar_sep())

        # Export (primary action)
        export_btn = QPushButton("Export DXF")
        export_btn.setFixedHeight(28)
        export_btn.setMinimumWidth(90)
        export_btn.setProperty("role", "primary")
        export_btn.setToolTip("Export canvas geometry as DXF")
        export_btn.clicked.connect(self._export)
        lay.addWidget(export_btn)

        return bar

    def _add_quick_btn(
        self,
        layout: QHBoxLayout,
        text: str,
        tip: str,
        slot,
        *,
        w: int | None = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(26)
        if w:
            btn.setFixedWidth(w)
        btn.setToolTip(tip)
        btn.setEnabled(False)
        btn.clicked.connect(slot)
        layout.addWidget(btn)
        self._quick_btns.append(btn)
        return btn

    def _on_grid_toggled(self, checked: bool) -> None:
        self._grid_btn.setProperty("active", checked)
        self._grid_btn.style().unpolish(self._grid_btn)
        self._grid_btn.style().polish(self._grid_btn)
        self._apply_grid_settings()

    def _on_snap_toggled(self, checked: bool) -> None:
        self._snap_btn.setProperty("active", checked)
        self._snap_btn.style().unpolish(self._snap_btn)
        self._snap_btn.style().polish(self._snap_btn)
        self._apply_grid_settings()

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
        )
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

        # Objects
        self._object_browser = CanvasObjectBrowser("Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection
        )
        self._object_browser.fitRequested.connect(self._fit_selection)
        layout.addWidget(self._object_browser, stretch=1)

        # Transform
        _section_label(layout, "Transform")

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotate (°)"))
        self._transform_rotate = QLineEdit("15")
        self._transform_rotate.setFixedWidth(60)
        rot_row.addWidget(self._transform_rotate)
        rot_apply = QPushButton("Apply")
        rot_apply.setFixedHeight(24)
        rot_apply.clicked.connect(lambda: self._rotate_selection())
        rot_row.addWidget(rot_apply)
        layout.addLayout(rot_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Scale (%)"))
        self._transform_scale = QLineEdit("100")
        self._transform_scale.setFixedWidth(60)
        scale_row.addWidget(self._transform_scale)
        scale_apply = QPushButton("Apply")
        scale_apply.setFixedHeight(24)
        scale_apply.clicked.connect(self._scale_selection)
        scale_row.addWidget(scale_apply)
        layout.addLayout(scale_row)

        align_row = QHBoxLayout()
        self._align_combo = QComboBox()
        self._align_combo.addItems(
            ["Left", "Center X", "Right", "Top", "Center Y", "Bottom"]
        )
        align_row.addWidget(self._align_combo, stretch=1)
        align_apply = QPushButton("Align")
        align_apply.setFixedHeight(24)
        align_apply.clicked.connect(self._align_selection)
        align_row.addWidget(align_apply)
        layout.addLayout(align_row)

        # Grid
        _section_label(layout, "Grid")

        grid_row = QHBoxLayout()
        grid_row.addWidget(QLabel("Spacing (mm)"))
        self._grid_spacing = QLineEdit("1.0")
        self._grid_spacing.setFixedWidth(60)
        self._grid_spacing.textChanged.connect(self._apply_grid_settings)
        grid_row.addWidget(self._grid_spacing)
        layout.addLayout(grid_row)

        fit_sel_btn = QPushButton("Fit Selection")
        fit_sel_btn.setFixedHeight(26)
        fit_sel_btn.setToolTip("Zoom to fit selected objects")
        fit_sel_btn.clicked.connect(self._fit_selection)
        layout.addWidget(fit_sel_btn)

        # Export feedback
        self._panel_status = QLabel("")
        self._panel_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self._panel_status.setWordWrap(True)
        layout.addWidget(self._panel_status)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setFixedHeight(24)
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_in_finder)
        layout.addWidget(self._reveal_btn)

        return panel

    # ── Mode switching ────────────────────────────────────────────────────

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

    # ── Selection ─────────────────────────────────────────────────────────

    def _on_sel_change(self, count: int) -> None:
        for btn in self._quick_btns:
            btn.setEnabled(count > 0)
        self._refresh_status()

    def _on_browser_selection(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)
        self._refresh_status()

    def _on_canvas_edit(self) -> None:
        self._refresh_status()
        self._emit_state_changed()

    # ── Insert shape ──────────────────────────────────────────────────────

    def _insert_shape(self) -> None:
        dlg = InsertShapeDialog(self, presets=self._presets)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            coords = dlg.get_coords()
            if coords:
                existing = self._canvas.get_polylines_state()
                was_empty = len(existing) == 0
                existing.append(coords)
                self._canvas.set_polylines_state(existing, fit=was_empty)
                # Select the newly inserted shape
                self._canvas.set_selection([len(existing) - 1])
                self._refresh_status()
                self._emit_state_changed()
        # Persist any preset changes regardless of insert/cancel
        self._presets = dlg.get_presets()
        self._settings["shape_presets"] = dict(self._presets)
        save_settings(self._settings)

    # ── Transforms ────────────────────────────────────────────────────────

    def _duplicate_selection(self) -> None:
        if self._canvas.duplicate_selected():
            self._refresh_status()
            self._emit_state_changed()

    def _rotate_selection(self, angle: float | None = None) -> None:
        if angle is None:
            try:
                angle = float(self._transform_rotate.text())
            except ValueError:
                return
        if self._canvas.rotate_selected(angle):
            self._refresh_status()
            self._emit_state_changed()

    def _scale_selection(self) -> None:
        try:
            factor = float(self._transform_scale.text()) / 100.0
        except ValueError:
            return
        if factor <= 0:
            return
        if self._canvas.scale_selected(factor):
            self._refresh_status()
            self._emit_state_changed()

    def _mirror_selection(self, axis: str) -> None:
        if self._canvas.mirror_selected(axis):
            self._refresh_status()
            self._emit_state_changed()

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
            self._refresh_status()
            self._emit_state_changed()

    def _fit_selection(self) -> None:
        self._canvas.fit_selection()

    # ── Grid settings ─────────────────────────────────────────────────────

    def _apply_grid_settings(self, *_) -> None:
        if not hasattr(self, "_canvas"):
            return
        try:
            spacing = float(self._grid_spacing.text().strip() or "1.0")
        except ValueError:
            return
        if spacing <= 0:
            return
        self._canvas.set_grid_visible(self._grid_btn.isChecked())
        self._canvas.set_grid_snap(self._snap_btn.isChecked())
        self._canvas.set_grid_spacing(spacing)
        self._emit_state_changed()

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self) -> None:
        polys = self._canvas.get_polylines_state()
        if not polys:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "The canvas is empty — draw or insert shapes first.",
            )
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export DXF",
            str(
                Path(self._settings.get("shape_output_dir", "")) / "draft.dxf"
            ),
            "DXF files (*.dxf);;All files (*)",
        )
        if not out_path:
            return
        try:
            write_polylines_dxf(polys, out_path, close=True)
            self._panel_status.setText(f"Exported → {Path(out_path).name}")
            self._panel_status.setStyleSheet("color: #3fb950; font-size: 11px;")
            self._last_out_path = out_path
            self._reveal_btn.setEnabled(True)
        except Exception as exc:
            self._panel_status.setText(f"Export error: {exc}")
            self._panel_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _reveal_in_finder(self) -> None:
        if self._last_out_path:
            p = Path(self._last_out_path)
            if not p.exists():
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"File no longer exists:\n{self._last_out_path}",
                )
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    # ── Status ────────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        summary = self._canvas.get_status_summary()
        n = self._canvas.poly_count
        readiness = "Ready" if n else "Empty canvas"
        tone = "accent" if n else "warn"
        zoom = (
            self._canvas.get_zoom_percent()
            if hasattr(self._canvas, "get_zoom_percent")
            else 100
        )
        cursor = (
            self._canvas.get_cursor_world_pos()
            if hasattr(self._canvas, "get_cursor_world_pos")
            else None
        )
        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._canvas.sel_count,
            object_count=n,
            precision_text=str(summary["precision"]),
            readiness_text=readiness,
            readiness_tone=tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        if hasattr(self, "_object_browser"):
            self._object_browser.set_objects(
                self._canvas.get_polylines_state(),
                self._canvas.get_selection_indices(),
            )

    def _emit_state_changed(self) -> None:
        if not self._suspend_state:
            self.stateChanged.emit()

    # ── Workspace persistence ─────────────────────────────────────────────

    def get_workspace_state(self) -> dict:
        return {
            "canvas_polys": self._canvas.get_polylines_state(),
            "canvas_view": self._canvas.get_view_state(),
            "grid_visible": self._grid_btn.isChecked(),
            "snap_enabled": self._snap_btn.isChecked(),
            "grid_spacing": self._grid_spacing.text(),
            "transform_rotate": self._transform_rotate.text(),
            "transform_scale": self._transform_scale.text(),
            "align_mode": self._align_combo.currentText(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state = True
        state = state or {}
        polys = state.get("canvas_polys", [])
        if polys:
            self._canvas.set_polylines_state(polys, fit=True)
        if state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
        self._grid_btn.setChecked(bool(state.get("grid_visible", True)))
        self._snap_btn.setChecked(bool(state.get("snap_enabled", False)))
        if "grid_spacing" in state:
            self._grid_spacing.setText(str(state["grid_spacing"]))
        if "transform_rotate" in state:
            self._transform_rotate.setText(str(state["transform_rotate"]))
        if "transform_scale" in state:
            self._transform_scale.setText(str(state["transform_scale"]))
        if "align_mode" in state:
            self._align_combo.setCurrentText(str(state["align_mode"]))
        self._apply_grid_settings()
        self._suspend_state = False
        self._refresh_status()

    def clear_workspace_state(self) -> None:
        self._suspend_state = True
        self._canvas.load([])
        self._grid_btn.setChecked(True)
        self._snap_btn.setChecked(False)
        self._grid_spacing.setText("1.0")
        self._transform_rotate.setText("15")
        self._transform_scale.setText("100")
        self._align_combo.setCurrentIndex(0)
        self._panel_status.setText("")
        self._panel_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self._reveal_btn.setEnabled(False)
        self._last_out_path = None
        self._apply_grid_settings()
        self._suspend_state = False
        self._refresh_status()

    def get_preset_state(self) -> dict[str, dict]:
        return {name: dict(p) for name, p in self._presets.items()}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        self._presets = {name: dict(p) for name, p in presets.items()}
