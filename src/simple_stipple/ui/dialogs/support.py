"""Support Me — donation link dialog."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

SUPPORT_URL = "https://buymeacoffee.com/KiidxAtlas"
SUPPORT_DESCRIPTION = "Simple Stipple is built in public and maintained by me. Your support means the world and helps keep the project alive."


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
        desc.setProperty("role", "dialog-message")

        layout.addWidget(desc)
        link = QLabel(f'<a href="{SUPPORT_URL}">{SUPPORT_URL}</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setAccessibleName("Buy Me a Coffee support link")
        link.setToolTip("Open Buy Me a Coffee")
        layout.addWidget(link, alignment=Qt.AlignmentFlag.AlignCenter)

        # Button
        btn = QPushButton("Visit Support Page")
        btn.setMinimumHeight(38)
        btn.clicked.connect(self._on_click)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _on_click(self) -> None:
        webbrowser.open(SUPPORT_URL)
