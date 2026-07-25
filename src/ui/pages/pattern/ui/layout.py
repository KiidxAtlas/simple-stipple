"""Widget construction for the Pattern page — left/right panel layout and
each collapsible section. Extracted from ``PatternPage`` (see plan.md
Section 9.1); follows the same ``page: Any``-first free-function
convention already used by ``domain/session.py`` and ``ui/params.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.canvas.canvas_runtime import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
    PatternCanvasPageRuntime,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components import (
    CollapsibleSection,
    RecentFilesButton,
    collapsible_content_widget,
    content_splitter,
    make_resettable_line_edit,
    primary_button,
    section_label,
    surface_frame,
)
from src.ui.pages.pattern.domain.defaults import (
    DEFAULT_BORDER_FADE,
    DEFAULT_DENSITY_ANGLE,
    DEFAULT_DENSITY_STRENGTH,
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_SPACING,
    DEFAULT_GRID_SPACING_MM,
    DEFAULT_GRID_VISIBLE,
    DEFAULT_MIN_ISLAND_AREA,
    DEFAULT_MIN_SEGMENT,
    DEFAULT_PATTERN_ROTATION,
    DEFAULT_PREVIEW_QUALITY,
    SCALE_MAX_MM,
    SCALE_MIN_MM,
)
from src.ui.pages.pattern.ui.form import build_param_widget
from src.ui.util import KIND_DXF
from src.ui.widgets.canvas.status_strip import CanvasStatusStrip

def build_right(page: Any, layout: QVBoxLayout) -> None:
    page._preview_btn = QPushButton("Show Preview")
    page._preview_btn.setCheckable(True)
    page._preview_btn.setMinimumHeight(28)
    page._preview_btn.setToolTip("Toggle between outline editing and pattern preview")
    page._preview_btn.clicked.connect(page._on_preview_clicked)
    page._cancel_preview_btn = QToolButton()
    page._cancel_preview_btn.setText("Cancel")
    page._cancel_preview_btn.setToolTip("Cancel the preview currently computing")
    page._cancel_preview_btn.setAccessibleName("Cancel preview")
    page._cancel_preview_btn.setVisible(False)
    page._cancel_preview_btn.clicked.connect(lambda: page._on_preview_clicked(False))
    page._auto_preview_cb = QCheckBox("Auto-preview")
    page._auto_preview_cb.setChecked(True)
    page._auto_preview_cb.setToolTip(
        "Show completed previews automatically when no selection or drawing gesture is active"
    )

    page._reset_preview_btn = QPushButton("Reset")
    page._reset_preview_btn.setToolTip("Clear the preview cache and rebuild")
    page._reset_preview_btn.clicked.connect(page._reset_preview)

    page._preview_status = QLabel("No preview available")
    page._set_preview_status("No preview available")
    page._preview_status.setWordWrap(True)

    page._canvas = DxfCanvas(
        selectable=True,
        on_change=page._on_sel_change,
        on_mode_change=page._on_canvas_mode_change,
        on_poly_change=page._on_canvas_geometry_change,
        on_send_selected_to_draft=page._on_send_selected_to_draft_from_canvas,
        on_use_selected_as_custom_tile=page.use_custom_tile,
        on_cutout_toggle=page._on_canvas_cutout_toggle,
        on_outline_role_change=page._on_canvas_outline_role_change,
        on_outline_role_explain=page._explain_outline_role,
        on_pattern_cell_cutout_toggle=page._on_pattern_cell_cutout_toggle,
        on_create_zone_from_selection=page._assign_zone,
        draft_profile=True,
    )
    page._canvas.set_empty_message(
        "Start a pattern\nImport an outline on the left, drop a DXF here, or send shapes from Draft"
    )
    page._canvas.set_grid_visible(DEFAULT_GRID_VISIBLE)
    page._canvas.set_grid_snap(False)
    page._canvas.set_grid_spacing(DEFAULT_GRID_SPACING_MM)
    page._canvas.set_selection_follows_geometry(True)
    page._canvas.backgroundSelectionChanged.connect(page._on_engraving_selection_changed)

    page._toolbar_module = CanvasToolbarModule(
        canvas=page._canvas,
        on_mode=page._on_toolbar_mode,
        on_fit=page._canvas.fit,
        extra_widgets=[
            page._auto_preview_cb,
            page._preview_btn,
            page._cancel_preview_btn,
            page._reset_preview_btn,
        ],
    )
    layout.addWidget(page._toolbar_module)

    page._grid_module = CanvasGridModule(
        canvas=page._canvas,
        on_changed=page._refresh_canvas_panels,
    )
    layout.addWidget(page._grid_module)
    page._precision_bar = page._grid_module

    # Placed at the bottom of the page (after the splitter) so every
    # canvas page keeps the same anatomy: toolbars up top, canvas in
    # the middle, status strip along the bottom — same as Draft.
    page._canvas_status = CanvasStatusStrip()
    page._canvas_status.set_zoom_callback(page._on_zoom_preset)

    canvas_shell = QWidget()
    canvas_shell_layout = QVBoxLayout(canvas_shell)
    canvas_shell_layout.setContentsMargins(0, 0, 0, 0)
    canvas_shell_layout.setSpacing(8)
    canvas_shell_layout.addWidget(page._preview_status)
    canvas_shell_layout.addWidget(page._canvas, stretch=1)

    side_panel = QWidget()
    side_panel.setProperty("role", "pattern-side-panel")
    side_layout = QVBoxLayout(side_panel)
    # Keep sticky footer controls clear of the splitter and window edge.
    side_layout.setContentsMargins(6, 0, 6, 6)
    side_layout.setSpacing(8)
    zone_scroll = QScrollArea()
    zone_scroll.setWidgetResizable(True)
    zone_scroll.setFrameShape(QFrame.Shape.NoFrame)
    zone_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    zone_scroll.setWidget(page._zones_section)
    page._zone_layers_splitter = QSplitter(Qt.Orientation.Vertical)
    page._zone_layers_splitter.setChildrenCollapsible(False)
    page._zone_layers_splitter.addWidget(zone_scroll)

    page._layer_module = CanvasLayerTreeModule(
        canvas=page._canvas,
        title="Layers",
        editable=True,
        get_active_layer_name=lambda: (
            "pattern_preview" if page._showing_preview else "pattern_active"
        ),
        build_layer_rows=page._build_layer_tree_rows,
        on_selection_requested=page._on_browser_selection_requested,
        on_fit_requested=page._fit_selection,
        on_visibility_changed=page._refresh_canvas_panels,
    )
    page._layers_tree = page._layer_module.tree
    page._layer_sidebar = page._layer_module.controller
    # Wire outline-mode shape rename to the runtime's label store.
    page._layers_tree.shapeRenamed.connect(page._on_shape_renamed)
    page._zone_layers_splitter.addWidget(page._layer_module)
    page._zone_layers_splitter.setStretchFactor(0, 3)
    page._zone_layers_splitter.setStretchFactor(1, 2)
    page._zone_layers_splitter.setSizes([390, 220])
    side_layout.addWidget(page._zone_layers_splitter, stretch=1)
    build_export_section(page, side_layout)

    page._canvas_runtime = PatternCanvasPageRuntime(
        canvas=page._canvas,
        toolbar_module=page._toolbar_module,
        layer_sidebar=page._layer_sidebar,
        canvas_status=page._canvas_status,
        precision_bar=page._precision_bar,
        get_orig_polys=lambda: page._edit_polys,
        get_showing_preview=lambda: page._showing_preview,
        is_preview_running=lambda: page._preview_task.running,
        has_preview_cache=lambda: bool(page._preview_polys_cache),
        has_zones=lambda: bool(page._zones),
        get_preview_categories=lambda: page._preview_categories,
    )

    splitter = content_splitter(canvas_shell, side_panel, sizes=(780, 340))
    # Layers, zones, and the only persistent Export surface share this
    # sidebar. Keep it visible at every supported window width.
    splitter.setCollapsible(1, False)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    page._canvas_splitter = splitter
    layout.addWidget(splitter, stretch=1)
    layout.addWidget(page._canvas_status)
    page._refresh_canvas_panels()


def build_pattern_properties_panel(page: Any) -> QWidget:
    panel = surface_frame("panel")
    root = QVBoxLayout(panel)
    root.setContentsMargins(10, 10, 10, 10)
    root.setSpacing(6)
    title = QLabel("Zones")
    title.setProperty("role", "section-title")
    root.addWidget(title)
    root.addWidget(page._zones_section)

    page._pattern_props_scope = QLabel("New zone defaults")
    page._pattern_props_scope.setWordWrap(True)
    page._pattern_props_scope.setProperty("role", "hint")
    root.addWidget(page._pattern_props_scope)

    output_row = QHBoxLayout()
    output_row.setContentsMargins(0, 0, 0, 0)
    output_row.setSpacing(8)
    output_row.addWidget(QLabel("Output"))
    output_row.addWidget(page._zone_output_combo, stretch=1)
    root.addLayout(output_row)

    # Reuse the real editors rather than maintaining a second, partial
    # set of parameter widgets.  Qt reparents these sections out of the
    # left workflow column into the selected-zone properties panel.
    root.addWidget(page._pattern_section)
    root.addWidget(page._fill_section)
    hint = QLabel("All changes above apply to the selected zone and rebuild the live preview.")
    hint.setWordWrap(True)
    hint.setProperty("role", "hint-sm")
    root.addWidget(hint)
    page._refresh_zone_list()
    refresh_pattern_properties_panel(page)
    return panel


def refresh_pattern_properties_panel(page: Any) -> None:
    if not hasattr(page, "_pattern_props_scope"):
        return
    row = page._zone_list.currentRow() if hasattr(page, "_zone_list") else -1
    if 0 <= row < len(page._zones):
        page._pattern_props_scope.setText(f"Editing Zone {row + 1}")
        page._pattern_props_scope.setProperty("editing", True)
    else:
        page._pattern_props_scope.setText(
            "New zone defaults — select geometry, then create a zone."
        )
        page._pattern_props_scope.setProperty("editing", False)
    page._pattern_props_scope.style().unpolish(page._pattern_props_scope)
    page._pattern_props_scope.style().polish(page._pattern_props_scope)


def build_left(page: Any, layout: QVBoxLayout) -> None:
    build_shape_section(page, layout)
    # Construct Zones before Pattern signal wiring; _build_right reparents
    # the completed card into the right inspector above Layers.
    build_zones_section(page, layout)
    build_pattern_section(page, layout)
    build_fill_section(page, layout)
    build_image_engraving_section(page, layout)
    layout.addStretch()
    page._install_pattern_shortcuts()
    page._refresh_section_subtitles()


def build_shape_section(page: Any, layout: QVBoxLayout) -> None:
    shape_content, shape_layout = collapsible_content_widget(spacing=8)
    file_row = QHBoxLayout()
    page._dxf_edit = QLineEdit()
    page._dxf_edit.setPlaceholderText("Select DXF, FVI, or SVG…")
    page._dxf_edit.setToolTip("Path to a DXF, FVI, or SVG outline (drag-and-drop supported)")
    page._dxf_edit.editingFinished.connect(page._reload_dxf)
    file_row.addWidget(page._dxf_edit, stretch=1)
    page._recent_btn = RecentFilesButton(
        page._settings,
        KIND_DXF,
        empty_message="No recent vector files.",
    )
    page._recent_btn.setToolTip("Pick from recently opened vector files")
    page._recent_btn.fileSelected.connect(page._quick_load)
    file_row.addWidget(page._recent_btn)
    browse_btn = QPushButton("Browse")
    browse_btn.setFixedWidth(72)
    browse_btn.setToolTip("Browse for a DXF, FVI, or SVG outline")
    browse_btn.clicked.connect(page._browse_dxf)
    file_row.addWidget(browse_btn)
    _reload_btn = QToolButton()
    _reload_btn.setIcon(
        QIcon(str(Path(__file__).parents[3] / "style" / "icons" / "reload.svg"))
    )
    _reload_btn.setAccessibleName("Reload outline file")
    _reload_btn.setFixedWidth(28)
    _reload_btn.setToolTip("Re-read the current vector file from disk  (⌘R)")
    _reload_btn.clicked.connect(page._reload_dxf)
    file_row.addWidget(_reload_btn)
    shape_layout.addLayout(file_row)
    orig_row = QHBoxLayout()
    orig_row.addWidget(QLabel("Original:"))
    page._orig_dims_label = QLabel("—")
    page._orig_dims_label.setProperty("role", "dim")
    orig_row.addWidget(page._orig_dims_label)
    orig_row.addStretch()
    shape_layout.addLayout(orig_row)
    dims_row = QHBoxLayout()
    dims_row.setSpacing(6)
    dims_row.addWidget(QLabel("W (mm)"))
    page._scale_w = QLineEdit()
    page._scale_w.setValidator(QDoubleValidator(SCALE_MIN_MM, SCALE_MAX_MM, 6, page._scale_w))
    page._scale_w.setFixedWidth(72)
    page._scale_w.setPlaceholderText("auto")
    page._scale_w.setToolTip("Target width of the outline in millimetres")
    page._scale_w.textChanged.connect(page._on_scale_w_changed)
    page._scale_w.textChanged.connect(page._schedule_preview)
    dims_row.addWidget(page._scale_w)
    page._ar_lock_btn = QToolButton()
    page._ar_lock_btn.setIcon(
        QIcon(str(Path(__file__).parents[3] / "style" / "icons" / "lock.svg"))
    )
    page._ar_lock_btn.setAccessibleName("Lock outline aspect ratio")
    page._ar_lock_btn.setFixedWidth(28)
    page._ar_lock_btn.setCheckable(True)
    page._ar_lock_btn.setChecked(True)
    page._ar_lock_btn.setToolTip("Lock aspect ratio — keep W and H proportional")
    dims_row.addWidget(page._ar_lock_btn)
    dims_row.addWidget(QLabel("H (mm)"))
    page._scale_h = QLineEdit()
    page._scale_h.setValidator(QDoubleValidator(SCALE_MIN_MM, SCALE_MAX_MM, 6, page._scale_h))
    page._scale_h.setFixedWidth(72)
    page._scale_h.setPlaceholderText("auto")
    page._scale_h.setToolTip("Target height of the outline in millimetres")
    page._scale_h.textChanged.connect(page._on_scale_h_changed)
    page._scale_h.textChanged.connect(page._schedule_preview)
    dims_row.addWidget(page._scale_h)
    dims_row.addStretch()
    shape_layout.addLayout(dims_row)
    page._shape_section = CollapsibleSection(
        "Shape", shape_content, expanded=True, subtitle="No file loaded"
    )
    layout.addWidget(page._shape_section)


def build_pattern_section(page: Any, layout: QVBoxLayout) -> None:
    pattern_content, pattern_layout = collapsible_content_widget(spacing=8)
    page._pattern_combo = QComboBox()
    page._pattern_combo.setToolTip("Choose the fill pattern")
    page._refresh_pattern_choices()
    page._pattern_combo.setCurrentText("— None —")
    page._pattern_combo.currentTextChanged.connect(page._switch_pattern)
    pattern_layout.addWidget(page._pattern_combo)

    section_label(pattern_layout, "Presets")
    page._preset_combo = QComboBox()
    page._preset_combo.setEditable(True)
    page._preset_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    page._preset_combo.setToolTip("Saved parameter presets for this pattern")
    page._preset_combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    page._preset_combo.setMinimumContentsLength(10)
    preset_editor = page._preset_combo.lineEdit()
    if preset_editor is not None:
        preset_editor.setPlaceholderText("Name or select preset…")
    pattern_layout.addWidget(page._preset_combo)
    preset_actions = QHBoxLayout()
    preset_actions.setSpacing(4)
    load_preset_btn = QPushButton("Apply Preset")
    load_preset_btn.setToolTip("Apply the selected preset to current parameters  (⌘P)")
    load_preset_btn.clicked.connect(page._apply_selected_preset)
    preset_actions.addWidget(load_preset_btn, stretch=1)
    save_preset_btn = QPushButton("Save")
    save_preset_btn.setFixedWidth(60)
    save_preset_btn.setToolTip("Save current parameters as a new preset")
    save_preset_btn.clicked.connect(page._save_preset)
    preset_actions.addWidget(save_preset_btn)
    overflow_btn = QToolButton()
    overflow_btn.setText("Options")
    overflow_btn.setProperty("role", "overflow")
    overflow_btn.setFixedWidth(72)
    overflow_btn.setToolTip("More preset actions")
    overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    overflow_menu = QMenu(overflow_btn)
    overflow_menu.addAction("Delete preset", page._delete_selected_preset)
    overflow_menu.addSeparator()
    overflow_menu.addAction("Manage presets…", page._open_preset_manager)
    overflow_btn.setMenu(overflow_menu)
    preset_actions.addWidget(overflow_btn)
    pattern_layout.addLayout(preset_actions)
    page._refresh_preset_combo()
    _sp = page._schedule_preview
    _named_patterns = [
        "Custom Tile",
        "Flow Lines",
        "Honeycomb",
        "Gradient Honeycomb",
        "Basketweave",
        "Stipple Dots",
        "Brick",
        "Mesh",
        "Voronoi",
        "Topographic",
    ]
    page._pattern_widgets: dict[str, QWidget] = {}
    for name in _named_patterns:
        w = build_param_widget(page, name, _sp)
        page._pattern_widgets[name] = w
        pattern_layout.addWidget(w)
        w.hide()
    tile_actions = QHBoxLayout()
    page._tile_name_edit = QLineEdit()
    page._tile_name_edit.setPlaceholderText("Custom tile name")
    page._tile_name_edit.setToolTip("Name the tile before saving it to the library")
    page._tile_name_edit.returnPressed.connect(page._save_tile_motif)
    tile_actions.addWidget(page._tile_name_edit, stretch=1)
    page._save_tile_btn = QPushButton("Save custom tile")
    page._save_tile_btn.setToolTip(
        "Save the current Custom Tile geometry into the Pattern list"
    )
    page._save_tile_btn.clicked.connect(page._save_tile_motif)
    tile_actions.addWidget(page._save_tile_btn)
    page._delete_tile_btn = QPushButton("Delete custom")
    page._delete_tile_btn.setToolTip("Delete the selected custom pattern")
    page._delete_tile_btn.clicked.connect(page._delete_tile_motif)
    tile_actions.addWidget(page._delete_tile_btn)
    open_tiles_btn = QPushButton("Open Tiles Folder")
    open_tiles_btn.setToolTip("Open the configured DXF custom-tile library")
    open_tiles_btn.clicked.connect(page._open_custom_tiles_folder)
    tile_actions.addWidget(open_tiles_btn)
    page._save_tile_btn.hide()
    page._tile_name_edit.hide()
    page._delete_tile_btn.hide()
    pattern_layout.addLayout(tile_actions)
    tile_asset_actions = QHBoxLayout()
    page._tile_asset_status = QLabel("")
    page._tile_asset_status.setWordWrap(True)
    page._tile_asset_status.setProperty("role", "status-neutral")
    tile_asset_actions.addWidget(page._tile_asset_status, stretch=1)
    page._locate_tile_btn = QPushButton("Locate…")
    page._locate_tile_btn.clicked.connect(page._locate_tile_asset)
    tile_asset_actions.addWidget(page._locate_tile_btn)
    page._repair_tile_btn = QPushButton("Repair / Convert")
    page._repair_tile_btn.clicked.connect(page._repair_tile_asset)
    tile_asset_actions.addWidget(page._repair_tile_btn)
    page._tile_asset_status.hide()
    page._locate_tile_btn.hide()
    page._repair_tile_btn.hide()
    pattern_layout.addLayout(tile_asset_actions)
    page._modifiers_label = section_label(pattern_layout, "Modifiers")
    page._modifiers_widget = QWidget()
    rot_row = QGridLayout(page._modifiers_widget)
    rot_row.setContentsMargins(0, 0, 0, 0)
    rot_row.addWidget(QLabel("Rotation (°)"), 0, 0)
    page._pattern_rotation = QLineEdit(DEFAULT_PATTERN_ROTATION)
    make_resettable_line_edit(page._pattern_rotation, DEFAULT_PATTERN_ROTATION)
    page._pattern_rotation.setValidator(
        QDoubleValidator(-36000, 36000, 4, page._pattern_rotation)
    )
    page._pattern_rotation.setFixedWidth(80)
    page._pattern_rotation.setToolTip("Rotate generated pattern around the outline center")
    page._pattern_rotation.textChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._pattern_rotation, 0, 1)
    rot_row.addWidget(QLabel("Pattern size (%)"), 1, 0)
    page._pattern_size_percent = QLineEdit("100")
    page._pattern_size_percent.setValidator(
        QDoubleValidator(1, 10000, 3, page._pattern_size_percent)
    )
    page._pattern_size_percent.setToolTip(
        "Scale pattern elements and spacing without resizing the outline"
    )
    page._pattern_size_percent.textChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._pattern_size_percent, 1, 1)
    rot_row.addWidget(QLabel("Fade (mm)"), 2, 0)
    page._border_fade = QLineEdit(DEFAULT_BORDER_FADE)
    make_resettable_line_edit(page._border_fade, DEFAULT_BORDER_FADE)
    page._border_fade.setValidator(QDoubleValidator(0, 1e9, 6, page._border_fade))
    page._border_fade.setFixedWidth(80)
    page._border_fade.setToolTip("Thin the pattern near the outline edge. 0 = off.")
    page._border_fade.textChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._border_fade, 2, 1)
    rot_row.addWidget(QLabel("Density field"), 3, 0)
    page._density_mode_combo = QComboBox()
    page._density_mode_combo.addItems(["Uniform", "Horizontal", "Radial", "Boundary"])
    page._density_mode_combo.setToolTip(
        "Deterministically thin pattern elements across a field"
    )
    page._density_mode_combo.currentTextChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._density_mode_combo, 3, 1)
    rot_row.addWidget(QLabel("Density strength"), 4, 0)
    page._density_strength = QLineEdit(DEFAULT_DENSITY_STRENGTH)
    make_resettable_line_edit(page._density_strength, DEFAULT_DENSITY_STRENGTH)
    page._density_strength.setValidator(QDoubleValidator(0, 1, 4, page._density_strength))
    page._density_strength.setToolTip("0 = uniform; 1 = strongest thinning")
    page._density_strength.textChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._density_strength, 4, 1)
    rot_row.addWidget(QLabel("Density angle (°)"), 5, 0)
    page._density_angle = QLineEdit(DEFAULT_DENSITY_ANGLE)
    make_resettable_line_edit(page._density_angle, DEFAULT_DENSITY_ANGLE)
    page._density_angle.setValidator(QDoubleValidator(-36000, 36000, 4, page._density_angle))
    page._density_angle.setToolTip("Direction of the linear density gradient")
    page._density_angle.textChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._density_angle, 5, 1)
    page._density_reverse = QCheckBox("Reverse density")
    page._density_reverse.setToolTip("Swap the dense and sparse sides of the field")
    page._density_reverse.stateChanged.connect(page._schedule_preview)
    rot_row.addWidget(page._density_reverse, 6, 0, 1, 2)
    pattern_layout.addWidget(page._modifiers_widget)
    page._modifiers_label.hide()
    page._modifiers_widget.hide()
    page._pattern_section = CollapsibleSection(
        "Pattern", pattern_content, expanded=True, subtitle=""
    )
    layout.addWidget(page._pattern_section)


def build_zones_section(page: Any, layout: QVBoxLayout) -> None:
    zones_content, zones_layout = collapsible_content_widget(spacing=6)
    section_label(zones_layout, "Assigned zones")
    scope_hint = QLabel(
        "Select shapes on the canvas, then create a zone. Selecting a zone below highlights its shapes."
    )
    scope_hint.setWordWrap(True)
    scope_hint.setProperty("role", "hint")
    zones_layout.addWidget(scope_hint)
    assign_row = QHBoxLayout()
    page._assign_zone_btn = QPushButton("Create Zone from Selection")
    page._assign_zone_btn.setMinimumHeight(30)
    page._assign_zone_btn.setToolTip(
        "Create a zone for the selected outlines using the current settings."
    )
    page._assign_zone_btn.clicked.connect(page._assign_zone)
    assign_row.addWidget(page._assign_zone_btn, stretch=1)
    zones_layout.addLayout(assign_row)
    page._zone_list = QListWidget()
    page._zone_list.setMinimumHeight(120)
    page._zone_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page._zone_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page._zone_list.setToolTip(
        "Assigned pattern zones — each outline group with its own pattern"
    )
    page._zone_list.currentRowChanged.connect(page._on_zone_selected)
    page._zone_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    page._zone_list.customContextMenuRequested.connect(page._show_zone_context_menu)
    page._delete_zone_shortcut = QShortcut(QKeySequence.StandardKey.Delete, page._zone_list)
    page._delete_zone_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
    page._delete_zone_shortcut.activated.connect(page._remove_selected_zone)
    zones_layout.addWidget(page._zone_list)

    section_label(zones_layout, "Selected zone settings")
    page._pattern_props_scope = QLabel("Select a zone to edit its settings")
    page._pattern_props_scope.setWordWrap(True)
    page._pattern_props_scope.setProperty("role", "zone-edit-scope")
    zones_layout.addWidget(page._pattern_props_scope)

    page._zone_output_combo = QComboBox()
    page._zone_output_combo.addItem("Pattern + Fill", "pattern_fill")
    page._zone_output_combo.addItem("Pattern only", "pattern")
    page._zone_output_combo.addItem("Fill only", "fill")
    page._zone_output_combo.addItem("Outline only", "outline")
    page._zone_output_combo.addItem("Disabled", "none")
    page._zone_output_combo.setToolTip(
        "Output for the selected zone, or the next zone when none is selected"
    )
    page._zone_output_combo.currentIndexChanged.connect(page._schedule_preview)
    page._zone_output_combo.currentIndexChanged.connect(page._live_update_selected_zone)
    pattern_row = QHBoxLayout()
    pattern_row.addWidget(QLabel("Pattern"))
    page._zone_pattern_combo = QComboBox()
    page._populate_pattern_combo(page._zone_pattern_combo)
    page._zone_pattern_combo.currentTextChanged.connect(page._rebuild_zone_parameter_editor)
    page._zone_pattern_combo.currentTextChanged.connect(page._live_update_selected_zone)
    page._zone_pattern_combo.currentTextChanged.connect(page._update_zone_actions)
    pattern_row.addWidget(page._zone_pattern_combo, stretch=1)
    zones_layout.addLayout(pattern_row)
    page._zone_params_widget = QWidget()
    page._zone_params_grid = QGridLayout(page._zone_params_widget)
    page._zone_params_grid.setContentsMargins(0, 0, 0, 0)
    page._zone_params_grid.setSpacing(5)
    page._zone_param_inputs: dict[str, QWidget] = {}
    zones_layout.addWidget(page._zone_params_widget)
    fill_grid = QGridLayout()
    fill_grid.addWidget(QLabel("Fill"), 0, 0)
    page._zone_fill_mode = QComboBox()
    page._zone_fill_mode.addItem("None", "none")
    page._zone_fill_mode.addItem("Lines", "lines")
    page._zone_fill_mode.addItem("Zigzag", "zigzag")
    page._zone_fill_mode.addItem("Crosshatch", "crosshatch")
    page._zone_fill_mode.addItem("Concentric", "concentric")
    page._zone_fill_mode.currentIndexChanged.connect(page._live_update_selected_zone)
    fill_grid.addWidget(page._zone_fill_mode, 0, 1)
    page._zone_fill_spacing = QLineEdit(DEFAULT_FILL_SPACING)
    page._zone_fill_angle = QLineEdit(DEFAULT_FILL_ANGLE)
    page._zone_fill_inset = QLineEdit(DEFAULT_FILL_INSET)
    for row, (label, field) in enumerate(
        (
            ("Spacing (mm)", page._zone_fill_spacing),
            ("Angle (°)", page._zone_fill_angle),
            ("Inset (mm)", page._zone_fill_inset),
        ),
        start=1,
    ):
        field.setValidator(QDoubleValidator(-1e9, 1e9, 6, field))
        field.textChanged.connect(page._live_update_selected_zone)
        fill_grid.addWidget(QLabel(label), row, 0)
        fill_grid.addWidget(field, row, 1)
    zones_layout.addLayout(fill_grid)
    fill_targets = QHBoxLayout()
    fill_targets.addWidget(QLabel("Fill targets"))
    page._zone_fill_target_outline = QCheckBox("Outline space")
    page._zone_fill_target_outline.setChecked(True)
    page._zone_fill_target_outline.toggled.connect(page._live_update_selected_zone)
    fill_targets.addWidget(page._zone_fill_target_outline)
    page._zone_fill_target_pattern = QCheckBox("Pattern cells")
    page._zone_fill_target_pattern.setChecked(False)
    page._zone_fill_target_pattern.toggled.connect(page._live_update_selected_zone)
    fill_targets.addWidget(page._zone_fill_target_pattern)
    fill_targets.addStretch()
    zones_layout.addLayout(fill_targets)
    output_row = QHBoxLayout()
    output_row.addWidget(QLabel("Output"))
    output_row.addWidget(page._zone_output_combo, stretch=1)
    zones_layout.addLayout(output_row)
    page._zones_section = CollapsibleSection(
        "Zone Manager", zones_content, expanded=True, subtitle="No zones assigned"
    )
    layout.addWidget(page._zones_section)
    page._rebuild_zone_parameter_editor()


def build_fill_section(page: Any, layout: QVBoxLayout) -> None:
    fill_content, fill_layout = collapsible_content_widget(spacing=8)
    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("Mode"))
    page._fill_mode_combo = QComboBox()
    page._fill_mode_combo.addItem("None", "none")
    page._fill_mode_combo.addItem("Lines", "lines")
    page._fill_mode_combo.addItem("Zigzag", "zigzag")
    page._fill_mode_combo.addItem("Crosshatch", "crosshatch")
    page._fill_mode_combo.addItem("Concentric", "concentric")
    page._fill_mode_combo.setToolTip(
        "Fill the shape with laser-engraving paths.\n"
        "Lines = separate hatch strokes. Zigzag = fewer travel moves.\n"
        "Crosshatch = two angled passes. Concentric = inward contour passes."
    )
    page._fill_mode_combo.currentIndexChanged.connect(page._on_fill_mode_changed)
    mode_row.addWidget(page._fill_mode_combo, stretch=1)
    fill_layout.addLayout(mode_row)
    params_row = QGridLayout()
    params_row.addWidget(QLabel("Spacing (mm)"), 0, 0)
    page._fill_spacing = QLineEdit(DEFAULT_FILL_SPACING)
    make_resettable_line_edit(page._fill_spacing, DEFAULT_FILL_SPACING)
    page._fill_spacing.setFixedWidth(80)
    page._fill_spacing.setToolTip("Distance between adjacent infill lines")
    page._fill_spacing.textChanged.connect(page._schedule_preview)
    params_row.addWidget(page._fill_spacing, 0, 1)
    params_row.addWidget(QLabel("Angle (°)"), 1, 0)
    page._fill_angle = QLineEdit(DEFAULT_FILL_ANGLE)
    make_resettable_line_edit(page._fill_angle, DEFAULT_FILL_ANGLE)
    page._fill_angle.setFixedWidth(80)
    page._fill_angle.setToolTip("Angle of the infill direction")
    page._fill_angle.textChanged.connect(page._schedule_preview)
    params_row.addWidget(page._fill_angle, 1, 1)
    params_row.addWidget(QLabel("Boundary inset (mm)"), 2, 0)
    page._fill_inset = QLineEdit(DEFAULT_FILL_INSET)
    make_resettable_line_edit(page._fill_inset, DEFAULT_FILL_INSET)
    page._fill_inset.setFixedWidth(80)
    page._fill_inset.setToolTip(
        "Keep engraving this far inside each target boundary; useful for kerf and edge clearance"
    )
    page._fill_inset.textChanged.connect(page._schedule_preview)
    params_row.addWidget(page._fill_inset, 2, 1)
    target_row = QHBoxLayout()
    target_row.setSpacing(8)
    target_row.addWidget(QLabel("Targets"))
    page._fill_target_outline_cb = QCheckBox("Outline space")
    page._fill_target_outline_cb.setToolTip(
        "Fill the outline's negative space without crossing into closed pattern cells"
    )
    page._fill_target_outline_cb.setChecked(False)
    page._fill_target_outline_cb.toggled.connect(page._schedule_preview)
    target_row.addWidget(page._fill_target_outline_cb)
    page._fill_target_pattern_cb = QCheckBox("Pattern cells")
    page._fill_target_pattern_cb.setToolTip(
        "Hatch each closed pattern stroke (tiles, tessellation, …)"
    )
    page._fill_target_pattern_cb.setChecked(True)
    page._fill_target_pattern_cb.toggled.connect(page._schedule_preview)
    target_row.addWidget(page._fill_target_pattern_cb)
    target_row.addStretch()
    page._fill_keep_outline_cb = QCheckBox("Keep pattern strokes alongside fill")
    page._fill_keep_outline_cb.setToolTip(
        "Output both pattern strokes and laser-fill lines.\nUncheck for fill-only output."
    )
    page._fill_keep_outline_cb.setChecked(True)
    page._fill_keep_outline_cb.stateChanged.connect(page._schedule_preview)
    page._fill_params_container = QWidget()
    _fpc_layout = QVBoxLayout(page._fill_params_container)
    _fpc_layout.setContentsMargins(0, 0, 0, 0)
    _fpc_layout.setSpacing(8)
    _fpc_layout.addLayout(params_row)
    _fpc_layout.addLayout(target_row)
    _fpc_layout.addWidget(page._fill_keep_outline_cb)
    page._fill_params_container.setVisible(False)
    fill_layout.addWidget(page._fill_params_container)
    section_label(fill_layout, "Cutouts")
    page._cutout_callout = QFrame()
    page._cutout_callout.setObjectName("cutoutCallout")
    cutout_callout_layout = QHBoxLayout(page._cutout_callout)
    cutout_callout_layout.setContentsMargins(8, 6, 8, 6)
    cutout_callout_layout.setSpacing(6)
    page._cutout_icon = QLabel()
    page._cutout_icon.setFixedWidth(18)
    page._cutout_icon.setPixmap(
        QIcon(str(Path(__file__).parents[3] / "style" / "icons" / "info.svg")).pixmap(16, 16)
    )
    page._cutout_icon.setProperty("role", "cutout-icon")
    cutout_callout_layout.addWidget(page._cutout_icon)
    page._cutout_status_label = QLabel("Right-click a shape on canvas to mark as cutout")
    page._cutout_status_label.setWordWrap(True)
    page._cutout_status_label.setProperty("role", "cutout-desc")
    cutout_callout_layout.addWidget(page._cutout_status_label, stretch=1)
    page._cutout_clear_btn = QPushButton("Clear")
    page._cutout_clear_btn.setFixedWidth(52)
    page._cutout_clear_btn.setToolTip("Remove all cutout assignments")
    page._cutout_clear_btn.setVisible(False)
    page._cutout_clear_btn.clicked.connect(page._clear_exclusions)
    cutout_callout_layout.addWidget(page._cutout_clear_btn)
    page._apply_cutout_callout_style(active=False)
    fill_layout.addWidget(page._cutout_callout)
    page._mark_cutout_btn = QPushButton("Mark Selected as Cutout")
    page._mark_cutout_btn.setMinimumHeight(28)
    page._mark_cutout_btn.setToolTip(
        "Mark the selected canvas shapes as cutout regions.\nCutouts exclude areas from laser fill.  Right-click a shape to toggle individually."
    )
    page._mark_cutout_btn.clicked.connect(page._mark_selection_as_cutout)
    fill_layout.addWidget(page._mark_cutout_btn)
    page._fill_section = CollapsibleSection(
        "Fill", fill_content, expanded=False, subtitle="None"
    )
    layout.addWidget(page._fill_section)
    page._on_fill_mode_changed()


def build_image_engraving_section(page: Any, layout: QVBoxLayout) -> None:
    content, form = collapsible_content_widget(spacing=8)
    choose = QPushButton("Choose engraving image…")
    choose.clicked.connect(page._choose_engraving_image)
    form.addWidget(choose)
    page._engraving_image_label = QLabel("No image selected")
    page._engraving_image_label.setWordWrap(True)
    form.addWidget(page._engraving_image_label)

    placement_content, placement = collapsible_content_widget(spacing=8)
    placement_grid = QGridLayout()

    def number(value, minimum, maximum, decimals=2, step=1.0):
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setSingleStep(step)
        widget.setValue(value)
        widget.valueChanged.connect(page._update_engraving_overlay)
        return widget

    page._engrave_x = number(0, -100000, 100000)
    page._engrave_y = number(0, -100000, 100000)
    page._engrave_w = number(100, 0.01, 100000)
    page._engrave_h = number(100, 0.01, 100000)
    page._engrave_rotation = number(0, -360, 360, 1, 1)
    for row, (label, widget) in enumerate(
        (
            ("X (mm)", page._engrave_x),
            ("Y (mm)", page._engrave_y),
            ("Width (mm)", page._engrave_w),
            ("Height (mm)", page._engrave_h),
            ("Rotation (°)", page._engrave_rotation),
        )
    ):
        placement_grid.addWidget(QLabel(label), row, 0)
        placement_grid.addWidget(widget, row, 1)
    placement.addLayout(placement_grid)
    page._engrave_canvas_edit = QCheckBox("Select, drag, and resize image on canvas")
    page._engrave_canvas_edit.setChecked(True)
    page._engrave_canvas_edit.toggled.connect(
        lambda enabled: page._canvas.set_background_image_editable(
            enabled, page._on_engraving_canvas_transform
        )
    )
    placement.addWidget(page._engrave_canvas_edit)
    page._engraving_placement_section = CollapsibleSection(
        "Placement", placement_content, expanded=True, subtitle="100 × 100 mm"
    )
    form.addWidget(page._engraving_placement_section)

    appearance_content, appearance = collapsible_content_widget(spacing=8)
    page._engrave_gamma = number(1, 0.1, 5, 2, 0.05)
    appearance_grid = QGridLayout()
    appearance_grid.addWidget(QLabel("Gamma / depth detail"), 0, 0)
    appearance_grid.addWidget(page._engrave_gamma, 0, 1)
    page._engrave_invert = QCheckBox("Invert light and dark")
    appearance_grid.addWidget(page._engrave_invert, 1, 0, 1, 2)
    appearance.addLayout(appearance_grid)
    page._engraving_appearance_section = CollapsibleSection(
        "Appearance", appearance_content, expanded=False, subtitle="Gamma 1.00 · Normal"
    )
    form.addWidget(page._engraving_appearance_section)

    process_content, process = collapsible_content_widget(spacing=8)
    material_row = QHBoxLayout()
    material_row.addWidget(QLabel("Material starting profile"))
    page._engrave_material = QComboBox()
    for label, key in (
        ("Custom", "custom"),
        ("Wood", "wood"),
        ("Laser-safe polymer", "polymer"),
        ("Anodized aluminum", "aluminum"),
        ("Coated / marking steel", "steel"),
    ):
        page._engrave_material.addItem(label, key)
    material_row.addWidget(page._engrave_material, stretch=1)
    page._apply_material_btn = QPushButton("Apply values")
    page._apply_material_btn.setEnabled(False)
    page._apply_material_btn.setToolTip(
        "Explicitly replace the current process values with conservative starting values"
    )
    page._apply_material_btn.clicked.connect(page._apply_engraving_material)
    page._engrave_material.currentIndexChanged.connect(
        lambda: page._apply_material_btn.setEnabled(
            page._engrave_material.currentData() != "custom"
        )
    )
    material_row.addWidget(page._apply_material_btn)
    process.addLayout(material_row)
    process_grid = QGridLayout()
    page._engrave_interval = number(0.1, 0.025, 2, 3, 0.025)
    page._engrave_min_power = number(0, 0, 100, 1)
    page._engrave_max_power = number(80, 0, 100, 1)
    page._engrave_speed = number(100, 0.1, 10000, 1, 10)
    page._engrave_passes = QSpinBox()
    page._engrave_passes.setRange(1, 100)
    labels = (
        ("Detail / interval", page._engrave_interval),
        ("Min power (%)", page._engrave_min_power),
        ("Max power (%)", page._engrave_max_power),
        ("Speed (mm/s)", page._engrave_speed),
    )
    slider_scales = {
        page._engrave_interval: 1000,
        page._engrave_min_power: 10,
        page._engrave_max_power: 10,
        page._engrave_gamma: 100,
    }

    def make_slider(field: QDoubleSpinBox, scale: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimumWidth(120)
        slider.setRange(round(field.minimum() * scale), round(field.maximum() * scale))
        slider.setValue(round(field.value() * scale))
        slider.valueChanged.connect(lambda value, f=field, s=scale: f.setValue(value / s))
        field.valueChanged.connect(
            lambda value, sl=slider, s=scale: sl.setValue(round(value * s))
        )
        return slider

    grid_row = 0
    for label, widget in labels:
        process_grid.addWidget(QLabel(label), grid_row, 0)
        process_grid.addWidget(widget, grid_row, 1)
        grid_row += 1
        scale = slider_scales.get(widget)
        if scale is not None:
            # Full-width second row remains usable in the narrow sidebar.
            process_grid.addWidget(make_slider(widget, scale), grid_row, 0, 1, 2)
            grid_row += 1
    process_grid.addWidget(QLabel("Passes"), grid_row, 0)
    process_grid.addWidget(page._engrave_passes, grid_row, 1)
    process.addLayout(process_grid)
    page._engraving_process_error = QLabel()
    page._engraving_process_error.setWordWrap(True)
    page._engraving_process_error.setProperty("role", "status-err")
    page._engraving_process_error.setVisible(False)
    process.addWidget(page._engraving_process_error)
    safety_callout = QLabel(
        "Machine and material settings are starting points only. Review settings before output."
    )
    safety_callout.setWordWrap(True)
    safety_callout.setProperty("role", "warning")
    process.addWidget(safety_callout)
    safety_detail_content, safety_detail = collapsible_content_widget(spacing=8)
    safety = QLabel(
        "Use only laser-safe materials; never engrave PVC or vinyl. Bare aluminum and steel "
        "usually require a fiber laser or approved marking compound. Frame the job and run a "
        "material test on scrap before production."
    )
    safety.setWordWrap(True)
    safety_detail.addWidget(safety)
    process.addWidget(
        CollapsibleSection(
            "Review settings", safety_detail_content, expanded=False, subtitle="Material safety"
        )
    )
    page._engraving_process_section = CollapsibleSection(
        "Laser Process", process_content, expanded=False, subtitle="Custom · 80% · 100 mm/s"
    )
    form.addWidget(page._engraving_process_section)

    output_content, output = collapsible_content_widget(spacing=8)
    target_row = QHBoxLayout()
    target_row.addWidget(QLabel("Clip to"))
    page._engrave_target = QComboBox()
    page._engrave_target.addItem("Entire outline", "outline")
    page._engrave_target.addItem("Selected zone", "zone")
    target_row.addWidget(page._engrave_target, stretch=1)
    output.addLayout(target_row)
    export = QPushButton("Export Positioned Engraving Package…")
    export.setProperty("role", "primary")
    export.clicked.connect(page._export_pattern_engraving)
    output.addWidget(export)
    note = QLabel(
        "Machine handoff: 1) Export the pattern DXF. 2) Export this engraving package. "
        "3) Import the DXF and .positioned.svg into the same laser-software job without "
        "moving either file. 4) Put the SVG raster on an engraving layer and copy speed, "
        "power, interval, and passes from the .engrave.json sidecar. 5) Frame the job and "
        "run a material test before production."
    )
    note.setWordWrap(True)
    output.addWidget(note)
    page._engraving_output_section = CollapsibleSection(
        "Output", output_content, expanded=False, subtitle="Entire outline · positioned assets"
    )
    form.addWidget(page._engraving_output_section)

    for field in (
        page._engrave_x,
        page._engrave_y,
        page._engrave_w,
        page._engrave_h,
        page._engrave_rotation,
        page._engrave_interval,
        page._engrave_min_power,
        page._engrave_max_power,
        page._engrave_speed,
        page._engrave_gamma,
    ):
        field.valueChanged.connect(page._update_engraving_section_summaries)
    page._engrave_passes.valueChanged.connect(page._update_engraving_section_summaries)
    page._engrave_invert.toggled.connect(page._update_engraving_section_summaries)
    page._engrave_material.currentIndexChanged.connect(page._update_engraving_section_summaries)
    page._engrave_target.currentIndexChanged.connect(page._update_engraving_section_summaries)
    page._engraving_section = CollapsibleSection(
        "Image Engraving", content, expanded=False, subtitle="No image"
    )
    layout.addWidget(page._engraving_section)


def build_export_section(page: Any, layout: QVBoxLayout) -> None:
    # Export options live in a card matching Shape/Pattern/Fill/Zones —
    # previously a bare caption label, the odd one out on this sidebar.
    # The action button/progress/status stay outside and unwrapped
    # below it, since a primary CTA should never be hidden behind a
    # collapsible header.
    card_content = QWidget()
    card_layout = QVBoxLayout(card_content)
    card_layout.setContentsMargins(0, 0, 0, 0)
    card_layout.setSpacing(4)
    page._include_border_cb = QCheckBox("Border on separate layer")
    page._include_border_cb.setToolTip(
        "Writes the pattern fill to a 'pattern' layer and every\noutline to a shared 'outline' layer (CAM-friendly)."
    )
    page._include_border_cb.setChecked(True)
    page._include_border_cb.stateChanged.connect(page._schedule_preview)
    card_layout.addWidget(page._include_border_cb)
    page._export_open_paths_cb = QCheckBox("Export as Open Paths")
    page._export_open_paths_cb.setToolTip(
        "Write pattern strokes as open polylines (no forced closure)."
    )
    page._export_open_paths_cb.setChecked(False)
    card_layout.addWidget(page._export_open_paths_cb)
    quality_row = QHBoxLayout()
    quality_row.addWidget(QLabel("Preview quality"))
    page._preview_quality_combo = QComboBox()
    page._preview_quality_combo.addItem("Fast", "fast")
    page._preview_quality_combo.addItem("Balanced", "balanced")
    page._preview_quality_combo.addItem("High", "high")
    page._preview_quality_combo.setCurrentIndex(
        max(0, page._preview_quality_combo.findData(DEFAULT_PREVIEW_QUALITY))
    )
    page._preview_quality_combo.currentIndexChanged.connect(page._schedule_preview)
    quality_row.addWidget(page._preview_quality_combo)
    card_layout.addLayout(quality_row)
    cleanup_grid = QGridLayout()
    cleanup_grid.addWidget(QLabel("Min segment (mm)"), 0, 0)
    page._minimum_segment_edit = QLineEdit(DEFAULT_MIN_SEGMENT)
    make_resettable_line_edit(page._minimum_segment_edit, DEFAULT_MIN_SEGMENT)
    page._minimum_segment_edit.setToolTip(
        "Remove vertices closer than this at export; 0 disables"
    )
    cleanup_grid.addWidget(page._minimum_segment_edit, 0, 1)
    cleanup_grid.addWidget(QLabel("Min island (mm²)"), 1, 0)
    page._minimum_area_edit = QLineEdit(DEFAULT_MIN_ISLAND_AREA)
    make_resettable_line_edit(page._minimum_area_edit, DEFAULT_MIN_ISLAND_AREA)
    page._minimum_area_edit.setToolTip(
        "Discard closed pattern islands smaller than this; 0 disables"
    )
    cleanup_grid.addWidget(page._minimum_area_edit, 1, 1)
    card_layout.addLayout(cleanup_grid)
    page._optimize_paths_cb = QCheckBox("Optimize path order")
    page._optimize_paths_cb.setToolTip(
        "Emit nested cutouts first, then reduce non-cutting travel between paths"
    )
    page._optimize_paths_cb.setChecked(True)
    card_layout.addWidget(page._optimize_paths_cb)
    page._summary_chip = QLabel("Preflight · Load an outline to begin")
    page._summary_chip.setProperty("role", "summary-banner")
    page._summary_chip.setProperty("tone", "neutral")
    page._summary_chip.setWordWrap(True)
    page._summary_chip.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    card_layout.addWidget(page._summary_chip)
    layout.addWidget(CollapsibleSection("Export options", card_content, expanded=False))
    export_default = str(page._settings.get("pattern_export_default", "vector"))
    if export_default not in {"vector", "engraving", "laserstar"}:
        export_default = "vector"
    page._export_default = export_default
    page._gen_btn = primary_button(
        "Export",
        height=38,
        tooltip="Run the remembered export format  (⌘E)",
    )
    page._gen_btn.clicked.connect(page._run_remembered_export)
    export_action_row = QHBoxLayout()
    export_action_row.addWidget(page._gen_btn, stretch=1)
    page._export_more = QToolButton()
    page._export_more.setText("Options")
    page._export_more.setMinimumSize(72, 38)
    page._export_more.setToolTip("Choose an export by operator purpose")
    page._export_more.setAccessibleName("Choose export format")
    page._export_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    page._export_menu = QMenu(page._export_more)
    page._export_actions = {
        "vector": page._export_menu.addAction("Vector-only — Pattern and fill DXF"),
        "engraving": page._export_menu.addAction("Engraving-only — Positioned image assets"),
        "laserstar": page._export_menu.addAction("Combined job — LaserStar operator package"),
    }
    for kind, action in page._export_actions.items():
        action.triggered.connect(
            lambda _checked=False, export_kind=kind: page._select_export_kind(export_kind)
        )
    page._export_more.setMenu(page._export_menu)
    export_action_row.addWidget(page._export_more)
    page._cancel_generate_btn = QToolButton()
    page._cancel_generate_btn.setText("Cancel")
    page._cancel_generate_btn.setToolTip("Cancel the current export")
    page._cancel_generate_btn.setAccessibleName("Cancel pattern export")
    page._cancel_generate_btn.setVisible(False)
    page._cancel_generate_btn.clicked.connect(page._cancel_generation)
    export_action_row.addWidget(page._cancel_generate_btn)
    layout.addLayout(export_action_row)
    page._progress = QProgressBar()
    page._progress.setRange(0, 100)
    page._progress.setValue(0)
    page._progress.setVisible(False)
    layout.addWidget(page._progress)
    page._status = QLabel("")
    page._status.setWordWrap(True)
    layout.addWidget(page._status)
    page._undo_transfer_btn = QPushButton("Undo transfer")
    page._undo_transfer_btn.setToolTip("Restore the Pattern outline from before this transfer")
    page._undo_transfer_btn.setVisible(False)
    page._undo_transfer_btn.clicked.connect(page._undo_outline_transfer)
    layout.addWidget(page._undo_transfer_btn)
    page._reveal_btn = QPushButton("Show in Finder")
    page._reveal_btn.setMinimumHeight(26)
    page._reveal_btn.setToolTip("Open the exported file location in Finder")
    # Hidden until an export exists — a permanently disabled button is
    # just sidebar noise before the first export.
    page._reveal_btn.setVisible(False)
    page._reveal_btn.clicked.connect(page._reveal_in_finder)
    layout.addWidget(page._reveal_btn)
    page._operator_notes_btn = QPushButton("Copy Operator Notes")
    page._operator_notes_btn.setVisible(False)
    page._operator_notes_btn.clicked.connect(page._copy_operator_notes)
    layout.addWidget(page._operator_notes_btn)
    page._refresh_export_default_label()