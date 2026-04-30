"""Pattern Generator page."""

# isort: skip_file
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportUndefinedVariable=false

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, PATTERNS
from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.settings import save_settings
from src.ui.components.common.recent_files_button import RecentFilesButton
from src.ui.util.dialog_paths import (
    pick_directory,
    pick_open_file,
    pick_save_file,
)
from src.ui.util.recent_files import KIND_DXF, KIND_IMAGE, record_recent
from src.ui.pages.pattern.session import (
    apply_pattern_workspace_state,
    clear_pattern_workspace_state,
    get_pattern_workspace_state,
)
from src.ui.pages.pattern.workers import (
    compute_preview,
    compute_preview_zones,
    run_generate,
    run_generate_zones,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components.canvas.modules import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
)
from src.ui.components.canvas.page_runtimes import PatternCanvasPageRuntime
from src.ui.components.canvas.widgets import (
    CanvasStatusStrip,
    CollapsibleSection,
)
from src.ui.components.common.factories import (
    _content_splitter,
    _section_label,
    _sidebar_panel,
    _surface_frame,
    clear_line_edit_error,
    parse_float_field_with_feedback,
    set_line_edit_error,
)
from src.ui.pages.pattern.params import (
    build_halftone_widget,
    build_param_widget,
    build_tile_library_widget,
    collect_form_state,
    collect_pattern_params,
    restore_form_state,
)
from src.ui.pages.pattern.presets import (
    SETTINGS_KEY as PRESET_SETTINGS_KEY,
    ensure_builtins_seeded,
)
from src.ui.pages.pattern.presets_dialog import PresetManagerDialog
from src.ui.pages.pattern.services import PatternProcessingService
from src.ui.pages.pattern.task_state import CancellableTaskState

LOGGER = logging.getLogger(__name__)


