"""Settings dialog window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
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
    _FOLDER_FIELDS = [
        ("workspace_dir", "Workspace folder"),
        ("outline_dxf_dir", "Outline DXF folder"),
        ("pattern_library_dir", "Pattern library folder"),
        ("pattern_output_dir", "Pattern output folder"),
        ("shape_output_dir", "Shape output folder"),
        ("fvi_source_dir", "FVI source folder"),
        ("fvi_output_dir", "FVI output folder"),
    ]

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 620)
        self.setMinimumSize(620, 520)
        self.setModal(True)

        self._settings: dict = settings or {}
        self._entries: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setStyleSheet("color: #f0f6fc; font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel("Configure default folders for workspace, source, and export paths.")
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

        workspace_card = _surface_frame("panel")
        workspace_layout = QVBoxLayout(workspace_card)
        workspace_layout.setContentsMargins(12, 12, 12, 12)
        workspace_layout.setSpacing(6)
        _section_label(workspace_layout, "Workspace & Libraries")
        for key, label in self._FOLDER_FIELDS[:3]:
            self._add_row(workspace_layout, key, label, browse=True)
        content_layout.addWidget(workspace_card)

        output_card = _surface_frame("panel")
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(6)
        _section_label(output_layout, "Outputs & Conversion")
        for key, label in self._FOLDER_FIELDS[3:]:
            self._add_row(output_layout, key, label, browse=True)
        content_layout.addWidget(output_card)

        content_layout.addStretch()
        _sep(layout)

        # Save / Cancel
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
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(170)
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

    def _browse_dir(self, key: str) -> None:
        current = self._entries[key].text().strip()
        d = QFileDialog.getExistingDirectory(
            self,
            "Select folder",
            current if current else str(Path.home()),
        )
        if d:
            self._entries[key].setText(d)

    def _save(self) -> None:
        for key, entry in self._entries.items():
            v = entry.text().strip()
            if v:
                self._settings[key] = v
            elif key in self._settings:
                del self._settings[key]
        save_settings(self._settings)
        self.accept()
