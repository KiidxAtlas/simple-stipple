"""Application theming: resolve design tokens into a Qt palette and stylesheet."""

from __future__ import annotations

import logging
import re
import tempfile
from functools import lru_cache
from importlib import resources
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

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
    return resolve(appearance, high_contrast)


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
    theme = resolve(appearance, high_contrast)
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
    min-height: 40px;
}
QPushButton[role="primary"] { min-height: 40px; }
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
    return _render_app_qss(
        qss_path.read_text(encoding="utf-8"),
        icons_dir,
        scale=scale,
        high_contrast=high_contrast,
        appearance=appearance,
        density=density,
    )


@lru_cache(maxsize=32)
def _render_app_qss(
    template: str,
    icons_dir: str,
    *,
    scale: float,
    high_contrast: bool,
    appearance: str,
    density: str,
) -> str:
    # Key by template contents too, so editing a theme never leaves a stale cache.
    qss = template.replace("{ICONS_DIR}", icons_dir)
    qss = substitute_tokens(qss, resolve(appearance, high_contrast))
    qss += _density_overrides(density)

    if scale != 1.0:
        # Scale text-adjacent chrome (control height and padding) alongside
        # font-size — otherwise growing text no longer fits the fixed-size
        # box around it and gets clipped at higher scale settings.
        qss = re.sub(
            r"(font-size|min-height|padding):([^;]+)",
            lambda match: (
                match.group(1)
                + ":"
                + re.sub(
                    r"(\d+(?:\.\d+)?)px",
                    lambda px: f"{float(px.group(1)) * scale:.1f}px",
                    match.group(2),
                )
            ),
            qss,
        )
    return qss


def apply_dark_theme(app: QApplication) -> None:
    """Apply app-wide palette and stylesheet at the composition boundary."""
    if app.style().objectName().casefold() != "fusion":
        app.setStyle("Fusion")
    palette = accessibility_palette()
    if app.palette() != palette:
        app.setPalette(palette)
    qss = load_app_qss()
    if app.styleSheet() != qss:
        app.setStyleSheet(qss)


# ── Spacing, motion ───────────────────────────────────────────────────────
# Widget code composes layouts in Python, so these stay plain ints.
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
MOTION_DURATION_MS = 150

# ── Scales shared by every theme ──────────────────────────────────────────
# A type ramp with visible steps: adjacent sizes differ enough to establish
# hierarchy on their own, so weight and color are not asked to do that work
# as well.
_SCALE = {
    "radius_sm": "4px",
    "radius_md": "6px",
    "radius_lg": "8px",
    "radius_pill": "999px",
    "font_2xs": "11px",
    "font_xs": "12px",
    "font_sm": "13px",
    "font_md": "14px",
    "font_lg": "16px",
    "font_xl": "20px",
    "font_2xl": "25px",
    "font_display": "36px",
    "weight_regular": "400",
    "weight_medium": "500",
    "weight_semibold": "600",
    "weight_bold": "700",
    "mono": "Menlo, Consolas, monospace",
    "check_icon": "check.svg",
    # Deliberately theme-invariant: these back drawing previews and the CAD
    # canvas, which are dark in every appearance so geometry reads the same
    # way regardless of the surrounding chrome.
    "bg_ink": "#0b0f14",
    "ink_text": "#f0f6fc",
    "ink_muted": "#b7c3d0",
}

# ── Themes ────────────────────────────────────────────────────────────────
# Engineering-blue marks actions and selection; neutral graphite surfaces keep
# the drawing primary. The canvas itself remains appearance-invariant.
_DARK = {
    "bg_app": "#14191f",
    "bg_surface": "#1c232c",
    "bg_surface_alt": "#252e39",
    "bg_input": "#11171e",
    "overlay": "rgba(28, 35, 44, 0.97)",
    "border": "#36414f",
    "border_strong": "#536477",
    "text": "#e6edf3",
    "text_strong": "#ffffff",
    "text_muted": "#b1bfcc",
    "text_subtle": "#8b9bad",
    "accent": "#68ade8",
    "check_icon": "check_dark.svg",
    "accent_hover": "#8ac3f1",
    "accent_pressed": "#5096d1",
    "accent_soft": "#1c364d",
    "accent_border": "#518cbd",
    "accent_text": "#a5d5fa",
    "on_accent": "#101c28",
    "focus": "#b0dcff",
    "success": "#56d3a8",
    "success_soft": "rgba(86, 211, 168, 0.14)",
    "success_border": "rgba(86, 211, 168, 0.5)",
    "warn": "#ffc36a",
    "warn_soft": "rgba(255, 195, 106, 0.14)",
    "warn_border": "rgba(255, 195, 106, 0.5)",
    "danger": "#ff8791",
    "danger_soft": "rgba(255, 135, 145, 0.13)",
    "danger_border": "rgba(255, 135, 145, 0.55)",
    "selection": "#254b6b",
    "scrim": "rgba(255, 255, 255, 0.06)",
}

