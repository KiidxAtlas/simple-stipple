"""Packaged launcher entry point for Simple Stipple."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.app import App, _apply_dark_palette


def main() -> int:
    app = QApplication(sys.argv)
    _apply_dark_palette(app)
    window = App()
    window.show()
    return app.exec()
