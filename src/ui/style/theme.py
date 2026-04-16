"""Application theming utilities (palette + external QSS)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def _build_dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#161b22"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Base, QColor("#0d1117"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#1c2128"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1c2128"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Text, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Button, QColor("#21262d"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2f81f7"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#484f58"))
    p.setColor(QPalette.ColorRole.Mid, QColor("#30363d"))
    p.setColor(QPalette.ColorRole.Dark, QColor("#0d1117"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#484f58"))
    p.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor("#484f58"),
    )
    return p


def load_app_qss() -> str:
    bundled_root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(__file__).with_name("theme.qss")]
    if bundled_root:
        candidates.append(Path(bundled_root) / "src" / "ui" / "style" / "theme.qss")

    for qss_path in candidates:
        if qss_path.exists():
            return qss_path.read_text(encoding="utf-8")

    logging.warning(
        "Theme stylesheet not found in expected locations: %s",
        ", ".join(str(path) for path in candidates),
    )
    return ""


def apply_dark_theme(app: QApplication) -> None:
    """Apply app-wide dark palette and stylesheet from external QSS file."""
    app.setStyle("Fusion")
    app.setPalette(_build_dark_palette())
    app.setStyleSheet(load_app_qss())
