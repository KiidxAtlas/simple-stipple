"""_BuildMixin — UI construction methods for PatternPage."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.core.factories import section_label
from src.ui.pages.pattern.params import (
    build_halftone_widget,
    build_param_widget,
    build_tile_library_widget,
)
from src.ui.util.recent_files import KIND_DXF
from src.ui.widgets.collapsible import CollapsibleSection
from src.ui.widgets.recent_files_button import RecentFilesButton


class _BuildMixin:
    """Mixin providing all _build_* UI construction methods for PatternPage."""

    def _build_left(self, layout: QVBoxLayout) -> None:
        # New IA: Shape → Pattern → Fill → Zones → Export.
        # Each section answers a single question. Modifiers (rotation,
        # symmetry, invert, fade) live with the pattern they affect; the
        # cutout panel lives with Fill (the only place cutouts matter);
        # "Include border on separate layer" and the export summary live
        # with Export. The legacy "Output Options" junk-drawer is gone.
        self._build_shape_section(layout)
        self._build_pattern_section(layout)
        self._build_fill_section(layout)
        self._build_zones_section(layout)
        self._build_export_section(layout)
        layout.addStretch()
        # Subtitles + ⌘K palette + ⌘E/⌘R shortcuts. Installed last so all
        # widgets they reference exist.
        self._install_pattern_shortcuts()
        self._refresh_section_subtitles()

    def _build_shape_section(self, layout: QVBoxLayout) -> None:
        """SHAPE: source DXF + size (width/height/lock-aspect)."""
        shape_content = QWidget()
        shape_layout = QVBoxLayout(shape_content)
        shape_layout.setContentsMargins(0, 0, 0, 0)
        shape_layout.setSpacing(8)

        # Source row: [path] [Recent] [Browse] [↺]
        file_row = QHBoxLayout()
        self._dxf_edit = QLineEdit()
        self._dxf_edit.setPlaceholderText("Select .dxf…")
        self._dxf_edit.setToolTip("Path to a DXF outline file (drag-and-drop)")
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
        _reload_btn = QToolButton()
        _reload_btn.setText("↺")
        _reload_btn.setFixedWidth(28)
        _reload_btn.setToolTip("Re-read the current DXF file from disk  (⌘R)")
        _reload_btn.clicked.connect(self._reload_dxf)
        file_row.addWidget(_reload_btn)
        shape_layout.addLayout(file_row)

        # Original-size readout.
        orig_row = QHBoxLayout()
        orig_row.addWidget(QLabel("Original:"))
        self._orig_dims_label = QLabel("—")
        self._orig_dims_label.setProperty("role", "dim")
        orig_row.addWidget(self._orig_dims_label)
        orig_row.addStretch()
        shape_layout.addLayout(orig_row)

        # Dimensions row: [W label] [W field] [⛓] [H label] [H field]
        dims_row = QHBoxLayout()
        dims_row.setSpacing(6)
        dims_row.addWidget(QLabel("W (mm)"))
        self._scale_w = QLineEdit()
        self._scale_w.setFixedWidth(72)
        self._scale_w.setPlaceholderText("auto")
        self._scale_w.setToolTip("Target width of the outline in millimetres")
        self._scale_w.textChanged.connect(self._on_scale_w_changed)
        self._scale_w.textChanged.connect(self._schedule_preview)
        dims_row.addWidget(self._scale_w)
        self._ar_lock_btn = QToolButton()
        self._ar_lock_btn.setText("⛓")
        self._ar_lock_btn.setFixedWidth(28)
        self._ar_lock_btn.setCheckable(True)
        self._ar_lock_btn.setChecked(True)
        self._ar_lock_btn.setToolTip("Lock aspect ratio — keep W and H proportional")
        dims_row.addWidget(self._ar_lock_btn)
        dims_row.addWidget(QLabel("H (mm)"))
        self._scale_h = QLineEdit()
        self._scale_h.setFixedWidth(72)
        self._scale_h.setPlaceholderText("auto")
        self._scale_h.setToolTip("Target height of the outline in millimetres")
        self._scale_h.textChanged.connect(self._on_scale_h_changed)
        self._scale_h.textChanged.connect(self._schedule_preview)
        dims_row.addWidget(self._scale_h)
        dims_row.addStretch()
        shape_layout.addLayout(dims_row)

        self._shape_section = CollapsibleSection(
            "Shape", shape_content, expanded=True, subtitle="No file loaded"
        )
        layout.addWidget(self._shape_section)

    def _build_pattern_section(self, layout: QVBoxLayout) -> None:
        """PATTERN: combo + presets + per-pattern params + modifiers."""
        pattern_content = QWidget()
        pattern_layout = QVBoxLayout(pattern_content)
        pattern_layout.setContentsMargins(0, 0, 0, 0)
        pattern_layout.setSpacing(8)

        self._pattern_combo = QComboBox()
        self._pattern_combo.setToolTip("Choose the fill pattern")
        self._refresh_pattern_choices()
        self._pattern_combo.currentTextChanged.connect(self._switch_pattern)
        pattern_layout.addWidget(self._pattern_combo)

        # Presets: combo full-width, then Apply (primary) + Save + ⋯ overflow.
        section_label(pattern_layout, "Presets")
        self._preset_combo = QComboBox()
        self._preset_combo.setEditable(True)
        self._preset_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._preset_combo.setToolTip("Saved parameter presets for this pattern")
        self._preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._preset_combo.setMinimumContentsLength(10)
        self._preset_combo.lineEdit().setPlaceholderText("Name or select preset…")
        pattern_layout.addWidget(self._preset_combo)

        preset_actions = QHBoxLayout()
        preset_actions.setSpacing(4)
        load_preset_btn = QPushButton("Apply Preset")
        load_preset_btn.setToolTip(
            "Apply the selected preset to current parameters  (⌘P)"
        )
        load_preset_btn.clicked.connect(self._apply_selected_preset)
        preset_actions.addWidget(load_preset_btn, stretch=1)
        save_preset_btn = QPushButton("Save")
        save_preset_btn.setFixedWidth(60)
        save_preset_btn.setToolTip("Save current parameters as a new preset")
        save_preset_btn.clicked.connect(self._save_preset)
        preset_actions.addWidget(save_preset_btn)
        overflow_btn = QToolButton()
        overflow_btn.setText("⋯")
        overflow_btn.setFixedWidth(32)
        overflow_btn.setToolTip("More preset actions")
        overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        from PySide6.QtWidgets import QMenu
        overflow_menu = QMenu(overflow_btn)
        overflow_menu.addAction("Delete preset", self._delete_selected_preset)
        overflow_menu.addSeparator()
        overflow_menu.addAction("Manage presets…", self._open_preset_manager)
        overflow_btn.setMenu(overflow_menu)
        preset_actions.addWidget(overflow_btn)
        pattern_layout.addLayout(preset_actions)
        self._refresh_preset_combo()

        # Per-pattern dynamic widgets (pattern_widgets[name] is shown/hidden
        # by _switch_pattern). Tile library + halftone hidden by default.
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
            pattern_layout.addWidget(w)
            w.hide()
        self._tile_library_w = build_tile_library_widget(self, _sp)
        pattern_layout.addWidget(self._tile_library_w)
        self._tile_library_w.hide()
        halftone_w = build_halftone_widget(self, _sp)
        self._pattern_widgets["Image Halftone"] = halftone_w
        pattern_layout.addWidget(halftone_w)
        halftone_w.hide()

        # ── Modifiers (folded in from old "Output Options"): rotation,
        # interlace, mirror, invert, fade. These are all "make the pattern
        # look different" knobs and belong with the pattern.
        section_label(pattern_layout, "Modifiers")

        rot_row = QGridLayout()
        rot_row.setContentsMargins(0, 0, 0, 0)
        rot_row.addWidget(QLabel("Rotation (°)"), 0, 0)
        self._pattern_rotation = QLineEdit("0")
        self._pattern_rotation.setFixedWidth(80)
        self._pattern_rotation.setToolTip(
            "Rotate generated pattern around the outline center"
        )
        self._pattern_rotation.textChanged.connect(self._schedule_preview)
        rot_row.addWidget(self._pattern_rotation, 0, 1)
        rot_row.addWidget(QLabel("Fade (mm)"), 1, 0)
        self._border_fade = QLineEdit("0")
        self._border_fade.setFixedWidth(80)
        self._border_fade.setToolTip("Thin the pattern near the outline edge. 0 = off.")
        self._border_fade.textChanged.connect(self._schedule_preview)
        rot_row.addWidget(self._border_fade, 1, 1)
        pattern_layout.addLayout(rot_row)

        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(8)
        self._interlace_cb = QCheckBox("Interlace")
        self._interlace_cb.setToolTip("Offset-grid interlacing for tessellation")
        self._interlace_cb.stateChanged.connect(self._schedule_preview)
        toggles_row.addWidget(self._interlace_cb)
        self._invert_fill_cb = QCheckBox("Invert (outside)")
        self._invert_fill_cb.setToolTip(
            "Generate the pattern in the region OUTSIDE the outline (background fill)"
        )
        self._invert_fill_cb.stateChanged.connect(self._schedule_preview)
        toggles_row.addWidget(self._invert_fill_cb)
        toggles_row.addStretch()
        toggles_row.addWidget(QLabel("Mirror"))
        self._mirror_v_cb = QCheckBox("↔")
        self._mirror_v_cb.setToolTip("Mirror left ↔ right")
        self._mirror_v_cb.stateChanged.connect(self._schedule_preview)
        toggles_row.addWidget(self._mirror_v_cb)
        self._mirror_h_cb = QCheckBox("↕")
        self._mirror_h_cb.setToolTip("Mirror top ↔ bottom")
        self._mirror_h_cb.stateChanged.connect(self._schedule_preview)
        toggles_row.addWidget(self._mirror_h_cb)
        pattern_layout.addLayout(toggles_row)

        self._pattern_section = CollapsibleSection(
            "Pattern", pattern_content, expanded=True, subtitle=""
        )
        layout.addWidget(self._pattern_section)

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
        zones_hint.setProperty("role", "hint")
        zones_layout.addWidget(zones_hint)

        self._zones_section = CollapsibleSection(
            "Zones", zones_content, expanded=False, subtitle="No zones assigned"
        )
        layout.addWidget(self._zones_section)

    def _build_fill_section(self, layout: QVBoxLayout) -> None:
        """FILL: laser-engrave infill mode + cutouts panel."""
        fill_content = QWidget()
        fill_layout = QVBoxLayout(fill_content)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        fill_layout.setSpacing(8)

        # Mode selector spans the row (the question this section answers).
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        self._fill_mode_combo = QComboBox()
        self._fill_mode_combo.addItem("None", "none")
        self._fill_mode_combo.addItem("Lines", "lines")
        self._fill_mode_combo.setToolTip(
            "Fill the shape with parallel laser-engrave lines.\n"
            "None = pattern strokes only."
        )
        self._fill_mode_combo.currentIndexChanged.connect(self._on_fill_mode_changed)
        mode_row.addWidget(self._fill_mode_combo, stretch=1)
        fill_layout.addLayout(mode_row)

        params_row = QGridLayout()
        params_row.addWidget(QLabel("Spacing (mm)"), 0, 0)
        self._fill_spacing = QLineEdit("0.5")
        self._fill_spacing.setFixedWidth(80)
        self._fill_spacing.setToolTip("Distance between adjacent infill lines")
        self._fill_spacing.textChanged.connect(self._schedule_preview)
        params_row.addWidget(self._fill_spacing, 0, 1)
        params_row.addWidget(QLabel("Angle (°)"), 1, 0)
        self._fill_angle = QLineEdit("0")
        self._fill_angle.setFixedWidth(80)
        self._fill_angle.setToolTip("Angle of the infill direction")
        self._fill_angle.textChanged.connect(self._schedule_preview)
        params_row.addWidget(self._fill_angle, 1, 1)

        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_row.addWidget(QLabel("Targets"))
        self._fill_target_outline_cb = QCheckBox("Outline")
        self._fill_target_outline_cb.setToolTip(
            "Fill the input outline region (minus exclusions)"
        )
        self._fill_target_outline_cb.setChecked(True)
        self._fill_target_outline_cb.stateChanged.connect(self._schedule_preview)
        target_row.addWidget(self._fill_target_outline_cb)
        self._fill_target_pattern_cb = QCheckBox("Pattern cells")
        self._fill_target_pattern_cb.setToolTip(
            "Hatch each closed pattern stroke (tiles, tessellation, …)"
        )
        self._fill_target_pattern_cb.setChecked(False)
        self._fill_target_pattern_cb.stateChanged.connect(self._schedule_preview)
        target_row.addWidget(self._fill_target_pattern_cb)
        target_row.addStretch()

        self._fill_keep_outline_cb = QCheckBox("Keep pattern strokes alongside fill")
        self._fill_keep_outline_cb.setToolTip(
            "Output both pattern strokes and laser-fill lines.\n"
            "Uncheck for fill-only output."
        )
        self._fill_keep_outline_cb.setChecked(True)
        self._fill_keep_outline_cb.stateChanged.connect(self._schedule_preview)

        # Pack params/targets/keep-outline into a collapsible container so
        # they can be hidden when mode == "none".
        self._fill_params_container = QWidget()
        _fpc_layout = QVBoxLayout(self._fill_params_container)
        _fpc_layout.setContentsMargins(0, 0, 0, 0)
        _fpc_layout.setSpacing(8)
        _fpc_layout.addLayout(params_row)
        _fpc_layout.addLayout(target_row)
        _fpc_layout.addWidget(self._fill_keep_outline_cb)
        self._fill_params_container.setVisible(False)
        fill_layout.addWidget(self._fill_params_container)

        # Cutouts callout — styled card that doubles as status + action.
        section_label(fill_layout, "Cutouts")

        self._cutout_callout = QFrame()
        self._cutout_callout.setObjectName("cutoutCallout")
        cutout_callout_layout = QHBoxLayout(self._cutout_callout)
        cutout_callout_layout.setContentsMargins(8, 6, 8, 6)
        cutout_callout_layout.setSpacing(6)
        self._cutout_icon = QLabel("ℹ")
        self._cutout_icon.setFixedWidth(14)
        self._cutout_icon.setProperty("role", "cutout-icon")
        cutout_callout_layout.addWidget(self._cutout_icon)
        self._cutout_status_label = QLabel(
            "Right-click a shape on canvas to mark as cutout"
        )
        self._cutout_status_label.setWordWrap(True)
        self._cutout_status_label.setProperty("role", "cutout-desc")
        cutout_callout_layout.addWidget(self._cutout_status_label, stretch=1)
        self._cutout_clear_btn = QPushButton("Clear")
        self._cutout_clear_btn.setFixedWidth(52)
        self._cutout_clear_btn.setToolTip("Remove all cutout assignments")
        self._cutout_clear_btn.setVisible(False)
        self._cutout_clear_btn.clicked.connect(self._clear_exclusions)
        cutout_callout_layout.addWidget(self._cutout_clear_btn)
        self._apply_cutout_callout_style(active=False)
        fill_layout.addWidget(self._cutout_callout)

        self._mark_cutout_btn = QPushButton("Mark Selected as Cutout")
        self._mark_cutout_btn.setMinimumHeight(28)
        self._mark_cutout_btn.setToolTip(
            "Mark the selected canvas shapes as cutout regions.\n"
            "Cutouts exclude areas from laser fill.  Right-click a shape to toggle individually."
        )
        self._mark_cutout_btn.clicked.connect(self._mark_selection_as_cutout)
        fill_layout.addWidget(self._mark_cutout_btn)

        self._fill_section = CollapsibleSection(
            "Fill", fill_content, expanded=False, subtitle="None"
        )
        layout.addWidget(self._fill_section)

        # Initial enable state — fill controls disabled while mode == None.
        self._on_fill_mode_changed()

    def _build_export_section(self, layout: QVBoxLayout) -> None:
        """EXPORT: per-export options + action + status."""
        section_label(layout, "Export")

        # Layer-naming option lives here (not Pattern) — it changes the
        # output file structure, nothing else.
        self._include_border_cb = QCheckBox("Border on separate layer")
        self._include_border_cb.setToolTip(
            "Writes pattern fill to 'background' and each outline to\n"
            "'outline', 'outline_1', … layers (CAM-friendly)."
        )
        self._include_border_cb.setChecked(True)
        self._include_border_cb.stateChanged.connect(self._schedule_preview)
        layout.addWidget(self._include_border_cb)

        self._export_open_paths_cb = QCheckBox("Export as Open Paths")
        self._export_open_paths_cb.setToolTip(
            "Write pattern strokes as open polylines (no forced closure)."
        )
        self._export_open_paths_cb.setChecked(False)
        layout.addWidget(self._export_open_paths_cb)

        # Summary chip — one-line readout of what will be written.
        self._summary_chip = QLabel("")
        self._summary_chip.setProperty("role", "chip")
        self._summary_chip.setProperty("tone", "neutral")
        self._summary_chip.setWordWrap(True)
        self._summary_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._summary_chip)

        from PySide6.QtWidgets import QProgressBar
        self._gen_btn = QPushButton("Export DXF")
        self._gen_btn.setMinimumHeight(38)
        self._gen_btn.setToolTip("Generate the pattern fill and save as a DXF  (⌘E)")
        self._gen_btn.setProperty("role", "primary")
        self._gen_btn.clicked.connect(self._generate)
        layout.addWidget(self._gen_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)  # only shown while exporting
        layout.addWidget(self._progress)

        # Status as a chip — _set_status() still drives it via the same
        # QLabel attribute name, so callers keep working unchanged.
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the exported file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_in_finder)
        layout.addWidget(self._reveal_btn)
