"""Settings dialog window."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.infra.settings import (
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
    SMOOTHING_METHODS,
    save_settings,
)
from src.ui.components import section_label, sep, surface_frame
from src.ui.pages.trace.form import TRACE_DEFAULT_FIELDS, trace_default
from src.ui.util import DEFAULT_UNIT_SYSTEM
from src.ui.widgets.customize_dialogs import (
    ContextMenuCustomizeDialog,
    DrawSidebarCustomizeDialog,
    RadialMenuDialog,
)
from src.ui.widgets.keybindings_dialog import KeybindingsDialog


class SettingsDialog(QDialog):
    """Settings dialog with folder paths and behavioral toggles."""

    _FOLDER_FIELDS = [
        ("workspace_dir", "Workspace folder"),
        ("outline_dxf_dir", "Pattern outline folder"),
        ("pattern_output_dir", "Pattern fill output folder"),
        ("draft_output_dir", "Draft output folder"),
        ("fvi_source_dir", "Trace source folder"),
        ("fvi_output_dir", "Trace output folder"),
    ]

    _REPO_FIELDS = [
        ("repo_dir", "Repository folder"),
    ]

    _TOGGLE_FIELDS = [
        ("check_updates_on_startup", "Check for app updates on startup", False),
        ("high_contrast", "High-contrast status and focus indicators", False),
        ("reduced_motion", "Reduce transient UI animation", False),
        ("persistent_notifications", "Keep canvas notifications visible longer", False),
    ]

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel("Configure workspace paths, folder locations, and application behavior.")
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll.setWidget(content)

        # ── Workspace & Source ────────────────────────────────────
        workspace_card = surface_frame("panel")
        workspace_layout = QVBoxLayout(workspace_card)
        workspace_layout.setContentsMargins(12, 12, 12, 12)
        workspace_layout.setSpacing(6)
        section_label(workspace_layout, "Workspace & Source")
        for key, label in self._FOLDER_FIELDS[:2]:
            self._add_row(workspace_layout, key, label, browse=True)
        content_layout.addWidget(workspace_card)

        # ── Outputs & Conversion ──────────────────────────────────
        output_card = surface_frame("panel")
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(6)
        section_label(output_layout, "Outputs & Conversion")
        for key, label in self._FOLDER_FIELDS[2:]:
            self._add_row(output_layout, key, label, browse=True)
        content_layout.addWidget(output_card)

        # ── Repository ────────────────────────────────────────────
        repo_card = surface_frame("panel")
        repo_layout = QVBoxLayout(repo_card)
        repo_layout.setContentsMargins(12, 12, 12, 12)
        repo_layout.setSpacing(6)
        section_label(repo_layout, "Repository")
        for key, label in self._REPO_FIELDS:
            self._add_row(repo_layout, key, label, browse=True)
        content_layout.addWidget(repo_card)

        # ── Behavior ──────────────────────────────────────────────
        behavior_card = surface_frame("panel")
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(12, 12, 12, 12)
        behavior_layout.setSpacing(8)
        section_label(behavior_layout, "Application Behavior")
        for key, label, default in self._TOGGLE_FIELDS:
            self._add_toggle(behavior_layout, key, label, default)

        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("Display units"))
        self._unit_combo = QComboBox()
        self._unit_combo.addItem("Millimeters (mm)", "mm")
        self._unit_combo.addItem("Inches (in)", "in")
        current_unit = self._settings.get("unit_system", DEFAULT_UNIT_SYSTEM)
        idx = self._unit_combo.findData(current_unit)
        self._unit_combo.setCurrentIndex(idx if idx >= 0 else 0)
        unit_row.addWidget(self._unit_combo)
        unit_row.addStretch()
        behavior_layout.addLayout(unit_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Interface scale"))
        self._ui_scale_combo = QComboBox()
        for label, value in (
            ("90%", 0.9),
            ("100%", 1.0),
            ("110%", 1.1),
            ("125%", 1.25),
            ("150%", 1.5),
        ):
            self._ui_scale_combo.addItem(label, value)
        current_scale = float(self._settings.get("ui_scale", 1.0) or 1.0)
        self._ui_scale_combo.setCurrentIndex(
            min(
                range(self._ui_scale_combo.count()),
                key=lambda i: abs(float(self._ui_scale_combo.itemData(i)) - current_scale),
            )
        )
        scale_row.addWidget(self._ui_scale_combo)
        scale_row.addStretch()
        behavior_layout.addLayout(scale_row)

        smoothing_row = QHBoxLayout()
        smoothing_row.addWidget(QLabel("Smoothing method"))
        self._smoothing_combo = QComboBox()
        for value, label in SMOOTHING_METHODS:
            self._smoothing_combo.addItem(label, value)
        current_smoothing = self._settings.get("smoothing_method", DEFAULT_SMOOTHING_METHOD)
        idx = self._smoothing_combo.findData(current_smoothing)
        self._smoothing_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._smoothing_combo.setToolTip(
            "Algorithm used by the Smooth path command (right-click a "
            "selected path, or the 'path.smooth' shortcut)"
        )
        smoothing_row.addWidget(self._smoothing_combo)
        smoothing_row.addStretch()
        behavior_layout.addLayout(smoothing_row)

        defaults_row = QHBoxLayout()
        defaults_row.addWidget(QLabel("Smooth iterations"))
        self._smooth_iterations_edit = QLineEdit(
            str(self._settings.get("smooth_iterations", DEFAULT_SMOOTH_ITERATIONS))
        )
        self._smooth_iterations_edit.setValidator(QIntValidator(1, 50))
        self._smooth_iterations_edit.setMaximumWidth(60)
        self._smooth_iterations_edit.setToolTip(
            "Default seeded into the Smooth command's HUD prompt"
        )
        defaults_row.addWidget(self._smooth_iterations_edit)
        defaults_row.addSpacing(16)
        defaults_row.addWidget(QLabel("Simplify tolerance (mm)"))
        self._simplify_tolerance_edit = QLineEdit(
            str(self._settings.get("simplify_tolerance", DEFAULT_SIMPLIFY_TOLERANCE))
        )
        self._simplify_tolerance_edit.setValidator(QDoubleValidator(0.001, 1000.0, 3))
        self._simplify_tolerance_edit.setMaximumWidth(70)
        self._simplify_tolerance_edit.setToolTip(
            "Default seeded into the Simplify command's HUD prompt"
        )
        defaults_row.addWidget(self._simplify_tolerance_edit)
        defaults_row.addStretch()
        behavior_layout.addLayout(defaults_row)

        self._add_toggle(
            behavior_layout,
            "draw_sidebar_always_visible",
            "Keep draw sidebar always visible",
            DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
        )

        kb_row = QHBoxLayout()
        kb_row.addStretch()
        radial_btn = QPushButton("Customize radial menu\u2026")
        radial_btn.setToolTip('Choose which tools appear in the "Q" quick menu')
        radial_btn.clicked.connect(self._open_radial_menu)
        kb_row.addWidget(radial_btn)
        sidebar_btn = QPushButton("Customize draw sidebar\u2026")
        sidebar_btn.setToolTip("Choose which sections show in the Draw sidebar")
        sidebar_btn.clicked.connect(self._open_draw_sidebar_customize)
        kb_row.addWidget(sidebar_btn)
        behavior_layout.addLayout(kb_row)

        command_row = QHBoxLayout()
        command_row.addStretch()
        context_btn = QPushButton("Customize context menu…")
        context_btn.setToolTip("Choose which sections appear when right-clicking a canvas")
        context_btn.clicked.connect(self._open_context_menu_customize)
        command_row.addWidget(context_btn)
        kb_btn = QPushButton("Edit shortcuts\u2026")
        kb_btn.setToolTip("Customize keyboard shortcuts")
        kb_btn.clicked.connect(self._open_keybindings)
        command_row.addWidget(kb_btn)
        behavior_layout.addLayout(command_row)
        content_layout.addWidget(behavior_card)

        # ── Trace Defaults ────────────────────────────────────────
        trace_card = surface_frame("panel")
        trace_layout = QVBoxLayout(trace_card)
        trace_layout.setContentsMargins(12, 12, 12, 12)
        trace_layout.setSpacing(6)
        section_label(trace_layout, "Trace Defaults")
        trace_hint = QLabel(
            "Values a newly opened image or a cleared workspace starts "
            "with on the Trace page. Leave a field blank to use the "
            "built-in default."
        )
        trace_hint.setProperty("role", "hint")
        trace_hint.setWordWrap(True)
        trace_layout.addWidget(trace_hint)
        for key, label, tooltip in TRACE_DEFAULT_FIELDS:
            _row, entry = self._add_text_row(
                trace_layout, label, trace_default(self._settings, key)
            )
            entry.setToolTip(tooltip)
            self._trace_default_entries[key] = entry
        content_layout.addWidget(trace_card)

        content_layout.addStretch()
        sep(layout)

        # ── Save / Cancel ─────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setMinimumWidth(110)
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(90)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

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
        current = self._settings.get("context_menu_sections", list(DEFAULT_CONTEXT_MENU_SECTIONS))
        dlg = ContextMenuCustomizeDialog(self, sections=current)
        if dlg.exec():
            self._settings["context_menu_sections"] = dlg.get_sections()

    def _add_row(self, layout: QVBoxLayout, key: str, label: str, browse: bool = False) -> None:
        """Add a folder path input row with optional browse button."""
        row, e = self._add_text_row(layout, label, self._settings.get(key, ""))
        self._entries[key] = e
        if browse:
            btn = QPushButton("Browse")
            btn.setFixedSize(64, 28)
            btn.setProperty("role", "browse-btn")
            btn.setToolTip("Choose a folder")
            btn.clicked.connect(lambda checked, k=key: self._browse_dir(k))
            row.addWidget(btn)
            clear_btn = QPushButton("\u2715")
            clear_btn.setFixedSize(28, 28)
            clear_btn.setProperty("role", "browse-btn")
            clear_btn.setToolTip("Clear this saved folder path")
            clear_btn.clicked.connect(e.clear)
            row.addWidget(clear_btn)

    def _add_toggle(self, layout: QVBoxLayout, key: str, label: str, default: bool = False) -> None:
        """Add a checkbox toggle for a boolean setting."""
        row = QHBoxLayout()
        cb = QCheckBox(label)
        cb.setChecked(self._settings.get(key, default))
        row.addWidget(cb)
        row.addStretch()
        self._toggles[key] = cb
        layout.addLayout(row)

    def _add_text_row(
        self,
        layout: QVBoxLayout,
        label: str,
        text: str,
        *,
        placeholder: str = "",
    ) -> tuple[QHBoxLayout, QLineEdit]:
        row = QHBoxLayout()
        lbl = QLabel(label)
        row.addWidget(lbl)
        entry = QLineEdit()
        if placeholder:
            entry.setPlaceholderText(placeholder)
        entry.setText(text)
        row.addWidget(entry, stretch=1)
        layout.addLayout(row)
        return row, entry

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

    def _save(self) -> None:
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
        self.accept()
