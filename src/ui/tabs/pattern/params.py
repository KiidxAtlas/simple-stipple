"""Pattern parameter widget builder and parameter collector.

Provides:
  build_param_widget(tab, pattern_name, schedule_fn) -> QWidget
  collect_pattern_params(tab, pattern) -> dict
  build_tile_library_widget(tab, schedule_fn) -> QWidget
  build_halftone_widget(tab, schedule_fn) -> QWidget
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM
from src.ui.tabs.pattern._spec import PARAM_SPECS

# ── Internal widget helpers ───────────────────────────────────────────────────


def _param_entry(
    grid: QGridLayout, row: int, label: str, default: str, width: int = 80
) -> QLineEdit:
    grid.addWidget(QLabel(label), row, 0)
    e = QLineEdit(default)
    e.setFixedWidth(width)
    grid.addWidget(e, row, 1)
    return e


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {DIM}; font-size: 9px;")
    return lbl


# ── Generic declarative param builder ────────────────────────────────────────


def build_param_widget(tab: Any, pattern_name: str, schedule_fn) -> QWidget:
    """Build a param widget from the declarative PARAM_SPECS and attach fields to tab."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)

    specs = PARAM_SPECS.get(pattern_name, [])
    grid_row = 0

    for spec in specs:
        if spec.kind in ("float", "int"):
            entry = _param_entry(g, grid_row, spec.label, spec.default)
            entry.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, entry)
            entry.textChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

        elif spec.kind == "checkbox":
            cb = QCheckBox(spec.label)
            cb.setChecked(False)
            cb.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, cb)
            g.addWidget(cb, grid_row, 0, 1, 2)
            cb.stateChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

        elif spec.kind == "combobox":
            g.addWidget(QLabel(spec.label), grid_row, 0)
            combo = QComboBox()
            combo.setFixedWidth(120)
            combo.addItems(spec.items)
            idx = combo.findText(spec.default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, combo)
            g.addWidget(combo, grid_row, 1)
            combo.currentTextChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

    return w


# ── Special complex widgets ───────────────────────────────────────────────────


def build_tile_library_widget(tab: Any, schedule_fn) -> QWidget:
    """Build the tile-library pattern param widget and attach fields to tab."""
    w = QWidget()
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    folder_lbl = QLabel("Pattern library")
    folder_lbl.setStyleSheet(f"color: {DIM}; font-size: 11px;")
    vl.addWidget(folder_lbl)
    tab._tile_library_folder_lbl = QLabel("No pattern folder selected")
    tab._tile_library_folder_lbl.setWordWrap(True)
    tab._tile_library_folder_lbl.setStyleSheet(f"color: {DIM};")
    vl.addWidget(tab._tile_library_folder_lbl)
    btn_row = QHBoxLayout()
    choose_btn = QPushButton("Choose Folder")
    choose_btn.setToolTip("Select a folder containing DXF tile patterns")
    choose_btn.clicked.connect(tab._choose_pattern_library_dir)
    btn_row.addWidget(choose_btn)
    refresh_btn = QPushButton("Refresh")
    refresh_btn.setToolTip("Rescan the pattern folder for new or changed tiles")
    refresh_btn.clicked.connect(tab._refresh_pattern_library)
    btn_row.addWidget(refresh_btn)
    vl.addLayout(btn_row)
    tile_lbl = QLabel("Selected tile")
    tile_lbl.setStyleSheet(f"color: {DIM}; font-size: 11px;")
    vl.addWidget(tile_lbl)
    tab._tile_name_lbl = QLabel("Choose a tile pattern from the list")
    tab._tile_name_lbl.setWordWrap(True)
    vl.addWidget(tab._tile_name_lbl)
    g = QGridLayout()
    tab._tile_gap = _param_entry(g, 0, "Gap (mm)", "0.5")
    tab._tile_gap.setToolTip("Spacing between repeated tile instances")
    tab._tile_angle = _param_entry(g, 1, "Tile rotation (°)", "0")
    tab._tile_angle.setToolTip("Rotate the tile pattern by this angle")
    tab._tile_interlock_cb = QCheckBox("Interlock rows")
    tab._tile_interlock_cb.setToolTip(
        "Stagger alternate rows by half a tile width like a brick bond"
    )
    tab._tile_gap.textChanged.connect(schedule_fn)
    tab._tile_angle.textChanged.connect(schedule_fn)
    tab._tile_interlock_cb.stateChanged.connect(schedule_fn)
    vl.addLayout(g)
    vl.addWidget(tab._tile_interlock_cb)
    hint = QLabel("DXF files in the folder appear in the pattern list as Tile: Name")
    hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
    vl.addWidget(hint)
    tab._update_tile_library_panel()
    return w


