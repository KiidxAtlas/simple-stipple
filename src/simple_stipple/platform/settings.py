"""Settings persistence — load/save a JSON config file in the user's home dir."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from PySide6.QtCore import QObject, Signal

from simple_stipple.platform.paths import custom_tiles_dir, user_data_dir
from simple_stipple.platform.storage import read_json_file, write_json_file_atomic

_SETTINGS_FILE = user_data_dir() / "settings.json"
_LOG = logging.getLogger(__name__)
_LIVE_SETTINGS: dict | None = None


class SettingsBus(QObject):
    """Process-wide settings propagation for multi-window sessions."""

    changed = Signal(str, object, object)

    def publish(self, key: str, value: object, source: object) -> None:
        self.changed.emit(key, value, source)


settings_bus = SettingsBus()


# =============================================================================
# Default keybindings for the app-level actions — workspace, app, canvas-mode
# switches, page tabs, and window management. These are the ids App._shortcut()
# resolves for its QActions.
#
# Every OTHER rebindable action (undo/redo, selection, path/boolean ops, tool
# modes, view/grid controls, draw-primitive shortcuts, ...) is a canvas
# Command in src/simple_stipple/canvas/commands.py — its default lives on Command.shortcut,
# not here, so there is exactly one place that owns each shortcut's default.
# simple_stipple.canvas.commands.apply_keybindings() reads the "keybindings" dict
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
    "app.command_palette": "Ctrl+K",
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


# No per-platform rewriting: Qt maps "Ctrl" to Command on macOS already
# (AA_MacDontSwapCtrlAndMeta is never set), so "Ctrl+S" is ⌘S there and
# Control+S everywhere else. "Meta" would bind the physical Control key.


# =============================================================================
# Radial ("Q") quick menu — which commands show up as wedges, and in what
# order. Every wedge id is a real simple_stipple.canvas.commands.Command id, so the
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
    "canvas.rounded_rectangle": "Rounded Rect",
    "canvas.star": "Star",
    "view.fit": "Fit",
    "view.rulers": "Rulers",
    "grid.toggle": "Grid",
    "grid.snap": "Snap",
    "grid.coarser": "Grid +",
    "grid.finer": "Grid −",
}


# Canvas context menu sections. Direct object actions (select/deselect/delete
# and role assignment) are intentionally not configurable: they are the small
# safety core that keeps right-click useful even when every optional section is
# hidden. ``view`` is likewise required by the customize dialog for empty
# canvas access.
DEFAULT_CONTEXT_MENU_SECTIONS: tuple[str, ...] = (
    "create",
    "selected",
    "selection",
    "share_diagnostics",
    "boolean",
    "arrange",
    "transform",
    "text",
    "view",
)

# Sections placed under "More actions…". Everything else stays directly
# visible; this replaces the old position-based cutoff that routinely buried
# the operation relevant to the current mode.
DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS: tuple[str, ...] = (
    "arrange",
    "text",
)

# Leaf actions initially placed under the action-level context menu's
# ``More actions…`` submenu. These are useful but not frequent enough to
# compete with selection, clipboard, and shape actions at the top level.
DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS: tuple[str, ...] = (
    "select.lasso",
    "context.selection.move",
    "context.selection.smooth",
    "context.selection.simplify",
    "context.selection.fit",
    "view.fit",
    "grid.toggle",
    "grid.snap",
    "context.view.select",
    "mode.draw",
    "mode.edit",
)
CONTEXT_MENU_PROFILES: tuple[str, ...] = ("draft", "pattern", "trace")

CONTEXT_MENU_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("create", "Create shapes"),
    ("selected", "Selected-object actions, paths, constraints & symbols"),
    ("selection", "Selection tools"),
    ("share_diagnostics", "Send, fill roles & geometry diagnostics"),
    ("boolean", "Boolean operations"),
    ("arrange", "Align & distribute"),
    ("transform", "Transform, trim, knife, explode & merge"),
    ("text", "Add text"),
    ("view", "View, grid & mode"),
)

CONTEXT_MENU_TRANSFORM_ITEMS: tuple[tuple[str, str], ...] = (
    ("rotate_cw", "Rotate +90°"),
    ("rotate_ccw", "Rotate −90°"),
    ("mirror_horizontal", "Mirror horizontal"),
    ("mirror_vertical", "Mirror vertical"),
    ("size", "Edit width + height…"),
    ("length", "Set line length…"),
    ("angle", "Set line angle…"),
    ("trim", "Trim segments…"),
    ("extend", "Extend to meet…"),
    ("knife", "Knife tool"),
    ("explode", "Explode to segments"),
    ("merge", "Merge segments to object"),
)


def normalize_context_menu_transform_items(value: object) -> list[str]:
    allowed = {key for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS}
    configured = [key for key in (value if isinstance(value, list) else []) if key in allowed]
    return list(dict.fromkeys(configured))


def normalize_context_menu_sections(value: object) -> list[str]:
    allowed = {key for key, _label in CONTEXT_MENU_SECTION_LABELS}
    configured = [
        key
        for key in (value if isinstance(value, list) else [])
        if isinstance(key, str) and key in allowed
    ]
    # Retain order, remove duplicates, and preserve a recovery/navigation core.
    result = list(dict.fromkeys(configured))
    if "view" not in result:
        result.append("view")
    return result


def normalize_context_menu_overflow_sections(value: object) -> list[str]:
    allowed = {key for key, _label in CONTEXT_MENU_SECTION_LABELS}
    values = value if isinstance(value, list) else list(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS)
    return list(dict.fromkeys(key for key in values if isinstance(key, str) and key in allowed))


def normalize_context_menu_profiles(value: object) -> dict[str, dict[str, list[str]]]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, list[str]]] = {}
    for profile in CONTEXT_MENU_PROFILES:
        saved = raw.get(profile)
        saved = saved if isinstance(saved, dict) else {}
        result[profile] = {
            "sections": normalize_context_menu_sections(
                saved.get("sections", list(DEFAULT_CONTEXT_MENU_SECTIONS))
            ),
            "overflow": normalize_context_menu_overflow_sections(
                saved.get("overflow", list(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS))
            ),
            "transform": normalize_context_menu_transform_items(
                saved.get("transform", [key for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS])
            ),
            # An empty list is an intentional "show none" choice after the
            # action-level customizer has been saved. Preserve that separately
            # from a legacy profile that has never supplied ``items``.
            "action_items_configured": (
                ["yes"]
                if bool(saved.get("action_items_configured")) or bool(saved.get("items"))
                else []
            ),
            "items": list(
                dict.fromkeys(
                    key
                    for key in saved.get("items", [])
                    if isinstance(key, str) and key
                )
            )
            if isinstance(saved.get("items", []), list)
            else [],
            "overflow_items": list(
                dict.fromkeys(
                    key
                    for key in saved.get("overflow_items", [])
                    if isinstance(key, str) and key
                )
            )
            if isinstance(saved.get("overflow_items", []), list)
            else [],
        }
    return result


# =============================================================================
# Smoothing method — which algorithm view.smooth_selected() runs. Chosen in
# Settings > Application Behavior; consumed by simple_stipple.canvas.view.
# =============================================================================

DEFAULT_SMOOTHING_METHOD: Literal["chaikin", "gaussian", "catmull_rom"] = "chaikin"

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
# sections show and in what order. Consumed by simple_stipple.canvas.widgets.draw_sidebar
# and simple_stipple.canvas.view.
# =============================================================================

# Two 44 px tool targets, their 6 px gap, section/content gutters, the
# always-available 24 px resize target, and the 8 px vertical scrollbar need
# 172 px before borders/style rounding. Keep a small safety gutter so the
# second column can never render beneath the scrollbar.
DEFAULT_DRAW_SIDEBAR_WIDTH = 176
MIN_DRAW_SIDEBAR_WIDTH = 176
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
    ("snapping", "Constraint (H/V/45°)"),
    ("mode", "Split"),
    ("sketch", "Sketch (Dimension)"),
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
    "rounded_rectangle",
    "slot",
    "circle",
    "ellipse",
    "polygon",
    "star",
)
DRAW_SIDEBAR_SHAPE_TOOL_LABELS: tuple[tuple[str, str], ...] = (
    ("rectangle", "Rectangle"),
    ("rounded_rectangle", "Rounded Rectangle"),
    ("slot", "Slot"),
    ("circle", "Circle"),
    ("ellipse", "Ellipse"),
    ("polygon", "Polygon"),
    ("star", "Star"),
)


def normalize_draw_sidebar_shape_tools(tools: object) -> list[str]:
    """Retain configured tools while making newly shipped primitives visible."""
    configured = [
        tool
        for tool in (tools if isinstance(tools, list) else [])
        if isinstance(tool, str) and tool in DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS
    ]
    if not configured:
        return list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
    # New primitives are product capabilities, not optional migration debris:
    # put them near Rectangle where they remain visible in short sidebars.
    for tool in ("star", "rounded_rectangle"):
        if tool in configured:
            configured.remove(tool)
    insert_at = configured.index("rectangle") + 1 if "rectangle" in configured else 0
    configured[insert_at:insert_at] = ["rounded_rectangle", "star"]
    return configured


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
    saved_shape_tools = data.get("draw_sidebar_shape_tools")
    if isinstance(saved_shape_tools, list):
        data["draw_sidebar_shape_tools"] = normalize_draw_sidebar_shape_tools(saved_shape_tools)
    return data


# (key, expected type, default, optional allowed-values check) for the
# settings this app reads directly with no other validation at the call
# site — a hand-edited settings.json with the wrong type for one of these
# used to pass straight through to whatever first read it (e.g.
# ``PageRuntime.apply_settings`` assigns the raw dict to every page's
# ``_settings`` with no checking at all) and surface as a crash somewhere
# downstream instead of at load time.
class SettingsSchema(BaseModel):
    """Validated settings owned by the application runtime.

    Unknown keys are retained so older builds do not destroy newer settings.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    unit_system: Literal["mm", "in"] = "mm"
    smoothing_method: Literal["chaikin", "gaussian", "catmull_rom"] = DEFAULT_SMOOTHING_METHOD
    smooth_iterations: Annotated[int, Field(ge=1, le=100)] = DEFAULT_SMOOTH_ITERATIONS
    simplify_tolerance: Annotated[float, Field(ge=0.0)] = DEFAULT_SIMPLIFY_TOLERANCE
    draw_sidebar_width: Annotated[
        int, Field(ge=MIN_DRAW_SIDEBAR_WIDTH, le=MAX_DRAW_SIDEBAR_WIDTH)
    ] = DEFAULT_DRAW_SIDEBAR_WIDTH
    draw_sidebar_height: Annotated[
        int | None, Field(ge=MIN_DRAW_SIDEBAR_HEIGHT, le=MAX_DRAW_SIDEBAR_HEIGHT)
    ] = None
    trace_sidebar_width: Annotated[int, Field(ge=300, le=420)] = 320
    convert_sidebar_width: Annotated[int, Field(ge=300, le=440)] = 380
    convert_selected_task: Annotated[int, Field(ge=0, le=3)] = 0
    draw_sidebar_always_visible: bool = DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE
    check_updates_on_startup: bool = False
    auto_fetch_on_startup: bool = False
    auto_fetch_periodic: bool = False
    auto_fetch_interval_minutes: Annotated[int, Field(ge=1, le=1440)] = 10
    ui_scale: Annotated[float, Field(ge=0.5, le=3.0)] = 1.0
    interface_density: Literal["compact", "comfortable"] = "compact"
    rotation_snap_increment: Annotated[float, Field(ge=0.1, le=180.0)] = 15.0
    custom_tiles_dir: str = Field(default_factory=lambda: str(custom_tiles_dir()))
    grid_visible: bool = True
    grid_snap: bool = False
    grid_spacing: Annotated[float, Field(ge=0.001, le=100000.0)] = 5.0
    snap_master: bool = True
    snap_vertex: bool = True
    snap_midpoint: bool = True
    snap_intersection: bool = True
    snap_edge: bool = True
    snap_tangent: bool = True
    snap_extension: bool = True
    snap_angle: bool = True
    snap_parallel: bool = True
    snap_perpendicular: bool = True
    snap_equal_length: bool = True
    snap_axis_alignment: bool = True
    snap_align_x: bool = True
    snap_align_y: bool = True
    construction_mode_default: bool = False
    geometry_health_visible: bool = False
    curvature_visible: bool = False
    high_contrast: bool = False
    reduced_motion: bool = False
    persistent_notifications: bool = False
    radial_menu_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_RADIAL_MENU_TOOLS))
    context_menu_sections: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CONTEXT_MENU_SECTIONS)
    )
    context_menu_overflow_sections: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS)
    )
    context_menu_profiles: dict[str, dict[str, list[str]]] = Field(
        default_factory=lambda: normalize_context_menu_profiles({})
    )
    draw_sidebar_sections: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
    )
    draw_sidebar_path_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
    )
    draw_sidebar_shape_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
    )
    # Sentry crash-reporting DSN — empty string means disabled.
    # Users can set this via environment variable SENTRY_DSN or here.
    sentry_dsn: str = ""


