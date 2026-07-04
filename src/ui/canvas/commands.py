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
from dataclasses import dataclass, field
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


def _add_text_at_cursor(v: Any) -> None:
    wx, wy = v._cursor_wx, v._cursor_wy
    if wx is None or wy is None:
        wx, wy = v._c2w(v.width() / 2.0, v.height() / 2.0)
    v.prompt_add_text(wx, wy)


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
    Command(
        "clipboard.cut", "Cut", lambda v: v._cut_selected(), "Ctrl+X", category="Edit"
    ),
    Command(
        "clipboard.copy", "Copy", lambda v: v._copy_selected(), "Ctrl+C", category="Edit"
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
        "edit.delete",
        "Delete Selected",
        lambda v: v._key_delete(),
        "Del",
        category="Edit",
    ),
    # ── Selection ───────────────────────────────────────────────────────────
    Command(
        "select.all", "Select All", lambda v: v.select_all(), "Ctrl+A", category="Selection"
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
        "text.add",
        "Add Text…",
        _add_text_at_cursor,
        "T",
        category="Path",
        when=lambda v: v.get_mode() == "select",
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
    # ── Modes ───────────────────────────────────────────────────────────────
    Command(
        "mode.draw",
        "Draw Mode",
        lambda v: v.set_mode("draw" if v.get_mode() != "draw" else "select"),
        "D",
        category="Modes",
    ),
    Command(
        "mode.edit",
        "Edit Mode",
        lambda v: v.set_mode("edit" if v.get_mode() != "edit" else "select"),
        "E",
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
        "measure.toggle",
        "Measure",
        lambda v: v.toggle_measure(),
        "M",
        category="Modes",
        requires_selectable=False,
    ),
    # ── View ────────────────────────────────────────────────────────────────
    Command(
        "view.fit",
        "Fit View",
        lambda v: v.fit(),
        "F",
        category="View",
        requires_selectable=False,
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
        "S",
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
    kc = seq[0]
    return int(kc.key()), int(kc.keyboardModifiers().value)


_KEYMAP: dict[tuple[int, int], Command] = {}
for _cmd in COMMANDS:
    for _spec in (_cmd.shortcut, *_cmd.aliases):
        if _spec:
            _KEYMAP[_combo(_spec)] = _cmd


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


def can_run(view: Any, cmd: Command) -> bool:
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
    """Platform-native display string for a command's shortcut (⌘D on mac)."""
    cmd = _BY_ID[cmd_id]
    if not cmd.shortcut:
        return ""
    return QKeySequence(cmd.shortcut).toString(QKeySequence.SequenceFormat.NativeText)


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
            if c.category != cat or c.hidden or not c.shortcut:
                continue
            keys = native_shortcut(c.id)
            alias = ", ".join(
                QKeySequence(a).toString(QKeySequence.SequenceFormat.NativeText)
                for a in c.aliases
            )
            rows.append((c.label, f"{keys}{f' / {alias}' if alias else ''}"))
        rows.append(("", ""))
    return rows
