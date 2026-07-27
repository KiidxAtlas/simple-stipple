"""Declarative canvas command registry.

Single source of truth for canvas actions: id, label, shortcut, and
enablement. The canvas keymap (``PolylineView.keyPressEvent``), the app's
Edit/View menus, the canvas context menu, and the keyboard-shortcuts
reference dialog all read this table, so a label or shortcut changes in
exactly one place and the surfaces can never drift apart.

Command ``run`` callables receive the canvas view. Stateful, gesture-like
key handling (space pan, arrow nudges, Escape cascade, the dimension-HUD
editing keys) stays in the view — this table is for discrete actions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    run: Callable[[Any], Any]
    shortcut: str = ""
    aliases: tuple[str, ...] = ()
    category: str = ""
    # False for view-level commands that also work on preview-only canvases.
    requires_selectable: bool = True
    when: Callable[[Any], bool] | None = None
    # Hide from the generated shortcuts reference (e.g. near-duplicates).
    hidden: bool = False
    # Some commands are reachable both as a canvas Command (this table) and
    # as a window-level QAction in app.py (e.g. draw/edit mode, measure, fit)
    # — the two must share one settings slot so a rebind applies to both and
    # they can never silently collide. Empty means "use id".
    settings_key: str = ""

    @property
    def keybinding_id(self) -> str:
        return self.settings_key or self.id


def _sel_or_edit(v: Any) -> bool:
    return v.get_mode() in ("select", "edit")


def _close_paths(v: Any) -> None:
    n = v.close_selected_polylines()
    v._show_flash(f"Closed {n} polyline(s)" if n else "No open polyline selected", 900)


def _open_paths(v: Any) -> None:
    n = v.open_selected_polylines()
    v._show_flash(f"Opened {n} polyline(s)" if n else "No closed polyline selected", 900)


def _toggle_construction(v: Any) -> None:
    if v._sel and _sel_or_edit(v):
        v._toggle_selected_construction()
        v._show_flash("Toggled construction for selection", 900)
        return
    v._draw_construction_mode = not v._draw_construction_mode
    if v.get_mode() != "draw":
        v.set_mode("draw")
    else:
        v._redraw()
    v._show_flash(
        "Construction draw: ON" if v._draw_construction_mode else "Construction draw: OFF",
        900,
    )
    v._refresh_draw_sidebar_state()


def _toggle_grid(v: Any) -> None:
    v.set_grid_visible(not v._grid_visible)


def _toggle_grid_snap(v: Any) -> None:
    v.set_grid_snap(not v._grid_snap)
    v._show_flash("Snap: ON" if v._grid_snap else "Snap: OFF")


def _grid_coarser(v: Any) -> None:
    v.set_grid_spacing(min(100.0, v._grid_spacing * 2.0))
    v._show_flash(f"Grid: {v._grid_spacing:g} mm")


def _grid_finer(v: Any) -> None:
    v.set_grid_spacing(max(0.1, v._grid_spacing / 2.0))
    v._show_flash(f"Grid: {v._grid_spacing:g} mm")


def _toggle_trim(v: Any) -> None:
    if v.get_mode() != "trim":
        v.set_mode("trim")
        v._show_flash("Trim: click the part of a shape to remove · Esc exits", 2000)
    else:
        v.set_mode("select")


def _toggle_extend(v: Any) -> None:
    if v.get_mode() != "extend":
        v.set_mode("extend")
        v._show_flash(
            "Extend: click an open shape to lengthen it to the next one · Esc exits",
            2000,
        )
    else:
        v.set_mode("select")


def _round_corner(v: Any) -> None:
    hv = v._hover_vert
    if hv is None:
        v._show_flash("Hover a corner first, then press R to round it", 1400)
        return
    pi, vi = hv
    v._show_hud_prompt(
        "Round radius (mm)",
        1.0,
        lambda r: v._round_vertex(pi, vi, r),
        minimum=0.01,
    )


def _chamfer_corner(v: Any) -> None:
    hv = v._hover_vert
    if hv is None:
        v._show_flash("Hover a corner first, then chamfer it", 1400)
        return
    pi, vi = hv
    v._show_hud_prompt(
        "Chamfer distance (mm)",
        1.0,
        lambda d: v._chamfer_vertex(pi, vi, d),
        minimum=0.01,
    )


def _simplify_selected(v: Any) -> None:
    if not v._sel:
        v._show_flash("Select a polyline to simplify", 900)
        return

    def _apply(tolerance: float) -> None:
        v._on_simplify_tolerance_changed(tolerance)
        n = v.simplify_selected(tolerance)
        v._show_flash(f"Simplified {n} shape(s)" if n else "No simplification possible", 900)

    v._show_hud_prompt("Simplify tolerance (mm)", v._simplify_tolerance, _apply, minimum=0.001)


def _smooth_selected(v: Any) -> None:
    if not v._sel:
        v._show_flash("Select a polyline to smooth", 900)
        return

    def _apply(iterations: float) -> None:
        v._on_smooth_iterations_changed(int(iterations))
        n = v.smooth_selected(int(iterations))
        v._show_flash(f"Smoothed {n} shape(s)" if n else "No change", 900)

    v._show_hud_prompt("Smooth iterations", float(v._smooth_iterations), _apply, minimum=1.0)


def _fit_selected_to_curve(v: Any) -> None:
    if not v._sel:
        v._show_flash("Select a polyline to fit to a curve", 900)
        return

    def _apply(tolerance: float) -> None:
        n = v.fit_selected_to_curve(tolerance)
        v._show_flash(f"Fit {n} shape(s) to curve" if n else "Could not fit a curve", 900)

    v._show_hud_prompt("Fit-to-curve tolerance (mm)", 0.3, _apply, minimum=0.001)


def _select_draw_primitive(tool: str) -> Callable[[Any], None]:
    def _run(v: Any) -> None:
        if v.get_mode() != "draw":
            v.set_mode("draw")
        v._set_draw_primitive(tool)

    return _run


def _toggle_pen(v: Any) -> None:
    """Bezier pen is a draw-mode primitive, not its own mode — toggle it
    like the other single-key mode commands (press again to return to
    select) while still going through the same draw-primitive path as
    every other draw tool."""
    if v.get_mode() == "draw" and v._draw_primitive == "bezier":
        v.set_mode("select")
    else:
        _select_draw_primitive("bezier")(v)


def _add_text_at_cursor(v: Any) -> None:
    wx, wy = v._cursor_wx, v._cursor_wy
    if wx is None or wy is None:
        wx, wy = v._c2w(v.width() / 2.0, v.height() / 2.0)
    v.prompt_add_text(wx, wy)


def _selected_objects(v: Any) -> list[str]:
    """One representative ID per distinct selected object — grouped
    entities (e.g. a multi-contour text) collapse to a single entry."""
    groups_seen: set[str] = set()
    objects: list[str] = []
    for eid in sorted(v._sel):
        entity = v._entity_for_id(eid)
        gid = entity.group if entity is not None else None
        if gid is not None:
            if gid in groups_seen:
                continue
            groups_seen.add(gid)
        objects.append(eid)
    return objects


def _attach_text_to_path(v: Any) -> None:
    objects = _selected_objects(v)
    if len(objects) != 2:
        v._show_flash("Select exactly one text object and one path", 1200)
        return
    text_candidates = [i for i in objects if v.text_params_at(i) is not None]
    if len(text_candidates) != 1:
        v._show_flash("Select exactly one text object and one path", 1200)
        return
    text_idx = text_candidates[0]
    path_idx = next(i for i in objects if i != text_idx)
    if not v.attach_text_to_path(text_idx, path_idx):
        v._show_flash("Could not attach text to that path", 1200)


COMMANDS: tuple[Command, ...] = (
    # ── Edit ────────────────────────────────────────────────────────────────
    Command("edit.undo", "Undo", lambda v: v.undo(), "Ctrl+Z", category="Edit"),
    Command(
        "edit.redo",
        "Redo",
        lambda v: v.redo(),
        "Ctrl+Shift+Z",
        aliases=("Ctrl+Y",),
        category="Edit",
    ),
    Command("clipboard.cut", "Cut", lambda v: v._cut_selected(), "Ctrl+X", category="Edit"),
    Command(
        "clipboard.copy",
        "Copy",
        lambda v: v._copy_selected(),
        "Ctrl+C",
        category="Edit",
    ),
    Command(
        "clipboard.paste",
        "Paste",
        lambda v: v._paste_clipboard(),
        "Ctrl+V",
        category="Edit",
    ),
    Command(
        "edit.duplicate",
        "Duplicate",
        lambda v: v._duplicate_selected(),
        "Ctrl+D",
        category="Edit",
    ),
    Command(
        "edit.duplicate_offset",
        "Duplicate with Offset",
        lambda v: v._duplicate_selected_with_offset(),
        "Ctrl+Shift+D",
        category="Edit",
    ),
    Command(
        "edit.array_grid",
        "Array — Grid…",
        lambda v: v._array_duplicate_grid(),
        category="Edit",
        when=lambda v: bool(v._sel),
    ),
    Command(
        "edit.array_radial",
        "Array — Radial…",
        lambda v: v._array_duplicate_radial(),
        category="Edit",
        when=lambda v: bool(v._sel),
    ),
    Command(
        "edit.delete",
        "Delete Selected",
        lambda v: v._key_delete(),
        "Del",
        category="Edit",
    ),
    # ── Selection ───────────────────────────────────────────────────────────
    Command(
        "select.all",
        "Select All",
        lambda v: v.select_all(),
        "Ctrl+A",
        category="Selection",
    ),
    Command(
        "select.none",
        "Deselect All",
        lambda v: v.deselect_all(),
        "Ctrl+Shift+A",
        category="Selection",
    ),
    Command(
        "select.invert",
        "Invert Selection",
        lambda v: v._invert_selection(),
        "Ctrl+I",
        category="Selection",
    ),
    Command(
        "select.lasso",
        "Lasso Selection",
        lambda v: v.arm_lasso_selection(),
        category="Selection",
    ),
    Command(
        "group.create",
        "Group",
        lambda v: v._group_selected(),
        "Ctrl+G",
        category="Selection",
    ),
    Command(
        "group.dissolve",
        "Ungroup",
        lambda v: v._ungroup_selected(),
        "Ctrl+Shift+G",
        category="Selection",
    ),
    # ── Constraints ─────────────────────────────────────────────────────────
    Command(
        "constraint.horizontal",
        "Horizontal",
        lambda v: v.add_geometric_constraint("horizontal"),
        category="Constraints",
    ),
    Command(
        "constraint.vertical",
        "Vertical",
        lambda v: v.add_geometric_constraint("vertical"),
        category="Constraints",
    ),
    Command(
        "constraint.parallel",
        "Parallel",
        lambda v: v.add_geometric_constraint("parallel"),
        category="Constraints",
    ),
    Command(
        "constraint.perpendicular",
        "Perpendicular",
        lambda v: v.add_geometric_constraint("perpendicular"),
        category="Constraints",
    ),
    Command(
        "constraint.equal_length",
        "Equal Length",
        lambda v: v.add_geometric_constraint("equal_length"),
        category="Constraints",
    ),
    Command(
        "constraint.coincident",
        "Coincident",
        lambda v: v.add_geometric_constraint("coincident"),
        category="Constraints",
    ),
    Command(
        "constraint.fixed",
        "Fixed",
        lambda v: v.add_geometric_constraint("fixed"),
        category="Constraints",
    ),
    Command(
        "constraint.remove",
        "Remove Constraints",
        lambda v: v.remove_constraints_for_selection(),
        category="Constraints",
    ),
    # ── Path ────────────────────────────────────────────────────────────────
    Command(
        "path.close",
        "Close Polyline(s)",
        _close_paths,
        "Shift+C",
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "path.open",
        "Open Polyline(s)",
        _open_paths,
        "Shift+O",
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "path.offset",
        "Offset Selection…",
        lambda v: v._prompt_offset_selected(),
        "O",
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "construction.toggle",
        "Toggle Construction",
        _toggle_construction,
        "X",
        category="Path",
    ),
    Command(
        "vertex.round",
        "Round Corner…",
        _round_corner,
        "R",
        category="Path",
        when=lambda v: v.get_mode() == "edit",
    ),
    Command(
        "vertex.chamfer",
        "Chamfer Corner…",
        _chamfer_corner,
        category="Path",
        when=lambda v: v.get_mode() == "edit",
    ),
    Command(
        "path.simplify",
        "Simplify Path…",
        _simplify_selected,
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "path.smooth",
        "Smooth…",
        _smooth_selected,
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "path.fit_curve",
        "Fit to Curve…",
        _fit_selected_to_curve,
        category="Path",
        when=_sel_or_edit,
    ),
    Command(
        "text.add",
        "Add Text…",
        _add_text_at_cursor,
        "T",
        category="Path",
        when=lambda v: v.get_mode() == "select",
    ),
    Command(
        "text.attach_to_path",
        "Attach Text to Path",
        _attach_text_to_path,
        category="Path",
        when=lambda v: len(v._sel) >= 2,
    ),
    # ── Boolean ─────────────────────────────────────────────────────────────
    Command(
        "boolean.union",
        "Union (Weld)",
        lambda v: v.boolean_selected("union"),
        "Ctrl+U",
        category="Boolean",
        when=_sel_or_edit,
    ),
    Command(
        "boolean.subtract",
        "Subtract",
        lambda v: v.boolean_selected("subtract"),
        "Ctrl+Shift+U",
        category="Boolean",
        when=_sel_or_edit,
    ),
    Command(
        "boolean.intersect",
        "Intersect",
        lambda v: v.boolean_selected("intersect"),
        category="Boolean",
        when=_sel_or_edit,
    ),
    Command(
        "boolean.divide",
        "Divide",
        lambda v: v.boolean_selected("divide"),
        category="Boolean",
        when=_sel_or_edit,
    ),
    # ── Modes ─────────────────────────────────────────────────────────────────
    # mode.draw / mode.edit / measure.toggle / mode.dimension / view.fit share
    # a settings slot with the equivalent app-level QAction in app.py (which
    # fires when the canvas doesn't have keyboard focus) — settings_key keeps
    # a rebind of either one in sync instead of the two silently colliding.
    Command(
        "mode.draw",
        "Draw Mode",
        lambda v: v.set_mode("draw" if v.get_mode() != "draw" else "select"),
        "D",
        category="Modes",
        settings_key="canvas.draw_mode",
    ),
    Command(
        "mode.edit",
        "Edit Mode",
        lambda v: v.set_mode("edit" if v.get_mode() != "edit" else "select"),
        "E",
        category="Modes",
        settings_key="canvas.edit_mode",
    ),
    Command(
        "mode.pan",
        "Pan Tool",
        lambda v: v.set_mode("pan" if v.get_mode() != "pan" else "select"),
        "P",
        category="Modes",
    ),
    Command(
        "mode.pen",
        "Bezier Pen Tool",
        _toggle_pen,
        category="Modes",
    ),
    Command(
        "mode.trim",
        "Trim Tool",
        _toggle_trim,
        "K",
        category="Modes",
    ),
    Command(
        "mode.extend",
        "Extend Tool",
        _toggle_extend,
        "L",
        category="Modes",
    ),
    Command(
        "mode.knife",
        "Knife Tool",
        lambda v: v.set_mode("knife"),
        category="Modes",
    ),
    Command(
        "measure.toggle",
        "Measure",
        lambda v: v.toggle_measure(),
        "M",
        category="Modes",
        requires_selectable=False,
        settings_key="canvas.measure",
    ),
    Command(
        "mode.dimension",
        "Dimension",
        lambda v: v.toggle_dimension_mode(),
        "Shift+M",
        category="Modes",
        requires_selectable=False,
        settings_key="canvas.dimension",
    ),
    Command(
        "canvas.radial_menu",
        "Radial Quick Menu",
        lambda v: v._toggle_radial_menu(),
        "Q",
        category="Modes",
        # Opens from any mode/tool — see DxfCanvas.mousePressEvent/
        # mouseMoveEvent/paintEvent, which handle it ahead of tool dispatch.
        when=lambda v: hasattr(v, "_toggle_radial_menu"),
    ),
    Command(
        "canvas.polyline",
        "Polyline Tool",
        _select_draw_primitive("polyline"),
        category="Modes",
    ),
    Command(
        "canvas.line",
        "Line Tool",
        _select_draw_primitive("line"),
        category="Modes",
    ),
    Command(
        "canvas.rectangle",
        "Rectangle Tool",
        _select_draw_primitive("rectangle"),
        category="Modes",
    ),
    Command(
        "canvas.circle",
        "Circle Tool",
        _select_draw_primitive("circle"),
        category="Modes",
    ),
    Command(
        "canvas.ellipse",
        "Ellipse Tool",
        _select_draw_primitive("ellipse"),
        category="Modes",
    ),
    Command(
        "canvas.arc",
        "Arc Tool",
        _select_draw_primitive("arc"),
        category="Modes",
    ),
    Command(
        "canvas.spline",
        "Spline Tool",
        _select_draw_primitive("spline"),
        category="Modes",
    ),
    Command(
        "canvas.polygon",
        "Polygon Tool",
        _select_draw_primitive("polygon"),
        category="Modes",
    ),
    # ── View ────────────────────────────────────────────────────────────────
    Command(
        "view.fit",
        "Fit View",
        lambda v: v.fit(),
        "F",
        category="View",
        requires_selectable=False,
        settings_key="canvas.fit",
    ),
    Command(
        "view.zoom_in",
        "Zoom In",
        lambda v: v._zoom_by(1.15),
        "+",
        aliases=("=",),
        category="View",
        requires_selectable=False,
    ),
    Command(
        "view.zoom_out",
        "Zoom Out",
        lambda v: v._zoom_by(1 / 1.15),
        "-",
        category="View",
        requires_selectable=False,
    ),
    Command(
        "view.rulers",
        "Show Rulers",
        lambda v: v.set_rulers_visible(not v._rulers_visible),
        "Ctrl+R",
        category="View",
        requires_selectable=False,
    ),
    Command(
        "grid.toggle",
        "Show Grid",
        _toggle_grid,
        "G",
        category="View",
        requires_selectable=False,
    ),
    Command(
        "grid.snap",
        "Snap to Grid",
        _toggle_grid_snap,
        "Shift+G",
        category="View",
        requires_selectable=False,
    ),
    Command(
        "grid.coarser",
        "Grid Spacing ×2",
        _grid_coarser,
        "]",
        category="View",
        requires_selectable=False,
    ),
    Command(
        "grid.finer",
        "Grid Spacing ÷2",
        _grid_finer,
        "[",
        category="View",
        requires_selectable=False,
    ),
)

_BY_ID: dict[str, Command] = {c.id: c for c in COMMANDS}


def get(cmd_id: str) -> Command:
    return _BY_ID[cmd_id]


def _combo(spec: str) -> tuple[int, int]:
    """Parse a shortcut spec into a (key, modifiers) pair for keymap lookup."""
    seq = QKeySequence(spec)
    assert seq.count() == 1, spec
    kc = seq[0]  # type: ignore[index]  # QKeySequence.__getitem__ is real at runtime; missing from stubs
    return int(kc.key()), int(kc.keyboardModifiers().value)


# User-configured overrides (id -> shortcut spec, "" meaning "unbound"),
# keyed by Command.keybinding_id. Populated by apply_keybindings(); empty
# until the app calls it, so importing this module keeps the hardcoded
# Command.shortcut defaults.
_OVERRIDES: dict[str, str] = {}


def _effective_shortcuts(cmd: Command) -> tuple[str, ...]:
    if cmd.keybinding_id in _OVERRIDES:
        override = _OVERRIDES[cmd.keybinding_id]
        return (override,) if override else ()
    return tuple(s for s in (cmd.shortcut, *cmd.aliases) if s)


def effective_shortcut(cmd_id: str) -> str:
    """The live shortcut for a command — the user's override if set, else
    its hardcoded default. Empty string means unbound."""
    cmd = _BY_ID[cmd_id]
    specs = _effective_shortcuts(cmd)
    return specs[0] if specs else ""


_KEYMAP: dict[tuple[int, int], Command] = {}


def _rebuild_keymap() -> None:
    _KEYMAP.clear()
    for cmd in COMMANDS:
        for spec in _effective_shortcuts(cmd):
            _KEYMAP[_combo(spec)] = cmd


_rebuild_keymap()


def apply_keybindings(keybindings: dict[str, str] | None) -> None:
    """Re-resolve every command's live shortcut from user settings (falling
    back to its hardcoded default when absent), and rebuild the keymap.

    Call at startup and whenever the Settings dialog is applied.
    """
    _OVERRIDES.clear()
    if keybindings:
        for key, value in keybindings.items():
            if isinstance(value, str):
                _OVERRIDES[key] = value.strip()
    _rebuild_keymap()


def match_key(key: int, mods: Qt.KeyboardModifier) -> Command | None:
    """Resolve a key event to a command (exact modifier match; the keypad
    modifier is ignored so numpad +/- behave like the top row)."""
    m = int(mods.value) & ~int(Qt.KeyboardModifier.KeypadModifier.value)
    cmd = _KEYMAP.get((int(key), m))
    if cmd is not None:
        return cmd
    # Symbol keys often arrive with Shift held (e.g. "+" on many layouts).
    if not (Qt.Key.Key_A <= key <= Qt.Key.Key_Z):
        return _KEYMAP.get((int(key), m & ~int(Qt.KeyboardModifier.ShiftModifier.value)))
    return None


def can_run(view: Any, cmd_id_or_cmd: str | Command) -> bool:
    cmd = _BY_ID[cmd_id_or_cmd] if isinstance(cmd_id_or_cmd, str) else cmd_id_or_cmd
    if cmd.requires_selectable and not getattr(view, "_selectable", False):
        return False
    if cmd.when is not None and not cmd.when(view):
        return False
    return True


def run(view: Any, cmd_id_or_cmd: str | Command) -> bool:
    cmd = _BY_ID[cmd_id_or_cmd] if isinstance(cmd_id_or_cmd, str) else cmd_id_or_cmd
    if not can_run(view, cmd):
        return False
    cmd.run(view)
    return True


def native_shortcut(cmd_id: str) -> str:
    """Platform-native display string for a command's live shortcut (⌘D on mac)."""
    sc = effective_shortcut(cmd_id)
    if not sc:
        return ""
    return QKeySequence(sc).toString(QKeySequence.SequenceFormat.NativeText)


