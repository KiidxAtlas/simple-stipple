"""Settings persistence — load/save a JSON config file in the user's home dir."""

from __future__ import annotations

import json
import logging
import platform as _platform

from src.backend.io import read_json_file, write_json_file_atomic
from src.paths import user_data_dir

_SETTINGS_FILE = user_data_dir() / "settings.json"
_LOG = logging.getLogger(__name__)


# =============================================================================
# Default keybindings for the app-level actions — workspace, app, canvas-mode
# switches, page tabs, and window management. These are the ids App._shortcut()
# resolves for its QActions.
#
# Every OTHER rebindable action (undo/redo, selection, path/boolean ops, tool
# modes, view/grid controls, draw-primitive shortcuts, ...) is a canvas
# Command in src/ui/canvas/commands.py — its default lives on Command.shortcut,
# not here, so there is exactly one place that owns each shortcut's default.
# src.ui.canvas.commands.apply_keybindings() reads the "keybindings" dict
# (this table merged with the user's overrides) to make those live too.
#
# Multi-key combinations (e.g. "Ctrl+Shift+D", "Meta+[") are fully supported
# by the Qt key sequence parser used throughout the codebase.
# =============================================================================

DEFAULT_KEYBINDINGS: dict[str, str] = {
    # ── Workspace ────────────────────────────────────────────────────────────────
    "workspace.new": "Ctrl+N",
    "workspace.new_window": "Ctrl+Shift+N",
    "workspace.open": "Ctrl+O",
    "workspace.save": "Ctrl+S",
    "workspace.save_as": "Ctrl+Shift+S",

    # ── Application ───────────────────────────────────────────────────────────────
    "app.settings": "Ctrl+,",
    "app.command_palette": "Meta+K",
    "window.fullscreen": "F11",

    # ── Canvas modes (single-key shortcuts; also drive the equivalent
    # commands.py Command when the canvas itself has focus) ───────────────────────
    "canvas.select_mode": "S",
    "canvas.draw_mode": "D",
    "canvas.edit_mode": "E",
    "canvas.measure": "M",
    "canvas.dimension": "Shift+M",
    "canvas.fit": "F",

    # ── Page tabs ─────────────────────────────────────────────────────────────────
    "tab.draft": "Alt+1",
    "tab.pattern": "Alt+2",
    "tab.trace": "Alt+3",
    "tab.convert": "Alt+4",
    "tab.repo": "Alt+5",
}


# Platform-adjusted default keybindings: on macOS prefer 'Meta' (Command)
if _platform.system() == "Darwin":
    # Copy and replace Ctrl with Meta where appropriate
    for k, v in list(DEFAULT_KEYBINDINGS.items()):
        if v.startswith("Ctrl"):
            DEFAULT_KEYBINDINGS[k] = v.replace("Ctrl", "Meta", 1)


# =============================================================================
# Radial ("Q") quick menu — which commands show up as wedges, and in what
# order. Every wedge id is a real src.ui.canvas.commands.Command id, so the
# full pool is "every command the canvas knows how to run" — no separate
# action list to keep in sync. Customizable via RadialMenuDialog /
# settings["radial_menu_tools"]; see DxfCanvas.set_radial_menu_tools().
#
# Command.label is often too long/descriptive for a small wedge ("Duplicate
# with Offset", "Union (Weld)") — RADIAL_MENU_SHORT_LABELS overrides just the
# on-wheel text for those; anything not listed here uses Command.label as-is
# (and gets elided if it still doesn't fit, see DxfCanvas._paint_radial_menu).
# =============================================================================

DEFAULT_RADIAL_MENU_TOOLS: tuple[str, ...] = (
    "canvas.polyline",
    "canvas.rectangle",
    "canvas.circle",
    "canvas.polygon",
    "canvas.line",
    "canvas.arc",
    "mode.pen",
)

RADIAL_MENU_SHORT_LABELS: dict[str, str] = {
    "edit.duplicate_offset": "Dup + Offset",
    "edit.array_grid": "Grid Array",
    "edit.array_radial": "Radial Array",
    "edit.delete": "Delete",
    "select.none": "Deselect",
    "select.invert": "Invert",
    "group.dissolve": "Ungroup",
    "path.close": "Close Path",
    "path.open": "Open Path",
    "path.offset": "Offset",
    "construction.toggle": "Construction",
    "vertex.round": "Round Corner",
    "vertex.chamfer": "Chamfer",
    "text.add": "Add Text",
    "text.attach_to_path": "Text on Path",
    "path.simplify": "Simplify",
    "path.smooth": "Smooth",
    "path.fit_curve": "Fit Curve",
    "boolean.union": "Union",
    "mode.draw": "Draw",
    "mode.edit": "Edit",
    "mode.pen": "Pen",
    "mode.trim": "Trim",
    "mode.extend": "Extend",
    "mode.dimension": "Dimension",
    "canvas.polyline": "Polyline",
    "canvas.line": "Line",
    "canvas.rectangle": "Rectangle",
    "canvas.circle": "Circle",
    "canvas.ellipse": "Ellipse",
    "canvas.arc": "Arc",
    "canvas.spline": "Spline",
    "canvas.polygon": "Polygon",
    "view.fit": "Fit",
    "view.rulers": "Rulers",
    "grid.toggle": "Grid",
    "grid.snap": "Snap",
    "grid.coarser": "Grid +",
    "grid.finer": "Grid −",
}


