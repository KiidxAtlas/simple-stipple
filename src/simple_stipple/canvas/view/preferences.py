"""User-configurable editor view and snap preferences.

These functions deliberately operate on the composed view instance so their
public APIs remain stable while the main widget keeps lifecycle and event
dispatch responsibilities.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer

from simple_stipple.canvas.constants import MIN_SCALE


def set_grid_visible(self, visible: bool) -> None:
    self._grid_visible = bool(visible)
    self._redraw()


def set_context_menu_sections(self, sections: list[str]) -> None:
    from simple_stipple.platform.settings import normalize_context_menu_sections

    normalized = normalize_context_menu_sections(sections)
    self._context_menu_sections = set(normalized)
    self._context_menu_section_order = normalized


def set_context_menu_overflow_sections(self, sections: list[str]) -> None:
    from simple_stipple.platform.settings import normalize_context_menu_overflow_sections

    self._context_menu_overflow_sections = set(normalize_context_menu_overflow_sections(sections))


def set_context_menu_profile(self, profile: str) -> None:
    self._context_menu_profile = str(profile)


def set_context_menu_profiles(self, profiles: dict) -> None:
    from simple_stipple.platform.settings import normalize_context_menu_profiles

    profile = normalize_context_menu_profiles(profiles).get(self._context_menu_profile, {})
    self.set_context_menu_sections(profile.get("sections", []))
    self.set_context_menu_overflow_sections(profile.get("overflow", []))
    self._context_menu_transform_items = list(profile.get("transform", []))
    self._context_menu_item_order = list(profile.get("items", []))
    self._context_menu_overflow_items = set(profile.get("overflow_items", []))
    self._context_menu_actions_configured = bool(profile.get("action_items_configured", []))


def _context_menu_section_enabled(self, section: str) -> bool:
    if self._context_menu_actions_configured or self._context_menu_item_order:
        return True
    return section in self._context_menu_sections


def set_grid_snap(self, enabled: bool) -> None:
    self._grid_snap = bool(enabled)
    self._refresh_draw_sidebar_state()
    self._redraw()


def set_grid_spacing(self, spacing: float) -> None:
    self._grid_spacing = max(0.001, float(spacing))
    self._redraw()


def _set_snap_flag(self, attr: str, enabled: bool, *, redraw: bool = False) -> None:
    setattr(self, attr, bool(enabled))
    self._refresh_draw_sidebar_state()
    if redraw:
        self._redraw()


def set_snap_master(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_master_enabled", enabled, redraw=True)


def set_snap_vertex(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_vertex_enabled", enabled)


def set_snap_midpoint(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_midpoint_enabled", enabled)


def set_snap_intersection(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_intersection_enabled", enabled)


def set_snap_edge(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_edge_enabled", enabled)


def set_snap_tangent(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_tangent_enabled", enabled)


def set_snap_extension(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_extension_enabled", enabled)


def set_snap_angle(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_angle_enabled", enabled)


def set_snap_parallel(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_parallel_enabled", enabled)


def set_snap_perpendicular(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_perpendicular_enabled", enabled)


def set_snap_equal_length(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_equal_length_enabled", enabled)


def set_snap_strength(self, strength: float) -> None:
    try:
        self._snap_strength = max(0.0, min(2.0, float(strength)))
    except (TypeError, ValueError):
        self._snap_strength = 1.0
    self._refresh_draw_sidebar_state()
    self._redraw()


def set_snap_axis_alignment(self, enabled: bool) -> None:
    self._snap_axis_alignment_enabled = bool(enabled)
    self._snap_align_x_enabled = bool(enabled)
    self._snap_align_y_enabled = bool(enabled)
    self._refresh_draw_sidebar_state()


def set_snap_align_x(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_align_x_enabled", enabled)


def set_snap_align_y(self, enabled: bool) -> None:
    _set_snap_flag(self, "_snap_align_y_enabled", enabled)


def set_construction_mode(self, enabled: bool) -> None:
    self._draw_construction_mode = bool(enabled)
    self._refresh_draw_sidebar_state()
    self._redraw()


def set_rotation_snap_increment(self, value: float) -> None:
    self._rotation_snap_increment = max(0.1, min(180.0, float(value)))


def set_aspect_ratio_locked(self, enabled: bool) -> None:
    self._aspect_ratio_locked = bool(enabled)


def set_property_highlight(self, key: str | None) -> None:
    self._property_highlight = str(key) if key else None
    self._redraw()


def get_zoom_percent(self) -> int:
    if self._fit_scale < MIN_SCALE:
        return 100
    return round(self._scale / self._fit_scale * 100)


def get_cursor_world_pos(self) -> tuple[float, float] | None:
    if self._cursor_wx is not None and self._cursor_wy is not None:
        return self._cursor_wx, self._cursor_wy
    return None


def _queue_cursor_position_update(self) -> None:
    if getattr(self, "_cursor_position_update_queued", False):
        return
    self._cursor_position_update_queued = True
    QTimer.singleShot(0, self._emit_cursor_position_update)


def _emit_cursor_position_update(self) -> None:
    self._cursor_position_update_queued = False
    if position := self.get_cursor_world_pos():
        self.cursorPositionChanged.emit(*position)


__all__ = [
    name for name in globals() if name.startswith(("set_", "get_", "_context", "_queue", "_emit"))
]
