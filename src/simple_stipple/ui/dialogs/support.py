"""Support Me — donation link dialog."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

SUPPORT_URL = "https://buymeacoffee.com/kiidxatlas"
SUPPORT_DESCRIPTION = "Simple Stipple is built open-source and maintained by volunteers. Your support keeps the project alive."


class SupportMeDialog(QDialog):
    """Show a simple dialog with a link to donate / support the developer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Support Simple Stipple")
        self.resize(480, 260)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Title
        title = QLabel("Support Simple Stipple")
        title.setProperty("role", "dialog-title")
        layout.addWidget(title)

        # Description
        desc = QLabel(SUPPORT_DESCRIPTION)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: qss:text; font-size: 12px;")
        layout.addWidget(desc)

        # Button
        btn = QPushButton("Visit Support Page")
        btn.setMinimumHeight(38)
        btn.clicked.connect(self._on_click)
        layout.addWidget(btn, alignment=Qt.AlignCenter)

    def _on_click(self) -> None:
        webbrowser.open(SUPPORT_URL)
