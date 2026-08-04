"""Settings and path access for the UI layer.

``ui`` code should import from here, not from ``simple_stipple.platform.settings``/
``simple_stipple.platform.paths`` directly (see plan.md Section 9.5 / Phase 3.5) — this is
a thin re-export, not new logic, so ``core`` stays the single source of
truth for what these values mean while ``ui`` doesn't reach past ``app``
down to ``core``.
"""

from __future__ import annotations

from simple_stipple.platform.paths import custom_tiles_dir, user_data_dir
from simple_stipple.platform.settings import (
    CONTEXT_MENU_SECTION_LABELS,
    CONTEXT_MENU_TRANSFORM_ITEMS,
    DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS,
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_DRAW_SIDEBAR_WIDTH,
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
    DRAW_SIDEBAR_PATH_TOOL_LABELS,
    DRAW_SIDEBAR_SECTION_LABELS,
    DRAW_SIDEBAR_SHAPE_TOOL_LABELS,
    MAX_DRAW_SIDEBAR_HEIGHT,
    MAX_DRAW_SIDEBAR_WIDTH,
    MIN_DRAW_SIDEBAR_HEIGHT,
    MIN_DRAW_SIDEBAR_WIDTH,
    RADIAL_MENU_SHORT_LABELS,
    SMOOTHING_METHODS,
    SettingsSchema,
    normalize_context_menu_overflow_sections,
    normalize_context_menu_profiles,
    normalize_context_menu_sections,
    normalize_draw_sidebar_shape_tools,
    save_settings,
)

__all__ = [
    "CONTEXT_MENU_SECTION_LABELS",
    "CONTEXT_MENU_TRANSFORM_ITEMS",
    "DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS",
    "DEFAULT_CONTEXT_MENU_SECTIONS",
    "DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE",
    "DEFAULT_DRAW_SIDEBAR_PATH_TOOLS",
    "DEFAULT_DRAW_SIDEBAR_SECTIONS",
    "DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS",
    "DEFAULT_DRAW_SIDEBAR_WIDTH",
    "DEFAULT_KEYBINDINGS",
    "DEFAULT_RADIAL_MENU_TOOLS",
    "DEFAULT_SIMPLIFY_TOLERANCE",
    "DEFAULT_SMOOTHING_METHOD",
    "DEFAULT_SMOOTH_ITERATIONS",
    "DRAW_SIDEBAR_PATH_TOOL_LABELS",
    "DRAW_SIDEBAR_SECTION_LABELS",
    "DRAW_SIDEBAR_SHAPE_TOOL_LABELS",
    "MAX_DRAW_SIDEBAR_HEIGHT",
    "MAX_DRAW_SIDEBAR_WIDTH",
    "MIN_DRAW_SIDEBAR_HEIGHT",
    "MIN_DRAW_SIDEBAR_WIDTH",
    "RADIAL_MENU_SHORT_LABELS",
    "SMOOTHING_METHODS",
    "SettingsSchema",
    "custom_tiles_dir",
    "normalize_context_menu_overflow_sections",
    "normalize_context_menu_profiles",
    "normalize_context_menu_sections",
    "normalize_draw_sidebar_shape_tools",
    "save_settings",
    "user_data_dir",
]
