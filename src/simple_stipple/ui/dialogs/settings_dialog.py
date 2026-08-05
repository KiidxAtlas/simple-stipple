"""Settings dialog window."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator, QValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.features.trace.form import TRACE_DEFAULT_FIELDS, trace_default
from simple_stipple.platform.config import (
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
    SMOOTHING_METHODS,
    SettingsSchema,
    custom_tiles_dir,
    save_settings,
)
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.icons import tool_icon
from simple_stipple.ui.components.layout import (
    section_label,
    sep,
    surface_frame,
)
from simple_stipple.ui.components.tokens import (
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
)
from simple_stipple.ui.dialogs.customize_dialogs import (
    ContextMenuActionCustomizeDialog,
    DrawSidebarCustomizeDialog,
    RadialMenuDialog,
)
from simple_stipple.ui.dialogs.keybindings_dialog import KeybindingsDialog
from simple_stipple.ui.units import DEFAULT_UNIT_SYSTEM


class SettingsDialog(QDialog):
    """Settings dialog with folder paths and behavioral toggles."""

    # Emitted after Apply writes settings to disk (Save uses the dialog's
    # normal accept()/exec() result instead). Previously Apply only saved to
    # disk — the running app kept its stale in-memory settings until the
    # dialog was later accepted or the app restarted.
    applied = Signal()

    _FOLDER_FIELDS = [
        ("workspace_dir", "Workspace folder"),
        ("outline_dxf_dir", "Pattern outline folder"),
        ("custom_tiles_dir", "Custom tiles folder"),
        ("pattern_output_dir", "Pattern fill output folder"),
        ("draft_output_dir", "Draft output folder"),
        ("fvi_source_dir", "FVI conversion source folder"),
        ("fvi_output_dir", "FVI conversion output folder"),
    ]

    _REPO_FIELDS = [
        ("repo_dir", "Repository folder"),
    ]

    _UPDATE_TOGGLES = [
        ("check_updates_on_startup", "Check for app updates on startup", False),
        ("auto_fetch_on_startup", "Fetch repository updates on startup", False),
        ("auto_fetch_periodic", "Periodically fetch repository updates", False),
    ]

    _ACCESSIBILITY_TOGGLES = [
        ("high_contrast", "High-contrast status and focus indicators", False),
        ("reduced_motion", "Reduce transient UI animation", False),
        ("persistent_notifications", "Keep canvas notifications visible longer", False),
    ]

    _SNAP_TOGGLES = [
        ("grid_visible", "Show grid by default", True),
        ("grid_snap", "Snap to grid by default", False),
        ("snap_master", "Enable object snapping", True),
        ("snap_vertex", "Snap to vertices and shape control points", True),
        ("snap_edge", "Snap to edges and curves", True),
        ("snap_tangent", "Infer tangent points", True),
        ("snap_extension", "Infer extended edges", True),
        ("snap_angle", "Enable angle, parallel, and perpendicular inference", True),
        ("snap_equal_length", "Snap new lines to existing line lengths", True),
        (
            "snap_axis_alignment",
            "Align new endpoints on the same X or Y axis as existing endpoints",
            True,
        ),
        ("construction_mode_default", "Start drawing as construction geometry", False),
        ("geometry_health_visible", "Show geometry-health warnings on canvas", False),
        ("curvature_visible", "Show curvature analysis while editing curves", False),
    ]

    _MARGIN = SPACE_LG
    _GAP = SPACE_MD
    _SPACE = SPACE_SM
    _CATEGORIES = (
        "All settings",
        "Appearance",
        "Files & Folders",
        "Canvas & Snapping",
        "Accessibility",
        "Customization",
        "Trace",
        "Updates",
    )
    _CATEGORY_CARDS = {
        "All settings": (
            "Workspace & Source",
            "Outputs & Conversion",
            "Repository",
            "Appearance & Units",
            "Updates & Sync",
            "Canvas & Snapping",
            "Accessibility",
            "Customization",
            "Trace Defaults",
        ),
        "Appearance": ("Appearance & Units",),
        "Files & Folders": ("Workspace & Source", "Outputs & Conversion", "Repository"),
        "Canvas & Snapping": ("Canvas & Snapping",),
        "Accessibility": ("Accessibility",),
        "Customization": ("Customization",),
        "Trace": ("Trace Defaults",),
        "Updates": ("Updates & Sync",),
    }

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 620)
        self.setMinimumSize(560, 480)
        self.setModal(True)

        # Deep-copy so the keybindings/radial-menu/draw-sidebar sub-dialogs
        # (which write straight into self._settings the instant they're
        # accepted) can't mutate the caller's live settings before this
        # dialog's own Save/Cancel — otherwise Cancel here left those three
        # changes applied while every other field was correctly discarded.
        self._settings: dict = deepcopy(settings) if settings else {}
        self._entries: dict[str, QLineEdit] = {}
        self._toggles: dict[str, QCheckBox] = {}
        self._trace_default_entries: dict[str, QLineEdit] = {}
        self._unit_combo: QComboBox | None = None
        self._smoothing_combo: QComboBox | None = None
        self._smooth_iterations_edit: QLineEdit | None = None
        self._simplify_tolerance_edit: QLineEdit | None = None
        self._ui_scale_combo: QComboBox | None = None
        self._appearance_combo: QComboBox | None = None
        self._density_combo: QComboBox | None = None
        self._rotation_snap_edit: QLineEdit | None = None
        self._grid_spacing_edit: QLineEdit | None = None
        self._fetch_interval_edit: QLineEdit | None = None
        self._settings_cards: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._MARGIN, self._MARGIN, self._MARGIN, self._MARGIN)
        layout.setSpacing(self._SPACE)

        title = QLabel("Settings")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            "All settings are shown below. Choose a section to jump to it, or search every setting."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        find_row = QHBoxLayout()
        find_row.setSpacing(self._SPACE)
        self._category_combo = QComboBox()
        self._category_combo.setAccessibleName("Show settings section")
        self._category_combo.setMinimumWidth(190)
        self._category_combo.addItems(self._CATEGORIES)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search all settings…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setAccessibleName("Search settings")
        find_row.addWidget(self._category_combo)
        find_row.addWidget(self._search_edit, 1)
        layout.addLayout(find_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(self._scroll, stretch=1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(self._GAP)
        self._scroll.setWidget(content)

        # A search with no matches used to leave every card hidden with no
        # explanation — just a blank scroll area.
        self._no_match_label = QLabel("No settings match your search.")
        self._no_match_label.setProperty("role", "hint")
        self._no_match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_match_label.setVisible(False)
        content_layout.addWidget(self._no_match_label)

        _, form = self._form_card(content_layout, "Workspace & Source")
        for key, label in self._FOLDER_FIELDS[:2]:
            self._add_folder_field(form, key, label)

        _, form = self._form_card(content_layout, "Outputs & Conversion")
        for key, label in self._FOLDER_FIELDS[2:]:
            self._add_folder_field(form, key, label)

        _, form = self._form_card(content_layout, "Repository")
        for key, label in self._REPO_FIELDS:
            self._add_folder_field(form, key, label)

        _, form = self._form_card(content_layout, "Appearance & Units")
        self._appearance_combo = self._add_combo(
            form,
            "Appearance",
            [("System", "system"), ("Light", "light"), ("Dark", "dark")],
            str(self._settings.get("appearance", "system")),
        )
        self._unit_combo = self._add_combo(
            form,
            "Display units",
            [("Millimeters (mm)", "mm"), ("Inches (in)", "in")],
            self._settings.get("unit_system", DEFAULT_UNIT_SYSTEM),
        )
        self._ui_scale_combo = self._add_scale_combo(form)
        self._density_combo = self._add_combo(
            form,
            "Control density",
            [("Compact", "compact"), ("Comfortable", "comfortable")],
            str(self._settings.get("interface_density", "compact")),
        )

        _, form = self._form_card(content_layout, "Updates & Sync")
        for key, label, default in self._UPDATE_TOGGLES:
            self._add_toggle(form, key, label, default)
        self._fetch_interval_edit = self._add_suffix_field(
            form,
            "Repository fetch interval",
            str(self._settings.get("auto_fetch_interval_minutes", 10)),
            "minutes",
            QIntValidator(1, 1440, self),
            width=72,
        )

        _, form = self._form_card(content_layout, "Canvas & Snapping")
        for key, label, default in self._SNAP_TOGGLES:
            self._add_toggle(form, key, label, default)
        self._grid_spacing_edit = self._add_suffix_field(
            form,
            "Default grid spacing",
            str(self._settings.get("grid_spacing", 10.0)),
            "mm",
            QDoubleValidator(0.001, 100000.0, 2, self),
            width=90,
        )
        self._rotation_snap_edit = self._add_suffix_field(
            form,
            "Rotation snap increment",
            str(self._settings.get("rotation_snap_increment", 15.0)),
            "°",
            QDoubleValidator(0.1, 180.0, 2, self),
            width=72,
        )
        self._rotation_snap_edit.setToolTip(
            "Angle used by drawing and rotation snapping while Shift is held"
        )
        self._smoothing_combo = self._add_smoothing_combo(form)
        self._smooth_iterations_edit = self._add_suffix_field(
            form,
            "Smooth iterations",
            str(self._settings.get("smooth_iterations", DEFAULT_SMOOTH_ITERATIONS)),
            "",
            QIntValidator(1, 50),
            width=60,
        )
        self._smooth_iterations_edit.setToolTip(
            "Default seeded into the Smooth command's HUD prompt"
        )
        self._simplify_tolerance_edit = self._add_suffix_field(
            form,
            "Simplify tolerance",
            str(self._settings.get("simplify_tolerance", DEFAULT_SIMPLIFY_TOLERANCE)),
            "mm",
            QDoubleValidator(0.001, 1000.0, 3),
            width=70,
        )
        self._simplify_tolerance_edit.setToolTip(
            "Default seeded into the Simplify command's HUD prompt"
        )
        self._add_toggle(
            form,
            "draw_sidebar_always_visible",
            "Keep draw sidebar always visible",
            DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
        )

        _, form = self._form_card(content_layout, "Accessibility")
        for key, label, default in self._ACCESSIBILITY_TOGGLES:
            self._add_toggle(form, key, label, default)

        _, body = self._card(content_layout, "Customization")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self._SPACE)
        grid.setVerticalSpacing(self._SPACE)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        customize_buttons = (
            (
                "Customize radial menu…",
                'Choose which tools appear in the "Q" quick menu',
                self._open_radial_menu,
            ),
            (
                "Customize draw sidebar…",
                "Choose which sections show in the Draw sidebar",
                self._open_draw_sidebar_customize,
            ),
            (
                "Customize context menu…",
                "Choose which sections appear when right-clicking a canvas",
                self._open_context_menu_customize,
            ),
            ("Edit shortcuts…", "Customize keyboard shortcuts", self._open_keybindings),
        )
        for index, (text, tip, slot) in enumerate(customize_buttons):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            grid.addWidget(button, index // 2, index % 2)
        body.addLayout(grid)

        _, form = self._form_card(content_layout, "Trace Defaults")
        trace_hint = QLabel(
            "Values a newly opened image or a cleared workspace starts "
            "with on the Trace page. Leave a field blank to use the "
            "built-in default."
        )
        trace_hint.setProperty("role", "hint")
        trace_hint.setWordWrap(True)
        form.addRow(trace_hint)
        for key, label, tooltip in TRACE_DEFAULT_FIELDS:
            entry = self._add_text_field(form, label, trace_default(self._settings, key))
            entry.setToolTip(tooltip)
            self._trace_default_entries[key] = entry

        content_layout.addStretch()
        self._category_combo.currentTextChanged.connect(self._show_category)
        self._search_edit.textChanged.connect(self._filter_settings)
        sep(layout)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset")
        reset_btn.setToolTip("Reset all settings fields to application defaults")
        reset_btn.setAutoDefault(False)
        reset_btn.clicked.connect(self._confirm_reset_fields)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(90)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply")
        apply_btn.setMinimumWidth(90)
        apply_btn.clicked.connect(lambda: self._save(close=False))
        save_btn = QPushButton("Save")
        save_btn.setMinimumWidth(110)
        save_btn.setProperty("role", "primary")
        save_btn.setDefault(True)
        save_btn.setAutoDefault(True)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        self._show_category(self._category_combo.currentText())
        install_dialog_focus_lifecycle(self, self._search_edit)

    # ── Layout helpers ────────────────────────────────────────────────────

    def _card(self, parent_layout: QVBoxLayout, title: str) -> tuple[QWidget, QVBoxLayout]:
        card = surface_frame("panel")
        card.setAccessibleName(title)
        body = QVBoxLayout(card)
        body.setContentsMargins(self._GAP, self._GAP, self._GAP, self._GAP)
        body.setSpacing(self._SPACE)
        section_label(body, title)
        parent_layout.addWidget(card)
        self._settings_cards[title] = card
        return card, body

    def _show_category(self, title: str) -> None:
        if self._search_edit.text():
            self._search_edit.clear()
        visible_titles = set(self._CATEGORY_CARDS.get(title, ()))
        first_card: QWidget | None = None
        for card_title, card in self._settings_cards.items():
            visible = card_title in visible_titles
            card.setVisible(visible)
            if visible and first_card is None:
                first_card = card
        if first_card is not None:
            self._scroll.ensureWidgetVisible(first_card, 0, self._SPACE)

    def _filter_settings(self, query: str) -> None:
        needle = query.strip().casefold()
        first_match: QWidget | None = None
        for title, card in self._settings_cards.items():
            searchable = [title]
            searchable.extend(label.text() for label in card.findChildren(QLabel))
            searchable.extend(button.text() for button in card.findChildren(QPushButton))
            searchable.extend(check.text() for check in card.findChildren(QCheckBox))
            category_cards = set(self._CATEGORY_CARDS.get(self._category_combo.currentText(), ()))
            matches = (
                needle in " ".join(searchable).casefold() if needle else title in category_cards
            )
            card.setVisible(matches)
            if matches and first_match is None:
                first_match = card
        if needle and first_match is not None:
            self._scroll.ensureWidgetVisible(first_match, 0, self._SPACE)
        self._no_match_label.setVisible(bool(needle) and first_match is None)

    def _form_card(self, parent_layout: QVBoxLayout, title: str) -> tuple[QWidget, QFormLayout]:
        card, body = self._card(parent_layout, title)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(self._GAP)
        form.setVerticalSpacing(self._SPACE)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        body.addLayout(form)
        return card, form

    def _add_folder_field(self, form: QFormLayout, key: str, label: str) -> None:
        fallback = str(custom_tiles_dir()) if key == "custom_tiles_dir" else ""
        entry = QLineEdit()
        entry.setText(self._settings.get(key, fallback))
        self._entries[key] = entry

        field = QHBoxLayout()
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(self._SPACE)
        field.addWidget(entry, 1)
        browse = QPushButton("Browse…")
        browse.setMinimumSize(72, 28)
        browse.setProperty("role", "browse-btn")
        browse.setToolTip("Choose a folder")
        browse.setAutoDefault(False)
        browse.clicked.connect(lambda _checked, k=key: self._browse_dir(k))
        field.addWidget(browse)
        clear_btn = QPushButton()
        clear_btn.setIcon(tool_icon("cancel", size=16))
        clear_btn.setAccessibleName("Clear saved folder path")
        clear_btn.setMinimumSize(28, 28)
        clear_btn.setProperty("role", "icon-sm")
        clear_btn.setToolTip("Clear this saved folder path")
        clear_btn.setAutoDefault(False)
        clear_btn.clicked.connect(entry.clear)
        field.addWidget(clear_btn)
        form.addRow(label, field)

    def _add_text_field(
        self, form: QFormLayout, label: str, text: str, *, placeholder: str = ""
    ) -> QLineEdit:
        entry = QLineEdit()
        if placeholder:
            entry.setPlaceholderText(placeholder)
        entry.setText(text)
        form.addRow(label, entry)
        return entry

    def _add_toggle(self, form: QFormLayout, key: str, label: str, default: bool = False) -> None:
        cb = QCheckBox(label)
        cb.setChecked(self._settings.get(key, default))
        self._toggles[key] = cb
        form.addRow(cb)

    def _add_combo(
        self, form: QFormLayout, label: str, items: list[tuple[str, str]], current: str
    ) -> QComboBox:
        combo = QComboBox()
        for text, data in items:
            combo.addItem(text, data)
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(label, self._compact(combo))
        return combo

    def _add_scale_combo(self, form: QFormLayout) -> QComboBox:
        combo = QComboBox()
        for label, value in (
            ("90%", 0.9),
            ("100%", 1.0),
            ("110%", 1.1),
            ("125%", 1.25),
            ("150%", 1.5),
        ):
            combo.addItem(label, value)
        current = float(self._settings.get("ui_scale", 1.0) or 1.0)
        combo.setCurrentIndex(
            min(range(combo.count()), key=lambda i: abs(float(combo.itemData(i)) - current))
        )
        form.addRow("Interface scale", self._compact(combo))
        return combo

    def _add_smoothing_combo(self, form: QFormLayout) -> QComboBox:
        combo = QComboBox()
        for value, label in SMOOTHING_METHODS:
            combo.addItem(label, value)
        idx = combo.findData(self._settings.get("smoothing_method", DEFAULT_SMOOTHING_METHOD))
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setToolTip(
            "Algorithm used by the Smooth path command (right-click a "
            "selected path, or the 'path.smooth' shortcut)"
        )
        form.addRow("Smoothing method", self._compact(combo))
        return combo

    def _add_suffix_field(
        self,
        form: QFormLayout,
        label: str,
        text: str,
        suffix: str,
        validator: QValidator,
        *,
        width: int,
    ) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setValidator(validator)
        edit.setMaximumWidth(width)
        field = QHBoxLayout()
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(self._SPACE)
        field.addWidget(edit)
        if suffix:
            field.addWidget(QLabel(suffix))
        field.addStretch()
        form.addRow(label, field)
        return edit

    def _compact(self, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self._SPACE)
        row.addWidget(widget)
        row.addStretch()
        return row

    def _open_keybindings(self) -> None:
        dlg = KeybindingsDialog(self, keybindings=self._settings.get("keybindings", {}))
        if dlg.exec():
            self._settings["keybindings"] = dlg.get_keybindings()

    def _open_radial_menu(self) -> None:
        current = self._settings.get("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        dlg = RadialMenuDialog(self, tools=current)
        if dlg.exec():
            self._settings["radial_menu_tools"] = dlg.get_tools()

    def _open_draw_sidebar_customize(self) -> None:
        current = self._settings.get("draw_sidebar_sections", list(DEFAULT_DRAW_SIDEBAR_SECTIONS))
        current_path_tools = self._settings.get(
            "draw_sidebar_path_tools", list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
        )
        current_shape_tools = self._settings.get(
            "draw_sidebar_shape_tools", list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
        )
        dlg = DrawSidebarCustomizeDialog(
            self,
            sections=current,
            path_tools=current_path_tools,
            shape_tools=current_shape_tools,
        )
        if dlg.exec():
            self._settings["draw_sidebar_sections"] = dlg.get_sections()
            self._settings["draw_sidebar_path_tools"] = dlg.get_path_tools()
            self._settings["draw_sidebar_shape_tools"] = dlg.get_shape_tools()

    def _open_context_menu_customize(self) -> None:
        dlg = ContextMenuActionCustomizeDialog(
            self, profiles=self._settings.get("context_menu_profiles", {})
        )
        if dlg.exec():
            self._settings["context_menu_profiles"] = dlg.get_profiles()

    def _browse_dir(self, key: str) -> None:
        """Open file browser to select a directory."""
        current = self._entries[key].text().strip()
        d = QFileDialog.getExistingDirectory(
            self,
            "Select folder",
            current if current else str(Path.home()),
        )
        if d:
            self._entries[key].setText(d)

    def _save(self, _checked: bool = False, *, close: bool = True) -> None:
        """Save all settings to disk."""
        for key, entry in self._entries.items():
            v = entry.text().strip()
            if v:
                self._settings[key] = v
            elif key in self._settings:
                del self._settings[key]

        for key, toggle in self._toggles.items():
            self._settings[key] = toggle.isChecked()

        trace_defaults = dict(self._settings.get("trace_defaults") or {})
        for key, entry in self._trace_default_entries.items():
            v = entry.text().strip()
            if v:
                trace_defaults[key] = v
            elif key in trace_defaults:
                del trace_defaults[key]
        if trace_defaults:
            self._settings["trace_defaults"] = trace_defaults
        elif "trace_defaults" in self._settings:
            del self._settings["trace_defaults"]

        if self._unit_combo is not None:
            self._settings["unit_system"] = self._unit_combo.currentData()
        if self._ui_scale_combo is not None:
            self._settings["ui_scale"] = self._ui_scale_combo.currentData()
        if self._appearance_combo is not None:
            self._settings["appearance"] = self._appearance_combo.currentData()
        if self._density_combo is not None:
            self._settings["interface_density"] = self._density_combo.currentData()
        if self._rotation_snap_edit is not None:
            try:
                self._settings["rotation_snap_increment"] = float(self._rotation_snap_edit.text())
            except ValueError:
                pass
        if self._grid_spacing_edit is not None:
            try:
                self._settings["grid_spacing"] = float(self._grid_spacing_edit.text())
            except ValueError:
                pass
        if self._fetch_interval_edit is not None:
            try:
                self._settings["auto_fetch_interval_minutes"] = int(
                    self._fetch_interval_edit.text()
                )
            except ValueError:
                pass

        if self._smoothing_combo is not None:
            self._settings["smoothing_method"] = self._smoothing_combo.currentData()

        if self._smooth_iterations_edit is not None:
            try:
                self._settings["smooth_iterations"] = int(self._smooth_iterations_edit.text())
            except ValueError:
                pass

        if self._simplify_tolerance_edit is not None:
            try:
                self._settings["simplify_tolerance"] = float(self._simplify_tolerance_edit.text())
            except ValueError:
                pass

        save_settings(self._settings)
        if close:
            self.accept()
        else:
            self.applied.emit()

    def _confirm_reset_fields(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset Settings",
            "Reset every setting on this page to its default value? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._reset_fields()

    def _reset_fields(self) -> None:
        defaults = SettingsSchema().model_dump()
        for key, entry in self._entries.items():
            entry.setText(str(defaults.get(key, "")))
        for key, toggle in self._toggles.items():
            toggle.setChecked(bool(defaults.get(key, False)))
        for entry in self._trace_default_entries.values():
            entry.clear()
        if self._unit_combo is not None:
            self._unit_combo.setCurrentIndex(
                max(0, self._unit_combo.findData(defaults["unit_system"]))
            )
        if self._appearance_combo is not None:
            self._appearance_combo.setCurrentIndex(
                max(0, self._appearance_combo.findData("system"))
            )
        if self._density_combo is not None:
            self._density_combo.setCurrentIndex(
                max(0, self._density_combo.findData(defaults["interface_density"]))
            )
        if self._ui_scale_combo is not None:
            self._ui_scale_combo.setCurrentIndex(
                max(0, self._ui_scale_combo.findData(defaults["ui_scale"]))
            )
        # These six were missing despite the tooltip promising "all"
        # settings fields — Reset silently left them untouched.
        if self._fetch_interval_edit is not None:
            self._fetch_interval_edit.setText(str(defaults["auto_fetch_interval_minutes"]))
        if self._grid_spacing_edit is not None:
            self._grid_spacing_edit.setText(str(defaults["grid_spacing"]))
        if self._rotation_snap_edit is not None:
            self._rotation_snap_edit.setText(str(defaults["rotation_snap_increment"]))
        if self._smoothing_combo is not None:
            idx = self._smoothing_combo.findData(defaults["smoothing_method"])
            self._smoothing_combo.setCurrentIndex(idx if idx >= 0 else 0)
        if self._smooth_iterations_edit is not None:
            self._smooth_iterations_edit.setText(str(defaults["smooth_iterations"]))
        if self._simplify_tolerance_edit is not None:
            self._simplify_tolerance_edit.setText(str(defaults["simplify_tolerance"]))