def validate_settings(data: dict) -> dict:
    """Validate known keys independently and preserve unknown future keys."""
    raw = dict(data or {})
    defaults = SettingsSchema().model_dump()
    validated = dict(raw)
    for key in SettingsSchema.model_fields:
        if key not in raw:
            validated[key] = defaults[key]
            continue
        try:
            one_field = SettingsSchema.model_validate({key: raw[key]})
            validated[key] = getattr(one_field, key)
        except ValidationError:
            validated[key] = defaults[key]
    validated["context_menu_sections"] = normalize_context_menu_sections(
        validated.get("context_menu_sections")
    )
    validated["context_menu_overflow_sections"] = normalize_context_menu_overflow_sections(
        validated.get("context_menu_overflow_sections")
    )
    validated["context_menu_profiles"] = normalize_context_menu_profiles(
        validated.get("context_menu_profiles")
    )
    return validated


def load_settings() -> dict:
    """Load settings from disk with automatic migration of legacy keys."""
    global _LIVE_SETTINGS
    if _LIVE_SETTINGS is not None:
        return _LIVE_SETTINGS
    if not _SETTINGS_FILE.exists():
        _LIVE_SETTINGS = validate_settings({})
        return _LIVE_SETTINGS
    try:
        data = read_json_file(_SETTINGS_FILE, default={})
        if not isinstance(data, dict):
            _LOG.warning(
                "Settings file %s did not contain a JSON object; resetting.",
                _SETTINGS_FILE,
            )
            _backup_corrupt_settings()
            _LIVE_SETTINGS = validate_settings({})
            return _LIVE_SETTINGS
        data = _migrate_settings(data)
        data = validate_settings(data)
        _LIVE_SETTINGS = data
        return _LIVE_SETTINGS
    except (OSError, json.JSONDecodeError) as exc:
        # Corrupt file: back it up so the user can recover, but don't keep
        # crashing on every launch.
        _LOG.warning(
            "Failed to load settings from %s (%s); backing up and starting fresh.",
            _SETTINGS_FILE,
            exc,
        )
        _backup_corrupt_settings()
        _LIVE_SETTINGS = validate_settings({})
        return _LIVE_SETTINGS


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
