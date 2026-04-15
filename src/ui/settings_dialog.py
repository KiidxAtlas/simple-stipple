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
from src.ui.helpers import _section_label, _sep, _surface_frame


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

    _TOGGLE_FIELDS = [
        ("auto_fetch_on_startup", "Fetch repository on startup", False),
        ("check_updates_on_startup", "Check for app updates on startup", False),
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setStyleSheet("color: #f0f6fc; font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Configure workspace paths, folder locations, and application behavior."
        )
        subtitle.setStyleSheet("color: #8b949e; font-size: 12px;")
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

        # ── Behavior ──────────────────────────────────────────────
        behavior_card = _surface_frame("panel")
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(12, 12, 12, 12)
        behavior_layout.setSpacing(8)
        _section_label(behavior_layout, "Application Behavior")
        for key, label, default in self._TOGGLE_FIELDS:
            self._add_toggle(behavior_layout, key, label, default)
        content_layout.addWidget(behavior_card)

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
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(200)
        row.addWidget(lbl)
        e = QLineEdit()
        e.setText(self._settings.get(key, ""))
        row.addWidget(e, stretch=1)
        self._entries[key] = e
        if browse:
            btn = QPushButton("…")
            btn.setFixedSize(28, 28)
            btn.clicked.connect(lambda checked, k=key: self._browse_dir(k))
            row.addWidget(btn)
        layout.addLayout(row)

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

        save_settings(self._settings)
        self.accept()