# =============================================================================
# Smoothing method — which algorithm view.smooth_selected() runs. Chosen in
# Settings > Application Behavior; consumed by src.ui.canvas.view.
# =============================================================================

DEFAULT_SMOOTHING_METHOD = "chaikin"

SMOOTHING_METHODS: tuple[tuple[str, str], ...] = (
    ("chaikin", "Chaikin (corner-cutting)"),
    ("gaussian", "Gaussian (neighbor averaging)"),
    ("catmull_rom", "Catmull-Rom (spline through points)"),
)

# Defaults seeded into the Smooth/Simplify HUD prompts (path.smooth /
# path.simplify commands), so the user doesn't have to retype the same
# value every time. Whatever they last typed is remembered here too.
DEFAULT_SMOOTH_ITERATIONS = 2
DEFAULT_SIMPLIFY_TOLERANCE = 0.2


# =============================================================================
# Draw sidebar layout — user-resizable width and, optionally, which
# sections show and in what order. Consumed by src.ui.sidebars.canvas_sidebar
# and src.ui.canvas.view.
# =============================================================================

DEFAULT_DRAW_SIDEBAR_WIDTH = 108
MIN_DRAW_SIDEBAR_WIDTH = 96
MAX_DRAW_SIDEBAR_WIDTH = 220
MIN_DRAW_SIDEBAR_HEIGHT = 200
MAX_DRAW_SIDEBAR_HEIGHT = 900
DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE = False

DEFAULT_DRAW_SIDEBAR_SECTIONS: tuple[str, ...] = (
    "path",
    "shapes",
    "text",
    "snapping",
    "mode",
    "sketch",
    "smoothing",
    "editing",
)

# (section key, display label) — used by the sidebar customize dialog.
DRAW_SIDEBAR_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("path", "Path (Polyline/Spline/Arc/Bezier)"),
    ("shapes", "Shapes (Rectangle/Slot/Circle/Ellipse/Polygon)"),
    ("text", "Text"),
    ("snapping", "Snapping"),
    ("mode", "Split / Construction"),
    ("sketch", "Sketch (Dimension/Measure)"),
    ("smoothing", "Smoothing method"),
    ("editing", "Contextual editing actions"),
)

# Per-icon customization within the Path/Shapes sections — which tools show
# and in what order, independent of whether the whole section is shown.
DEFAULT_DRAW_SIDEBAR_PATH_TOOLS: tuple[str, ...] = ("polyline", "spline", "arc", "bezier")
DRAW_SIDEBAR_PATH_TOOL_LABELS: tuple[tuple[str, str], ...] = (
    ("polyline", "Polyline"),
    ("spline", "Spline"),
    ("arc", "Arc"),
    ("bezier", "Bezier Pen"),
)

DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS: tuple[str, ...] = (
    "rectangle",
    "slot",
    "circle",
    "ellipse",
    "polygon",
)
DRAW_SIDEBAR_SHAPE_TOOL_LABELS: tuple[tuple[str, str], ...] = (
    ("rectangle", "Rectangle"),
    ("slot", "Slot"),
    ("circle", "Circle"),
    ("ellipse", "Ellipse"),
    ("polygon", "Polygon"),
)


def _migrate_settings(data: dict) -> dict:
    """Upgrade legacy settings keys to current names."""
    keybindings = data.get("keybindings")
    if not isinstance(keybindings, dict):
        data["keybindings"] = dict(DEFAULT_KEYBINDINGS)
    else:
        merged = dict(DEFAULT_KEYBINDINGS)
        for key, value in keybindings.items():
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
        data["keybindings"] = merged
    return data


def load_settings() -> dict:
    """Load settings from disk with automatic migration of legacy keys."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = read_json_file(_SETTINGS_FILE, default={})
        if not isinstance(data, dict):
            _LOG.warning(
                "Settings file %s did not contain a JSON object; resetting.",
                _SETTINGS_FILE,
            )
            _backup_corrupt_settings()
            return {}
        data = _migrate_settings(data)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        # Corrupt file: back it up so the user can recover, but don't keep
        # crashing on every launch.
        _LOG.warning(
            "Failed to load settings from %s (%s); backing up and starting fresh.",
            _SETTINGS_FILE,
            exc,
        )
        _backup_corrupt_settings()
        return {}


def _backup_corrupt_settings() -> None:
    try:
        if not _SETTINGS_FILE.exists():
            return
        backup = _SETTINGS_FILE.with_suffix(_SETTINGS_FILE.suffix + ".corrupt")
        # Overwrite any prior backup so we don't accumulate cruft.
        _SETTINGS_FILE.replace(backup)
        _LOG.info("Backed up corrupt settings to %s", backup)
    except OSError as exc:
        _LOG.debug("Could not back up corrupt settings: %s", exc)


def save_settings(d: dict) -> None:
    """Save settings to disk."""
    try:
        write_json_file_atomic(_SETTINGS_FILE, d)
    except (OSError, TypeError, ValueError) as exc:
        _LOG.warning("Failed to save settings to %s: %s", _SETTINGS_FILE, exc)