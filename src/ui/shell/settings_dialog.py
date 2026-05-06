"""Settings dialog window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
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

from src.settings import DEFAULT_KEYBINDINGS, save_settings
from src.ui.components.common.factories import _section_label, _sep, _surface_frame


class SettingsDialog(QDialog):
    """Settings dialog with folder paths and behavioral toggles."""

    _FOLDER_FIELDS = [
        ("workspace_dir", "Workspace folder"),
        ("pattern_library_dir", "Patterns folder"),
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
        ("auto_fetch_on_startup", "Fetch repository on startup", False),
        (
            "auto_fetch_periodic",
            "Fetch repository periodically while app is open",
            False,
        ),
        ("check_updates_on_startup", "Check for app updates on startup", False),
    ]

    _KEYBINDING_FIELDS = [
        ("workspace.new", "New workspace"),
        ("workspace.open", "Open workspace"),
        ("workspace.save", "Save workspace"),
        ("workspace.save_as", "Save workspace as"),
        ("app.settings", "Open settings"),
        ("app.command_palette", "Open command palette"),
        ("canvas.select_mode", "Canvas select mode"),
        ("canvas.draw_mode", "Canvas draw mode"),
        ("canvas.edit_mode", "Canvas edit mode"),
        ("canvas.measure", "Canvas measure"),
        ("canvas.fit", "Canvas fit view"),
        ("tab.draft", "Switch to Draft page"),
        ("tab.pattern", "Switch to Pattern page"),
        ("tab.trace", "Switch to Trace page"),
        ("tab.convert", "Switch to Convert page"),
        ("tab.repo", "Switch to Repo page"),
    ]

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(700, 800)
        self.setMinimumSize(640, 600)
        self.setModal(True)

        self._settings: dict = settings or {}
        self._entries: dict[str, QLineEdit] = {}
        self._toggles: dict[str, QCheckBox] = {}
        self._keybinding_entries: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            "Configure workspace paths, folder locations, and application behavior."
        )
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
        workspace_card = _surface_frame("panel")
        workspace_layout = QVBoxLayout(workspace_card)
        workspace_layout.setContentsMargins(12, 12, 12, 12)
        workspace_layout.setSpacing(6)
        _section_label(workspace_layout, "Workspace & Source")
        for key, label in self._FOLDER_FIELDS[:2]:
            self._add_row(workspace_layout, key, label, browse=True)
        content_layout.addWidget(workspace_card)

        # ── Outputs & Conversion ──────────────────────────────────
        output_card = _surface_frame("panel")
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(6)
        _section_label(output_layout, "Outputs & Conversion")
        for key, label in self._FOLDER_FIELDS[2:]:
            self._add_row(output_layout, key, label, browse=True)
        content_layout.addWidget(output_card)

        # ── Repository ────────────────────────────────────────────
        repo_card = _surface_frame("panel")
        repo_layout = QVBoxLayout(repo_card)
        repo_layout.setContentsMargins(12, 12, 12, 12)
        repo_layout.setSpacing(6)
        _section_label(repo_layout, "Repository")
        for key, label in self._REPO_FIELDS:
            self._add_row(repo_layout, key, label, browse=True)
        content_layout.addWidget(repo_card)

        # ── Behavior ──────────────────────────────────────────────
        behavior_card = _surface_frame("panel")
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(12, 12, 12, 12)
        behavior_layout.setSpacing(8)
        _section_label(behavior_layout, "Application Behavior")
        for key, label, default in self._TOGGLE_FIELDS:
            self._add_toggle(behavior_layout, key, label, default)

        interval_row, self._auto_fetch_interval_entry = self._add_text_row(
            behavior_layout,
            "Auto-fetch interval (minutes)",
            str(self._settings.get("auto_fetch_interval_minutes", 10)),
            placeholder="10",
        )
        self._auto_fetch_interval_entry.setToolTip(
            "How often to run background repository fetch while app is open"
        )
        interval_hint = QLabel(
            "Used only when periodic auto-fetch is enabled. Minimum 1 minute."
        )
        interval_hint.setProperty("role", "hint")
        behavior_layout.addWidget(interval_hint)
        content_layout.addWidget(behavior_card)

        keybinding_card = _surface_frame("panel")
        keybinding_layout = QVBoxLayout(keybinding_card)
        keybinding_layout.setContentsMargins(12, 12, 12, 12)
        keybinding_layout.setSpacing(6)
        _section_label(keybinding_layout, "Keyboard Shortcuts")
        import platform as _platform

        _kbd_mod = "Meta" if _platform.system() == "Darwin" else "Ctrl"
        kb_help = QLabel(f"Use Qt shortcut syntax (e.g. {_kbd_mod}+K, Shift+R, F).")
        kb_help.setProperty("role", "hint")
        keybinding_layout.addWidget(kb_help)
        kb_actions = QHBoxLayout()
        kb_actions.addStretch()
        reset_keys_btn = QPushButton("Reset shortcuts")
        reset_keys_btn.setToolTip("Restore all keyboard shortcuts to their defaults")
        reset_keys_btn.clicked.connect(self._reset_keybindings)
        kb_actions.addWidget(reset_keys_btn)
        keybinding_layout.addLayout(kb_actions)
        for key, label in self._KEYBINDING_FIELDS:
            self._add_keybinding_row(keybinding_layout, key, label)
        content_layout.addWidget(keybinding_card)

        content_layout.addStretch()
        _sep(layout)

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

    def _add_row(
        self, layout: QVBoxLayout, key: str, label: str, browse: bool = False
    ) -> None:
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
            clear_btn = QPushButton("✕")
            clear_btn.setFixedSize(28, 28)
            clear_btn.setProperty("role", "browse-btn")
            clear_btn.setToolTip("Clear this saved folder path")
            clear_btn.clicked.connect(e.clear)
            row.addWidget(clear_btn)

    def _add_toggle(
        self, layout: QVBoxLayout, key: str, label: str, default: bool = False
    ) -> None:
        """Add a checkbox toggle for a boolean setting."""
        row = QHBoxLayout()
        cb = QCheckBox(label)
        cb.setChecked(self._settings.get(key, default))
        row.addWidget(cb)
        row.addStretch()
        self._toggles[key] = cb
        layout.addLayout(row)

    def _add_keybinding_row(self, layout: QVBoxLayout, key: str, label: str) -> None:
        _, entry = self._add_text_row(
            layout,
            label,
            str(
                self._settings.get("keybindings", {}).get(
                    key, DEFAULT_KEYBINDINGS.get(key, "")
                )
            ),
            placeholder=DEFAULT_KEYBINDINGS.get(key, ""),
        )
        entry.setToolTip(f"Shortcut id: {key}")
        self._keybinding_entries[key] = entry

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

    def _reset_keybindings(self) -> None:
        """Restore all shortcut fields to default values."""
        for key, entry in self._keybinding_entries.items():
            entry.setText(DEFAULT_KEYBINDINGS.get(key, ""))

    def _save(self) -> None:
        """Save all settings to disk."""
        # Save folder paths
        for key, entry in self._entries.items():
            v = entry.text().strip()
            if v:
                self._settings[key] = v
            elif key in self._settings:
                del self._settings[key]

        # Save toggles
        for key, toggle in self._toggles.items():
            self._settings[key] = toggle.isChecked()

        interval_raw = self._auto_fetch_interval_entry.text().strip()
        if interval_raw:
            try:
                self._settings["auto_fetch_interval_minutes"] = max(
                    1, int(interval_raw)
                )
            except ValueError:
                self._settings["auto_fetch_interval_minutes"] = 10
        else:
            self._settings["auto_fetch_interval_minutes"] = 10

        keybindings = dict(DEFAULT_KEYBINDINGS)
        for key, entry in self._keybinding_entries.items():
            value = entry.text().strip()
            if value:
                keybindings[key] = value
        self._settings["keybindings"] = keybindings

        save_settings(self._settings)
        self.accept()