def build_halftone_widget(tab: Any, schedule_fn) -> QWidget:
    """Build the image-halftone pattern param widget and attach fields to tab."""
    w = QWidget()
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    pick_row = QHBoxLayout()
    tab._htone_img_edit = QLineEdit()
    tab._htone_img_edit.setPlaceholderText("Select image (jpg/png)…")
    tab._htone_img_edit.setToolTip("Source image whose brightness drives cell sizes")
    pick_row.addWidget(tab._htone_img_edit, stretch=1)
    browse_btn = QPushButton("Browse")
    browse_btn.setFixedWidth(64)
    browse_btn.setToolTip("Browse for a halftone source image")
    browse_btn.clicked.connect(tab._browse_halftone_image)
    pick_row.addWidget(browse_btn)
    vl.addLayout(pick_row)
    g = QGridLayout()
    tab._htone_r_min = _param_entry(g, 0, "Cell min (mm)", "0.3")
    tab._htone_r_min.setToolTip("Smallest cell size (brightest areas)")
    tab._htone_r_max = _param_entry(g, 1, "Cell max (mm)", "1.8")
    tab._htone_r_max.setToolTip("Largest cell size (darkest areas)")
    tab._htone_spacing = _param_entry(g, 2, "Grid spacing (mm)", "2.2")
    tab._htone_spacing.setToolTip("Centre-to-centre distance of the halftone grid")
    tab._htone_r_min.textChanged.connect(schedule_fn)
    tab._htone_r_max.textChanged.connect(schedule_fn)
    tab._htone_spacing.textChanged.connect(schedule_fn)
    vl.addLayout(g)
    tab._htone_invert = QCheckBox("Invert  (dark → small cells)")
    tab._htone_invert.setToolTip("Swap which tones get large vs. small cells")
    tab._htone_invert.stateChanged.connect(schedule_fn)
    vl.addWidget(tab._htone_invert)
    return w


# ── Parameter collection ──────────────────────────────────────────────────────


