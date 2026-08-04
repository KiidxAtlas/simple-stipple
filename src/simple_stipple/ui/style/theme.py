"""Application theming: resolve design tokens into a Qt palette and stylesheet."""

from __future__ import annotations

import logging
import re
import tempfile
from importlib import resources
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from simple_stipple.ui.style import tokens

# Standard status-label colors, shared by every page's status/footer label
# (previously hardcoded independently in several pages).
STATUS_OK = "#3fb950"
STATUS_ERR = "#f85149"
STATUS_WARN = "#e3b341"
STATUS_NEUTRAL = "#8b949e"

# ``$name`` in theme.qss. QSS already uses ``{}`` for rule blocks, so ``$`` is
# the placeholder rather than ``str.format``.
_TOKEN_PATTERN = re.compile(r"\$([a-z0-9_]+)")

_STYLE_TEMP_DIR: tempfile.TemporaryDirectory[str] | None = None


def _style_resource_root() -> Path:
    """Return a real directory containing the packaged QSS and SVG icons."""
    global _STYLE_TEMP_DIR

    resource_root = resources.files(__package__)
    # ``Traversable`` deliberately does not promise ``os.PathLike``. The
    # normal filesystem loader returns a concrete Path; archive-backed
    # loaders fall through to the persistent extraction below.
    if isinstance(resource_root, Path) and resource_root.is_dir():
        return resource_root

    # Qt requires real filesystem paths for QSS url() references. Materialize
    # resources once when the package comes from a non-filesystem importer.
    if _STYLE_TEMP_DIR is None:
        _STYLE_TEMP_DIR = tempfile.TemporaryDirectory(prefix="simple-stipple-style-")
        extracted_root = Path(_STYLE_TEMP_DIR.name)
        extracted_root.joinpath("theme.qss").write_bytes(
            resource_root.joinpath("theme.qss").read_bytes()
        )
        icons_root = extracted_root / "icons"
        icons_root.mkdir()
        for icon in resource_root.joinpath("icons").iterdir():
            if icon.name.lower().endswith(".svg"):
                icons_root.joinpath(icon.name).write_bytes(icon.read_bytes())
    return Path(_STYLE_TEMP_DIR.name)


def style_resource_path(relative_path: str) -> Path:
    """Resolve a packaged style resource to a filesystem path for Qt."""
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid style resource path: {relative_path!r}")
    return _style_resource_root().joinpath(candidate)


def icon_path(name: str) -> Path:
    """Resolve a packaged SVG icon for ``QIcon`` and stylesheet consumers."""
    return style_resource_path(f"icons/{name}")


def resolve_tokens(*, appearance: str = "dark", high_contrast: bool = False) -> dict[str, str]:
    """Return the active token map — the same one the stylesheet is built from.

    Exposed so widgets that must paint outside QSS (canvas overlays, rendered
    icons) can read the theme instead of hardcoding a color that then only
    looks right in one appearance.
    """
    return tokens.resolve(appearance, high_contrast)


def substitute_tokens(template: str, values: dict[str, str]) -> str:
    """Replace every ``$name`` in *template*.

    An unknown name is a typo in the stylesheet, not something to paper over
    with a default that silently renders the wrong color, so it raises.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return values[name]
        except KeyError:
            raise KeyError(f"Unknown style token ${name}") from None

    return _TOKEN_PATTERN.sub(replace, template)


def accessibility_palette(high_contrast: bool = False, appearance: str = "dark") -> QPalette:
    """Return the application palette built from the active token set.

    Qt draws some chrome (native menus, non-styled sub-controls, the text
    cursor) from the palette rather than the stylesheet, so both are derived
    from the same tokens and cannot disagree.
    """
    theme = tokens.resolve(appearance, high_contrast)
    palette = QPalette()
    for role, token in (
        (QPalette.ColorRole.Window, "bg_app"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "bg_input"),
        (QPalette.ColorRole.AlternateBase, "bg_surface_alt"),
        (QPalette.ColorRole.ToolTipBase, "bg_surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "bg_surface"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.BrightText, "text_strong"),
        (QPalette.ColorRole.Highlight, "accent"),
        (QPalette.ColorRole.HighlightedText, "on_accent"),
        (QPalette.ColorRole.PlaceholderText, "text_subtle"),
        (QPalette.ColorRole.Mid, "border"),
        (QPalette.ColorRole.Dark, "bg_app"),
    ):
        palette.setColor(role, QColor(theme[token]))
    disabled = QColor(theme["text_subtle"])
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return palette


def _density_overrides(density: str) -> str:
    """Comfortable density enlarges pointer targets without rescaling the canvas."""
    if density != "comfortable":
        return ""
    return """

QPushButton, QToolButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    min-height: 36px;
}
QPushButton[role="primary"] { min-height: 44px; }
QCheckBox, QRadioButton, QSlider { min-height: 44px; }
QListView::item, QTreeView::item, QListWidget::item, QTreeWidget::item {
    min-height: 36px;
}
QFrame[role="collapsible"] { padding-top: 4px; padding-bottom: 4px; }
"""


def load_app_qss(
    *,
    scale: float = 1.0,
    high_contrast: bool = False,
    appearance: str = "dark",
    density: str = "compact",
) -> str:
    """Render the stylesheet template for one appearance."""
    qss_path = style_resource_path("theme.qss")
    if not qss_path.exists():
        logging.warning("Theme stylesheet not found at %s", qss_path)
        return ""

    # Qt's QSS url() needs forward slashes even on Windows, and the icon
    # directory may be a package path or a materialized archive.
    icons_dir = qss_path.with_name("icons").as_posix()
    qss = qss_path.read_text(encoding="utf-8").replace("{ICONS_DIR}", icons_dir)
    qss = substitute_tokens(qss, tokens.resolve(appearance, high_contrast))
    qss += _density_overrides(density)

    if scale != 1.0:
        qss = re.sub(
            r"font-size:\s*(\d+(?:\.\d+)?)px",
            lambda match: f"font-size: {float(match.group(1)) * scale:.1f}px",
            qss,
        )
    return qss


def apply_dark_theme(app: QApplication) -> None:
    """Apply app-wide palette and stylesheet at the composition boundary."""
    app.setStyle("Fusion")
    app.setPalette(accessibility_palette())
    app.setStyleSheet(load_app_qss())