_LIGHT = {
    "bg_app": "#edf1f4",
    "bg_surface": "#ffffff",
    "bg_surface_alt": "#e7edf2",
    "bg_input": "#f7f9fb",
    "overlay": "rgba(255, 255, 255, 0.97)",
    "border": "#c9d3dd",
    "border_strong": "#8e9faf",
    "text": "#243342",
    "text_strong": "#142330",
    # Muted labels remain readable on both white and alternate surfaces.
    "text_muted": "#506274",
    "text_subtle": "#5c6f80",
    "accent": "#225f98",
    "accent_hover": "#194e80",
    "accent_pressed": "#123d66",
    "accent_soft": "#e3eff9",
    "accent_border": "#5b8fb8",
    "accent_text": "#194f7d",
    "on_accent": "#ffffff",
    "focus": "#125fa5",
    "success": "#0f7a5c",
    "success_soft": "rgba(15, 122, 92, 0.10)",
    "success_border": "rgba(15, 122, 92, 0.42)",
    "warn": "#8a5400",
    "warn_soft": "rgba(138, 84, 0, 0.10)",
    "warn_border": "rgba(138, 84, 0, 0.40)",
    "danger": "#b8323f",
    "danger_soft": "rgba(184, 50, 63, 0.09)",
    "danger_border": "rgba(184, 50, 63, 0.45)",
    "selection": "#d5e7f5",
    "scrim": "rgba(11, 22, 38, 0.05)",
}

# High contrast is a third theme rather than a post-processing pass, so it
# picks up every rule the other two get instead of only the subset whose hex
# codes happened to appear in a replacement table.
_HIGH_CONTRAST = {
    "bg_app": "#000000",
    "bg_surface": "#0a0a0a",
    "bg_surface_alt": "#1a1a1a",
    "bg_input": "#000000",
    "overlay": "rgba(0, 0, 0, 0.97)",
    "border": "#8a8a8a",
    "border_strong": "#d0d0d0",
    "text": "#ffffff",
    "text_strong": "#ffffff",
    "text_muted": "#e6e6e6",
    "text_subtle": "#c8c8c8",
    "accent": "#7cc4ff",
    "check_icon": "check_dark.svg",
    "accent_hover": "#a8d8ff",
    "accent_pressed": "#58a6ff",
    "accent_soft": "#10233a",
    "accent_border": "#7cc4ff",
    "accent_text": "#a8d8ff",
    "on_accent": "#000000",
    "focus": "#ffd400",
    "success": "#5ce68f",
    "success_soft": "#04220f",
    "success_border": "#5ce68f",
    "warn": "#ffd400",
    "warn_soft": "#2a2200",
    "warn_border": "#ffd400",
    "danger": "#ff9a9a",
    "danger_soft": "#2c0b0b",
    "danger_border": "#ff9a9a",
    "selection": "#10233a",
    "scrim": "rgba(255, 255, 255, 0.14)",
}

THEMES = {"dark": _DARK, "light": _LIGHT, "high_contrast": _HIGH_CONTRAST}

# Status colors are also read from Python (canvas flashes, status labels), so
# they are exposed per theme under stable names rather than duplicated there.
STATUS_ROLE_TOKENS = {
    "ok": "success",
    "err": "danger",
    "warn": "warn",
    "neutral": "text_muted",
}


def resolve(appearance: str = "dark", high_contrast: bool = False) -> dict[str, str]:
    """Return the flat ``name -> value`` map for one appearance.

    ``high_contrast`` wins over ``appearance``: it is an accessibility
    override, not a third preference to be combined with the others.
    """
    if high_contrast:
        theme = THEMES["high_contrast"]
    else:
        theme = THEMES.get(appearance, _DARK)
    return {**_SCALE, **theme}