def collect_pattern_params(tab: Any, pattern: str) -> dict:
    """Collect validated generator parameters for the selected pattern."""
    params: dict
    if pattern == "Honeycomb":
        params = {
            "r": tab._parse_float_field(
                tab._hex_r, "Hex size", minimum=0.001, maximum=1000
            ),
            "gap": tab._parse_float_field(
                tab._hex_gap, "Gap", minimum=0.0, maximum=1000
            ),
        }
    elif pattern == "Gradient Honeycomb":
        params = {
            "r_min": tab._parse_float_field(
                tab._grad_r_min, "Min size", minimum=0.0, maximum=1000
            ),
            "r_max": tab._parse_float_field(
                tab._grad_r_max, "Max size", minimum=0.001, maximum=1000
            ),
            "gap": tab._parse_float_field(
                tab._grad_gap, "Gap", minimum=0.0, maximum=1000
            ),
            "angle": tab._parse_float_field(tab._grad_angle, "Direction"),
        }
    elif pattern == "Basketweave":
        params = {
            "strip_w": tab._parse_float_field(
                tab._basket_strip_w, "Strip width", minimum=0.001, maximum=1000
            ),
            "strip_l": tab._parse_float_field(
                tab._basket_strip_l, "Strip length", minimum=0.001, maximum=1000
            ),
            "gap": tab._parse_float_field(
                tab._basket_gap, "Gap", minimum=0.0, maximum=1000
            ),
        }
    elif pattern == "Braid":
        params = {
            "strip_width": tab._parse_float_field(
                tab._braid_strip_w, "Strip width", minimum=0.001, maximum=1000
            ),
            "spacing": tab._parse_float_field(
                tab._braid_spacing, "Spacing", minimum=0.001, maximum=1000
            ),
        }
    elif pattern == "Fish Scale":
        params = {
            "sw": tab._parse_float_field(
                tab._fish_w, "Scale width", minimum=0.001, maximum=1000
            ),
            "sh": tab._parse_float_field(
                tab._fish_h, "Scale height", minimum=0.001, maximum=1000
            ),
        }
    elif pattern == "Stipple Dots":
        params = {
            "r": tab._parse_float_field(
                tab._stip_r, "Dot radius", minimum=0.001, maximum=100
            ),
            "spacing": tab._parse_float_field(
                tab._stip_spacing, "Spacing", minimum=0.001, maximum=1000
            ),
            "interlaced": tab._stip_layout.isChecked(),
        }
    elif pattern == "Brick":
        params = {
            "brick_w": tab._parse_float_field(
                tab._brick_w_e, "Brick width", minimum=0.001, maximum=1000
            ),
            "brick_h": tab._parse_float_field(
                tab._brick_h_e, "Brick height", minimum=0.001, maximum=1000
            ),
            "gap": tab._parse_float_field(
                tab._brick_gap, "Gap", minimum=0.0, maximum=1000
            ),
        }
    elif pattern == "Diagonal Lines":
        params = {
            "spacing": tab._parse_float_field(
                tab._diag_spacing, "Line spacing", minimum=0.001, maximum=1000
            ),
            "angle": tab._parse_float_field(tab._diag_angle, "Angle"),
        }
    elif pattern == "Square Grid":
        params = {
            "spacing": tab._parse_float_field(
                tab._sq_spacing, "Grid spacing", minimum=0.001, maximum=1000
            )
        }
    elif pattern == "Mesh":
        params = {
            "r": tab._parse_float_field(
                tab._mesh_r, "Circle radius", minimum=0.001, maximum=100
            ),
            "spacing": tab._parse_float_field(
                tab._mesh_spacing, "Grid spacing", minimum=0.001, maximum=1000
            ),
        }
    elif pattern == "Concentric Rings":
        params = {
            "spacing": tab._parse_float_field(
                tab._conc_spacing, "Ring spacing", minimum=0.1, maximum=500
            )
        }
    elif pattern == "Wave Fill":
        params = {
            "spacing": tab._parse_float_field(
                tab._wave_spacing, "Row spacing", minimum=0.001, maximum=1000
            ),
            "amplitude": tab._parse_float_field(
                tab._wave_amplitude, "Amplitude", maximum=500
            ),
            "wavelength": tab._parse_float_field(
                tab._wave_wavelength, "Wavelength", minimum=0.1, maximum=1000
            ),
        }
    elif pattern == "Sunburst":
        params = {
            "spacing_deg": tab._parse_float_field(
                tab._sunburst_spacing, "Spoke spacing", minimum=0.5, maximum=180
            )
        }
    elif pattern == "Voronoi":
        params = {
            "n_cells": tab._parse_int_field(
                tab._vor_cells, "Cell count", minimum=2, maximum=10000
            ),
            "gap": tab._parse_float_field(
                tab._vor_gap, "Gap", minimum=0.0, maximum=1000
            ),
            "seed": tab._parse_int_field(tab._vor_seed, "Seed"),
        }
    elif pattern == "Penrose Tiling":
        params = {
            "scale": tab._parse_float_field(
                tab._penrose_scale, "Tile size", minimum=0.1, maximum=1000
            ),
            "gap": tab._parse_float_field(
                tab._penrose_gap, "Gap", minimum=0.0, maximum=1000
            ),
        }
    elif pattern == "Topographic":
        params = {
            "spacing": tab._parse_float_field(
                tab._topo_spacing, "Contour spacing", minimum=0.1, maximum=500
            )
        }
    elif pattern == "Hilbert Curve":
        params = {
            "order": tab._parse_int_field(
                tab._hilbert_order, "Order", minimum=1, maximum=8
            ),
            "margin": tab._parse_float_field(
                tab._hilbert_margin, "Margin", minimum=0.0, maximum=1000
            ),
        }
    elif pattern == "Reaction Diffuse":
        params = {
            "pattern": tab._rd_pattern.currentText(),
            "cell": tab._parse_float_field(
                tab._rd_cell, "Cell", minimum=0.1, maximum=10000
            ),
            "iters": tab._parse_int_field(
                tab._rd_iters, "Iterations", minimum=10, maximum=8000
            ),
            "threshold": tab._parse_float_field(
                tab._rd_threshold, "Threshold", minimum=0.01, maximum=0.99
            ),
            "seed": tab._parse_int_field(tab._rd_seed, "Seed"),
        }
    elif pattern == "Celtic Knot":
        params = {
            "cell_size": tab._parse_float_field(
                tab._celtic_cell, "Cell size", minimum=0.5, maximum=1000
            ),
            "line_width": tab._parse_float_field(
                tab._celtic_line_w, "Line width", minimum=0.1, maximum=100
            ),
            "gap": tab._parse_float_field(
                tab._celtic_gap, "Gap", minimum=0.0, maximum=100
            ),
        }
    elif pattern == "Lissajous":
        params = {
            "freq_x": tab._parse_int_field(
                tab._liss_freq_x, "Freq X", minimum=1, maximum=20
            ),
            "freq_y": tab._parse_int_field(
                tab._liss_freq_y, "Freq Y", minimum=1, maximum=20
            ),
            "spacing": tab._parse_float_field(
                tab._liss_spacing, "Row spacing", minimum=0.1, maximum=1000
            ),
            "amplitude": tab._parse_float_field(
                tab._liss_amplitude, "Amplitude", minimum=0.1, maximum=1000
            ),
        }
    elif pattern == "Golden Spiral":
        params = {
            "turns": tab._parse_float_field(
                tab._golden_turns, "Turns", minimum=1.0, maximum=30.0
            ),
            "spacing_mm": tab._parse_float_field(
                tab._golden_spacing, "Spacing hint", minimum=0.1, maximum=100.0
            ),
            "direction": tab._golden_dir.currentText(),
        }
    elif pattern == "Rose Curve":
        params = {
            "petals": tab._parse_int_field(
                tab._rose_petals, "Petals", minimum=2, maximum=24
            ),
            "copies": tab._parse_int_field(
                tab._rose_copies, "Copies", minimum=1, maximum=8
            ),
            "margin_mm": tab._parse_float_field(
                tab._rose_margin, "Margin", minimum=0.0, maximum=1000.0
            ),
        }
    elif tab._is_tile_pattern(pattern):
        tile_path = tab._library_patterns.get(pattern, "")
        if not tile_path:
            raise ValueError("Selected tile pattern is unavailable.")
        params = {
            "tile_path": tile_path,
            "gap": tab._parse_float_field(
                tab._tile_gap, "Gap", minimum=0.0, maximum=1000
            ),
            "angle": tab._parse_float_field(tab._tile_angle, "Tile rotation"),
            "interlock": tab._tile_interlock_cb.isChecked(),
        }
    elif pattern == "Image Halftone":
        img_path = tab._parse_path_field(tab._htone_img_edit, "Halftone image")
        params = {
            "img_path": img_path,
            "r_min": tab._parse_float_field(
                tab._htone_r_min, "Cell min", minimum=0.0, maximum=100
            ),
            "r_max": tab._parse_float_field(
                tab._htone_r_max, "Cell max", minimum=0.001, maximum=100
            ),
            "spacing": tab._parse_float_field(
                tab._htone_spacing, "Grid spacing", minimum=0.001, maximum=1000
            ),
            "invert": tab._htone_invert.isChecked(),
        }
    else:
        raise ValueError(f"Pattern '{pattern}' is no longer available.")

    params["rotation"] = tab._parse_float_field(
        tab._pattern_rotation, "Pattern rotation"
    )
    return params
