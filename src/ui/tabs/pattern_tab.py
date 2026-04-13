"""Pattern Generator tab."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, QUrl, Signal
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
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, PATTERNS, SEL
from src.core.document_graph import DocumentGraph
from src.core.document_migration import graph_from_polylines, polylines_from_graph
from src.core.dxf_io import (
    load_dxf_polylines,
    polylines_to_outline,
    write_polylines_dxf,
)
from src.core.generators import (
    apply_interlace,
    gen_basketweave,
    gen_brick,
    gen_celtic_knot,
    gen_concentric_rings,
    gen_custom_tile,
    gen_diagonal_lines,
    gen_diamond_checkering,
    gen_fish_scale,
    gen_gradient_honeycomb,
    gen_honeycomb,
    gen_image_halftone,
    gen_lissajous,
    gen_moroccan_zellige,
    gen_penrose_tiling,
    gen_spiral,
    gen_square_grid,
    gen_stipple_dots,
    gen_stipple_interlaced,
    gen_sunburst,
    gen_topographic,
    gen_tri_weave,
    gen_triangle_grid,
    gen_voronoi,
    gen_wave_fill,
)
from src.settings import save_settings
from src.ui.action_maps import PATTERN_ACTION_MAP
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

ACTION_MAP = PATTERN_ACTION_MAP


def _param_entry(
    grid: QGridLayout, row: int, label: str, default: str, width: int = 80
) -> QLineEdit:
    grid.addWidget(QLabel(label), row, 0)
    e = QLineEdit(default)
    e.setFixedWidth(width)
    grid.addWidget(e, row, 1)
    return e


class PatternTab(QWidget):
    _gen_done = Signal(object)  # (count, name, polys)
    _gen_error = Signal(str)
    _preview_done = Signal(object)  # (display_polys, count)
    _preview_error = Signal(str)
    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

        # Runtime state
        self._orig_polys: list[list[tuple[float, float]]] = []
        self._edit_polys: list[list[tuple[float, float]]] = []
        self._orig_w: float = 0.0
        self._orig_h: float = 0.0
        self._ar_locked: bool = True
        self._updating_dims: bool = False
        self._preview_running: bool = False
        self._preview_pending: bool = False
        self._cancel_event = threading.Event()
        self._last_out_path: str | None = None
        self._suspend_state_changes: bool = False
        self._presets: dict[str, dict] = dict(self._settings.get("pattern_presets", {}))
        self._base_patterns: list[str] = list(PATTERNS)
        self._library_patterns: dict[str, str] = {}

        self._showing_preview: bool = False
        self._preview_polys_cache: list[list[tuple[float, float]]] = []

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._start_preview_thread)

        self._gen_done.connect(self._handle_gen_done)
        self._gen_error.connect(self._handle_gen_error)
        self._preview_done.connect(self._handle_preview_done)
        self._preview_error.connect(self._handle_preview_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)

        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        self._left_panel = _sidebar_panel(left_w, min_width=320, max_width=370)
        self._splitter = _content_splitter(
            self._left_panel,
            right_w,
            sizes=(320, 950),
        )
        root.addWidget(self._splitter)

        self._build_left(left)
        self._build_right(right)

        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith('.dxf'):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.dxf'):
                self._dxf_edit.setText(path)
                self._load_dxf(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Source")

        file_row = QHBoxLayout()
        self._dxf_edit = QLineEdit()
        self._dxf_edit.setPlaceholderText("Select .dxf…")
        self._dxf_edit.setToolTip("Path to a DXF outline file (drag-and-drop supported)")
        file_row.addWidget(self._dxf_edit, stretch=1)
        self._recent_btn = QPushButton("Recent ▾")
        self._recent_btn.setFixedWidth(76)
        self._recent_btn.setToolTip("Pick from recently opened DXF files")
        self._recent_btn.clicked.connect(self._show_recent_menu)
        file_row.addWidget(self._recent_btn)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(72)
        browse_btn.setToolTip("Browse for a DXF outline file on disk")
        browse_btn.clicked.connect(self._browse_dxf)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        reload_btn = QPushButton("↺  Reload")
        reload_btn.setMinimumHeight(28)
        reload_btn.setToolTip("Re-read the current DXF file from disk")
        reload_btn.clicked.connect(self._reload_dxf)
        layout.addWidget(reload_btn)

        scale_content = QWidget()
        scale_layout = QVBoxLayout(scale_content)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(8)

        orig_row = QHBoxLayout()
        orig_row.addWidget(QLabel("Original:"))
        self._orig_dims_label = QLabel("—")
        self._orig_dims_label.setStyleSheet(f"color: {DIM};")
        orig_row.addWidget(self._orig_dims_label)
        orig_row.addStretch()
        scale_layout.addLayout(orig_row)

        dims_g = QGridLayout()
        dims_g.addWidget(QLabel("Width (mm)"), 0, 0)
        self._scale_w = QLineEdit()
        self._scale_w.setFixedWidth(80)
        self._scale_w.setPlaceholderText("auto")
        self._scale_w.setToolTip("Target width of the outline in millimetres")
        self._scale_w.textChanged.connect(self._on_scale_w_changed)
        self._scale_w.textChanged.connect(self._schedule_preview)
        dims_g.addWidget(self._scale_w, 0, 1)
        dims_g.addWidget(QLabel("Height (mm)"), 1, 0)
        self._scale_h = QLineEdit()
        self._scale_h.setFixedWidth(80)
        self._scale_h.setPlaceholderText("auto")
        self._scale_h.setToolTip("Target height of the outline in millimetres")
        self._scale_h.textChanged.connect(self._on_scale_h_changed)
        self._scale_h.textChanged.connect(self._schedule_preview)
        dims_g.addWidget(self._scale_h, 1, 1)
        scale_layout.addLayout(dims_g)

        self._ar_cb = QCheckBox("Lock aspect ratio")
        self._ar_cb.setChecked(True)
        self._ar_cb.setToolTip("Keep width and height proportional when resizing")
        self._ar_cb.stateChanged.connect(self._on_ar_toggle)
        scale_layout.addWidget(self._ar_cb)
        layout.addWidget(
            CollapsibleSection("Scale & Outline", scale_content, expanded=True)
        )

        fill_content = QWidget()
        fill_layout = QVBoxLayout(fill_content)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        fill_layout.setSpacing(8)
        self._pattern_combo = QComboBox()
        self._pattern_combo.setToolTip(
            "Choose the fill pattern to apply inside the outline"
        )
        self._refresh_pattern_choices()
        self._pattern_combo.currentTextChanged.connect(self._switch_pattern)
        fill_layout.addWidget(self._pattern_combo)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setToolTip("Saved parameter presets for the current pattern")
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
        fill_layout.addLayout(preset_row)
        self._refresh_preset_combo()

        # Global pattern modifiers
        self._interlace_cb = QCheckBox("Interlace pattern")
        self._interlace_cb.setChecked(False)
        self._interlace_cb.setToolTip(
            "Apply offset grid interlacing to any pattern for tessellating effect"
        )
        self._interlace_cb.stateChanged.connect(self._schedule_preview)
        fill_layout.addWidget(self._interlace_cb)

        # Pattern param panels (stacked manually — show/hide)
        self._honeycomb_w = self._make_honeycomb_params()
        self._gradient_w = self._make_gradient_params()
        self._checkering_w = self._make_checkering_params()
        self._basketweave_w = self._make_basketweave_params()
        self._fishscale_w = self._make_fishscale_params()
        self._stipple_w = self._make_stipple_params()
        self._brick_w = self._make_brick_params()
        self._diagonal_w = self._make_diagonal_lines_params()
        self._square_grid_w = self._make_square_grid_params()
        self._concentric_w = self._make_concentric_rings_params()
        self._wave_w = self._make_wave_fill_params()
        self._sunburst_w = self._make_sunburst_params()
        self._voronoi_w = self._make_voronoi_params()
        self._triangle_w = self._make_triangle_grid_params()
        self._penrose_w = self._make_penrose_params()
        self._spiral_w = self._make_spiral_params()
        self._celtic_w = self._make_celtic_knot_params()
        self._lissajous_w = self._make_lissajous_params()
        self._zellige_w = self._make_zellige_params()
        self._tri_weave_w = self._make_tri_weave_params()
        self._topographic_w = self._make_topographic_params()
        self._tile_library_w = self._make_tile_library_params()
        self._halftone_w = self._make_halftone_params()

        self._pattern_widgets = [
            self._honeycomb_w,
            self._gradient_w,
            self._checkering_w,
            self._basketweave_w,
            self._fishscale_w,
            self._stipple_w,
            self._brick_w,
            self._diagonal_w,
            self._square_grid_w,
            self._concentric_w,
            self._wave_w,
            self._sunburst_w,
            self._voronoi_w,
            self._triangle_w,
            self._penrose_w,
            self._spiral_w,
            self._celtic_w,
            self._lissajous_w,
            self._zellige_w,
            self._tri_weave_w,
            self._topographic_w,
            self._tile_library_w,
            self._halftone_w,
        ]
        for w in self._pattern_widgets:
            fill_layout.addWidget(w)
            w.hide()

        self._include_border_cb = QCheckBox("Include border on separate layer")
        self._include_border_cb.setToolTip(
            "Writes the outline on a 'BORDER' DXF layer so your laser\n"
            "program can treat it separately from the pattern fill."
        )
        fill_layout.addWidget(self._include_border_cb)
        layout.addWidget(
            CollapsibleSection("Fill Parameters", fill_content, expanded=True)
        )

        # ── Export ───────────────────────────────────────────────────────────
        _section_label(layout, "Export")

        self._gen_btn = QPushButton("Export DXF")
        self._gen_btn.setMinimumHeight(38)
        self._gen_btn.setToolTip("Generate the pattern fill and save as a DXF file")
        self._gen_btn.setProperty("role", "primary")
        self._gen_btn.clicked.connect(self._generate)
        layout.addWidget(self._gen_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {DIM};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the exported file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_in_finder)
        layout.addWidget(self._reveal_btn)

        layout.addStretch()

    # ── Pattern param builders ────────────────────────────────────────────────

    def _make_honeycomb_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._hex_r = _param_entry(g, 0, "Hex size (mm)", "1.75")
        self._hex_r.setToolTip("Radius of each hexagonal cell")
        self._hex_gap = _param_entry(g, 1, "Gap (mm)", "0.5")
        self._hex_gap.setToolTip("Spacing between adjacent hexagons")
        self._hex_r.textChanged.connect(self._schedule_preview)
        self._hex_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_gradient_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._grad_r_min = _param_entry(g, 0, "Min size (mm)", "0.8")
        self._grad_r_min.setToolTip("Smallest hex cell size at one end of the gradient")
        self._grad_r_max = _param_entry(g, 1, "Max size (mm)", "2.5")
        self._grad_r_max.setToolTip("Largest hex cell size at the other end")
        self._grad_gap = _param_entry(g, 2, "Gap (mm)", "0.5")
        self._grad_gap.setToolTip("Spacing between hexagons")
        self._grad_angle = _param_entry(g, 3, "Direction (°)", "0")
        self._grad_angle.setToolTip("Gradient direction in degrees (0 = left to right)")
        for e in (self._grad_r_min, self._grad_r_max, self._grad_gap, self._grad_angle):
            e.textChanged.connect(self._schedule_preview)
        hint = QLabel("0° = left→right  ·90° = vertical")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 4, 0, 1, 2)
        return w

    def _make_checkering_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._check_cell = _param_entry(g, 0, "Cell size (mm)", "2.0")
        self._check_cell.setToolTip("Side length of each diamond cell")
        self._check_gap = _param_entry(g, 1, "Gap (mm)", "0.15")
        self._check_gap.setToolTip("Gap between adjacent diamond cells")
        self._check_cell.textChanged.connect(self._schedule_preview)
        self._check_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_basketweave_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._basket_strip_w = _param_entry(g, 0, "Strip width (mm)", "2.0")
        self._basket_strip_w.setToolTip("Width of each woven strip")
        self._basket_strip_l = _param_entry(g, 1, "Strip length (mm)", "8.0")
        self._basket_strip_l.setToolTip("Length of each woven strip")
        self._basket_gap = _param_entry(g, 2, "Gap (mm)", "0.2")
        self._basket_gap.setToolTip("Gap between woven strips")
        self._basket_strip_w.textChanged.connect(self._schedule_preview)
        self._basket_strip_l.textChanged.connect(self._schedule_preview)
        self._basket_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_voronoi_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._vor_cells = _param_entry(g, 0, "Cell count", "60")
        self._vor_cells.setToolTip("Number of random Voronoi cells to generate")
        self._vor_gap = _param_entry(g, 1, "Gap (mm)", "0.15")
        self._vor_gap.setToolTip("Inset distance between Voronoi cells")
        self._vor_seed = _param_entry(g, 2, "Seed", "42")
        self._vor_seed.setToolTip("Random seed for reproducible cell placement")
        self._vor_cells.textChanged.connect(self._schedule_preview)
        self._vor_gap.textChanged.connect(self._schedule_preview)
        self._vor_seed.textChanged.connect(self._schedule_preview)
        return w

    def _make_triangle_grid_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._tri_size = _param_entry(g, 0, "Side length (mm)", "3.0")
        self._tri_size.setToolTip("Side length of each triangle")
        self._tri_gap = _param_entry(g, 1, "Gap (mm)", "0.15")
        self._tri_gap.setToolTip("Gap between adjacent triangles")
        self._tri_size.textChanged.connect(self._schedule_preview)
        self._tri_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_penrose_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._penrose_scale = _param_entry(g, 0, "Tile size (mm)", "3.0")
        self._penrose_scale.setToolTip("Approximate size of each Penrose tile")
        self._penrose_gap = _param_entry(g, 1, "Gap (mm)", "0.1")
        self._penrose_gap.setToolTip("Spacing between adjacent tiles")
        self._penrose_scale.textChanged.connect(self._schedule_preview)
        self._penrose_gap.textChanged.connect(self._schedule_preview)
        hint = QLabel("Aperiodic kite-and-dart tiling (P2)")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 2, 0, 1, 2)
        return w

    def _make_spiral_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._spiral_spacing = _param_entry(g, 0, "Arm spacing (mm)", "1.5")
        self._spiral_spacing.setToolTip("Gap between successive spiral arms")
        g.addWidget(QLabel("Direction"), 1, 0)
        self._spiral_dir = QComboBox()
        self._spiral_dir.addItems(["cw", "ccw"])
        self._spiral_dir.setFixedWidth(80)
        self._spiral_dir.setToolTip(
            "Spiral winding direction (clockwise / counter-clockwise)"
        )
        g.addWidget(self._spiral_dir, 1, 1)
        self._spiral_spacing.textChanged.connect(self._schedule_preview)
        self._spiral_dir.currentTextChanged.connect(self._schedule_preview)
        return w

    def _make_celtic_knot_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._celtic_cell = _param_entry(g, 0, "Cell size (mm)", "5.0")
        self._celtic_cell.setToolTip("Grid cell size for the knot pattern")
        self._celtic_lw = _param_entry(g, 1, "Line width (mm)", "1.0")
        self._celtic_lw.setToolTip("Width of each knot band")
        self._celtic_gap = _param_entry(g, 2, "Gap (mm)", "0.3")
        self._celtic_gap.setToolTip("Gap at crossings for the over-under effect")
        self._celtic_cell.textChanged.connect(self._schedule_preview)
        self._celtic_lw.textChanged.connect(self._schedule_preview)
        self._celtic_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_lissajous_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._liss_fx = _param_entry(g, 0, "Freq X", "3")
        self._liss_fx.setToolTip("Horizontal frequency (integer)")
        self._liss_fy = _param_entry(g, 1, "Freq Y", "2")
        self._liss_fy.setToolTip("Vertical frequency (integer)")
        self._liss_spacing = _param_entry(g, 2, "Spacing (mm)", "2.0")
        self._liss_spacing.setToolTip("Vertical offset between repeated curves")
        self._liss_amp = _param_entry(g, 3, "Amplitude (mm)", "5.0")
        self._liss_amp.setToolTip("Peak amplitude of the Lissajous figure")
        self._liss_fx.textChanged.connect(self._schedule_preview)
        self._liss_fy.textChanged.connect(self._schedule_preview)
        self._liss_spacing.textChanged.connect(self._schedule_preview)
        self._liss_amp.textChanged.connect(self._schedule_preview)
        hint = QLabel("Try 3:2, 5:4, 7:6 for interesting curves")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 4, 0, 1, 2)
        return w

    def _make_zellige_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._zellige_size = _param_entry(g, 0, "Tile size (mm)", "5.0")
        self._zellige_size.setToolTip("Size of each zellige tile cell")
        self._zellige_gap = _param_entry(g, 1, "Gap (mm)", "0.15")
        self._zellige_gap.setToolTip("Spacing between star and cross tiles")
        self._zellige_size.textChanged.connect(self._schedule_preview)
        self._zellige_gap.textChanged.connect(self._schedule_preview)
        hint = QLabel("8-pointed star and cross pattern")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 2, 0, 1, 2)
        return w

    def _make_tri_weave_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._tri_weave_size = _param_entry(g, 0, "Cell size (mm)", "3.0")
        self._tri_weave_size.setToolTip("Size of each triangular cell")
        self._tri_weave_width = _param_entry(g, 1, "Stroke width (mm)", "0.3")
        self._tri_weave_width.setToolTip("Width of the diagonal strokes")
        self._tri_weave_size.textChanged.connect(self._schedule_preview)
        self._tri_weave_width.textChanged.connect(self._schedule_preview)
        hint = QLabel("Interlocking triangular weave pattern")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 2, 0, 1, 2)
        return w

    def _make_topographic_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._topo_spacing = _param_entry(g, 0, "Contour spacing (mm)", "1.5")
        self._topo_spacing.setToolTip("Distance between successive contour lines")
        self._topo_spacing.textChanged.connect(self._schedule_preview)
        hint = QLabel("Inward offset contours from the outline edge")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 1, 0, 1, 2)
        return w

    def _make_fishscale_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._fish_w = _param_entry(g, 0, "Scale width (mm)", "3.0")
        self._fish_w.setToolTip("Horizontal span of each fish-scale arc")
        self._fish_h = _param_entry(g, 1, "Scale height (mm)", "2.0")
        self._fish_h.setToolTip("Vertical height of each fish-scale arc")
        self._fish_w.textChanged.connect(self._schedule_preview)
        self._fish_h.textChanged.connect(self._schedule_preview)
        return w

    def _make_stipple_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._stip_r = _param_entry(g, 0, "Dot radius (mm)", "0.4")
        self._stip_r.setToolTip("Radius of each stipple dot")
        self._stip_spacing = _param_entry(g, 1, "Spacing (mm)", "1.2")
        self._stip_spacing.setToolTip("Centre-to-centre distance between dots")
        self._stip_layout = QCheckBox("Interlaced (offset grid)")
        self._stip_layout.setChecked(False)
        self._stip_layout.setToolTip(
            "Use interlaced offset grid instead of Poisson-disk distribution"
        )
        g.addWidget(self._stip_layout, 2, 0, 1, 2)
        self._stip_r.textChanged.connect(self._schedule_preview)
        self._stip_spacing.textChanged.connect(self._schedule_preview)
        self._stip_layout.stateChanged.connect(self._schedule_preview)
        return w

    def _make_brick_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._brick_w_e = _param_entry(g, 0, "Brick width (mm)", "4.0")
        self._brick_w_e.setToolTip("Width of each brick")
        self._brick_h_e = _param_entry(g, 1, "Brick height (mm)", "2.0")
        self._brick_h_e.setToolTip("Height of each brick")
        self._brick_gap = _param_entry(g, 2, "Gap (mm)", "0.5")
        self._brick_gap.setToolTip("Mortar gap between bricks")
        self._brick_w_e.textChanged.connect(self._schedule_preview)
        self._brick_h_e.textChanged.connect(self._schedule_preview)
        self._brick_gap.textChanged.connect(self._schedule_preview)
        return w

    def _make_diagonal_lines_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._diag_spacing = _param_entry(g, 0, "Line spacing (mm)", "1.0")
        self._diag_spacing.setToolTip("Distance between parallel diagonal lines")
        self._diag_angle = _param_entry(g, 1, "Angle (°)", "45")
        self._diag_angle.setToolTip("Angle of the diagonal lines in degrees")
        self._diag_spacing.textChanged.connect(self._schedule_preview)
        self._diag_angle.textChanged.connect(self._schedule_preview)
        return w

    def _make_square_grid_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._sq_spacing = _param_entry(g, 0, "Grid spacing (mm)", "1.0")
        self._sq_spacing.setToolTip("Distance between grid lines")
        self._sq_spacing.textChanged.connect(self._schedule_preview)
        return w

    def _make_concentric_rings_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._conc_spacing = _param_entry(g, 0, "Ring spacing (mm)", "1.5")
        self._conc_spacing.setToolTip("Distance between concentric rings")
        self._conc_spacing.textChanged.connect(self._schedule_preview)
        return w

    def _make_wave_fill_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._wave_spacing = _param_entry(g, 0, "Row spacing (mm)", "1.5")
        self._wave_spacing.setToolTip("Vertical distance between wave rows")
        self._wave_amplitude = _param_entry(g, 1, "Amplitude (mm)", "0.5")
        self._wave_amplitude.setToolTip("Peak-to-centre height of each wave")
        self._wave_wavelength = _param_entry(g, 2, "Wavelength (mm)", "3.0")
        self._wave_wavelength.setToolTip("Horizontal length of one full wave cycle")
        self._wave_spacing.textChanged.connect(self._schedule_preview)
        self._wave_amplitude.textChanged.connect(self._schedule_preview)
        self._wave_wavelength.textChanged.connect(self._schedule_preview)
        return w

    def _make_sunburst_params(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        self._sunburst_spacing = _param_entry(g, 0, "Spoke spacing (°)", "5.0")
        self._sunburst_spacing.setToolTip(
            "Angular spacing between spokes (smaller = more spokes)"
        )
        self._sunburst_spacing.textChanged.connect(self._schedule_preview)
        hint = QLabel("5° → 36 spokes  ·  10° → 18 spokes")
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        g.addWidget(hint, 1, 0, 1, 2)
        return w

    def _make_tile_library_params(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        folder_lbl = QLabel("Pattern library")
        folder_lbl.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        vl.addWidget(folder_lbl)
        self._tile_library_folder_lbl = QLabel("No pattern folder selected")
        self._tile_library_folder_lbl.setWordWrap(True)
        self._tile_library_folder_lbl.setStyleSheet(f"color: {DIM};")
        vl.addWidget(self._tile_library_folder_lbl)
        btn_row = QHBoxLayout()
        choose_btn = QPushButton("Choose Folder")
        choose_btn.setToolTip("Select a folder containing DXF tile patterns")
        choose_btn.clicked.connect(self._choose_pattern_library_dir)
        btn_row.addWidget(choose_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setToolTip("Rescan the pattern folder for new or changed tiles")
        refresh_btn.clicked.connect(self._refresh_pattern_library)
        btn_row.addWidget(refresh_btn)
        vl.addLayout(btn_row)
        tile_lbl = QLabel("Selected tile")
        tile_lbl.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        vl.addWidget(tile_lbl)
        self._tile_name_lbl = QLabel("Choose a tile pattern from the list")
        self._tile_name_lbl.setWordWrap(True)
        vl.addWidget(self._tile_name_lbl)
        g = QGridLayout()
        self._tile_gap = _param_entry(g, 0, "Gap (mm)", "0.5")
        self._tile_gap.setToolTip("Spacing between repeated tile instances")
        self._tile_angle = _param_entry(g, 1, "Tile rotation (°)", "0")
        self._tile_angle.setToolTip("Rotate the tile pattern by this angle")
        self._tile_gap.textChanged.connect(self._schedule_preview)
        self._tile_angle.textChanged.connect(self._schedule_preview)
        vl.addLayout(g)
        hint = QLabel(
            "DXF files in the folder appear in the pattern list as Tile: Name"
        )
        hint.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        vl.addWidget(hint)
        self._update_tile_library_panel()
        return w

    def _make_halftone_params(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        pick_row = QHBoxLayout()
        self._htone_img_edit = QLineEdit()
        self._htone_img_edit.setPlaceholderText("Select image (jpg/png)…")
        self._htone_img_edit.setToolTip(
            "Source image whose brightness drives cell sizes"
        )
        pick_row.addWidget(self._htone_img_edit, stretch=1)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(64)
        browse_btn.setToolTip("Browse for a halftone source image")
        browse_btn.clicked.connect(self._browse_halftone_image)
        pick_row.addWidget(browse_btn)
        vl.addLayout(pick_row)
        g = QGridLayout()
        self._htone_r_min = _param_entry(g, 0, "Cell min (mm)", "0.3")
        self._htone_r_min.setToolTip("Smallest cell size (brightest areas)")
        self._htone_r_max = _param_entry(g, 1, "Cell max (mm)", "1.8")
        self._htone_r_max.setToolTip("Largest cell size (darkest areas)")
        self._htone_spacing = _param_entry(g, 2, "Grid spacing (mm)", "2.2")
        self._htone_spacing.setToolTip("Centre-to-centre distance of the halftone grid")
        self._htone_r_min.textChanged.connect(self._schedule_preview)
        self._htone_r_max.textChanged.connect(self._schedule_preview)
        self._htone_spacing.textChanged.connect(self._schedule_preview)
        vl.addLayout(g)
        self._htone_invert = QCheckBox("Invert  (dark → small cells)")
        self._htone_invert.setToolTip("Swap which tones get large vs. small cells")
        self._htone_invert.stateChanged.connect(self._schedule_preview)
        vl.addWidget(self._htone_invert)
        return w

    def _switch_pattern(self, value: str) -> None:
        mapping = {
            "Honeycomb": self._honeycomb_w,
            "Gradient Honeycomb": self._gradient_w,
            "Diamond Checkering": self._checkering_w,
            "Basketweave": self._basketweave_w,
            "Fish Scale": self._fishscale_w,
            "Stipple Dots": self._stipple_w,
            "Brick": self._brick_w,
            "Diagonal Lines": self._diagonal_w,
            "Square Grid": self._square_grid_w,
            "Concentric Rings": self._concentric_w,
            "Wave Fill": self._wave_w,
            "Sunburst": self._sunburst_w,
            "Voronoi": self._voronoi_w,
            "Triangle Grid": self._triangle_w,
            "Penrose Tiling": self._penrose_w,
            "Spiral": self._spiral_w,
            "Celtic Knot": self._celtic_w,
            "Lissajous": self._lissajous_w,
            "Moroccan Zellige": self._zellige_w,
            "Tri-Weave": self._tri_weave_w,
            "Topographic": self._topographic_w,
            "Image Halftone": self._halftone_w,
        }
        for w in self._pattern_widgets:
            w.hide()
        target = (
            self._tile_library_w if self._is_tile_pattern(value) else mapping.get(value)
        )
        self._update_tile_library_panel()
        if target:
            target.show()
            self._schedule_preview()

    # ── Dimension callbacks ───────────────────────────────────────────────────

    def _on_scale_w_changed(self, *_) -> None:
        if self._updating_dims or not self._ar_cb.isChecked() or self._orig_w <= 0:
            return
        try:
            w = float(self._scale_w.text())
            h = w * self._orig_h / self._orig_w
            self._updating_dims = True
            self._scale_h.setText(f"{h:.3f}")
        except ValueError:
            pass
        finally:
            self._updating_dims = False

    def _on_scale_h_changed(self, *_) -> None:
        if self._updating_dims or not self._ar_cb.isChecked() or self._orig_h <= 0:
            return
        try:
            h = float(self._scale_h.text())
            w = h * self._orig_w / self._orig_h
            self._updating_dims = True
            self._scale_w.setText(f"{w:.3f}")
        except ValueError:
            pass
        finally:
            self._updating_dims = False

    def _on_ar_toggle(self, state: int) -> None:
        self._ar_locked = bool(state)

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self, layout: QVBoxLayout) -> None:
        # Preview toggle in the toolbar
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setMinimumHeight(28)
        self._preview_btn.setToolTip(
            "Toggle between outline editing and pattern preview"
        )
        self._preview_btn.clicked.connect(self._on_preview_toggled)

        toolbar, self._mode_btns, self._sel_label = _canvas_toolbar(
            self._on_toolbar_mode,
            lambda: self._canvas.fit(),
            secondary_actions=[
                ("Select All", lambda: self._canvas.select_all()),
                ("Deselect", lambda: self._canvas.deselect_all()),
                ("Delete", self._delete_selected, "danger"),
                ("Undo", self._undo_delete),
            ],
        )
        # Insert preview toggle after the last button, before the selection label
        toolbar_layout = toolbar.layout()
        if isinstance(toolbar_layout, QHBoxLayout):
            toolbar_layout.insertWidget(toolbar_layout.count() - 1, self._preview_btn)
        layout.addWidget(toolbar)

        self._canvas_status = CanvasStatusStrip()
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_shell_layout = QVBoxLayout(canvas_shell)
        canvas_shell_layout.setContentsMargins(0, 0, 0, 0)
        canvas_shell_layout.setSpacing(8)

        self._preview_status = QLabel("Load a DXF and select a pattern")
        self._preview_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self._preview_status.setWordWrap(True)
        canvas_shell_layout.addWidget(self._preview_status)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_geometry_change,
        )
        canvas_shell_layout.addWidget(self._canvas, stretch=1)

        self._object_browser = CanvasObjectBrowser("Outline Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection_requested
        )
        self._object_browser.fitRequested.connect(self._fit_selection)
        splitter = _content_splitter(canvas_shell, self._object_browser, sizes=(860, 260))
        layout.addWidget(splitter, stretch=1)
        self._refresh_canvas_panels()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_preview_toggled(self, checked: bool) -> None:
        """Toggle between outline editing and pattern preview display."""
        if checked and self._preview_polys_cache:
            # Switch to preview view
            self._showing_preview = True
            self._canvas.load(self._preview_polys_cache)
            self._preview_status.setText(
                f"{len(self._preview_polys_cache)} shapes — preview"
            )
            self._preview_status.setStyleSheet("color: #3fb950; font-size: 11px;")
        elif checked and not self._preview_polys_cache:
            # No preview available yet
            self._preview_btn.setChecked(False)
            self._preview_status.setText(
                "No preview available — select a pattern first"
            )
            self._preview_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
            return
        else:
            # Switch back to outline editing
            self._showing_preview = False
            if self._edit_polys:
                self._canvas.load(self._edit_polys)
            self._preview_status.setText("")
        self._preview_btn.setProperty("active", self._showing_preview)
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)
        self._refresh_canvas_panels()

    def _on_sel_change(self, count: int) -> None:
        if self._showing_preview:
            return
        self._sel_label.setText(f"{count} selected" if count else "0 selected")
        self._sel_label.setStyleSheet(f"color: {SEL};" if count else f"color: {DIM};")
        # When polys are selected, use them as the clip outline; otherwise use all.
        if count:
            self._edit_polys = self._canvas.get_selected()
        else:
            self._edit_polys = self._canvas.get_active()
        self._refresh_canvas_panels()
        self._schedule_preview()

    def _browse_dxf(self) -> None:
        idir = self._settings.get("outline_dxf_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select outline DXF",
            idir,
            "DXF files (*.dxf *.Dxf *.DXF);;All files (*)",
        )
        if path:
            self._dxf_edit.setText(path)
            self._load_dxf(path)

    def _reload_dxf(self) -> None:
        path = self._dxf_edit.text().strip()
        if path:
            self._load_dxf(path)

    def _load_dxf(self, path: str) -> None:
        try:
            polys = load_dxf_polylines(path)
            self._orig_polys = polys
            self._edit_polys = list(polys)
            self._canvas.load(polys)

            all_pts = [pt for p in polys for pt in p]
            if all_pts:
                xs, ys = zip(*all_pts)
                self._orig_w = max(xs) - min(xs)
                self._orig_h = max(ys) - min(ys)
                self._orig_dims_label.setText(
                    f"{self._orig_w:.2f} × {self._orig_h:.2f} mm"
                )
                self._scale_w.blockSignals(True)
                self._scale_h.blockSignals(True)
                self._scale_w.setText(f"{self._orig_w:.3f}")
                self._scale_h.setText(f"{self._orig_h:.3f}")
                self._scale_w.blockSignals(False)
                self._scale_h.blockSignals(False)
            else:
                self._orig_w = self._orig_h = 0.0
                self._orig_dims_label.setText("—")

            self._settings["outline_dxf_dir"] = str(Path(path).parent)
            self._set_status(f"Loaded {len(polys)} polylines from {Path(path).name}")
            recent = self._settings.get("recent_dxf", [])
            if path in recent:
                recent.remove(path)
            recent.insert(0, path)
            self._settings["recent_dxf"] = recent[:8]
            save_settings(self._settings)
            self._schedule_preview()
            self._emit_state_changed()
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _delete_selected(self) -> None:
        if self._showing_preview:
            return
        n = self._canvas.delete_selected()
        if n:
            self._edit_polys = list(self._canvas.get_active())
            self._set_status(f"Deleted {n} polyline(s). Use ↩ Undo to restore.")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _undo_delete(self) -> None:
        if self._showing_preview:
            return
        if not self._canvas.undo_delete():
            self._set_status("Nothing to undo.")
        else:
            self._edit_polys = list(self._canvas.get_active())
            self._set_status("Undo: polylines restored.")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _set_active_mode_btn(self, value: str) -> None:
        v = value.lower()
        for k, b in self._mode_btns.items():
            b.setProperty("active", k.lower() == v)
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_toolbar_mode(self, value: str) -> None:
        self._set_active_mode_btn(value)
        self._canvas.set_mode(value.lower())
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._refresh_canvas_panels()

    def _on_canvas_geometry_change(self) -> None:
        if self._showing_preview:
            return
        if self._canvas.sel_count:
            self._edit_polys = self._canvas.get_selected()
        else:
            self._edit_polys = self._canvas.get_active()
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        self._canvas.set_selection(indices)
        self._refresh_canvas_panels()

    def _fit_selection(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        if self._canvas.fit_selection():
            self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        summary = self._canvas.get_status_summary()
        if self._preview_running:
            readiness_text = "Previewing"
            readiness_tone = "warn"
        elif self._showing_preview:
            readiness_text = "Preview"
            readiness_tone = "success"
        elif self._preview_polys_cache:
            readiness_text = "Preview ready"
            readiness_tone = "success"
        elif self._canvas.poly_count:
            readiness_text = "Outline ready"
            readiness_tone = "accent"
        else:
            readiness_text = "No outline"
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

    def _show_recent_menu(self) -> None:
        recent = [r for r in self._settings.get("recent_dxf", []) if Path(r).exists()]
        if not recent:
            QMessageBox.information(self, "Recent Files", "No recent DXF files.")
            return
        menu = QMenu(self)
        for path in recent:
            lbl = Path(path).name + f"  ‹{Path(path).parent.name}›"
            menu.addAction(lbl, lambda p=path: self._quick_load(p))
        menu.addSeparator()
        menu.addAction("Clear history", self._clear_recent)
        menu.popup(self._recent_btn.mapToGlobal(QPoint(0, self._recent_btn.height())))

    def _quick_load(self, path: str) -> None:
        self._dxf_edit.setText(path)
        self._load_dxf(path)

    def _clear_recent(self) -> None:
        self._settings["recent_dxf"] = []
        save_settings(self._settings)

    def _choose_pattern_library_dir(self) -> None:
        current = self._settings.get("pattern_library_dir", "")
        path = QFileDialog.getExistingDirectory(
            self, "Select pattern library folder", current
        )
        if not path:
            return
        self._settings["pattern_library_dir"] = path
        save_settings(self._settings)
        self._refresh_pattern_library()

    def _refresh_pattern_library(self) -> None:
        current = self._pattern_combo.currentText()
        self._refresh_pattern_choices(current=current)
        self._update_tile_library_panel()
        if self._is_tile_pattern(self._pattern_combo.currentText()):
            self._schedule_preview()

    def _tile_pattern_label(self, path: Path, used: set[str]) -> str:
        base = f"Tile: {path.stem.replace('_', ' ').strip() or path.stem}"
        if base not in used:
            used.add(base)
            return base
        label = f"{base} ({path.parent.name})"
        used.add(label)
        return label

    def _refresh_pattern_choices(
        self,
        current: str | None = None,
        extra_tile_path: str | None = None,
    ) -> None:
        if not hasattr(self, "_pattern_combo"):
            return
        current = self._pattern_combo.currentText() if current is None else current
        library_dir = self._settings.get("pattern_library_dir", "")
        used_labels = set(self._base_patterns)
        library_patterns: dict[str, str] = {}
        paths: list[Path] = []
        if library_dir and Path(library_dir).is_dir():
            paths.extend(sorted(Path(library_dir).glob("*.dxf")))
            paths.extend(sorted(Path(library_dir).glob("*.DXF")))
        if extra_tile_path:
            extra = Path(extra_tile_path)
            if extra.exists() and extra not in paths:
                paths.append(extra)
        self._pattern_combo.blockSignals(True)
        self._pattern_combo.clear()
        self._pattern_combo.addItems(self._base_patterns)
        for path in paths:
            label = self._tile_pattern_label(path, used_labels)
            library_patterns[label] = str(path)
            self._pattern_combo.addItem(label)
        self._library_patterns = library_patterns
        target = current if self._pattern_combo.findText(current) >= 0 else "— None —"
        self._pattern_combo.setCurrentText(target)
        self._pattern_combo.blockSignals(False)

    def _is_tile_pattern(self, pattern: str) -> bool:
        return pattern in self._library_patterns

    def _update_tile_library_panel(self) -> None:
        if not hasattr(self, "_tile_library_folder_lbl"):
            return
        folder = self._settings.get("pattern_library_dir", "")
        self._tile_library_folder_lbl.setText(folder or "No pattern folder selected")
        pattern = (
            self._pattern_combo.currentText() if hasattr(self, "_pattern_combo") else ""
        )
        if self._is_tile_pattern(pattern):
            tile_path = self._library_patterns.get(pattern, "")
            self._tile_name_lbl.setText(f"{pattern}\n{tile_path}")
        else:
            self._tile_name_lbl.setText("Choose a tile pattern from the list")

    def _browse_halftone_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select image for halftone",
            "",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*)",
        )
        if path:
            self._htone_img_edit.setText(path)
            self._schedule_preview()

    def _set_status(self, text: str, color: str = DIM) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

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

    def _current_param_text_payload(self) -> dict:
        return {
            "pattern": self._pattern_combo.currentText(),
            "scale_w": self._scale_w.text(),
            "scale_h": self._scale_h.text(),
            "ar_locked": self._ar_cb.isChecked(),
            "include_border": self._include_border_cb.isChecked(),
            "interlace": self._interlace_cb.isChecked(),
            "hex_r": self._hex_r.text(),
            "hex_gap": self._hex_gap.text(),
            "grad_r_min": self._grad_r_min.text(),
            "grad_r_max": self._grad_r_max.text(),
            "grad_gap": self._grad_gap.text(),
            "grad_angle": self._grad_angle.text(),
            "check_cell": self._check_cell.text(),
            "check_gap": self._check_gap.text(),
            "basket_strip_w": self._basket_strip_w.text(),
            "basket_strip_l": self._basket_strip_l.text(),
            "basket_gap": self._basket_gap.text(),
            "fish_w": self._fish_w.text(),
            "fish_h": self._fish_h.text(),
            "stip_r": self._stip_r.text(),
            "stip_spacing": self._stip_spacing.text(),
            "stip_layout": self._stip_layout.isChecked(),
            "brick_w": self._brick_w_e.text(),
            "brick_h": self._brick_h_e.text(),
            "brick_gap": self._brick_gap.text(),
            "diag_spacing": self._diag_spacing.text(),
            "diag_angle": self._diag_angle.text(),
            "sq_spacing": self._sq_spacing.text(),
            "conc_spacing": self._conc_spacing.text(),
            "wave_spacing": self._wave_spacing.text(),
            "wave_amplitude": self._wave_amplitude.text(),
            "wave_wavelength": self._wave_wavelength.text(),
            "sunburst_spacing": self._sunburst_spacing.text(),
            "vor_cells": self._vor_cells.text(),
            "vor_gap": self._vor_gap.text(),
            "vor_seed": self._vor_seed.text(),
            "tri_size": self._tri_size.text(),
            "tri_gap": self._tri_gap.text(),
            "tri_weave_size": self._tri_weave_size.text(),
            "tri_weave_width": self._tri_weave_width.text(),
            "tile_pattern_path": self._library_patterns.get(
                self._pattern_combo.currentText(), ""
            ),
            "tile_gap": self._tile_gap.text(),
            "tile_angle": self._tile_angle.text(),
            "htone_img_path": self._htone_img_edit.text(),
            "htone_r_min": self._htone_r_min.text(),
            "htone_r_max": self._htone_r_max.text(),
            "htone_spacing": self._htone_spacing.text(),
            "htone_invert": self._htone_invert.isChecked(),
        }

    def _apply_param_text_payload(self, payload: dict) -> None:
        values = self._current_param_text_payload()
        values.update(payload or {})
        tile_path = str(values.get("tile_pattern_path", "")).strip()
        self._refresh_pattern_choices(
            current=str(values.get("pattern", "— None —")),
            extra_tile_path=tile_path or None,
        )
        pattern = str(values.get("pattern", "— None —"))
        if (
            pattern not in self._base_patterns
            and self._pattern_combo.findText(pattern) < 0
            and tile_path
        ):
            pattern = next(
                (
                    label
                    for label, path in self._library_patterns.items()
                    if path == tile_path
                ),
                "— None —",
            )
        self._pattern_combo.setCurrentText(pattern)
        self._scale_w.setText(str(values.get("scale_w", "")))
        self._scale_h.setText(str(values.get("scale_h", "")))
        self._ar_cb.setChecked(bool(values.get("ar_locked", True)))
        self._include_border_cb.setChecked(bool(values.get("include_border", False)))
        self._interlace_cb.setChecked(bool(values.get("interlace", False)))
        self._hex_r.setText(str(values.get("hex_r", "1.75")))
        self._hex_gap.setText(str(values.get("hex_gap", "0.5")))
        self._grad_r_min.setText(str(values.get("grad_r_min", "0.8")))
        self._grad_r_max.setText(str(values.get("grad_r_max", "2.5")))
        self._grad_gap.setText(str(values.get("grad_gap", "0.5")))
        self._grad_angle.setText(str(values.get("grad_angle", "0")))
        self._check_cell.setText(str(values.get("check_cell", "2.0")))
        self._check_gap.setText(str(values.get("check_gap", "0.15")))
        self._basket_strip_w.setText(str(values.get("basket_strip_w", "2.0")))
        self._basket_strip_l.setText(str(values.get("basket_strip_l", "8.0")))
        self._basket_gap.setText(str(values.get("basket_gap", "0.2")))
        self._fish_w.setText(str(values.get("fish_w", "3.0")))
        self._fish_h.setText(str(values.get("fish_h", "2.0")))
        self._stip_r.setText(str(values.get("stip_r", "0.4")))
        self._stip_spacing.setText(str(values.get("stip_spacing", "1.2")))
        self._stip_layout.setChecked(bool(values.get("stip_layout", False)))
        self._brick_w_e.setText(str(values.get("brick_w", "4.0")))
        self._brick_h_e.setText(str(values.get("brick_h", "2.0")))
        self._brick_gap.setText(str(values.get("brick_gap", "0.5")))
        self._diag_spacing.setText(str(values.get("diag_spacing", "1.0")))
        self._diag_angle.setText(str(values.get("diag_angle", "45")))
        self._sq_spacing.setText(str(values.get("sq_spacing", "1.0")))
        self._conc_spacing.setText(str(values.get("conc_spacing", "1.5")))
        self._wave_spacing.setText(str(values.get("wave_spacing", "1.5")))
        self._wave_amplitude.setText(str(values.get("wave_amplitude", "0.5")))
        self._wave_wavelength.setText(str(values.get("wave_wavelength", "3.0")))
        self._sunburst_spacing.setText(str(values.get("sunburst_spacing", "5.0")))
        self._vor_cells.setText(str(values.get("vor_cells", "60")))
        self._vor_gap.setText(str(values.get("vor_gap", "0.15")))
        self._vor_seed.setText(str(values.get("vor_seed", "42")))
        self._tri_size.setText(str(values.get("tri_size", "3.0")))
        self._tri_gap.setText(str(values.get("tri_gap", "0.15")))
        self._tri_weave_size.setText(str(values.get("tri_weave_size", "3.0")))
        self._tri_weave_width.setText(str(values.get("tri_weave_width", "0.3")))
        self._tile_gap.setText(str(values.get("tile_gap", "0.5")))
        self._tile_angle.setText(str(values.get("tile_angle", "0")))
        self._htone_img_edit.setText(str(values.get("htone_img_path", "")))
        self._htone_r_min.setText(str(values.get("htone_r_min", "0.3")))
        self._htone_r_max.setText(str(values.get("htone_r_max", "1.8")))
        self._htone_spacing.setText(str(values.get("htone_spacing", "2.2")))
        self._htone_invert.setChecked(bool(values.get("htone_invert", False)))
        self._update_tile_library_panel()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Pattern Preset", "Preset name")
        name = name.strip()
        if not ok or not name:
            return
        self._presets[name] = self._current_param_text_payload()
        self._settings["pattern_presets"] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._preset_combo.setCurrentText(name)
        self._set_status(f"Saved preset: {name}", "#3fb950")
        self._emit_state_changed()

    def _apply_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets":
            return
        payload = self._presets.get(name)
        if not payload:
            return
        self._suspend_state_changes = True
        self._apply_param_text_payload(payload)
        self._suspend_state_changes = False
        self._set_status(f"Loaded preset: {name}", "#3fb950")
        self._schedule_preview()
        self._emit_state_changed()

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets" or name not in self._presets:
            return
        self._presets.pop(name, None)
        self._settings["pattern_presets"] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._set_status(f"Deleted preset: {name}")
        self._emit_state_changed()

    def get_preset_state(self) -> dict[str, dict]:
        return {name: dict(payload) for name, payload in self._presets.items()}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        self._presets = {name: dict(payload) for name, payload in presets.items()}
        self._refresh_preset_combo()

    def get_workspace_state(self) -> dict:
        # If showing preview, the canvas has preview polys — save edit_polys from our snapshot
        polys_to_save = (
            self._edit_polys
            if self._showing_preview
            else self._canvas.get_polylines_state()
        )
        doc_graph = graph_from_polylines(
            polys_to_save,
            layer="pattern_active",
            as_segments=False,
        )
        return {
            "dxf_path": self._dxf_edit.text(),
            "params": self._current_param_text_payload(),
            "orig_polys": self._orig_polys,
            "edit_polys": polys_to_save,
            "orig_w": self._orig_w,
            "orig_h": self._orig_h,
            "canvas_view": self._canvas.get_view_state(),
            "preview_polys": self._preview_polys_cache,
            "showing_preview": self._showing_preview,
            "document_graph": doc_graph.snapshot(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state_changes = True
        state = state or {}
        self._dxf_edit.setText(str(state.get("dxf_path", "")))
        self._apply_param_text_payload(state.get("params", {}))
        self._orig_polys = [list(poly) for poly in state.get("orig_polys", [])]
        graph_state = state.get("document_graph")
        if isinstance(graph_state, dict):
            doc_graph = DocumentGraph()
            doc_graph.restore(graph_state)
            migrated = polylines_from_graph(doc_graph, layer="pattern_active")
            if not migrated:
                migrated = polylines_from_graph(doc_graph, layer="geometry")
            edit_polys = [list(poly) for poly in migrated]
        else:
            edit_polys = [
                list(poly) for poly in state.get("edit_polys", self._orig_polys)
            ]
        self._edit_polys = [list(poly) for poly in edit_polys]
        self._orig_w = float(state.get("orig_w", 0.0))
        self._orig_h = float(state.get("orig_h", 0.0))
        if self._orig_w > 0 and self._orig_h > 0:
            self._orig_dims_label.setText(f"{self._orig_w:.2f} × {self._orig_h:.2f} mm")
        else:
            self._orig_dims_label.setText("—")
        self._preview_polys_cache = [
            list(poly) for poly in state.get("preview_polys", [])
        ]
        show_preview = bool(state.get("showing_preview", False)) and bool(
            self._preview_polys_cache
        )
        if show_preview:
            self._canvas.set_polylines_state(self._preview_polys_cache, fit=True)
            self._showing_preview = True
            self._preview_btn.setChecked(True)
            self._preview_btn.setProperty("active", True)
            self._preview_btn.style().unpolish(self._preview_btn)
            self._preview_btn.style().polish(self._preview_btn)
        else:
            self._canvas.set_polylines_state(
                self._edit_polys, fit=bool(self._edit_polys)
            )
            self._showing_preview = False
            self._preview_btn.setChecked(False)
            self._preview_btn.setProperty("active", False)
            self._preview_btn.style().unpolish(self._preview_btn)
            self._preview_btn.style().polish(self._preview_btn)
        if state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
        self._suspend_state_changes = False
        self._refresh_canvas_panels()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._set_status("")
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
            self._set_status(message, "#f85149")
            raise ValueError(message) from exc
        clear_line_edit_error(entry)
        return value

    def _parse_int_field(
        self,
        entry: QLineEdit,
        label: str,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self._parse_float_field(
            entry,
            label,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        )
        assert value is not None
        return int(value)

    def _parse_path_field(self, entry: QLineEdit, label: str) -> str:
        value = entry.text().strip()
        if not value:
            message = f"{label} is required."
            set_line_edit_error(entry, message)
            self._set_status(message, "#f85149")
            raise ValueError(message)
        clear_line_edit_error(entry)
        return value

    def _generate(self) -> None:
        if not self._edit_polys:
            QMessageBox.critical(self, "Error", "No polylines available for outline.")
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save pattern DXF",
            str(Path(self._settings.get("pattern_output_dir", "")) / "pattern.dxf"),
            "DXF files (*.dxf);;All files (*)",
        )
        if not out_path:
            return

        # Read widget values on the GUI thread (thread-safe)
        pattern = self._pattern_combo.currentText()
        include_border = self._include_border_cb.isChecked()
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern)
        except ValueError:
            return
        polys_snap = list(self._edit_polys)
        border_polys = self._apply_scale(polys_snap, *scale) if include_border else None

        self._gen_btn.setEnabled(False)
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status("Generating…")

        self._cancel_event.set()
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        threading.Thread(
            target=self._run_generate,
            args=(polys_snap, out_path, pattern, params, scale, border_polys, cancel_event),
            daemon=True,
        ).start()

    def _run_generate(
        self,
        active: list[list[tuple[float, float]]],
        out_path: str,
        pattern: str,
        params: dict,
        scale: tuple[float, float],
        border_polys: list[list[tuple[float, float]]] | None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            if cancel_event and cancel_event.is_set():
                return
            scaled = self._apply_scale(active, *scale)
            if cancel_event and cancel_event.is_set():
                return
            outline = polylines_to_outline(scaled)
            if cancel_event and cancel_event.is_set():
                return
            polys = self._gen_pattern(outline, pattern, params)
            if self._interlace_cb.isChecked():
                polys = apply_interlace(polys, spacing=params.get("spacing", 1.0))
            if cancel_event and cancel_event.is_set():
                return
            close = pattern not in (
                "Fish Scale",
                "Diagonal Lines",
                "Square Grid",
                "Concentric Rings",
                "Wave Fill",
                "Sunburst",
                "Spiral",
                "Celtic Knot",
                "Lissajous",
                "Topographic",
            )
            write_polylines_dxf(polys, out_path, close=close, border_polys=border_polys)

            count = len(polys)
            name = Path(out_path).name
            self._gen_done.emit((count, name, out_path, polys))

        except Exception as exc:
            if cancel_event and cancel_event.is_set():
                return
            self._gen_error.emit(str(exc))

    def _handle_gen_done(self, payload: tuple) -> None:
        count, name, out_path, polys = payload
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._gen_btn.setEnabled(True)
        self._set_status(f"Done — {count} shapes → {name}", "#3fb950")
        self._last_out_path = out_path
        self._reveal_btn.setEnabled(True)
        self._preview_polys_cache = list(polys)
        # Update canvas if preview is already showing; otherwise just cache
        if self._showing_preview:
            self._canvas.load(polys)
        self._preview_status.setText(f"{count} shapes exported")
        self._preview_status.setStyleSheet("color: #3fb950; font-size: 11px;")

    def _handle_gen_error(self, msg: str) -> None:
        self._preview_running = False
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._gen_btn.setEnabled(True)
        self._set_status(f"Error: {msg}", "#f85149")
        if self._preview_pending and self._edit_polys:
            self._preview_pending = False
            self._preview_timer.start(0)

    # ── Live preview ─────────────────────────────────────────────────────────

    def _schedule_preview(self, *_) -> None:
        if self._suspend_state_changes:
            return
        if self._pattern_combo.currentText() == "— None —":
            return
        if not self._edit_polys:
            return
        if self._preview_running:
            self._preview_pending = True
        self._preview_timer.start(400)
        self._emit_state_changed()

    def _start_preview_thread(self) -> None:
        if not self._edit_polys:
            return
        if self._preview_running:
            self._preview_pending = True
            return
        self._preview_running = True
        self._preview_pending = False
        self._cancel_event.set()
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        polys_snap = list(self._edit_polys)
        pattern = self._pattern_combo.currentText()
        include_border = self._include_border_cb.isChecked()
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern)
        except ValueError:
            self._preview_running = False
            return
        border_polys = self._apply_scale(polys_snap, *scale) if include_border else None
        self._preview_status.setText("Previewing…")
        self._preview_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        threading.Thread(
            target=self._compute_preview,
            args=(polys_snap, pattern, params, scale, border_polys, cancel_event),
            daemon=True,
        ).start()

    def _compute_preview(
        self,
        outline_polys,
        pattern: str,
        params: dict,
        scale: tuple[float, float],
        border_polys: list[list[tuple[float, float]]] | None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            if cancel_event and cancel_event.is_set():
                return
            scaled = self._apply_scale(outline_polys, *scale)
            if cancel_event and cancel_event.is_set():
                return
            outline = polylines_to_outline(scaled)
            if cancel_event and cancel_event.is_set():
                return
            polys = self._gen_pattern(outline, pattern, params)
            if cancel_event and cancel_event.is_set():
                return
            if self._interlace_cb.isChecked():
                polys = apply_interlace(polys, spacing=params.get("spacing", 1.0))
            if border_polys:
                display_polys = polys + border_polys
            else:
                display_polys = polys
            self._preview_done.emit((display_polys, len(polys)))
        except Exception as exc:
            if cancel_event and cancel_event.is_set():
                return
            self._preview_error.emit(str(exc))

    def _handle_preview_done(self, payload: tuple) -> None:
        display_polys, count = payload
        self._preview_running = False
        self._preview_polys_cache = list(display_polys)
        # Update canvas if preview is already showing; otherwise just cache
        if self._showing_preview:
            self._canvas.load(display_polys)
            self._preview_status.setText(f"{count} shapes — preview")
            self._preview_status.setStyleSheet("color: #3fb950; font-size: 11px;")
        self._refresh_canvas_panels()
        if self._preview_pending and self._edit_polys:
            self._preview_pending = False
            self._preview_timer.start(0)

    def _handle_preview_error(self, msg: str) -> None:
        self._preview_running = False
        self._preview_status.setText(f"Preview error: {msg}")
        self._preview_status.setStyleSheet("color: #f85149; font-size: 11px;")
        self._refresh_canvas_panels()
        if self._preview_pending and self._edit_polys:
            self._preview_pending = False
            self._preview_timer.start(0)

    # ── Param collection (GUI thread only) ───────────────────────────────────

    def _collect_scale(self) -> tuple[float, float]:
        sw = self._parse_float_field(
            self._scale_w,
            "Scale width",
            minimum=0.001,
            allow_empty=True,
        )
        sh = self._parse_float_field(
            self._scale_h,
            "Scale height",
            minimum=0.001,
            allow_empty=True,
        )
        sw = self._orig_w if sw is None else sw
        sh = self._orig_h if sh is None else sh
        return sw, sh

    def _collect_pattern_params(self, pattern: str) -> dict:
        if pattern == "Honeycomb":
            return {
                "r": self._parse_float_field(
                    self._hex_r, "Hex size", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._hex_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Gradient Honeycomb":
            return {
                "r_min": self._parse_float_field(
                    self._grad_r_min, "Min size", minimum=0.0, maximum=1000,
                ),
                "r_max": self._parse_float_field(
                    self._grad_r_max, "Max size", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._grad_gap, "Gap", minimum=0.0, maximum=1000,
                ),
                "angle": self._parse_float_field(self._grad_angle, "Direction"),
            }
        elif pattern == "Diamond Checkering":
            return {
                "cell_size": self._parse_float_field(
                    self._check_cell, "Cell size", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._check_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Basketweave":
            return {
                "strip_w": self._parse_float_field(
                    self._basket_strip_w, "Strip width", minimum=0.001, maximum=1000,
                ),
                "strip_l": self._parse_float_field(
                    self._basket_strip_l, "Strip length", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._basket_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Fish Scale":
            return {
                "sw": self._parse_float_field(
                    self._fish_w, "Scale width", minimum=0.001, maximum=1000,
                ),
                "sh": self._parse_float_field(
                    self._fish_h, "Scale height", minimum=0.001, maximum=1000,
                ),
            }
        elif pattern == "Stipple Dots":
            return {
                "r": self._parse_float_field(
                    self._stip_r,
                    "Dot radius",
                    minimum=0.001,
                    maximum=100,
                ),
                "spacing": self._parse_float_field(
                    self._stip_spacing,
                    "Spacing",
                    minimum=0.001,
                    maximum=1000,
                ),
                "interlaced": self._stip_layout.isChecked(),
            }
        elif pattern == "Brick":
            return {
                "brick_w": self._parse_float_field(
                    self._brick_w_e, "Brick width", minimum=0.001, maximum=1000,
                ),
                "brick_h": self._parse_float_field(
                    self._brick_h_e, "Brick height", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._brick_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Diagonal Lines":
            return {
                "spacing": self._parse_float_field(
                    self._diag_spacing, "Line spacing", minimum=0.001, maximum=1000,
                ),
                "angle": self._parse_float_field(self._diag_angle, "Angle"),
            }
        elif pattern == "Square Grid":
            return {
                "spacing": self._parse_float_field(
                    self._sq_spacing, "Grid spacing", minimum=0.001, maximum=1000,
                )
            }
        elif pattern == "Concentric Rings":
            return {
                "spacing": self._parse_float_field(
                    self._conc_spacing, "Ring spacing", minimum=0.1, maximum=500,
                )
            }
        elif pattern == "Wave Fill":
            return {
                "spacing": self._parse_float_field(
                    self._wave_spacing, "Row spacing", minimum=0.001, maximum=1000,
                ),
                "amplitude": self._parse_float_field(
                    self._wave_amplitude, "Amplitude", maximum=500,
                ),
                "wavelength": self._parse_float_field(
                    self._wave_wavelength, "Wavelength", minimum=0.1, maximum=1000,
                ),
            }
        elif pattern == "Sunburst":
            return {
                "spacing_deg": self._parse_float_field(
                    self._sunburst_spacing, "Spoke spacing", minimum=0.5, maximum=180,
                ),
            }
        elif pattern == "Voronoi":
            return {
                "n_cells": self._parse_int_field(
                    self._vor_cells, "Cell count", minimum=2, maximum=10000,
                ),
                "gap": self._parse_float_field(
                    self._vor_gap, "Gap", minimum=0.0, maximum=1000,
                ),
                "seed": self._parse_int_field(self._vor_seed, "Seed"),
            }
        elif pattern == "Triangle Grid":
            return {
                "size": self._parse_float_field(
                    self._tri_size, "Side length", minimum=0.001, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._tri_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Penrose Tiling":
            return {
                "scale": self._parse_float_field(
                    self._penrose_scale, "Tile size", minimum=0.1, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._penrose_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Spiral":
            return {
                "spacing": self._parse_float_field(
                    self._spiral_spacing, "Arm spacing", minimum=0.1, maximum=1000,
                ),
                "direction": self._spiral_dir.currentText(),
            }
        elif pattern == "Celtic Knot":
            return {
                "cell_size": self._parse_float_field(
                    self._celtic_cell, "Cell size", minimum=0.5, maximum=1000,
                ),
                "line_width": self._parse_float_field(
                    self._celtic_lw, "Line width", minimum=0.1, maximum=500,
                ),
                "gap": self._parse_float_field(
                    self._celtic_gap, "Gap", minimum=0.0, maximum=500,
                ),
            }
        elif pattern == "Lissajous":
            return {
                "freq_x": self._parse_int_field(
                    self._liss_fx, "Freq X", minimum=1, maximum=100,
                ),
                "freq_y": self._parse_int_field(
                    self._liss_fy, "Freq Y", minimum=1, maximum=100,
                ),
                "spacing": self._parse_float_field(
                    self._liss_spacing, "Spacing", minimum=0.1, maximum=1000,
                ),
                "amplitude": self._parse_float_field(
                    self._liss_amp, "Amplitude", minimum=0.1, maximum=1000,
                ),
            }
        elif pattern == "Moroccan Zellige":
            return {
                "size": self._parse_float_field(
                    self._zellige_size, "Tile size", minimum=0.5, maximum=1000,
                ),
                "gap": self._parse_float_field(
                    self._zellige_gap, "Gap", minimum=0.0, maximum=1000,
                ),
            }
        elif pattern == "Tri-Weave":
            return {
                "cell_size": self._parse_float_field(
                    self._tri_weave_size,
                    "Cell size",
                    minimum=0.5,
                    maximum=1000,
                ),
                "stroke_width": self._parse_float_field(
                    self._tri_weave_width,
                    "Stroke width",
                    minimum=0.01,
                    maximum=100,
                ),
            }
        elif pattern == "Topographic":
            return {
                "spacing": self._parse_float_field(
                    self._topo_spacing, "Contour spacing", minimum=0.1, maximum=500,
                ),
            }
        elif self._is_tile_pattern(pattern):
            tile_path = self._library_patterns.get(pattern, "")
            if not tile_path:
                raise ValueError("Selected tile pattern is unavailable.")
            return {
                "tile_path": tile_path,
                "gap": self._parse_float_field(
                    self._tile_gap, "Gap", minimum=0.0, maximum=1000,
                ),
                "angle": self._parse_float_field(self._tile_angle, "Tile rotation"),
            }
        else:  # Image Halftone
            img_path = self._parse_path_field(self._htone_img_edit, "Halftone image")
            return {
                "img_path": img_path,
                "r_min": self._parse_float_field(
                    self._htone_r_min, "Cell min", minimum=0.0, maximum=100,
                ),
                "r_max": self._parse_float_field(
                    self._htone_r_max, "Cell max", minimum=0.001, maximum=100,
                ),
                "spacing": self._parse_float_field(
                    self._htone_spacing, "Grid spacing", minimum=0.001, maximum=1000,
                ),
                "invert": self._htone_invert.isChecked(),
            }

    # ── Pure helpers (safe from any thread) ──────────────────────────────────

    def _apply_scale(
        self,
        polys: list[list[tuple[float, float]]],
        sw: float,
        sh: float,
    ) -> list[list[tuple[float, float]]]:
        if self._orig_w <= 0 or self._orig_h <= 0:
            return polys
        if sw <= 0 or sh <= 0:
            return polys
        sx = sw / self._orig_w
        sy = sh / self._orig_h
        if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
            return polys
        all_pts = [pt for p in polys for pt in p]
        if not all_pts:
            return polys
        xs, ys = zip(*all_pts)
        ox, oy = min(xs), min(ys)
        return [
            [(ox + (x - ox) * sx, oy + (y - oy) * sy) for x, y in poly]
            for poly in polys
        ]

    def _gen_pattern(
        self,
        outline,
        pattern: str,
        params: dict,
    ) -> list[list[tuple[float, float]]]:
        if pattern == "Honeycomb":
            return gen_honeycomb(outline, params["r"], params["gap"])
        elif pattern == "Gradient Honeycomb":
            return gen_gradient_honeycomb(
                outline,
                params["r_min"],
                params["r_max"],
                params["gap"],
                params["angle"],
            )
        elif pattern == "Diamond Checkering":
            return gen_diamond_checkering(outline, params["cell_size"], params["gap"])
        elif pattern == "Basketweave":
            return gen_basketweave(
                outline, params["strip_w"], params["strip_l"], params["gap"]
            )
        elif pattern == "Fish Scale":
            return gen_fish_scale(outline, params["sw"], params["sh"])
        elif pattern == "Stipple Dots":
            if params.get("interlaced"):
                return gen_stipple_interlaced(outline, params["r"], params["spacing"])
            else:
                return gen_stipple_dots(outline, params["r"], params["spacing"])
        elif pattern == "Brick":
            return gen_brick(
                outline, params["brick_w"], params["brick_h"], params["gap"]
            )
        elif pattern == "Diagonal Lines":
            return gen_diagonal_lines(outline, params["spacing"], params["angle"])
        elif pattern == "Square Grid":
            return gen_square_grid(outline, params["spacing"])
        elif pattern == "Concentric Rings":
            return gen_concentric_rings(outline, params["spacing"])
        elif pattern == "Wave Fill":
            return gen_wave_fill(
                outline, params["spacing"], params["amplitude"], params["wavelength"]
            )
        elif pattern == "Sunburst":
            return gen_sunburst(outline, params["spacing_deg"])
        elif pattern == "Voronoi":
            return gen_voronoi(
                outline, params["n_cells"], params["gap"], params["seed"]
            )
        elif pattern == "Triangle Grid":
            return gen_triangle_grid(outline, params["size"], params["gap"])
        elif pattern == "Penrose Tiling":
            return gen_penrose_tiling(outline, params["scale"], params["gap"])
        elif pattern == "Spiral":
            return gen_spiral(outline, params["spacing"], params["direction"])
        elif pattern == "Celtic Knot":
            return gen_celtic_knot(
                outline, params["cell_size"], params["line_width"], params["gap"]
            )
        elif pattern == "Lissajous":
            return gen_lissajous(
                outline,
                params["freq_x"],
                params["freq_y"],
                params["spacing"],
                params["amplitude"],
            )
        elif pattern == "Moroccan Zellige":
            return gen_moroccan_zellige(outline, params["size"], params["gap"])
        elif pattern == "Tri-Weave":
            return gen_tri_weave(outline, params["cell_size"], params["stroke_width"])
        elif pattern == "Topographic":
            return gen_topographic(outline, params["spacing"])
        elif self._is_tile_pattern(pattern):
            tile_polys = load_dxf_polylines(params["tile_path"])
            return gen_custom_tile(outline, tile_polys, params["gap"], params["angle"])
        else:  # Image Halftone
            return gen_image_halftone(
                outline,
                params["img_path"],
                params["r_min"],
                params["r_max"],
                params["spacing"],
                params["invert"],
            )

    def _reset_preview(self) -> None:
        self._preview_polys_cache = []
        if self._showing_preview:
            self._preview_btn.setChecked(False)
            self._on_preview_toggled(False)
        self._preview_status.setText("")
        self._schedule_preview()
        self._emit_state_changed()
