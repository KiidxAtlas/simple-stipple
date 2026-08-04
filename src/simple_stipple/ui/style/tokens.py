"""The single source of truth for every visual value in the application.

``theme.qss`` is a template written against the semantic names below rather
than raw colors: ``background: $bg_surface`` instead of ``background: #181e2a``.
:func:`resolve` turns an appearance choice into the flat substitution map that
:mod:`simple_stipple.ui.style.theme` applies to that template.

Naming is by *role*, never by value, so a theme is a complete set of answers to
the same questions. Adding a theme means adding one dict here — not a table of
hex-to-hex replacements applied to already-rendered CSS, which is how light and
high-contrast modes used to be produced and why they drifted from dark mode
every time a color was added.
"""

from __future__ import annotations

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


__all__ = [
    "MOTION_DURATION_MS",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "STATUS_ROLE_TOKENS",
    "THEMES",
    "resolve",
]