def menu_text(cmd_id: str, label: str | None = None) -> str:
    """Context-menu text: label plus bracketed native shortcut."""
    cmd = _BY_ID[cmd_id]
    text = label or cmd.label
    sc = native_shortcut(cmd_id)
    return f"{text}  [{sc}]" if sc else text


def shortcut_reference_rows() -> list[tuple[str, str]]:
    """(label, keys) rows for the shortcuts dialog, grouped by category."""
    rows: list[tuple[str, str]] = []
    seen: list[str] = []
    for c in COMMANDS:
        if c.category and c.category not in seen:
            seen.append(c.category)
    for cat in seen:
        rows.append((cat, ""))
        for c in COMMANDS:
            if c.category != cat or c.hidden:
                continue
            keys = native_shortcut(c.id)
            if not keys:
                continue
            # Aliases are only meaningful for the hardcoded default; a user
            # override replaces them (see _effective_shortcuts).
            has_override = c.keybinding_id in _OVERRIDES
            alias = (
                ""
                if has_override
                else ", ".join(
                    QKeySequence(a).toString(QKeySequence.SequenceFormat.NativeText)
                    for a in c.aliases
                )
            )
            rows.append((c.label, f"{keys}{f' / {alias}' if alias else ''}"))
        rows.append(("", ""))
    return rows
