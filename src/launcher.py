"""Packaged launcher entry point for Simple Stipple."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.app import App
from src.ui.style.theme import apply_dark_theme


def _resolve_icon_path() -> Path | None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(__file__).parent.parent / "assets" / "icon.png"]
    if bundled_root:
        candidates.append(Path(bundled_root) / "assets" / "icon.png")

    for path in candidates:
        if path.exists():
            return path
    return None


def main() -> int:
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    icon_path = _resolve_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    window = App()
    window.show()
    return app.exec()
