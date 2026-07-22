"""Application theming utilities (palette + external QSS)."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Standard status-label colors, shared by every page's status/footer label
# (previously hardcoded independently in several pages).
STATUS_OK = "#3fb950"
STATUS_ERR = "#f85149"
STATUS_WARN = "#e3b341"
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


def _build_light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f6f8fa"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#eef1f4"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f0f2f4"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0969da"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6e7781"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#d0d7de"))
    return palette


def accessibility_palette(high_contrast: bool = False, appearance: str = "dark") -> QPalette:
    """Return the application palette, optionally with stronger separation."""
    palette = _build_light_palette() if appearance == "light" else _build_dark_palette()
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


def load_app_qss(
    *,
    scale: float = 1.0,
    high_contrast: bool = False,
    appearance: str = "dark",
    density: str = "compact",
) -> str:
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
            qss = qss_path.read_text(encoding="utf-8").replace("{ICONS_DIR}", icons_dir)
            if scale != 1.0:
                qss = re.sub(
                    r"font-size:\s*(\d+(?:\.\d+)?)px",
                    lambda match: f"font-size: {float(match.group(1)) * scale:.1f}px",
                    qss,
                )
            if high_contrast:
                for source, target in {
                    "#0d1117": "#000000",
                    "#161b22": "#080808",
                    "#1a222d": "#151515",
                    "#21262d": "#202020",
                    "#30363d": "#8a8a8a",
                    "#484f58": "#b8b8b8",
                    "#6e7681": "#d0d0d0",
                    "#8b949e": "#e0e0e0",
                    "#e6edf3": "#ffffff",
                }.items():
                    qss = qss.replace(source, target)
            elif appearance == "light":
                for source, target in {
                    "rgba(17, 23, 32, 0.92)": "rgba(255, 255, 255, 0.96)",
                    "rgba(17, 23, 32, 0.95)": "rgba(255, 255, 255, 0.98)",
                    "rgba(23, 30, 40, 0.94)": "rgba(246, 248, 250, 0.98)",
                    "rgba(31, 42, 56, 0.72)": "rgba(246, 248, 250, 0.96)",
                    "rgba(31, 42, 56, 0.9)": "rgba(246, 248, 250, 0.98)",
                    "#0d1117": "#f6f8fa",
                    "#0c1117": "#ffffff",
                    "#0f141b": "#ffffff",
                    "#111720": "#f6f8fa",
                    "#121922": "#eaeef2",
                    "#161b22": "#ffffff",
                    "#1a222d": "#f0f2f4",
                    "#1c2128": "#ffffff",
                    "#21262d": "#eaeef2",
                    "#212b37": "#e1e6eb",
                    "#26303b": "#d0d7de",
                    "#2b3440": "#d0d7de",
                    "#30363d": "#d0d7de",
                    "#303a47": "#b6bec8",
                    "#484f58": "#8c959f",
                    "#6e7681": "#57606a",
                    "#8b949e": "#57606a",
                    "#c9d1d9": "#24292f",
                    "#e6edf3": "#1f2328",
                    "#f0f6fc": "#1f2328",
                }.items():
                    qss = qss.replace(source, target)
                qss += """

/* Light chrome around the deliberately dark CAD canvas. */
QWidget#canvas-toolbar {
    background: #f6f8fa;
    border-color: #d0d7de;
}
QWidget#canvas-toolbar QLabel[role="toolbar-guidance"],
QWidget#canvas-toolbar QLabel[role="toolbar-selection"],
QWidget#canvas-toolbar QToolButton {
    color: #57606a;
}
QToolButton[role="drawer-toggle"] {
    background: rgba(13, 17, 23, 0.92);
    border: 1px solid #58a6ff;
    color: #f0f6fc;
}
QToolButton[role="workflow-step"][state="current"] {
    color: #0969da;
}
QFrame#conversion-preview QLabel[role="empty-title"] { color: #f0f6fc; }
QFrame#conversion-preview QLabel[role="empty-hint"] { color: #b7c3d0; }
"""
            if density == "comfortable":
                qss += """

/* Comfortable density: larger pointer targets without changing canvas scale. */
QPushButton, QToolButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    min-height: 36px;
}
QPushButton[role="primary"] { min-height: 44px; }
QCheckBox, QSlider { min-height: 44px; }
QListView::item, QTreeView::item, QListWidget::item, QTreeWidget::item {
    min-height: 36px;
}
QFrame[role="collapsible"] { padding-top: 4px; padding-bottom: 4px; }
"""
            return qss

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
