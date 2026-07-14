"""Application theming utilities (palette + external QSS)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Standard status-label colors, shared by every page's status/footer label
# (previously hardcoded independently in several pages).
STATUS_OK = "#3fb950"
STATUS_ERR = "#f85149"
STATUS_NEUTRAL = "#8b949e"


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


def accessibility_palette(high_contrast: bool = False) -> QPalette:
    """Return the application palette, optionally with stronger separation."""
    palette = _build_dark_palette()
    if high_contrast:
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#151515"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Mid, QColor("#8a8a8a"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#58a6ff"))
    return palette


def load_app_qss() -> str:
    bundled_root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(__file__).with_name("theme.qss")]
    if bundled_root:
        candidates.append(Path(bundled_root) / "src" / "ui" / "style" / "theme.qss")

    for qss_path in candidates:
        if qss_path.exists():
            icons_dir = qss_path.with_name("icons").as_posix()
            # Qt's QSS url() needs forward slashes even on Windows, and the
            # icon dir must be resolved at load time since it differs
            # between a dev checkout and a PyInstaller-bundled _MEIPASS root.
            return qss_path.read_text(encoding="utf-8").replace("{ICONS_DIR}", icons_dir)

    logging.warning(
        "Theme stylesheet not found in expected locations: %s",
        ", ".join(str(path) for path in candidates),
    )
    return ""


def apply_dark_theme(app: QApplication) -> None:
    """Apply app-wide dark palette and stylesheet from external QSS file."""
    app.setStyle("Fusion")
    app.setPalette(accessibility_palette())
    app.setStyleSheet(load_app_qss())
