"""Packaged launcher entry point for Simple Stipple."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.app import App, _apply_dark_palette

_ICON_PATH = Path(__file__).parent.parent / "assets" / "icon.png"


def main() -> int:
    app = QApplication(sys.argv)
    _apply_dark_palette(app)
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))
    window = App()
    window.show()
    return app.exec()
