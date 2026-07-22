"""The rebindable-keyboard-shortcut mechanism: canvas Commands must actually
pick up a user's saved override (previously the keymap was frozen at import
time from the hardcoded defaults and settings were silently ignored), and the
app-level/canvas-level duplicate mode toggles must share one settings slot so
they can't collide.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from src.core.settings import DEFAULT_KEYBINDINGS  # noqa: E402
from src.ui.canvas.interaction import commands as canvas_commands  # noqa: E402


def test_default_keymap_has_no_collisions():
    """Two distinct settings slots must never resolve to the same physical
    key combo — that's what silently shadows one command behind another
    depending on focus/dispatch order."""
    canvas_commands.apply_keybindings(None)
    seen: dict[tuple[int, int], str] = {}
    for cmd in canvas_commands.COMMANDS:
        for spec in (cmd.shortcut, *cmd.aliases):
            if not spec:
                continue
            combo = canvas_commands._combo(spec)
            owner = seen.get(combo)
            assert owner is None or owner == cmd.keybinding_id, (
                f"{cmd.keybinding_id!r} and {owner!r} both bind {spec!r}"
            )
            seen[combo] = cmd.keybinding_id


def test_default_keybindings_settings_key_ids_do_not_collide_with_commands():
    """Every DEFAULT_KEYBINDINGS default and every canvas Command default
    must resolve to a distinct physical key (per the same-owner rule above),
    checked from the settings.py side too."""
    canvas_commands.apply_keybindings(None)
    seen: dict[tuple[int, int], str] = {}
    for key, spec in DEFAULT_KEYBINDINGS.items():
        if not spec:
            continue
        combo = canvas_commands._combo(spec)
        assert combo not in seen, f"{key!r} collides with {seen[combo]!r} on {spec!r}"
        seen[combo] = key


def test_apply_keybindings_overrides_a_command_shortcut(qapp):
    from tests.test_canvas_behavior import key, make_view

    canvas_commands.apply_keybindings(None)
    v = make_view(qapp, [])
    key(v, Qt.Key.Key_G)
    assert v._grid_visible  # default "G" toggles grid

    canvas_commands.apply_keybindings({"grid.toggle": "Ctrl+Shift+G"})
    try:
        v2 = make_view(qapp, [])
        key(v2, Qt.Key.Key_G)
        assert not v2._grid_visible  # "G" alone no longer bound

        key(
            v2,
            Qt.Key.Key_G,
            mods=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        assert v2._grid_visible  # the new binding works
    finally:
        canvas_commands.apply_keybindings(None)


def test_apply_keybindings_can_unbind_a_command(qapp):
    from tests.test_canvas_behavior import key, make_view

    canvas_commands.apply_keybindings({"grid.toggle": ""})
    try:
        v = make_view(qapp, [])
        key(v, Qt.Key.Key_G)
        assert not v._grid_visible
    finally:
        canvas_commands.apply_keybindings(None)


def test_mode_draw_shares_settings_slot_with_canvas_draw_mode():
    """mode.draw (canvas Command) and canvas.draw_mode (app-level QAction)
    are the same user-facing concept and must move together when either
    settings key is rebound."""
    cmd = canvas_commands.get("mode.draw")
    assert cmd.keybinding_id == "canvas.draw_mode"

    canvas_commands.apply_keybindings({"canvas.draw_mode": "Ctrl+Shift+D"})
    try:
        assert canvas_commands.effective_shortcut("mode.draw") == "Ctrl+Shift+D"
    finally:
        canvas_commands.apply_keybindings(None)


@pytest.mark.parametrize(
    "cmd_id,settings_key",
    [
        ("mode.draw", "canvas.draw_mode"),
        ("mode.edit", "canvas.edit_mode"),
        ("measure.toggle", "canvas.measure"),
        ("mode.dimension", "canvas.dimension"),
        ("view.fit", "canvas.fit"),
    ],
)
def test_unified_mode_commands_point_at_their_app_level_settings_key(cmd_id, settings_key):
    assert canvas_commands.get(cmd_id).keybinding_id == settings_key


def test_keybindings_dialog_has_one_row_per_settings_slot(qapp):
    from src.ui.widgets.dialogs.keybindings_dialog import _KEYBINDING_FIELDS

    keys = [key for key, _label, _group, _default in _KEYBINDING_FIELDS]
    assert len(keys) == len(set(keys))
    # The unified mode ids appear once (as their app-level settings_key),
    # not once per canvas Command id too.
    assert "canvas.draw_mode" in keys
    assert "mode.draw" not in keys


def test_keybindings_import_export_roundtrip(qapp, tmp_path):
    from src.ui.widgets.dialogs.keybindings_dialog import KeybindingsDialog

    dialog = KeybindingsDialog(keybindings={})
    dialog._entries["workspace.open"].setText("Ctrl+Alt+O")
    path = tmp_path / "shortcuts.json"
    dialog.export_to_path(path)
    dialog._entries["workspace.open"].setText("changed")
    dialog.import_from_path(path)

    assert dialog._entries["workspace.open"].text() == "Ctrl+Alt+O"


def test_essential_shortcuts_cannot_all_be_removed(qapp):
    from src.ui.widgets.dialogs.keybindings_dialog import KeybindingsDialog

    dialog = KeybindingsDialog(keybindings={})
    bindings = dialog._current_bindings()
    bindings["workspace.open"] = ""
    bindings["app.settings"] = ""

    assert KeybindingsDialog._missing_essential_bindings(bindings) == [
        "Open Workspace",
        "Open Settings",
    ]


def test_new_draw_primitive_commands_are_registered_and_unbound_by_default():
    for tool in (
        "polyline",
        "line",
        "rectangle",
        "circle",
        "ellipse",
        "arc",
        "spline",
        "polygon",
    ):
        cmd = canvas_commands.get(f"canvas.{tool}")
        assert cmd.shortcut == ""


def test_pan_owns_p_and_bezier_pen_is_unbound_by_default():
    assert canvas_commands.get("mode.pan").shortcut == "P"
    assert canvas_commands.get("mode.pen").shortcut == ""

    from src.ui.widgets.dialogs.keybindings_dialog import _KEYBINDING_FIELDS

    fields = {key: (label, default) for key, label, _group, default in _KEYBINDING_FIELDS}
    assert fields["mode.pan"] == ("Pan Tool", "P")
    assert fields["mode.pen"] == ("Bezier Pen Tool", "")


def test_simplify_and_chamfer_commands_are_registered():
    assert canvas_commands.get("path.simplify") is not None
    assert canvas_commands.get("vertex.chamfer") is not None
