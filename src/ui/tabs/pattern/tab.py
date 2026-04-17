"""Pattern Generator tab."""

# isort: skip_file

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import threading
from uuid import uuid4

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
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
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, PATTERNS, SEL
from src.backend.generators import (
    apply_border_fade,
    apply_interlace,
    apply_invert_fill,
    apply_mirror,
    get_generator,
)
from src.backend.dxf.io import (
    analyze_outline_polylines,
    load_dxf_polylines,
    load_dxf_polylines_with_report,
    polylines_to_outline,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import graph_from_polylines, polylines_from_graph
from src.settings import save_settings
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components.action_maps import PATTERN_ACTION_MAP
from src.ui.components.containers import (
    CanvasObjectBrowser,
    CanvasPrecisionBar,
    CanvasStatusStrip,
    CollapsibleSection,
    DxfLayersTree,
)
from src.ui.components.factories import (
    _canvas_toolbar,
    _content_splitter,
    _section_label,
    _sidebar_panel,
    _surface_frame,
    parse_float_field_with_feedback,
)
from src.ui.tabs.pattern.params import (
    build_halftone_widget,
    build_param_widget,
    build_tile_library_widget,
    collect_pattern_params,
)
from src.ui.tabs.task_state import CancellableTaskState

ACTION_MAP = PATTERN_ACTION_MAP
LOGGER = logging.getLogger(__name__)


class PatternTab(QWidget):
    _gen_done = Signal(object)  # (count, name, polys)
    _gen_error = Signal(object)
    _preview_done = Signal(object)  # (display_polys, count)
    _preview_error = Signal(object)
    stateChanged = Signal()
    sendSelectedToDraftRequested = Signal(object)

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
        self._preview_task = CancellableTaskState()
        self._generate_task = CancellableTaskState()
        self._last_out_path: str | None = None
        self._suspend_state_changes: bool = False
        self._presets: dict[str, dict] = dict(self._settings.get("pattern_presets", {}))
        self._base_patterns: list[str] = list(PATTERNS)
        self._library_patterns: dict[str, str] = {}
        self._imported_dxf_layers: list[tuple[str, int, bool, bool]] = []
        self._tile_interlock_cb: QCheckBox | None = None

        self._showing_preview: bool = False
        self._preview_polys_cache: list[list[tuple[float, float]]] = []
        self._outline_ids: list[str] = []
        self._preview_revision: int = 0
        self._generation_revision: int = 0
        # Per-zone pattern assignments: each zone is a snapshot of
        # {"outline_ids": [...], "pattern": str, "params": dict,
        #  "interlace": bool, "scale": (w, h), "label": str}
        self._zones: list[dict] = []
        # Outline IDs marked as exclusion cutouts (pattern fills around them)
        self._exclusion_ids: list[str] = []

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
        self._update_preview_controls()

        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".dxf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".dxf"):
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
        self._dxf_edit.setToolTip(
            "Path to a DXF outline file (drag-and-drop supported)"
        )
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

        rot_row = QGridLayout()
        rot_row.setContentsMargins(0, 0, 0, 0)
        rot_row.addWidget(QLabel("Pattern rotation (°)"), 0, 0)
        self._pattern_rotation = QLineEdit("0")
        self._pattern_rotation.setFixedWidth(80)
        self._pattern_rotation.setToolTip(
            "Rotate generated pattern geometry around outline center"
        )
        self._pattern_rotation.textChanged.connect(self._schedule_preview)
        rot_row.addWidget(self._pattern_rotation, 0, 1)
        fill_layout.addLayout(rot_row)

        # Pattern param panels (stacked manually — show/hide)
        _sp = self._schedule_preview
        self._honeycomb_w = build_param_widget(self, "Honeycomb", _sp)
        self._gradient_w = build_param_widget(self, "Gradient Honeycomb", _sp)
        self._basketweave_w = build_param_widget(self, "Basketweave", _sp)
        self._braid_w = build_param_widget(self, "Braid", _sp)
        self._fishscale_w = build_param_widget(self, "Fish Scale", _sp)
        self._stipple_w = build_param_widget(self, "Stipple Dots", _sp)
        self._brick_w = build_param_widget(self, "Brick", _sp)
        self._diagonal_w = build_param_widget(self, "Diagonal Lines", _sp)
        self._square_grid_w = build_param_widget(self, "Square Grid", _sp)
        self._mesh_w = build_param_widget(self, "Mesh", _sp)
        self._concentric_w = build_param_widget(self, "Concentric Rings", _sp)
        self._wave_w = build_param_widget(self, "Wave Fill", _sp)
        self._sunburst_w = build_param_widget(self, "Sunburst", _sp)
        self._voronoi_w = build_param_widget(self, "Voronoi", _sp)
        self._penrose_w = build_param_widget(self, "Penrose Tiling", _sp)
        self._topographic_w = build_param_widget(self, "Topographic", _sp)
        self._hilbert_w = build_param_widget(self, "Hilbert Curve", _sp)
        self._reaction_diffuse_w = build_param_widget(self, "Reaction Diffuse", _sp)
        self._celtic_w = build_param_widget(self, "Celtic Knot", _sp)
        self._lissajous_w = build_param_widget(self, "Lissajous", _sp)
        self._golden_spiral_w = build_param_widget(self, "Golden Spiral", _sp)
        self._rose_curve_w = build_param_widget(self, "Rose Curve", _sp)
        self._tile_library_w = build_tile_library_widget(self, _sp)
        self._halftone_w = build_halftone_widget(self, _sp)

        self._pattern_widgets = [
            self._honeycomb_w,
            self._gradient_w,
            self._basketweave_w,
            self._braid_w,
            self._fishscale_w,
            self._stipple_w,
            self._brick_w,
            self._diagonal_w,
            self._square_grid_w,
            self._mesh_w,
            self._concentric_w,
            self._wave_w,
            self._sunburst_w,
            self._voronoi_w,
            self._penrose_w,
            self._topographic_w,
            self._hilbert_w,
            self._reaction_diffuse_w,
            self._celtic_w,
            self._lissajous_w,
            self._golden_spiral_w,
            self._rose_curve_w,
            self._tile_library_w,
            self._halftone_w,
        ]
        for w in self._pattern_widgets:
            fill_layout.addWidget(w)
            w.hide()

        self._include_border_cb = QCheckBox("Include border on separate layer")
        self._include_border_cb.setToolTip(
            "Writes pattern fill to 'background' and each outline geometry\n"
            "to 'outline', 'outline_1', 'outline_2', ... layers."
        )
        fill_layout.addWidget(self._include_border_cb)
        layout.addWidget(
            CollapsibleSection("Fill Parameters", fill_content, expanded=False)
        )

        # ── Pattern Zones ─────────────────────────────────────────────────────
        zones_content = QWidget()
        zones_layout = QVBoxLayout(zones_content)
        zones_layout.setContentsMargins(0, 0, 0, 0)
        zones_layout.setSpacing(6)

        assign_row = QHBoxLayout()
        self._assign_zone_btn = QPushButton("Assign Pattern to Selection")
        self._assign_zone_btn.setMinimumHeight(30)
        self._assign_zone_btn.setToolTip(
            "Save the current pattern and parameters as a named zone\n"
            "for the selected outlines. Different zones can use different patterns."
        )
        self._assign_zone_btn.clicked.connect(self._assign_zone)
        assign_row.addWidget(self._assign_zone_btn, stretch=1)
        zones_layout.addLayout(assign_row)

        self._zone_list = QListWidget()
        self._zone_list.setMaximumHeight(110)
        self._zone_list.setToolTip(
            "Assigned pattern zones — each outline group with its own pattern"
        )
        zones_layout.addWidget(self._zone_list)

        outline_action_row = QHBoxLayout()
        self._close_outlines_btn = QPushButton("Close Selected")
        self._close_outlines_btn.setToolTip("Close the selected open outlines")
        self._close_outlines_btn.clicked.connect(self._close_selected_outlines)
        outline_action_row.addWidget(self._close_outlines_btn)
        self._open_outlines_btn = QPushButton("Open Selected")
        self._open_outlines_btn.setToolTip("Open the selected closed outlines")
        self._open_outlines_btn.clicked.connect(self._open_selected_outlines)
        outline_action_row.addWidget(self._open_outlines_btn)
        zones_layout.addLayout(outline_action_row)

        zone_action_row = QHBoxLayout()
        self._remove_zone_btn = QPushButton("Remove Zone")
        self._remove_zone_btn.setToolTip("Remove the selected zone from the list")
        self._remove_zone_btn.clicked.connect(self._remove_selected_zone)
        zone_action_row.addWidget(self._remove_zone_btn)
        self._clear_zones_btn = QPushButton("Clear All")
        self._clear_zones_btn.setToolTip("Remove all pattern zone assignments")
        self._clear_zones_btn.clicked.connect(self._clear_zones)
        zone_action_row.addWidget(self._clear_zones_btn)
        zones_layout.addLayout(zone_action_row)

        zones_hint = QLabel(
            "Select outlines → configure pattern → 'Assign'. Repeat for each region."
        )
        zones_hint.setWordWrap(True)
        zones_hint.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        zones_layout.addWidget(zones_hint)

        layout.addWidget(
            CollapsibleSection("Pattern Zones", zones_content, expanded=False)
        )

        # ── Output Options (always visible) ───────────────────────────────────
        _section_label(layout, "Output Options")

        opt_row1 = QHBoxLayout()
        self._invert_fill_cb = QCheckBox("Invert fill")
        self._invert_fill_cb.setToolTip(
            "Stipple the area OUTSIDE the outline instead of inside.\n"
            "Useful for backgrounds and frames around a clean design."
        )
        self._invert_fill_cb.stateChanged.connect(self._schedule_preview)
        opt_row1.addWidget(self._invert_fill_cb)
        opt_row1.addStretch()
        opt_row1.addWidget(QLabel("Fade (mm):"))
        self._border_fade = QLineEdit("0")
        self._border_fade.setFixedWidth(52)
        self._border_fade.setToolTip(
            "Thin the pattern near the outline edge.\n"
            "0 = off. Higher values = wider fade zone."
        )
        self._border_fade.textChanged.connect(self._schedule_preview)
        opt_row1.addWidget(self._border_fade)
        layout.addLayout(opt_row1)

        opt_row2 = QHBoxLayout()
        opt_row2.addWidget(QLabel("Symmetry:"))
        self._mirror_v_cb = QCheckBox("\u2190 \u2192")
        self._mirror_v_cb.setToolTip("Mirror pattern left \u2194 right")
        self._mirror_v_cb.stateChanged.connect(self._schedule_preview)
        opt_row2.addWidget(self._mirror_v_cb)
        self._mirror_h_cb = QCheckBox("\u2191 \u2193")
        self._mirror_h_cb.setToolTip("Mirror pattern top \u2194 bottom")
        self._mirror_h_cb.stateChanged.connect(self._schedule_preview)
        opt_row2.addWidget(self._mirror_h_cb)
        opt_row2.addStretch()
        cutout_clear_btn = QPushButton("Clear cutouts")
        cutout_clear_btn.setFixedHeight(22)
        cutout_clear_btn.setStyleSheet("font-size: 10px;")
        cutout_clear_btn.setToolTip("Remove all cutout assignments")
        cutout_clear_btn.clicked.connect(self._clear_exclusions)
        opt_row2.addWidget(cutout_clear_btn)
        layout.addLayout(opt_row2)

        self._cutout_status_label = QLabel(
            "No cutouts \u2014 right-click a shape on canvas to mark it"
        )
        self._cutout_status_label.setWordWrap(True)
        self._cutout_status_label.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        layout.addWidget(self._cutout_status_label)

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

    # ── Pattern param builders removed — now data-driven via params.py ──────────

    def _switch_pattern(self, value: str) -> None:
        mapping = {
            "Honeycomb": self._honeycomb_w,
            "Gradient Honeycomb": self._gradient_w,
            "Basketweave": self._basketweave_w,
            "Braid": self._braid_w,
            "Fish Scale": self._fishscale_w,
            "Stipple Dots": self._stipple_w,
            "Brick": self._brick_w,
            "Diagonal Lines": self._diagonal_w,
            "Square Grid": self._square_grid_w,
            "Mesh": self._mesh_w,
            "Concentric Rings": self._concentric_w,
            "Wave Fill": self._wave_w,
            "Sunburst": self._sunburst_w,
            "Voronoi": self._voronoi_w,
            "Penrose Tiling": self._penrose_w,
            "Topographic": self._topographic_w,
            "Hilbert Curve": self._hilbert_w,
            "Reaction Diffuse": self._reaction_diffuse_w,
            "Celtic Knot": self._celtic_w,
            "Lissajous": self._lissajous_w,
            "Golden Spiral": self._golden_spiral_w,
            "Rose Curve": self._rose_curve_w,
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
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setMinimumHeight(28)
        self._preview_btn.setToolTip(
            "Toggle between outline editing and pattern preview"
        )
        self._preview_btn.clicked.connect(self._on_preview_toggled)

        self._reset_preview_btn = QPushButton("Reset")
        self._reset_preview_btn.setToolTip("Clear the preview cache and rebuild")
        self._reset_preview_btn.clicked.connect(self._reset_preview)

        toolbar, self._mode_btns, self._sel_label = _canvas_toolbar(
            self._on_toolbar_mode,
            lambda: self._canvas.fit(),
        )
        toolbar_layout = toolbar.layout()
        if isinstance(toolbar_layout, QHBoxLayout):
            toolbar_layout.insertWidget(toolbar_layout.count() - 1, self._preview_btn)
            toolbar_layout.insertWidget(
                toolbar_layout.count() - 1, self._reset_preview_btn
            )
        layout.addWidget(toolbar)

        self._precision_bar = CanvasPrecisionBar(
            None, on_changed=self._refresh_canvas_panels
        )
        layout.addWidget(self._precision_bar)

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_shell_layout = QVBoxLayout(canvas_shell)
        canvas_shell_layout.setContentsMargins(0, 0, 0, 0)
        canvas_shell_layout.setSpacing(8)

        self._preview_status = QLabel("No preview available")
        self._set_preview_status("No preview available")
        self._preview_status.setWordWrap(True)
        canvas_shell_layout.addWidget(self._preview_status)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_geometry_change,
            on_send_selected_to_draft=self._on_send_selected_to_draft_from_canvas,
            on_cutout_toggle=self._on_canvas_cutout_toggle,
            draft_profile=True,
        )
        self._canvas.set_grid_visible(True)
        self._canvas.set_grid_snap(False)
        self._canvas.set_grid_spacing(1.0)
        self._precision_bar.bind_canvas(self._canvas)
        canvas_shell_layout.addWidget(self._canvas, stretch=1)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._object_browser = CanvasObjectBrowser("Outline Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection_requested
        )
        self._object_browser.fitRequested.connect(self._fit_selection)
        side_layout.addWidget(self._object_browser, stretch=3)

        self._layers_tree = DxfLayersTree("DXF Layers")
        side_layout.addWidget(self._layers_tree, stretch=2)

        splitter = _content_splitter(canvas_shell, side_panel, sizes=(860, 260))
        layout.addWidget(splitter, stretch=1)
        self._refresh_canvas_panels()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_preview_toggled(self, checked: bool) -> None:
        """Toggle between outline editing and pattern preview display."""
        if checked and self._preview_polys_cache:
            # Switch to preview view
            self._showing_preview = True
            self._canvas.load(self._preview_polys_cache)
            self._set_preview_status(
                f"{len(self._preview_polys_cache)} shapes — preview", "success"
            )
        elif checked and not self._preview_polys_cache:
            # No preview available yet
            self._preview_btn.setChecked(False)
            self._set_preview_status("No preview available")
            return
        else:
            # Switch back to outline editing
            self._showing_preview = False
            if self._edit_polys:
                self._canvas.load(self._edit_polys)
            if self._preview_polys_cache:
                self._set_preview_status("Editing outline — preview cached")
            else:
                self._set_preview_status("Adjust settings to build a preview")
        self._preview_btn.setProperty("active", self._showing_preview)
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)
        self._update_preview_controls()
        self._update_zone_actions()
        self._refresh_canvas_panels()

    def _on_sel_change(self, count: int) -> None:
        if self._showing_preview:
            return
        self._sel_label.setText(f"{count} selected" if count else "0 selected")
        self._sel_label.setStyleSheet(f"color: {SEL};" if count else f"color: {DIM};")
        # When zones are active: always target all outlines so the composite
        # preview can show context for unassigned shapes alongside zone fills.
        # Without zones: target only selection when something is selected.
        if self._zones:
            self._edit_polys = self._canvas.get_polylines_state()
        elif count:
            self._edit_polys = self._canvas.get_selected()
        else:
            self._edit_polys = self._canvas.get_polylines_state()
        self._update_zone_actions()
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

    def load_outline_polys(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft selection",
    ) -> None:
        """Load outline polylines from another tab and prepare Pattern Fill."""
        if not polys:
            return

        incoming = [[(x, y) for x, y in poly] for poly in polys]
        self._suspend_state_changes = True

        self._showing_preview = False
        self._preview_btn.setChecked(False)
        self._preview_btn.setProperty("active", False)
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)

        self._orig_polys = [list(poly) for poly in incoming]
        self._edit_polys = [list(poly) for poly in incoming]
        self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
        self._exclusion_ids.clear()
        self._preview_polys_cache = []
        self._zones.clear()
        self._refresh_zone_list()

        self._canvas.set_polylines_state(self._edit_polys, fit=True)
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._canvas.set_mode("select")
        self._canvas.deselect_all()

        all_pts = [pt for p in self._orig_polys for pt in p]
        if all_pts:
            xs, ys = zip(*all_pts)
            self._orig_w = max(xs) - min(xs)
            self._orig_h = max(ys) - min(ys)
            self._orig_dims_label.setText(f"{self._orig_w:.2f} × {self._orig_h:.2f} mm")
            self._scale_w.blockSignals(True)
            self._scale_h.blockSignals(True)
            self._scale_w.setText(f"{self._orig_w:.3f}")
            self._scale_h.setText(f"{self._orig_h:.3f}")
            self._scale_w.blockSignals(False)
            self._scale_h.blockSignals(False)
        else:
            self._orig_w = self._orig_h = 0.0
            self._orig_dims_label.setText("—")

        self._dxf_edit.setText(f"[{source_label}]")
        self._set_status(
            f"Loaded {len(self._edit_polys)} outline(s) from {source_label}", "#3fb950"
        )

        self._suspend_state_changes = False
        self._update_preview_controls()
        self._update_zone_actions()
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _reload_dxf(self) -> None:
        path = self._dxf_edit.text().strip()
        if path:
            self._load_dxf(path)

    def _load_dxf(self, path: str) -> None:
        try:
            polys, report = load_dxf_polylines_with_report(path)
            self._orig_polys = polys
            self._edit_polys = list(polys)
            self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
            self._exclusion_ids.clear()
            self._imported_dxf_layers = [
                (name, count, False, False)
                for name, count in report.layer_counts.items()
            ]
            self._zones.clear()
            self._refresh_zone_list()
            self._canvas.load(polys)
            self._sync_canvas_cutout_highlight()
            self._refresh_cutout_status()

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

            self._set_status(f"Loaded {len(polys)} polylines from {Path(path).name}")
            recent = self._settings.get("recent_dxf", [])
            if path in recent:
                recent.remove(path)
            recent.insert(0, path)
            self._settings["recent_dxf"] = recent[:8]
            save_settings(self._settings)
            if report.has_issues:
                detail = summarize_dxf_import_report(report)
                if detail:
                    QMessageBox.warning(
                        self,
                        "DXF Import Notice",
                        f"{Path(path).name} loaded, but some DXF content could not be preserved.\n\n{detail}",
                    )
            self._update_preview_controls()
            self._update_zone_actions()
            self._schedule_preview()
            self._emit_state_changed()
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _delete_selected(self) -> None:
        if self._showing_preview:
            return
        n = self._canvas.delete_selected()
        if n:
            self._edit_polys = list(self._canvas.get_active())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Deleted {n} polyline(s). Use ↩ Undo to restore.")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _close_selected_outlines(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        changed = self._canvas.close_selected_polylines()
        if changed:
            self._edit_polys = list(self._canvas.get_polylines_state())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Closed {changed} outline(s).", "#3fb950")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()
        else:
            self._set_status("No open outlines selected.")

    def _open_selected_outlines(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        changed = self._canvas.open_selected_polylines()
        if changed:
            self._edit_polys = list(self._canvas.get_polylines_state())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Opened {changed} outline(s).", "#3fb950")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()
        else:
            self._set_status("No closed outlines selected.")

    def _undo_delete(self) -> None:
        if self._showing_preview:
            return
        if not self._canvas.undo_delete():
            self._set_status("Nothing to undo.")
        else:
            self._edit_polys = list(self._canvas.get_active())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
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
        if self._zones:
            self._invalidate_zones_for_geometry_change()
        self._edit_polys = self._canvas.get_polylines_state()
        self._outline_ids = self._sync_outline_ids(self._edit_polys)
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        self._canvas.set_selection(indices)
        self._refresh_canvas_panels()

    def _on_send_selected_to_draft_from_canvas(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self.sendSelectedToDraftRequested.emit(polys)

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
        topo = self._canvas.get_topology_summary()
        if self._preview_task.running:
            readiness_text = "Previewing"
            readiness_tone = "warn"
        elif self._showing_preview:
            readiness_text = "Preview"
            readiness_tone = "success"
        elif topo["open"] > 0 and not self._zones:
            readiness_text = f"{topo['open']} open outline(s)"
            readiness_tone = "warn"
        elif self._preview_polys_cache:
            readiness_text = "Preview ready"
            readiness_tone = "success"
        elif self._canvas.poly_count:
            readiness_text = "Outline ready"
            readiness_tone = "accent"
        else:
            readiness_text = "No outline"
            readiness_tone = "warn"
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
            object_count=self._canvas.poly_count,
            precision_text=str(summary["precision"]),
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        if hasattr(self, "_precision_bar"):
            self._precision_bar.refresh()

        if hasattr(self, "_object_browser"):
            self._object_browser.set_objects(
                self._canvas.get_polylines_state(),
                self._canvas.get_selection_indices(),
            )

        if hasattr(self, "_layers_tree"):
            active_name = (
                "pattern_preview" if self._showing_preview else "pattern_active"
            )
            rows: list[tuple[str, int, bool, bool]] = list(self._imported_dxf_layers)
            rows.append((active_name, self._canvas.poly_count, False, True))
            if self._orig_polys:
                rows.append((
                    "geometry",
                    len(self._orig_polys),
                    False,
                    active_name == "geometry",
                ))
            self._layers_tree.set_layers(rows)

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

    def use_polys_as_fill_pattern(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft selection",
    ) -> bool:
        """Persist selected geometry as a temporary tile and activate it as pattern."""
        if not polys:
            return False
        try:
            out_dir = Path(self._settings.get("pattern_output_dir", "") or "")
            if not out_dir:
                out_dir = Path.cwd() / "job"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            tile_path = out_dir / f"tile_from_selection_{stamp}.dxf"
            write_polylines_dxf(polys, str(tile_path), close=False)

            self._refresh_pattern_choices(extra_tile_path=str(tile_path))
            match_label = next(
                (
                    label
                    for label, path in self._library_patterns.items()
                    if path == str(tile_path)
                ),
                "",
            )
            if match_label:
                self._pattern_combo.setCurrentText(match_label)
            self._switch_pattern(self._pattern_combo.currentText())
            self._set_status(
                f"Using selected geometry as fill pattern ({source_label})",
                "#3fb950",
            )
            self._schedule_preview()
            self._emit_state_changed()
            return True
        except (OSError, ValueError) as exc:
            self._set_status(f"Failed to create fill pattern: {exc}", "#f85149")
            return False

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
            "rotation": self._pattern_rotation.text(),
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
            "basket_strip_w": self._basket_strip_w.text(),
            "basket_strip_l": self._basket_strip_l.text(),
            "basket_gap": self._basket_gap.text(),
            "braid_strip_w": self._braid_strip_w.text(),
            "braid_spacing": self._braid_spacing.text(),
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
            "mesh_r": self._mesh_r.text(),
            "mesh_spacing": self._mesh_spacing.text(),
            "conc_spacing": self._conc_spacing.text(),
            "wave_spacing": self._wave_spacing.text(),
            "wave_amplitude": self._wave_amplitude.text(),
            "wave_wavelength": self._wave_wavelength.text(),
            "sunburst_spacing": self._sunburst_spacing.text(),
            "vor_cells": self._vor_cells.text(),
            "vor_gap": self._vor_gap.text(),
            "vor_seed": self._vor_seed.text(),
            "penrose_scale": self._penrose_scale.text(),
            "penrose_gap": self._penrose_gap.text(),
            "topo_spacing": self._topo_spacing.text(),
            "hilbert_order": self._hilbert_order.text(),
            "hilbert_margin": self._hilbert_margin.text(),
            "rd_pattern": self._rd_pattern.currentText(),
            "rd_cell": self._rd_cell.text(),
            "rd_iters": self._rd_iters.text(),
            "rd_threshold": self._rd_threshold.text(),
            "rd_seed": self._rd_seed.text(),
            "celtic_cell": self._celtic_cell.text(),
            "celtic_line_w": self._celtic_line_w.text(),
            "celtic_gap": self._celtic_gap.text(),
            "liss_freq_x": self._liss_freq_x.text(),
            "liss_freq_y": self._liss_freq_y.text(),
            "liss_spacing": self._liss_spacing.text(),
            "liss_amplitude": self._liss_amplitude.text(),
            "golden_turns": self._golden_turns.text(),
            "golden_spacing": self._golden_spacing.text(),
            "golden_dir": self._golden_dir.currentText(),
            "rose_petals": self._rose_petals.text(),
            "rose_copies": self._rose_copies.text(),
            "rose_margin": self._rose_margin.text(),
            "tile_pattern_path": self._library_patterns.get(
                self._pattern_combo.currentText(), ""
            ),
            "tile_gap": self._tile_gap.text(),
            "tile_angle": self._tile_angle.text(),
            "tile_interlock": self._tile_interlock_cb.isChecked(),
            "htone_img_path": self._htone_img_edit.text(),
            "htone_r_min": self._htone_r_min.text(),
            "htone_r_max": self._htone_r_max.text(),
            "htone_spacing": self._htone_spacing.text(),
            "htone_invert": self._htone_invert.isChecked(),
            "invert_fill": self._invert_fill_cb.isChecked(),
            "border_fade": self._border_fade.text(),
            "mirror_v": self._mirror_v_cb.isChecked(),
            "mirror_h": self._mirror_h_cb.isChecked(),
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
        self._pattern_rotation.setText(str(values.get("rotation", "0")))
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
        self._basket_strip_w.setText(str(values.get("basket_strip_w", "2.0")))
        self._basket_strip_l.setText(str(values.get("basket_strip_l", "8.0")))
        self._basket_gap.setText(str(values.get("basket_gap", "0.2")))
        self._braid_strip_w.setText(str(values.get("braid_strip_w", "2.0")))
        self._braid_spacing.setText(str(values.get("braid_spacing", "3.0")))
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
        self._mesh_r.setText(str(values.get("mesh_r", "0.35")))
        self._mesh_spacing.setText(str(values.get("mesh_spacing", "1.2")))
        self._conc_spacing.setText(str(values.get("conc_spacing", "1.5")))
        self._wave_spacing.setText(str(values.get("wave_spacing", "1.5")))
        self._wave_amplitude.setText(str(values.get("wave_amplitude", "0.5")))
        self._wave_wavelength.setText(str(values.get("wave_wavelength", "3.0")))
        self._sunburst_spacing.setText(str(values.get("sunburst_spacing", "5.0")))
        self._vor_cells.setText(str(values.get("vor_cells", "60")))
        self._vor_gap.setText(str(values.get("vor_gap", "0.15")))
        self._vor_seed.setText(str(values.get("vor_seed", "42")))
        self._penrose_scale.setText(str(values.get("penrose_scale", "3.0")))
        self._penrose_gap.setText(str(values.get("penrose_gap", "0.1")))
        self._topo_spacing.setText(str(values.get("topo_spacing", "1.5")))
        self._hilbert_order.setText(str(values.get("hilbert_order", "5")))
        self._hilbert_margin.setText(str(values.get("hilbert_margin", "1.0")))
        self._rd_pattern.setCurrentText(str(values.get("rd_pattern", "labyrinth")))
        self._rd_cell.setText(str(values.get("rd_cell", "0.8")))
        self._rd_iters.setText(str(values.get("rd_iters", "1200")))
        self._rd_threshold.setText(str(values.get("rd_threshold", "0.22")))
        self._rd_seed.setText(str(values.get("rd_seed", "42")))
        self._celtic_cell.setText(str(values.get("celtic_cell", "5.0")))
        self._celtic_line_w.setText(str(values.get("celtic_line_w", "1.0")))
        self._celtic_gap.setText(str(values.get("celtic_gap", "0.2")))
        self._liss_freq_x.setText(str(values.get("liss_freq_x", "3")))
        self._liss_freq_y.setText(str(values.get("liss_freq_y", "2")))
        self._liss_spacing.setText(str(values.get("liss_spacing", "2.0")))
        self._liss_amplitude.setText(str(values.get("liss_amplitude", "5.0")))
        self._golden_turns.setText(str(values.get("golden_turns", "4.5")))
        self._golden_spacing.setText(str(values.get("golden_spacing", "1.5")))
        self._golden_dir.setCurrentText(str(values.get("golden_dir", "ccw")))
        self._rose_petals.setText(str(values.get("rose_petals", "7")))
        self._rose_copies.setText(str(values.get("rose_copies", "2")))
        self._rose_margin.setText(str(values.get("rose_margin", "1.0")))
        self._tile_gap.setText(str(values.get("tile_gap", "0.5")))
        self._tile_angle.setText(str(values.get("tile_angle", "0")))
        self._tile_interlock_cb.setChecked(bool(values.get("tile_interlock", False)))
        self._htone_img_edit.setText(str(values.get("htone_img_path", "")))
        self._htone_r_min.setText(str(values.get("htone_r_min", "0.3")))
        self._htone_r_max.setText(str(values.get("htone_r_max", "1.8")))
        self._htone_spacing.setText(str(values.get("htone_spacing", "2.2")))
        self._htone_invert.setChecked(bool(values.get("htone_invert", False)))
        self._invert_fill_cb.setChecked(bool(values.get("invert_fill", False)))
        self._border_fade.setText(str(values.get("border_fade", "0")))
        self._mirror_v_cb.setChecked(bool(values.get("mirror_v", False)))
        self._mirror_h_cb.setChecked(bool(values.get("mirror_h", False)))
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
            "outline_ids": list(self._outline_ids),
            "orig_w": self._orig_w,
            "orig_h": self._orig_h,
            "canvas_view": self._canvas.get_view_state(),
            "preview_polys": self._preview_polys_cache,
            "showing_preview": self._showing_preview,
            "document_graph": doc_graph.snapshot(),
            "zones": list(self._zones),
            "exclusion_ids": list(self._exclusion_ids),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state_changes = True
        if not isinstance(state, dict):
            state = {}
        self._imported_dxf_layers = []
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
        outline_ids = state.get("outline_ids", [])
        if isinstance(outline_ids, list) and len(outline_ids) == len(self._edit_polys):
            self._outline_ids = [str(v) for v in outline_ids]
        else:
            self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
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
        self._zones = list(state.get("zones", []))
        self._refresh_zone_list()
        self._exclusion_ids = [str(v) for v in state.get("exclusion_ids", [])]
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._imported_dxf_layers = []
        self._outline_ids = []
        self._set_status("")
        self._refresh_canvas_panels()

    def _parse_float_field(
        self,
        entry: QLineEdit,
        label: str,
        **kw,
    ) -> float | None:
        return parse_float_field_with_feedback(entry, label, self._set_status, **kw)

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
        if not self._edit_polys and not self._zones:
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
        invert_fill = self._invert_fill_cb.isChecked()
        mirror_v = self._mirror_v_cb.isChecked()
        mirror_h = self._mirror_h_cb.isChecked()
        try:
            border_fade = max(0.0, float(self._border_fade.text() or "0"))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None

        self._gen_btn.setEnabled(False)
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status("Generating…")

        self._generation_revision += 1
        generation_token = self._generation_revision
        _, cancel_event = self._generate_task.request_start()
        if self._zones:
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._generate_task.finish_run()
                self._gen_btn.setEnabled(True)
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
                self._set_status(str(exc), "#f85149")
                return
            threading.Thread(
                target=self._run_generate_zones,
                args=(
                    zones_snap,
                    out_path,
                    include_border,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    generation_token,
                    cancel_event,
                ),
                daemon=True,
            ).start()
        else:
            try:
                scale = self._collect_scale()
                params = self._collect_pattern_params(pattern)
                self._validate_outline_inputs(self._edit_polys)
            except ValueError:
                self._gen_btn.setEnabled(True)
                self._progress.setRange(0, 100)
                return
            polys_snap = list(self._edit_polys)
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            interlace = self._interlace_cb.isChecked()
            threading.Thread(
                target=self._run_generate,
                args=(
                    polys_snap,
                    out_path,
                    pattern,
                    params,
                    scale,
                    border_polys,
                    interlace,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    generation_token,
                    cancel_event,
                ),
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
        interlace: bool = False,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        generation_token: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            if cancel_event and cancel_event.is_set():
                return
            scaled = self._apply_scale(active, *scale)
            if cancel_event and cancel_event.is_set():
                return
            orig_outline = polylines_to_outline(scaled)
            fill_outline = orig_outline
            if exclusion_polys:
                excl_scaled = self._apply_scale(exclusion_polys, *scale)
                excl_outline = polylines_to_outline(excl_scaled)
                fill_outline = fill_outline.difference(excl_outline)
            if invert_fill:
                fill_outline = apply_invert_fill(orig_outline)
                if exclusion_polys:
                    excl_scaled = self._apply_scale(exclusion_polys, *scale)
                    excl_outline = polylines_to_outline(excl_scaled)
                    fill_outline = fill_outline.difference(excl_outline)
            if cancel_event and cancel_event.is_set():
                return
            polys = self._gen_pattern(fill_outline, pattern, params)
            if interlace:
                polys = apply_interlace(polys, spacing=params.get("spacing", 1.0))
            if mirror_v or mirror_h:
                polys = apply_mirror(polys, orig_outline, mirror_v, mirror_h)
            if border_fade > 0:
                polys = apply_border_fade(polys, orig_outline, border_fade)
            if cancel_event and cancel_event.is_set():
                return
            close = pattern not in (
                "Fish Scale",
                "Diagonal Lines",
                "Square Grid",
                "Concentric Rings",
                "Wave Fill",
                "Sunburst",
                "Topographic",
                "Hilbert Curve",
                "Reaction Diffuse",
                "Golden Spiral",
                "Rose Curve",
            )
            write_polylines_dxf(
                polys,
                out_path,
                close=close,
                border_polys=border_polys,
                pattern_layer="background" if border_polys else None,
                border_layer_prefix="outline",
            )

            count = len(polys)
            name = Path(out_path).name
            self._gen_done.emit((generation_token, count, name, out_path, polys))

        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            if cancel_event and cancel_event.is_set():
                return
            LOGGER.debug("Pattern generation failed: %s", exc)
            self._gen_error.emit((generation_token, str(exc)))

    def _run_generate_zones(
        self,
        zones: list[dict],
        out_path: str,
        include_border: bool,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        generation_token: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Worker: generate all zone patterns and write to a single DXF."""
        try:
            all_polys: list[list[tuple[float, float]]] = []
            border_polys: list[list[tuple[float, float]]] = []
            for zone in zones:
                if cancel_event and cancel_event.is_set():
                    return
                scaled = self._apply_scale(zone["polys"], *zone["scale"])
                if cancel_event and cancel_event.is_set():
                    return
                orig_outline = polylines_to_outline(scaled)
                fill_outline = orig_outline
                if exclusion_polys:
                    excl_scaled = self._apply_scale(exclusion_polys, *zone["scale"])
                    excl_outline = polylines_to_outline(excl_scaled)
                    fill_outline = fill_outline.difference(excl_outline)
                if invert_fill:
                    fill_outline = apply_invert_fill(orig_outline)
                    if exclusion_polys:
                        excl_scaled = self._apply_scale(
                            exclusion_polys, *zone["scale"]
                        )
                        excl_outline = polylines_to_outline(excl_scaled)
                        fill_outline = fill_outline.difference(excl_outline)
                if cancel_event and cancel_event.is_set():
                    return
                polys = self._gen_pattern(fill_outline, zone["pattern"], zone["params"])
                if zone["interlace"]:
                    polys = apply_interlace(
                        polys, spacing=zone["params"].get("spacing", 1.0)
                    )
                if mirror_v or mirror_h:
                    polys = apply_mirror(polys, orig_outline, mirror_v, mirror_h)
                if border_fade > 0:
                    polys = apply_border_fade(polys, orig_outline, border_fade)
                all_polys.extend(polys)
                if include_border:
                    border_polys.extend(scaled)
            if cancel_event and cancel_event.is_set():
                return
            write_polylines_dxf(
                all_polys,
                out_path,
                close=True,
                border_polys=border_polys if border_polys else None,
                pattern_layer="background" if border_polys else None,
                border_layer_prefix="outline",
            )
            count = len(all_polys)
            name = Path(out_path).name
            self._gen_done.emit((generation_token, count, name, out_path, all_polys))
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            if cancel_event and cancel_event.is_set():
                return
            LOGGER.debug("Zone pattern generation failed: %s", exc)
            self._gen_error.emit((generation_token, str(exc)))

    def _handle_gen_done(self, payload: tuple) -> None:
        generation_token, count, name, out_path, polys = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
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
        self._set_preview_status(f"{count} shapes exported", "success")
        self._update_preview_controls()
        self._refresh_canvas_panels()

    def _handle_gen_error(self, payload: tuple) -> None:
        generation_token, msg = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._gen_btn.setEnabled(True)
        self._set_status(f"Error: {msg}", "#f85149")
        if self._preview_task.has_pending() and (self._edit_polys or self._zones):
            self._preview_task.pending = False
            self._preview_timer.start(0)

    # ── Live preview ─────────────────────────────────────────────────────────

    def _schedule_preview(self, *_) -> None:
        if self._suspend_state_changes:
            return
        # Allow preview if zones exist (even if current pattern is "— None —" or
        # no manual selection is targeted), otherwise require normal preconditions.
        if not self._zones and self._pattern_combo.currentText() == "— None —":
            return
        if not self._zones and not self._edit_polys:
            return
        self._preview_revision += 1
        self._invalidate_preview_cache()
        if self._preview_task.running:
            self._preview_task.pending = True
        self._preview_timer.start(400)
        self._emit_state_changed()

    def _start_preview_thread(self) -> None:
        can_start, cancel_event = self._preview_task.request_start()
        if not can_start:
            return
        if not self._zones and not self._edit_polys:
            self._preview_task.finish_run()
            return
        preview_token = self._preview_revision
        pattern = self._pattern_combo.currentText()
        include_border = self._include_border_cb.isChecked()
        try:
            scale = self._collect_scale()
            params = (
                self._collect_pattern_params(pattern) if pattern != "— None —" else {}
            )
            if not self._zones:
                self._validate_outline_inputs(self._edit_polys)
        except ValueError:
            self._preview_task.finish_run()
            return
        interlace = self._interlace_cb.isChecked()
        invert_fill = self._invert_fill_cb.isChecked()
        mirror_v = self._mirror_v_cb.isChecked()
        mirror_h = self._mirror_h_cb.isChecked()
        try:
            border_fade = max(0.0, float(self._border_fade.text() or "0"))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None
        self._set_preview_status("Previewing…")
        if self._zones:
            # Zone mode: snapshot zone data + all polys for context
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._preview_task.finish_run()
                self._set_preview_status(str(exc), "error")
                self._update_preview_controls()
                return
            all_polys_snap = list(self._edit_polys)
            threading.Thread(
                target=self._compute_preview_zones,
                args=(
                    zones_snap,
                    all_polys_snap,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    preview_token,
                    cancel_event,
                ),
                daemon=True,
            ).start()
        else:
            polys_snap = list(self._edit_polys)
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            threading.Thread(
                target=self._compute_preview,
                args=(
                    polys_snap,
                    pattern,
                    params,
                    scale,
                    border_polys,
                    interlace,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    preview_token,
                    cancel_event,
                ),
                daemon=True,
            ).start()

    def _compute_preview(
        self,
        outline_polys,
        pattern: str,
        params: dict,
        scale: tuple[float, float],
        border_polys: list[list[tuple[float, float]]] | None,
        interlace: bool = False,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        preview_token: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            if cancel_event and cancel_event.is_set():
                return
            scaled = self._apply_scale(outline_polys, *scale)
            if cancel_event and cancel_event.is_set():
                return
            orig_outline = polylines_to_outline(scaled)
            fill_outline = orig_outline
            if exclusion_polys:
                excl_scaled = self._apply_scale(exclusion_polys, *scale)
                excl_outline = polylines_to_outline(excl_scaled)
                fill_outline = fill_outline.difference(excl_outline)
            if invert_fill:
                fill_outline = apply_invert_fill(orig_outline)
                if exclusion_polys:
                    excl_scaled = self._apply_scale(exclusion_polys, *scale)
                    excl_outline = polylines_to_outline(excl_scaled)
                    fill_outline = fill_outline.difference(excl_outline)
            if cancel_event and cancel_event.is_set():
                return
            polys = self._gen_pattern(fill_outline, pattern, params)
            if cancel_event and cancel_event.is_set():
                return
            if interlace:
                polys = apply_interlace(polys, spacing=params.get("spacing", 1.0))
            if mirror_v or mirror_h:
                polys = apply_mirror(polys, orig_outline, mirror_v, mirror_h)
            if border_fade > 0:
                polys = apply_border_fade(polys, orig_outline, border_fade)
            if border_polys:
                display_polys = polys + border_polys
            else:
                display_polys = polys
            self._preview_done.emit((preview_token, display_polys, len(polys)))
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            if cancel_event and cancel_event.is_set():
                return
            LOGGER.debug("Preview generation failed: %s", exc)
            self._preview_error.emit((preview_token, str(exc)))

    def _compute_preview_zones(
        self,
        zones: list[dict],
        all_polys: list[list[tuple[float, float]]],
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        preview_token: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Worker: generate each zone's pattern and combine for composite preview."""
        try:
            # Collect every poly that belongs to at least one zone (for context detection)
            zone_poly_ids: set[int] = set()
            zone_results: list[list[tuple[float, float]]] = []

            for zone in zones:
                if cancel_event and cancel_event.is_set():
                    return
                zone_polys = zone["polys"]
                scaled = self._apply_scale(zone_polys, *zone["scale"])
                if cancel_event and cancel_event.is_set():
                    return
                orig_outline = polylines_to_outline(scaled)
                fill_outline = orig_outline
                if exclusion_polys:
                    excl_scaled = self._apply_scale(exclusion_polys, *zone["scale"])
                    excl_outline = polylines_to_outline(excl_scaled)
                    fill_outline = fill_outline.difference(excl_outline)
                if invert_fill:
                    fill_outline = apply_invert_fill(orig_outline)
                    if exclusion_polys:
                        excl_scaled = self._apply_scale(
                            exclusion_polys, *zone["scale"]
                        )
                        excl_outline = polylines_to_outline(excl_scaled)
                        fill_outline = fill_outline.difference(excl_outline)
                if cancel_event and cancel_event.is_set():
                    return
                polys = self._gen_pattern(fill_outline, zone["pattern"], zone["params"])
                if cancel_event and cancel_event.is_set():
                    return
                if zone["interlace"]:
                    polys = apply_interlace(
                        polys, spacing=zone["params"].get("spacing", 1.0)
                    )
                if mirror_v or mirror_h:
                    polys = apply_mirror(polys, orig_outline, mirror_v, mirror_h)
                if border_fade > 0:
                    polys = apply_border_fade(polys, orig_outline, border_fade)
                zone_results.extend(polys)
                # Track which canvas polys are covered by a zone (by identity comparison)
                for zp in zone_polys:
                    for idx, cp in enumerate(all_polys):
                        if cp == zp:
                            zone_poly_ids.add(idx)

            # Unassigned polys shown as plain context borders
            context_polys = [
                p for i, p in enumerate(all_polys) if i not in zone_poly_ids
            ]
            # Always show unassigned polys as context borders in preview
            display_polys = zone_results + context_polys
            self._preview_done.emit((preview_token, display_polys, len(zone_results)))
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            if cancel_event and cancel_event.is_set():
                return
            LOGGER.debug("Zone preview generation failed: %s", exc)
            self._preview_error.emit((preview_token, str(exc)))

    def _handle_preview_done(self, payload: tuple) -> None:
        preview_token, display_polys, count = payload
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        self._preview_polys_cache = list(display_polys)
        # Update canvas if preview is already showing; otherwise just cache
        if self._showing_preview:
            self._canvas.load(display_polys)
            self._set_preview_status(f"{count} shapes — preview", "success")
        else:
            self._set_preview_status(f"{count} shapes ready — click Preview", "success")
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
            self._preview_timer.start(0)

    def _handle_preview_error(self, payload: tuple) -> None:
        preview_token, msg = payload
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        self._set_preview_status(f"Preview error: {msg}", "error")
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
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
        return collect_pattern_params(self, pattern)

    def _set_preview_status(self, text: str, tone: str = "dim") -> None:
        self._preview_status.setText(text)
        if tone == "success":
            self._preview_status.setStyleSheet("color: #3fb950; font-size: 11px;")
            return
        if tone == "error":
            self._preview_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        self._preview_status.setStyleSheet(f"color: {DIM}; font-size: 11px;")

    def _invalidate_preview_cache(self) -> None:
        had_cache = bool(self._preview_polys_cache)
        was_showing = self._showing_preview
        self._preview_polys_cache = []
        if was_showing:
            # Keep preview mode active while parameters/settings refresh.
            # The canvas continues to display the last preview until the new
            # preview result arrives.
            self._preview_btn.blockSignals(True)
            self._preview_btn.setChecked(True)
            self._preview_btn.blockSignals(False)
            self._preview_btn.setProperty("active", True)
            self._preview_btn.style().unpolish(self._preview_btn)
            self._preview_btn.style().polish(self._preview_btn)
        if had_cache or was_showing:
            self._set_preview_status("Refreshing preview…")
        self._update_preview_controls()

    def _invalidate_zones_for_geometry_change(self) -> None:
        if not self._zones:
            return
        self._zones.clear()
        self._refresh_zone_list()
        self._set_status(
            "Outline changed — cleared assigned zones to avoid mismatched pattern results.",
            "#e3b341",
        )

    def _update_preview_controls(self) -> None:
        has_preview = bool(self._preview_polys_cache)
        self._preview_btn.setEnabled(has_preview or self._showing_preview)
        self._preview_btn.setText("Outline" if self._showing_preview else "Preview")
        if self._showing_preview:
            self._preview_btn.setToolTip("Return to outline editing")
        elif has_preview:
            self._preview_btn.setToolTip(
                "Toggle between outline editing and pattern preview"
            )
        else:
            self._preview_btn.setToolTip(
                "Preview becomes available after the current outline and parameters produce a valid preview"
            )

    def _update_zone_actions(self) -> None:
        has_selection = bool(getattr(self._canvas, "sel_count", 0))
        can_assign = (not self._showing_preview) and has_selection
        self._assign_zone_btn.setEnabled(can_assign)
        self._assign_zone_btn.setToolTip(
            "Select one or more outlines to assign this pattern"
            if not can_assign
            else "Save the current pattern and parameters for the selected outlines"
        )
        self._remove_zone_btn.setEnabled(
            (not self._showing_preview) and bool(self._zones)
        )
        self._clear_zones_btn.setEnabled(
            (not self._showing_preview) and bool(self._zones)
        )

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

    def _fresh_outline_ids(self, count: int) -> list[str]:
        return [uuid4().hex for _ in range(count)]

    @staticmethod
    def _poly_signature(
        poly: list[tuple[float, float]],
    ) -> tuple[tuple[float, float], ...]:
        return tuple((round(x, 6), round(y, 6)) for x, y in poly)

    def _sync_outline_ids(
        self, new_polys: list[list[tuple[float, float]]]
    ) -> list[str]:
        old_polys = list(self._edit_polys)
        old_ids = list(self._outline_ids)
        if len(new_polys) == len(old_ids):
            return list(old_ids)
        sig_to_ids: dict[tuple[tuple[float, float], ...], list[str]] = {}
        for poly, oid in zip(old_polys, old_ids):
            sig_to_ids.setdefault(self._poly_signature(poly), []).append(oid)
        resolved: list[str] = []
        for poly in new_polys:
            sig = self._poly_signature(poly)
            ids = sig_to_ids.get(sig, [])
            if ids:
                resolved.append(ids.pop(0))
            else:
                resolved.append(uuid4().hex)
        return resolved

    def _resolve_outline_ids(self, ids: list[str]) -> list[list[tuple[float, float]]]:
        id_map = {oid: poly for oid, poly in zip(self._outline_ids, self._edit_polys)}
        return [list(id_map[oid]) for oid in ids if oid in id_map]

    def _validate_outline_inputs(self, polys: list[list[tuple[float, float]]]) -> None:
        analysis = analyze_outline_polylines(polys)
        if analysis.usable_closed_count <= 0:
            raise ValueError(
                "No valid closed outline was found. Close or repair the outline before generating a pattern."
            )
        if analysis.open_count > 0:
            self._set_status(
                f"Using {analysis.usable_closed_count} closed outline(s); ignoring {analysis.open_count} open outline(s).",
                "#e3b341",
            )

    def _snapshot_zone_jobs(self) -> list[dict]:
        jobs: list[dict] = []
        for zone in self._zones:
            outline_ids = [str(v) for v in zone.get("outline_ids", [])]
            resolved = self._resolve_outline_ids(outline_ids)
            if not resolved:
                continue
            self._validate_outline_inputs(resolved)
            jobs.append({**zone, "polys": resolved})
        if not jobs:
            raise ValueError(
                "No valid closed zone outlines were found. Reassign zones after repairing the outlines."
            )
        return jobs

    def _gen_pattern(
        self,
        outline,
        pattern: str,
        params: dict,
    ) -> list[list[tuple[float, float]]]:
        rot_deg = float(params.get("rotation", 0.0) or 0.0)

        if pattern == "Honeycomb":
            polys = get_generator("gen_honeycomb")(outline, params["r"], params["gap"])
        elif pattern == "Gradient Honeycomb":
            polys = get_generator("gen_gradient_honeycomb")(
                outline,
                params["r_min"],
                params["r_max"],
                params["gap"],
                params["angle"],
            )
        elif pattern == "Basketweave":
            polys = get_generator("gen_basketweave")(
                outline, params["strip_w"], params["strip_l"], params["gap"]
            )
        elif pattern == "Braid":
            polys = get_generator("gen_braid")(
                outline, params["strip_width"], params["spacing"]
            )
        elif pattern == "Fish Scale":
            polys = get_generator("gen_fish_scale")(outline, params["sw"], params["sh"])
        elif pattern == "Stipple Dots":
            if params.get("interlaced"):
                polys = get_generator("gen_stipple_interlaced")(
                    outline,
                    params["r"],
                    params["spacing"],
                )
            else:
                polys = get_generator("gen_stipple_dots")(
                    outline,
                    params["r"],
                    params["spacing"],
                )
        elif pattern == "Brick":
            polys = get_generator("gen_brick")(
                outline, params["brick_w"], params["brick_h"], params["gap"]
            )
        elif pattern == "Diagonal Lines":
            polys = get_generator("gen_diagonal_lines")(
                outline,
                params["spacing"],
                params["angle"],
            )
        elif pattern == "Square Grid":
            polys = get_generator("gen_square_grid")(outline, params["spacing"])
        elif pattern == "Mesh":
            polys = get_generator("gen_mesh")(outline, params["r"], params["spacing"])
        elif pattern == "Concentric Rings":
            polys = get_generator("gen_concentric_rings")(outline, params["spacing"])
        elif pattern == "Wave Fill":
            polys = get_generator("gen_wave_fill")(
                outline, params["spacing"], params["amplitude"], params["wavelength"]
            )
        elif pattern == "Sunburst":
            polys = get_generator("gen_sunburst")(outline, params["spacing_deg"])
        elif pattern == "Voronoi":
            polys = get_generator("gen_voronoi")(
                outline, params["n_cells"], params["gap"], params["seed"]
            )
        elif pattern == "Penrose Tiling":
            polys = get_generator("gen_penrose_tiling")(
                outline,
                params["scale"],
                params["gap"],
            )
        elif pattern == "Topographic":
            polys = get_generator("gen_topographic")(outline, params["spacing"])
        elif pattern == "Hilbert Curve":
            polys = get_generator("gen_hilbert_curve")(
                outline,
                params["order"],
                params["margin"],
            )
        elif pattern == "Reaction Diffuse":
            polys = get_generator("gen_reaction_diffuse")(
                outline,
                params["cell"],
                params["iters"],
                params["threshold"],
                params["seed"],
                params.get("pattern", "labyrinth"),
            )
        elif pattern == "Celtic Knot":
            polys = get_generator("gen_celtic_knot")(
                outline,
                params["cell_size"],
                params["line_width"],
                params["gap"],
            )
        elif pattern == "Lissajous":
            polys = get_generator("gen_lissajous")(
                outline,
                params["freq_x"],
                params["freq_y"],
                params["spacing"],
                params["amplitude"],
            )
        elif pattern == "Golden Spiral":
            polys = get_generator("gen_golden_spiral")(
                outline,
                params["turns"],
                params["spacing_mm"],
                params["direction"],
            )
        elif pattern == "Rose Curve":
            polys = get_generator("gen_rose_curve")(
                outline,
                params["petals"],
                params["copies"],
                params["margin_mm"],
            )
        elif self._is_tile_pattern(pattern):
            tile_polys = load_dxf_polylines(params["tile_path"])
            polys = get_generator("gen_custom_tile")(
                outline,
                tile_polys,
                params["gap"],
                params["angle"],
                params.get("interlock", False),
            )
        else:  # Image Halftone
            polys = get_generator("gen_image_halftone")(
                outline,
                params["img_path"],
                params["r_min"],
                params["r_max"],
                params["spacing"],
                params["invert"],
            )

        if abs(rot_deg) > 1e-9:
            all_pts = [pt for poly in polys for pt in poly]
            if all_pts:
                xs, ys = zip(*all_pts)
                cx = (min(xs) + max(xs)) / 2.0
                cy = (min(ys) + max(ys)) / 2.0
                rad = rot_deg * 3.141592653589793 / 180.0
                ca, sa = math.cos(rad), math.sin(rad)
                polys = [
                    [
                        (
                            cx + (x - cx) * ca - (y - cy) * sa,
                            cy + (x - cx) * sa + (y - cy) * ca,
                        )
                        for x, y in poly
                    ]
                    for poly in polys
                ]
        return polys

    # ── Pattern Zone management ───────────────────────────────────────────────

    def _assign_zone(self) -> None:
        """Save current pattern+params as a zone for the selected outlines."""
        sel_polys = self._canvas.get_selected()
        sel_ids = [
            self._outline_ids[idx]
            for idx in self._canvas.get_selection_indices()
            if 0 <= idx < len(self._outline_ids)
        ]
        if not sel_polys:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more outlines on the canvas first, then click 'Assign'.",
            )
            return
        pattern = self._pattern_combo.currentText()
        if pattern == "— None —":
            QMessageBox.information(
                self, "No Pattern", "Choose a pattern before assigning."
            )
            return
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern)
            self._validate_outline_inputs(sel_polys)
        except ValueError:
            return
        interlace = self._interlace_cb.isChecked()
        if any(
            zone.get("outline_ids", []) == sel_ids
            and zone["pattern"] == pattern
            and zone["params"] == params
            and zone["interlace"] == interlace
            and zone["scale"] == scale
            for zone in self._zones
        ):
            self._set_status("Matching zone already exists.", "#e3b341")
            return
        label = f"Zone {len(self._zones) + 1}: {pattern} ({len(sel_polys)} outline{'s' if len(sel_polys) != 1 else ''})"
        self._zones.append({
            "outline_ids": list(sel_ids),
            "pattern": pattern,
            "params": params,
            "interlace": interlace,
            "scale": scale,
            "label": label,
        })
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()

    def _remove_selected_zone(self) -> None:
        """Remove the currently highlighted zone from the list."""
        row = self._zone_list.currentRow()
        if 0 <= row < len(self._zones):
            del self._zones[row]
            self._refresh_zone_list()
            self._schedule_preview()
            self._emit_state_changed()

    def _clear_zones(self) -> None:
        if not self._zones:
            return
        reply = QMessageBox.question(
            self,
            "Clear all zones?",
            "This removes every assigned pattern zone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._zones.clear()
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()

    def _refresh_zone_list(self) -> None:
        if not hasattr(self, "_zone_list"):
            return
        self._zone_list.blockSignals(True)
        self._zone_list.clear()
        if self._zones:
            for zone in self._zones:
                self._zone_list.addItem(zone["label"])
        else:
            self._zone_list.addItem("No zones assigned yet")
        self._zone_list.blockSignals(False)
        if not self._zones and self._zone_list.count() > 0:
            item = self._zone_list.item(0)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._update_zone_actions()

    # ── Exclusion Cutout management ───────────────────────────────────────────

    def _on_canvas_cutout_toggle(self, idx: int) -> None:
        """Toggle cutout status for a canvas poly index (called from right-click menu)."""
        if self._showing_preview:
            self._canvas._show_flash("Exit preview mode to assign cutouts", 1200)
            return
        if not (0 <= idx < len(self._outline_ids)):
            return
        oid = self._outline_ids[idx]
        if oid in self._exclusion_ids:
            self._exclusion_ids.remove(oid)
        else:
            self._exclusion_ids.append(oid)
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._schedule_preview()
        self._emit_state_changed()

    def _clear_exclusions(self) -> None:
        if not self._exclusion_ids:
            return
        self._exclusion_ids.clear()
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._schedule_preview()
        self._emit_state_changed()

    def _sync_canvas_cutout_highlight(self) -> None:
        """Update canvas accent colors to reflect current cutout assignments."""
        if not hasattr(self, "_canvas"):
            return
        id_to_idx = {oid: i for i, oid in enumerate(self._outline_ids)}
        cutout_idxs = {
            id_to_idx[eid] for eid in self._exclusion_ids if eid in id_to_idx
        }
        self._canvas.set_cutout_indices(cutout_idxs)

    def _refresh_cutout_status(self) -> None:
        """Update the always-visible cutout status label."""
        if not hasattr(self, "_cutout_status_label"):
            return
        n = len(self._exclusion_ids)
        if n == 0:
            self._cutout_status_label.setText(
                "No cutouts \u2014 right-click a shape on canvas to mark it"
            )
            self._cutout_status_label.setStyleSheet(
                f"color: {DIM}; font-size: 10px;"
            )
        else:
            self._cutout_status_label.setText(
                f"{n} cutout{'s' if n != 1 else ''} active \u2014 shown orange on canvas"
            )
            self._cutout_status_label.setStyleSheet(
                "color: #f0883e; font-size: 10px;"
            )

    def _resolve_exclusion_polys(self) -> list[list[tuple[float, float]]]:
        """Return polylines for all current exclusion IDs."""
        return self._resolve_outline_ids(self._exclusion_ids)

    # ── Preview / reset ───────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._preview_polys_cache = []
        if self._showing_preview:
            self._preview_btn.setChecked(False)
            self._on_preview_toggled(False)
        self._set_preview_status("Adjust settings to build a preview")
        self._update_preview_controls()
        self._schedule_preview()
        self._emit_state_changed()
