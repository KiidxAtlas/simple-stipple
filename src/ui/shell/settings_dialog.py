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

from src.settings import save_settings
from src.ui.core.factories import section_label, sep, surface_frame
from src.ui.shell.keybindings_dialog import KeybindingsDialog


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
        ("check_updates_on_startup", "Check for app updates on startup", False),
    ]

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 620)
        self.setMinimumSize(560, 480)
        self.setModal(True)

        self._settings: dict = settings or {}
        self._entries: dict[str, QLineEdit] = {}
        self._toggles: dict[str, QCheckBox] = {}

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

        kb_row = QHBoxLayout()
        kb_row.addStretch()
        kb_btn = QPushButton("Edit shortcuts\u2026")
        kb_btn.setToolTip("Customize keyboard shortcuts")
        kb_btn.clicked.connect(self._open_keybindings)
        kb_row.addWidget(kb_btn)
        behavior_layout.addLayout(kb_row)
        content_layout.addWidget(behavior_card)

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
            clear_btn = QPushButton("\u2715")
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

        save_settings(self._settings)
        self.accept()
