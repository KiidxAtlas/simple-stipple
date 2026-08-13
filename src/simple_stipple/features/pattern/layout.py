"""Widget construction for the Pattern page — left/right panel layout and
each collapsible section. Extracted from ``PatternPage`` (see plan.md
Section 9.1); follows the same ``page: Any``-first free-function
convention already used by ``domain/session.py`` and ``ui/params.py``.
"""

from __future__ import annotations

from simple_stipple.features.pattern.layout_sections import (
    build_export_section,
    build_fill_section,
    build_image_engraving_section,
    build_pattern_section,
    build_shape_section,
)

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIcon, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.canvas.runtime import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
    PatternCanvasPageRuntime,
)
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.canvas.widgets.toolbar import CanvasStatusStrip
from simple_stipple.features.pattern.defaults import (
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
from simple_stipple.features.pattern.export import (
    EXPORT_BUTTON_LABEL,
    EXPORT_FORMAT_KEYS,
    EXPORT_FORMATS,
)
from simple_stipple.features.pattern.form import PARAM_SPECS
from simple_stipple.features.pattern.form import build_param_widget
from simple_stipple.ui.components.layout import (
    CollapsibleSection,
    collapsible_content_widget,
)
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.inputs import (
    make_resettable_line_edit,
    primary_button,
)
from simple_stipple.ui.components.layout import (
    content_splitter,
    section_label,
)
from simple_stipple.ui.components.recent import KIND_DXF
from simple_stipple.ui.components.recent import RecentFilesButton
from simple_stipple.ui.dialogs.files import reveal_label
from simple_stipple.ui.style import icon_path


class ZoneListWidget(QListWidget):
    """Zone list with a deterministic keyboard delete affordance.

    QShortcut can lose precedence when focus is inside an editor or a native
    list viewport. Handling the key at the list boundary guarantees Delete
    acts on the selected zone and never falls through to canvas deletion.
    """

    deletePressed = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.currentRow() >= 0:
            self.deletePressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def build_right(page: Any, layout: QVBoxLayout) -> None:
    # The solved pattern is always on screen; its visibility lives on the
    # `pattern_result` row of the layer tree with every other layer. All that
    # is left here is a transient "cancel the solve in flight" affordance.
    page._cancel_preview_btn = QToolButton()
    page._cancel_preview_btn.setText("Solving… Cancel")
    page._cancel_preview_btn.setToolTip("Cancel the pattern currently solving")
    page._cancel_preview_btn.setAccessibleName("Cancel solve")
    page._cancel_preview_btn.setVisible(False)
    page._cancel_preview_btn.clicked.connect(page._cancel_solve)
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
        on_pattern_cell_cutout_toggle=page._on_pattern_cell_cutout_toggle,
        on_result_cell_convert=page._on_result_cell_convert,
        on_issue_marker_clicked=page._on_issue_marker_clicked,
        on_create_zone_from_selection=page._assign_zone,
        draft_profile=True,
    )
    page._canvas.set_context_menu_profile("pattern")
    page._canvas.set_context_menu_profiles(page._settings.get("context_menu_profiles", {}))
    # Preview rows are virtual categories, but editable outlines retain their
    # original document layers so the layer tree remains useful.
    page._canvas.set_layer_model([], None)
    page._canvas.set_empty_message(
        "Start a pattern\nImport a closed outline, draw one, or trace an image"
    )
    # The next actions are buttons, not numbered prose telling you to go and
    # find them somewhere else.
    page._canvas.set_empty_actions(
        [
            ("Import outline…", page._browse_dxf),
            ("Draw one", lambda: page._canvas.set_mode("draw")),
            ("Trace an image", lambda: page.openPageRequested.emit("trace")),
        ]
    )
    page._canvas.set_grid_visible(DEFAULT_GRID_VISIBLE)
    page._canvas.set_grid_snap(False)
    page._canvas.set_grid_spacing(DEFAULT_GRID_SPACING_MM)
    page._canvas.set_selection_follows_geometry(True)
    # Clicking inside a closed shape picks the region it bounds — the whole
    # point of Phase 1's model is that an area is the thing you select.
    page._canvas.set_region_picking(True)
    # Undo reaches the canvas from the Edit menu, the command palette and the
    # radial menu. Hooking it here is the only way all of them see treatments.
    page._canvas.set_undo_hooks(page._undo_treatment_hook, page._redo_treatment_hook)
    page._canvas.set_selection_drag_edits(False)
    page._canvas.backgroundSelectionChanged.connect(page._on_engraving_selection_changed)

    page._toolbar_module = CanvasToolbarModule(
        canvas=page._canvas,
        on_mode=page._on_toolbar_mode,
        on_fit=page._canvas.fit,
        extra_widgets=[page._reset_preview_btn],
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
    page._canvas_status.bind_canvas(page._canvas)
    page._canvas_status.add_status_widget(page._cancel_preview_btn)

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
    side_layout.setContentsMargins(8, 0, 8, 8)
    side_layout.setSpacing(8)
    # Compatibility alias: the Zone Manager is mounted in the left workflow
    # panel, so the right inspector no longer spends space on a second copy.
    page._zone_scroll = page._zones_section
    page._zone_layers_splitter = QSplitter(Qt.Orientation.Vertical)
    page._zone_layers_splitter.setChildrenCollapsible(False)

    page._layer_module = CanvasLayerTreeModule(
        canvas=page._canvas,
        title="Layers",
        editable=True,
        get_active_layer_name=lambda: page._canvas.active_layer or "Outline",
        build_layer_rows=page._build_layer_tree_rows,
        on_selection_requested=page._on_browser_selection_requested,
        on_fit_requested=page._fit_selection,
        on_visibility_changed=page._refresh_canvas_panels,
    )
    page._layers_tree = page._layer_module.tree
    page._layer_sidebar = page._layer_module.controller
    # Wire outline-mode shape rename to the runtime's label store.
    page._layers_tree.shapeRenamed.connect(page._on_shape_renamed)
    page._layers_tree.layerSettingsRequested.connect(page._open_pattern_layer_settings)
    page._layers_tree.layerVisibilityChanged.connect(page._on_pattern_layer_visibility_changed)
    page._zone_layers_splitter.addWidget(page._layer_module)
    page._zone_layers_splitter.setStretchFactor(0, 1)
    side_layout.addWidget(page._zone_layers_splitter, stretch=1)
    build_export_section(page, side_layout)

    page._canvas_runtime = PatternCanvasPageRuntime(
        canvas=page._canvas,
        toolbar_module=page._toolbar_module,
        layer_sidebar=page._layer_sidebar,
        canvas_status=page._canvas_status,
        precision_bar=page._precision_bar,
        get_orig_polys=lambda: page._edit_polys,
        is_preview_running=lambda: page._preview_task.running,
        has_preview_cache=lambda: bool(page._preview_polys_cache),
        has_zones=lambda: bool(page._zones),
        get_preview_categories=lambda: page._preview_categories,
    )

    splitter = content_splitter(canvas_shell, side_panel, sizes=(780, 340))
    # Preserve the canvas at compact widths; the standard drawer toggle keeps
    # layers, zones, and export controls available without squeezing content.
    splitter.setCollapsible(1, True)
    splitter.set_responsive_secondary(1, "Pattern details")
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    page._canvas_splitter = splitter
    layout.addWidget(splitter, stretch=1)
    layout.addWidget(page._canvas_status)
    page._refresh_canvas_panels()


def refresh_pattern_properties_panel(page: Any) -> None:
    """Name what the inspector is editing, rather than describing a mode."""
    if not hasattr(page, "_pattern_props_scope"):
        return
    from simple_stipple.features.pattern.regions.treatments import TREATMENT_LABELS, treatment_kind

    row = page._zone_list.currentRow() if hasattr(page, "_zone_list") else -1
    region_ids = [rid for rid in page._outline_ids if rid in page._region_tree()]
    editing = 0 <= row < len(region_ids)
    if editing:
        kind = treatment_kind(page, region_ids[row])
        page._pattern_props_scope.setText(f"Region {row + 1} · {TREATMENT_LABELS[kind]}")
    else:
        page._pattern_props_scope.setText("Document defaults · nothing selected")
    page._pattern_props_scope.setProperty("editing", editing)
    refresh_style(page._pattern_props_scope)


def build_left(page: Any, layout: QVBoxLayout) -> None:
    page._advanced_mode_cb = QCheckBox("Advanced controls")
    page._advanced_mode_cb.setChecked(bool(page._settings.get("pattern_advanced_mode", False)))
    page._advanced_mode_cb.setToolTip("Show image engraving placement and fabrication controls")
    layout.addWidget(page._advanced_mode_cb)
    build_shape_section(page, layout)
    # One inspector, read top-down: what is selected → what it produces →
    # the pattern and fill that produce it. There is no second copy of the
    # Pattern/Fill controls scoped to something else.
    build_zones_section(page, layout)
    build_pattern_section(page, layout)
    build_fill_section(page, layout)
    # The engraving controls are not a sidebar section of their own: they
    # belong to whichever region carries an image, so they are built into the
    # region editor and shown only when that region's pattern is Image.
    build_image_engraving_section(page, page._zone_editor_layout)
    page._engraving_section.setVisible(False)
    page._advanced_mode_cb.toggled.connect(page._set_advanced_mode)
    layout.addStretch()
    page._install_pattern_shortcuts()
    page._refresh_section_subtitles()


def build_zones_section(page: Any, layout: QVBoxLayout) -> None:
    zones_content, zones_layout = collapsible_content_widget(spacing=6)
    section_label(zones_layout, "Regions")
    scope_hint = QLabel(
        "Every closed shape is a region. Click inside one on the canvas, then choose its treatment."
    )
    scope_hint.setWordWrap(True)
    scope_hint.setProperty("role", "hint")
    zones_layout.addWidget(scope_hint)
    assign_row = QHBoxLayout()
    page._assign_zone_btn = QPushButton("Apply to Selection")
    page._assign_zone_btn.setMinimumHeight(30)
    page._assign_zone_btn.setToolTip("Apply these settings to the selected region(s).")
    page._assign_zone_btn.clicked.connect(page._assign_zone)
    assign_row.addWidget(page._assign_zone_btn, stretch=1)
    zones_layout.addLayout(assign_row)
    page._zone_list = ZoneListWidget()
    # One row per region, so the list is unbounded — it scrolls rather than
    # growing to fit. Sizing it to its contents with the scrollbar off used to
    # clip rows out of reach as soon as a document had more than a few shapes.
    page._zone_list.setMinimumHeight(120)
    page._zone_list.setMaximumHeight(240)
    page._zone_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    page._zone_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    page._zone_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page._zone_list.setToolTip("Regions in this document, indented by containment")
    page._zone_list.currentRowChanged.connect(page._on_zone_selected)
    page._zone_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    page._zone_list.customContextMenuRequested.connect(page._show_zone_context_menu)
    page._zone_list.deletePressed.connect(page._remove_selected_zone)
    zones_layout.addWidget(page._zone_list)

    page._pattern_props_scope = QLabel("Document defaults")
    page._pattern_props_scope.setWordWrap(True)
    page._pattern_props_scope.setProperty("role", "zone-edit-scope")
    zones_layout.addWidget(page._pattern_props_scope)

    page._zone_output_combo = QComboBox()
    page._zone_output_combo.addItem("Pattern + Fill", "pattern_fill")
    page._zone_output_combo.addItem("Pattern only", "pattern")
    page._zone_output_combo.addItem("Fill only", "fill")
    page._zone_output_combo.addItem("Engrave image", "engrave")
    page._zone_output_combo.addItem("Cut only", "cut")
    page._zone_output_combo.addItem("None", "none")
    page._zone_output_combo.setToolTip(
        "What this region produces. A region with a treatment subtracts itself "
        "from the region containing it."
    )
    page._zone_output_combo.currentIndexChanged.connect(page._on_inspector_edit)
    output_row = QHBoxLayout()
    output_row.addWidget(QLabel("Treatment"))
    output_row.addWidget(page._zone_output_combo, stretch=1)
    zones_layout.addLayout(output_row)
    # The Pattern and Fill sections below this one *are* the region editor —
    # there is no second copy of them here. Image controls mount here so they
    # sit with the region that carries the image.
    page._zone_editor_layout = zones_layout
    page._zones_section = CollapsibleSection(
        "Regions", zones_content, expanded=True, subtitle="No regions yet"
    )
    layout.addWidget(page._zones_section)


