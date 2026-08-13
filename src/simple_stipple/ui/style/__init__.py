"""Application theming: resolve design tokens into a Qt palette and stylesheet."""

from __future__ import annotations

import logging
import re
import tempfile
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
    app.setStyle("Fusion")
    app.setPalette(accessibility_palette())
    app.setStyleSheet(load_app_qss())


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
    "radius_sm": "6px",
    "radius_md": "9px",
    "radius_lg": "13px",
    "radius_pill": "999px",
    "font_2xs": "10px",
    "font_xs": "11px",
    "font_sm": "12px",
    "font_md": "13px",
    "font_lg": "16px",
    "font_xl": "20px",
    "font_2xl": "25px",
    "font_display": "36px",
    "weight_regular": "400",
    "weight_medium": "600",
    "weight_semibold": "700",
    "weight_bold": "800",
    "mono": "Menlo, Consolas, monospace",
    # Deliberately theme-invariant: these back drawing previews and the CAD
    # canvas, which are dark in every appearance so geometry reads the same
    # way regardless of the surrounding chrome.
    "bg_ink": "#0b0f14",
    "ink_text": "#f0f6fc",
    "ink_muted": "#b7c3d0",
}

# ── Themes ────────────────────────────────────────────────────────────────
# One accent runs through the whole product. Two used to compete: a blue
# (#2f81f7) in the base stylesheet and a violet in the layer applied over it,
# so a button and the canvas toolbar beside it disagreed about what "active"
# looks like.
_DARK = {
    "bg_app": "#10131a",
    "bg_surface": "#181e2a",
    "bg_surface_alt": "#202838",
    "bg_input": "#121824",
    "overlay": "rgba(18, 24, 36, 0.94)",
    "border": "#2c3648",
    "border_strong": "#43526b",
    "text": "#f2f5fa",
    "text_strong": "#ffffff",
    "text_muted": "#aab6c9",
    "text_subtle": "#7d8aa0",
    "accent": "#9181ff",
    "accent_hover": "#a99dff",
    "accent_pressed": "#7a68f0",
    "accent_soft": "#28234b",
    "accent_border": "#6d5cf0",
    "accent_text": "#c2b8ff",
    # A light violet fill needs dark text on it; white would land near 2.7:1.
    "on_accent": "#10131a",
    "focus": "#55d6ff",
    "success": "#56d3a8",
    "success_soft": "rgba(86, 211, 168, 0.14)",
    "success_border": "rgba(86, 211, 168, 0.5)",
    "warn": "#ffc36a",
    "warn_soft": "rgba(255, 195, 106, 0.14)",
    "warn_border": "rgba(255, 195, 106, 0.5)",
    "danger": "#ff8791",
    "danger_soft": "rgba(255, 135, 145, 0.13)",
    "danger_border": "rgba(255, 135, 145, 0.55)",
    "selection": "#2f3f6b",
    "scrim": "rgba(255, 255, 255, 0.06)",
}

_LIGHT = {
    "bg_app": "#f4f6fb",
    "bg_surface": "#ffffff",
    "bg_surface_alt": "#edf1f8",
    "bg_input": "#f8faff",
    "overlay": "rgba(255, 255, 255, 0.96)",
    "border": "#d9e0ec",
    "border_strong": "#bdc9dc",
    "text": "#17233a",
    "text_strong": "#0b1626",
    # Muted and status colors are darker than their dark-theme counterparts so
    # they still clear 4.5:1 against a white surface.
    "text_muted": "#56647d",
    "text_subtle": "#6f7d94",
    "accent": "#5646c9",
    "accent_hover": "#4838b4",
    "accent_pressed": "#3b2da0",
    "accent_soft": "#eeebff",
    "accent_border": "#8b7cf0",
    "accent_text": "#4736bd",
    "on_accent": "#ffffff",
    "focus": "#1160c4",
    "success": "#0f7a5c",
    "success_soft": "rgba(15, 122, 92, 0.10)",
    "success_border": "rgba(15, 122, 92, 0.42)",
    "warn": "#8a5400",
    "warn_soft": "rgba(138, 84, 0, 0.10)",
    "warn_border": "rgba(138, 84, 0, 0.40)",
    "danger": "#b8323f",
    "danger_soft": "rgba(184, 50, 63, 0.09)",
    "danger_border": "rgba(184, 50, 63, 0.45)",
    "selection": "#dbe6fb",
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