class PatternPage(QWidget):
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
        # Seed factory starter presets once on first run; respects deletions.
        seeded = ensure_builtins_seeded(self._settings, self._presets)
        if (
            seeded is not self._presets
            or self._settings.get("pattern_presets") != seeded
        ):
            self._presets = seeded
            self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
            try:
                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        self._base_patterns: list[str] = list(PATTERNS)
        self._library_patterns: dict[str, str] = {}
        self._imported_dxf_layers: list[tuple[str, int, bool, bool]] = []
        self._tile_interlock_cb: QCheckBox | None = None

        self._showing_preview: bool = False
        self._preview_polys_cache: list[list[tuple[float, float]]] = []
        self._preview_categories: dict[str, list[list[tuple[float, float]]]] = {
            "outline": [],
            "pattern": [],
            "fill": [],
        }
        self._outline_ids: list[str] = []
        self._preview_revision: int = 0
        self._generation_revision: int = 0
        self._pattern_service = PatternProcessingService()
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
        self._build_source_section(layout)
        self._build_scale_section(layout)
        self._build_fill_params_section(layout)
        self._build_zones_section(layout)
        self._build_output_options_section(layout)
        self._build_export_section(layout)
        layout.addStretch()

    def _build_source_section(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Source")

        file_row = QHBoxLayout()
        self._dxf_edit = QLineEdit()
        self._dxf_edit.setPlaceholderText("Select .dxf…")
        self._dxf_edit.setToolTip(
            "Path to a DXF outline file (drag-and-drop supported)"
        )
        file_row.addWidget(self._dxf_edit, stretch=1)
        self._recent_btn = RecentFilesButton(
            self._settings,
            KIND_DXF,
            empty_message="No recent DXF files.",
        )
        self._recent_btn.setToolTip("Pick from recently opened DXF files")
        self._recent_btn.fileSelected.connect(self._quick_load)
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

    def _build_scale_section(self, layout: QVBoxLayout) -> None:
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

    def _build_fill_params_section(self, layout: QVBoxLayout) -> None:
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

        preset_row = QGridLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setHorizontalSpacing(4)
        preset_row.setVerticalSpacing(4)
        self._preset_combo = QComboBox()
        self._preset_combo.setToolTip("Saved parameter presets for the current pattern")
        self._preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._preset_combo.setMinimumContentsLength(10)
        # Combo spans the full row so long preset names stay readable.
        preset_row.addWidget(self._preset_combo, 0, 0, 1, 4)
        # Action buttons share row 2 equally — no fixed widths so the row
        # always fits inside the sidebar's minimum width (~320 px).
        load_preset_btn = QPushButton("Load")
        load_preset_btn.setToolTip("Apply the selected preset to the parameter fields")
        load_preset_btn.clicked.connect(self._apply_selected_preset)
        preset_row.addWidget(load_preset_btn, 1, 0)
        save_preset_btn = QPushButton("Save")
        save_preset_btn.setToolTip(
            "Save the current parameters; reuses the selected name to update"
        )
        save_preset_btn.clicked.connect(self._save_preset)
        preset_row.addWidget(save_preset_btn, 1, 1)
        delete_preset_btn = QPushButton("Delete")
        delete_preset_btn.setToolTip("Remove the selected preset permanently")
        delete_preset_btn.clicked.connect(self._delete_selected_preset)
        preset_row.addWidget(delete_preset_btn, 1, 2)
        manage_preset_btn = QPushButton("Manage…")
        manage_preset_btn.setToolTip(
            "Rename, duplicate, import or export pattern presets"
        )
        manage_preset_btn.clicked.connect(self._open_preset_manager)
        preset_row.addWidget(manage_preset_btn, 1, 3)
        for col in range(4):
            preset_row.setColumnStretch(col, 1)
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

        # Pattern param panels keyed by pattern name — show/hide on selection
        _sp = self._schedule_preview
        _named_patterns = [
            "Honeycomb",
            "Gradient Honeycomb",
            "Basketweave",
            "Braid",
            "Fish Scale",
            "Stipple Dots",
            "Brick",
            "Diagonal Lines",
            "Square Grid",
            "Mesh",
            "Concentric Rings",
            "Wave Fill",
            "Sunburst",
            "Voronoi",
            "Penrose Tiling",
            "Topographic",
            "Hilbert Curve",
            "Reaction Diffuse",
            "Celtic Knot",
            "Lissajous",
            "Golden Spiral",
            "Rose Curve",
        ]
        self._pattern_widgets: dict[str, QWidget] = {}
        for name in _named_patterns:
            w = build_param_widget(self, name, _sp)
            self._pattern_widgets[name] = w
            fill_layout.addWidget(w)
            w.hide()
        self._tile_library_w = build_tile_library_widget(self, _sp)
        fill_layout.addWidget(self._tile_library_w)
        self._tile_library_w.hide()
        halftone_w = build_halftone_widget(self, _sp)
        self._pattern_widgets["Image Halftone"] = halftone_w
        fill_layout.addWidget(halftone_w)
        halftone_w.hide()

        self._include_border_cb = QCheckBox("Include border on separate layer")
        self._include_border_cb.setToolTip(
            "Writes pattern fill to 'background' and each outline geometry\n"
            "to 'outline', 'outline_1', 'outline_2', ... layers."
        )
        self._include_border_cb.setChecked(True)
        self._include_border_cb.stateChanged.connect(self._schedule_preview)
        fill_layout.addWidget(self._include_border_cb)
        layout.addWidget(
            CollapsibleSection("Fill Parameters", fill_content, expanded=False)
        )

    def _build_zones_section(self, layout: QVBoxLayout) -> None:
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

    def _build_output_options_section(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Output Options")

        opt_row1 = QHBoxLayout()
        self._invert_fill_cb = QCheckBox("Invert fill")
        self._invert_fill_cb.setToolTip(
            "Fill the area OUTSIDE the outline instead of inside.\n"
            "Generates the pattern in a frame around the outline — useful\n"
            "for backgrounds and engraved borders around a clean design."
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
        opt_row2.setSpacing(6)
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
        layout.addLayout(opt_row2)

        # Cutouts get their own labelled row so the action verb is clearly
        # separated from the symmetry toggles above it.
        cutout_row = QHBoxLayout()
        cutout_row.setSpacing(6)
        cutout_row.addWidget(QLabel("Cutouts:"))
        cutout_row.addStretch()
        cutout_clear_btn = QPushButton("Clear all")
        cutout_clear_btn.setToolTip("Remove all cutout assignments")
        cutout_clear_btn.clicked.connect(self._clear_exclusions)
        cutout_row.addWidget(cutout_clear_btn)
        layout.addLayout(cutout_row)

        self._cutout_status_label = QLabel(
            "No cutouts \u2014 right-click a shape on canvas to mark it"
        )
        self._cutout_status_label.setWordWrap(True)
        self._cutout_status_label.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        layout.addWidget(self._cutout_status_label)

        # ── Laser fill (infill) ──────────────────────────────────────────
        # Fills the input outline (minus exclusions) with parallel infill
        # polylines. The pattern strokes are an independent overlay — they
        # do NOT subdivide the fill region. Use the per-zone "Assign zone"
        # control to give each shape its own fill snapshot.
        fill_label_row = QHBoxLayout()
        fill_label_row.setContentsMargins(0, 6, 0, 0)
        fill_label_row.addWidget(QLabel("Laser fill:"))
        self._fill_mode_combo = QComboBox()
        self._fill_mode_combo.addItem("None", "none")
        self._fill_mode_combo.addItem("Lines", "lines")
        self._fill_mode_combo.setToolTip(
            "Fill the shape with parallel laser-engrave lines.\n"
            "Combine with pattern '— None —' to get an outline-only or\n"
            "fill-only result without any pattern strokes."
        )
        self._fill_mode_combo.currentIndexChanged.connect(self._on_fill_mode_changed)
        fill_label_row.addWidget(self._fill_mode_combo, stretch=1)
        layout.addLayout(fill_label_row)

        fill_params_row = QHBoxLayout()
        fill_params_row.setContentsMargins(0, 0, 0, 0)
        fill_params_row.setSpacing(6)
        fill_params_row.addWidget(QLabel("Spacing (mm)"))
        self._fill_spacing = QLineEdit("0.5")
        self._fill_spacing.setFixedWidth(56)
        self._fill_spacing.setToolTip("Distance between adjacent infill lines")
        self._fill_spacing.textChanged.connect(self._schedule_preview)
        fill_params_row.addWidget(self._fill_spacing)
        fill_params_row.addWidget(QLabel("Angle (°)"))
        self._fill_angle = QLineEdit("0")
        self._fill_angle.setFixedWidth(56)
        self._fill_angle.setToolTip("Angle of the infill line direction")
        self._fill_angle.textChanged.connect(self._schedule_preview)
        fill_params_row.addWidget(self._fill_angle)
        fill_params_row.addStretch()
        layout.addLayout(fill_params_row)

        self._fill_keep_outline_cb = QCheckBox("Keep pattern strokes alongside fill")
        self._fill_keep_outline_cb.setToolTip(
            "Output both the pattern strokes and the laser-fill lines.\n"
            "Uncheck for fill-only output (the pattern is suppressed)."
        )
        self._fill_keep_outline_cb.setChecked(True)
        self._fill_keep_outline_cb.stateChanged.connect(self._schedule_preview)
        layout.addWidget(self._fill_keep_outline_cb)

        # Initial enable state — fill controls disabled while mode == None.
        self._on_fill_mode_changed()

    def _build_export_section(self, layout: QVBoxLayout) -> None:
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

    # ── Pattern param builders removed — now data-driven via params.py ──────────

    def _switch_pattern(self, value: str) -> None:
        for w in self._pattern_widgets.values():
            w.hide()
        self._tile_library_w.hide()
        self._update_tile_library_panel()
        if self._is_tile_pattern(value):
            self._tile_library_w.show()
            self._schedule_preview()
        elif value in self._pattern_widgets:
            self._pattern_widgets[value].show()
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

        self._preview_status = QLabel("No preview available")
        self._set_preview_status("No preview available")
        self._preview_status.setWordWrap(True)

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

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
            extra_widgets=[self._preview_btn, self._reset_preview_btn],
        )
        layout.addWidget(self._toolbar_module)

        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_canvas_panels,
        )
        layout.addWidget(self._grid_module)
        self._precision_bar = self._grid_module

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_shell_layout = QVBoxLayout(canvas_shell)
        canvas_shell_layout.setContentsMargins(0, 0, 0, 0)
        canvas_shell_layout.setSpacing(8)
        canvas_shell_layout.addWidget(self._preview_status)
        canvas_shell_layout.addWidget(self._canvas, stretch=1)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._layer_module = CanvasLayerTreeModule(
            canvas=self._canvas,
            title="Layers",
            editable=True,
            get_active_layer_name=lambda: (
                "pattern_preview" if self._showing_preview else "pattern_active"
            ),
            build_layer_rows=self._build_layer_tree_rows,
            on_selection_requested=self._on_browser_selection_requested,
            on_fit_requested=self._fit_selection,
            on_visibility_changed=self._refresh_canvas_panels,
        )
        self._layers_tree = self._layer_module.tree
        self._layer_sidebar = self._layer_module.controller
        # Wire outline-mode shape rename to the runtime’s label store.
        self._layers_tree.shapeRenamed.connect(self._on_shape_renamed)
        side_layout.addWidget(self._layer_module, stretch=1)

        self._canvas_runtime = PatternCanvasPageRuntime(
            canvas=self._canvas,
            toolbar_module=self._toolbar_module,
            layer_sidebar=self._layer_sidebar,
            canvas_status=self._canvas_status,
            precision_bar=self._precision_bar,
            get_orig_polys=lambda: self._edit_polys,
            get_showing_preview=lambda: self._showing_preview,
            is_preview_running=lambda: self._preview_task.running,
            has_preview_cache=lambda: bool(self._preview_polys_cache),
            has_zones=lambda: bool(self._zones),
            get_preview_categories=lambda: self._preview_categories,
        )

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
            # Show the source outline as a faded ghost overlay so the user can
            # see both the outline and the generated pattern at the same time.
            if self._edit_polys:
                self._canvas.set_ghost_polylines(self._edit_polys)
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
            self._canvas.set_ghost_polylines(None)
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
        self._canvas_runtime.on_selection_change(count)  # updates toolbar
        # `_edit_polys` always mirrors the FULL canvas state — never just the
        # selection subset. Otherwise toggling preview off would only restore
        # the previously-selected shapes (and silently drop all the others).
        # If users want to pattern only specific outlines, they should create
        # zones; selection is purely for selection, not for scoping the fill.
        self._edit_polys = self._canvas.get_polylines_state()
        self._update_zone_actions()
        # Update status strip selection count without rebuilding the tree.
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        return self._canvas_runtime.build_layer_tree_rows(layer_view_state)

    def _browse_dxf(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "pattern_outline_dxf",
            "Select outline DXF",
            "DXF files (*.dxf *.Dxf *.DXF);;All files (*)",
            fallback_dir=self._settings.get("outline_dxf_dir", ""),
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
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        self._zones.clear()
        self._refresh_zone_list()

        self._canvas.set_polylines_state(self._edit_polys, fit=True)
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._canvas.set_mode("select")
        self._canvas.deselect_all()

        self._update_dims_from_polys(self._orig_polys)

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

    def _update_dims_from_polys(self, polys: list[list[tuple[float, float]]]) -> None:
        """Recompute orig_w/h and sync the scale fields from the polyline bounding box."""
        all_pts = [pt for p in polys for pt in p]
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

            self._update_dims_from_polys(polys)

            self._set_status(f"Loaded {len(polys)} polylines from {Path(path).name}")
            record_recent(self._settings, KIND_DXF, path)
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

    def _on_toolbar_mode(self, value: str) -> None:
        self._canvas_runtime.on_toolbar_mode(value)
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._canvas_runtime.on_canvas_mode_change(mode)
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
        # Select shapes on canvas without toggling preview mode — the user
        # should be able to highlight shapes in the layer tree while reviewing
        # the generated pattern.
        self._canvas_runtime.on_tree_selection_requested(indices)
        # Update toolbar and status strip without rebuilding the tree —
        # rebuilding would immediately clear the visual selection just made.
        self._canvas_runtime.on_selection_change(len(indices))
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(len(indices))

    def _on_shape_renamed(
        self, layer_name: str, shape_key: object, new_label: str
    ) -> None:
        """Persist a custom display label for an outline shape."""
        self._canvas_runtime.rename_shape(layer_name, shape_key, new_label)

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
        if self._canvas_runtime.fit_selection():
            self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        self._canvas_runtime.refresh_canvas_panels()

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

    def _show_recent_menu(
        self,
    ) -> None:  # pragma: no cover - retained for API stability
        # Recent menu is now driven by RecentFilesButton; this shim keeps any
        # external callers working.
        self._recent_btn._open_menu()

    def _quick_load(self, path: str) -> None:
        self._dxf_edit.setText(path)
        self._load_dxf(path)

    def _clear_recent(self) -> None:  # pragma: no cover - kept for back-compat
        from src.ui.util.recent_files import clear_recent

        clear_recent(self._settings, KIND_DXF)

    def _choose_pattern_library_dir(self) -> None:
        path = pick_directory(
            self,
            self._settings,
            "pattern_library",
            "Select pattern library folder",
            fallback_dir=self._settings.get("pattern_library_dir", ""),
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
        path = pick_open_file(
            self,
            self._settings,
            "halftone_image",
            "Select image for halftone",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*)",
            recent_kind=KIND_IMAGE,
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

    def _save_preset(self) -> None:
        # Pre-fill with the currently selected preset name (if any) to make
        # "update" the natural fast path.
        prefill = self._preset_combo.currentText().strip()
        if prefill == "Presets":
            prefill = ""
        name, ok = QInputDialog.getText(
            self, "Save Pattern Preset", "Preset name:", text=prefill
        )
        name = name.strip()
        if not ok or not name:
            return
        is_update = name in self._presets
        if is_update:
            reply = QMessageBox.question(
                self,
                "Overwrite preset",
                f"A preset called {name!r} already exists.\nReplace it with the current parameters?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._presets[name] = collect_form_state(self)
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._preset_combo.setCurrentText(name)
        verb = "Updated" if is_update else "Saved"
        self._set_status(f"{verb} preset: {name}", "#3fb950")
        self._emit_state_changed()

    def _apply_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets":
            return
        payload = self._presets.get(name)
        if not payload:
            return
        self._suspend_state_changes = True
        restore_form_state(self, payload)
        self._suspend_state_changes = False
        self._set_status(f"Loaded preset: {name}", "#3fb950")
        self._schedule_preview()
        self._emit_state_changed()

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name == "Presets" or name not in self._presets:
            return
        self._presets.pop(name, None)
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._set_status(f"Deleted preset: {name}")
        self._emit_state_changed()

    def _open_preset_manager(self) -> None:
        current = self._preset_combo.currentText().strip()
        if current == "Presets":
            current = ""
        dlg = PresetManagerDialog(
            self._presets,
            self._settings,
            current_preset=current or None,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.is_dirty:
            return
        self._presets = dlg.result_presets
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        if current and current in self._presets:
            self._preset_combo.setCurrentText(current)
        self._set_status(f"Pattern presets updated ({len(self._presets)} total)")
        self._emit_state_changed()

    def get_preset_state(self) -> dict[str, dict]:
        return {name: dict(payload) for name, payload in self._presets.items()}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        self._presets = {name: dict(payload) for name, payload in presets.items()}
        self._refresh_preset_combo()

    def get_workspace_state(self) -> dict:
        return get_pattern_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_pattern_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_pattern_workspace_state(self)

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

        out_path = pick_save_file(
            self,
            self._settings,
            "pattern_output",
            "Save pattern DXF",
            "pattern.dxf",
            "DXF files (*.dxf);;All files (*)",
            fallback_dir=self._settings.get("pattern_output_dir", ""),
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
        gen_fill_options = self._collect_fill_options()

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
                target=run_generate_zones,
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
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._gen_done.emit,
                    "on_error": self._gen_error.emit,
                    "fill_options": gen_fill_options,
                },
                daemon=True,
            ).start()
        else:
            polys_snap = list(self._edit_polys)
            try:
                scale = self._collect_scale()
                params = (
                    self._collect_pattern_params(pattern)
                    if pattern != "— None —"
                    else {}
                )
            except ValueError:
                self._generate_task.finish_run()
                self._gen_btn.setEnabled(True)
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
                return
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            interlace = self._interlace_cb.isChecked()
            threading.Thread(
                target=run_generate,
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
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._gen_done.emit,
                    "on_error": self._gen_error.emit,
                    "fill_options": gen_fill_options,
                },
                daemon=True,
            ).start()

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

    def _on_fill_mode_changed(self, *_) -> None:
        """Enable/disable infill spacing/angle widgets and refresh preview."""
        mode = self._fill_mode_combo.currentData()
        active = mode and mode != "none"
        self._fill_spacing.setEnabled(bool(active))
        self._fill_angle.setEnabled(bool(active))
        self._fill_keep_outline_cb.setEnabled(bool(active))
        self._schedule_preview()

    def _collect_fill_options(self) -> dict | None:
        """Read the laser-fill widget state into a plain dict for the worker."""
        mode = self._fill_mode_combo.currentData()
        if not mode or mode == "none":
            return None
        try:
            spacing = max(0.05, float(self._fill_spacing.text() or "0.5"))
        except ValueError:
            spacing = 0.5
        try:
            angle = float(self._fill_angle.text() or "0")
        except ValueError:
            angle = 0.0
        return {
            "mode": str(mode),
            "spacing": spacing,
            "angle_deg": angle,
            "keep_pattern": self._fill_keep_outline_cb.isChecked(),
        }

    def _schedule_preview(self, *_) -> None:
        if self._suspend_state_changes:
            return
        # Allow preview if zones exist OR a fill is configured (outline + fill
        # mode), even when pattern is "— None —". Otherwise require normal
        # preconditions.
        fill_active = bool(self._collect_fill_options())
        if (
            not self._zones
            and not fill_active
            and self._pattern_combo.currentText() == "— None —"
        ):
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
        fill_options = self._collect_fill_options()
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
                target=compute_preview_zones,
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
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._preview_done.emit,
                    "on_error": self._preview_error.emit,
                    "fill_options": fill_options,
                },
                daemon=True,
            ).start()
        else:
            polys_snap = list(self._edit_polys)
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            threading.Thread(
                target=compute_preview,
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
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._preview_done.emit,
                    "on_error": self._preview_error.emit,
                    "fill_options": fill_options,
                },
                daemon=True,
            ).start()

    def _handle_preview_done(self, payload: tuple) -> None:
        # Workers emit either (token, display, count) for legacy callers or
        # (token, display, count, categories) for the new three-layer split.
        if len(payload) == 4:
            preview_token, display_polys, count, categories = payload
        else:
            preview_token, display_polys, count = payload
            categories = None
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        self._preview_polys_cache = list(display_polys)
        self._preview_categories = categories or {
            "outline": [],
            "pattern": list(display_polys),
            "fill": [],
        }
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
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
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
        return self._pattern_service.apply_scale(
            polys,
            sw,
            sh,
            orig_w=self._orig_w,
            orig_h=self._orig_h,
        )

    def _fresh_outline_ids(self, count: int) -> list[str]:
        return self._pattern_service.fresh_outline_ids(count)

    def _sync_outline_ids(
        self, new_polys: list[list[tuple[float, float]]]
    ) -> list[str]:
        return self._pattern_service.sync_outline_ids(
            new_polys,
            list(self._edit_polys),
            list(self._outline_ids),
        )

    def _resolve_outline_ids(self, ids: list[str]) -> list[list[tuple[float, float]]]:
        return self._pattern_service.resolve_outline_ids(
            ids,
            self._outline_ids,
            self._edit_polys,
        )

    def _validate_outline_inputs(self, polys: list[list[tuple[float, float]]]) -> None:
        warning = self._pattern_service.validate_outline_inputs(polys)
        if warning:
            self._set_status(
                warning,
                "#e3b341",
            )

    def _snapshot_zone_jobs(self) -> list[dict]:
        jobs, warnings = self._pattern_service.snapshot_zone_jobs(
            self._zones,
            self._outline_ids,
            self._edit_polys,
        )
        if warnings:
            self._set_status(warnings[-1], "#e3b341")
        return jobs

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
        # NOTE: "— None —" is now allowed — it means outline-only / fill-only
        # zone (e.g. a region you want filled but not patterned).
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern)
            self._validate_outline_inputs(sel_polys)
        except ValueError:
            return
        interlace = self._interlace_cb.isChecked()
        # Capture the current fill settings as the per-zone fill override
        # so each zone carries its own fill snapshot. Stored as a dict to
        # match the worker's serialization contract.
        fill_snapshot = self._collect_fill_options()
        if any(
            zone.get("outline_ids", []) == sel_ids
            and zone["pattern"] == pattern
            and zone["params"] == params
            and zone["interlace"] == interlace
            and zone["scale"] == scale
            and zone.get("fill") == fill_snapshot
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
            "fill": fill_snapshot,
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
            self._cutout_status_label.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        else:
            self._cutout_status_label.setText(
                f"{n} cutout{'s' if n != 1 else ''} active \u2014 shown orange on canvas"
            )
            self._cutout_status_label.setStyleSheet("color: #f0883e; font-size: 10px;")

    def _resolve_exclusion_polys(self) -> list[list[tuple[float, float]]]:
        """Return polylines for all current exclusion IDs."""
        return self._resolve_outline_ids(self._exclusion_ids)

    # ── Preview / reset ───────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        if self._showing_preview:
            self._preview_btn.setChecked(False)
            self._on_preview_toggled(False)
        self._set_preview_status("Adjust settings to build a preview")
        self._update_preview_controls()
        self._schedule_preview()
        self._emit_state_changed()
