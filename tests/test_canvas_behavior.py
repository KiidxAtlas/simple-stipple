"""Behavioral safety net for the canvas stack.

These tests pin down user-observable canvas behavior (selection, editing,
undo, clipboard, transforms, drawing) through public APIs and synthesized
mouse/keyboard events, so the architecture refactors can be verified against
the same behavior before and after.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

# ── helpers ──────────────────────────────────────────────────────────────────


def make_view(qapp, polys=None, size=(800, 600)):
    from src.ui.canvas.view import PolylineView

    v = PolylineView()
    v.resize(*size)
    if polys is not None:
        v.load(polys)
        v.fit()
    return v


def make_canvas(qapp, polys=None, size=(800, 600)):
    from src.ui.canvas.dxf_canvas import DxfCanvas

    c = DxfCanvas()
    c.resize(*size)
    # Rulers eat clicks along the top/left edges (guide creation); most
    # tests use world coordinates that can land there, so keep them off
    # except in the dedicated ruler/guide tests.
    c.set_rulers_visible(False)
    if polys is not None:
        c.load(polys)
        c.fit()
    return c


NO_MOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
LMB = Qt.MouseButton.LeftButton


def _mouse_event(etype, cx, cy, button=LMB, mods=NO_MOD):
    buttons = (
        Qt.MouseButton.NoButton if etype == QEvent.Type.MouseButtonRelease else button
    )
    return QMouseEvent(etype, QPointF(cx, cy), QPointF(cx, cy), button, buttons, mods)


def press(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, cx, cy, button, mods)
    )


def move(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, cx, cy, button, mods))


def release(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, cx, cy, button, mods)
    )


def click(view, cx, cy, button=LMB, mods=NO_MOD):
    press(view, cx, cy, button, mods)
    release(view, cx, cy, button, mods)


def click_world(view, wx, wy, mods=NO_MOD):
    cx, cy = view._w2c(wx, wy)
    click(view, cx, cy, mods=mods)


def drag_world(view, wx0, wy0, wx1, wy1, mods=NO_MOD, steps=4):
    cx0, cy0 = view._w2c(wx0, wy0)
    cx1, cy1 = view._w2c(wx1, wy1)
    press(view, cx0, cy0, mods=mods)
    for i in range(1, steps + 1):
        t = i / steps
        move(view, cx0 + (cx1 - cx0) * t, cy0 + (cy1 - cy0) * t, mods=mods)
    release(view, cx1, cy1, mods=mods)


def key(view, k, mods=NO_MOD, text=""):
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    view.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, k, mods, text))


def square(x, y, s=10.0):
    return [(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y)]


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


THREE_SQUARES = [square(0, 0), square(30, 0), square(60, 0)]


# ── load / persistence round trips ───────────────────────────────────────────


def test_load_round_trip(qapp):
    v = make_view(qapp, THREE_SQUARES)
    assert v.poly_count == 3
    state = v.get_polylines_state()
    assert [len(p) for p in state] == [5, 5, 5]
    assert state[0][0] == (0.0, 0.0)


def test_entity_records_round_trip(qapp):
    v = make_view(qapp, THREE_SQUARES)
    ents = v._entities
    ents[0].hidden = True
    ents[1].locked = True
    ents[2].construction = True
    ents[1].group = 7
    ents[2].group = 7
    v._group_labels[7] = "pair"
    records = v.get_entity_records()

    v2 = make_view(qapp)
    v2.set_entity_records(records)
    e2 = v2._entities
    assert [e.hidden for e in e2] == [True, False, False]
    assert [e.locked for e in e2] == [False, True, False]
    assert [e.construction for e in e2] == [False, False, True]
    assert [e.group for e in e2] == [None, 7, 7]
    assert v2.get_polylines_state() == v.get_polylines_state()


def test_view_state_round_trip(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v._scale = 3.5
    v._ox, v._oy = 12.0, 34.0
    v.set_hidden_indices([1])
    st = v.get_view_state()
    v2 = make_view(qapp, THREE_SQUARES)
    v2.set_view_state(st)
    assert v2._scale == pytest.approx(3.5)
    assert (v2._ox, v2._oy) == (pytest.approx(12.0), pytest.approx(34.0))
    assert [e.hidden for e in v2._entities] == [False, True, False]


# ── selection ────────────────────────────────────────────────────────────────


def test_click_selects_and_empty_click_deselects(qapp):
    v = make_view(qapp, THREE_SQUARES)
    click_world(v, 5.0, 0.0)  # on first square's bottom edge
    assert v.get_selection_indices() == [0]
    click_world(v, 45.0, -20.0)  # empty space
    assert v.get_selection_indices() == []


def test_shift_click_adds_to_selection(qapp):
    v = make_view(qapp, THREE_SQUARES)
    click_world(v, 5.0, 0.0)
    click_world(v, 35.0, 0.0, mods=SHIFT)
    assert v.get_selection_indices() == [0, 1]


def test_select_all_and_invert(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.select_all()
    assert v.get_selection_indices() == [0, 1, 2]
    v.set_selection([0])
    v._invert_selection()
    assert v.get_selection_indices() == [1, 2]
    v.deselect_all()
    assert v.get_selection_indices() == []


def test_hidden_poly_not_clickable_locked_not_moved(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_hidden_indices([0])
    click_world(v, 5.0, 0.0)
    assert v.get_selection_indices() == []

    v.set_hidden_indices([])
    v.set_locked_indices([1])
    before = [tuple(p) for p in v._entities[1].points]
    drag_world(v, 35.0, 0.0, 45.0, 15.0)  # try to drag the locked square
    assert [tuple(p) for p in v._entities[1].points] == before


def test_group_click_selects_whole_group(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0, 1])
    v._group_selected()
    v.deselect_all()
    click_world(v, 5.0, 0.0)
    assert v.get_selection_indices() == [0, 1]
    v._ungroup_selected()
    assert all(e.group is None for e in v._entities)


# ── undo / redo ──────────────────────────────────────────────────────────────


def test_delete_undo_redo_preserves_flags(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v._entities[2].hidden = True
    v._entities[1].group = 3
    v.set_selection([0])
    assert v.delete_selected() == 1
    assert v.poly_count == 2
    assert v.undo()
    assert v.poly_count == 3
    assert v._entities[2].hidden is True
    assert v._entities[1].group == 3
    assert v.redo()
    assert v.poly_count == 2


def test_drag_move_translates_and_undo_restores(qapp):
    v = make_view(qapp, THREE_SQUARES)
    click_world(v, 5.0, 0.0)
    assert v.get_selection_indices() == [0]
    before = [tuple(p) for p in v._entities[0].points]
    drag_world(v, 5.0, 0.0, 20.0, 15.0)  # grab the bottom edge, move
    after = [tuple(p) for p in v._entities[0].points]
    assert after != before
    dx = after[0][0] - before[0][0]
    dy = after[0][1] - before[0][1]
    for (bx, by), (ax, ay) in zip(before, after):
        assert ax - bx == pytest.approx(dx, abs=1e-6)
        assert ay - by == pytest.approx(dy, abs=1e-6)
    assert v.undo()
    assert [tuple(p) for p in v._entities[0].points] == before


def test_nudge_arrow_key_moves_selection_once_per_undo(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0])
    before = [tuple(p) for p in v._entities[0].points]
    key(v, Qt.Key.Key_Right)
    key(v, Qt.Key.Key_Right)
    after = [tuple(p) for p in v._entities[0].points]
    assert after[0][0] > before[0][0]
    assert after[0][1] == pytest.approx(before[0][1])
    assert v.undo()  # both nudges coalesce into one undo step
    assert [tuple(p) for p in v._entities[0].points] == before


# ── clipboard ────────────────────────────────────────────────────────────────


def test_copy_paste_and_duplicate(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0])
    v._copy_selected()
    v._paste_clipboard()
    assert v.poly_count == 4
    # paste selects the new entity
    assert v.get_selection_indices() == [3]
    v.set_selection([1])
    v.duplicate_selected()
    assert v.poly_count == 5
    assert v.undo() and v.undo()
    assert v.poly_count == 3


def test_cut_removes_and_paste_restores_geometry(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([2])
    orig = [tuple(p) for p in v._entities[2].points]
    v._cut_selected()
    assert v.poly_count == 2
    v._paste_records(0.0)
    assert v.poly_count == 3
    assert [tuple(p) for p in v._entities[2].points] == orig


# ── transforms ───────────────────────────────────────────────────────────────


def test_align_left(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.select_all()
    assert v.align_selected("left")
    for e in v._entities:
        assert bbox(e.points)[0] == pytest.approx(0.0)


def test_rotate_selected_keeps_center(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    assert v.rotate_selected(45.0)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert (x0 + x1) / 2 == pytest.approx(5.0, abs=1e-6)
    assert (y0 + y1) / 2 == pytest.approx(5.0, abs=1e-6)
    # 45° rotated square bbox is sqrt(2) * 10 wide
    assert x1 - x0 == pytest.approx(10 * math.sqrt(2), abs=1e-6)


def test_mirror_selected(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]])
    v.set_selection([0])
    assert v.mirror_selected("horizontal")
    # mirrored about the selection bbox center (x=5)
    assert v._entities[0].points[0][0] == pytest.approx(10.0)
    assert v._entities[0].points[1][0] == pytest.approx(0.0)


def test_set_selected_width_scales_uniformly(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    assert v._set_selected_width(20.0)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 - x0 == pytest.approx(20.0)


def test_offset_selected_adds_offset_copy(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    assert v.offset_selected(2.0) >= 1
    assert v.poly_count >= 2
    # outward offset of a 10mm square is a 14mm-wide ring (with round joins)
    x0, y0, x1, y1 = bbox(v._entities[-1].points)
    assert x1 - x0 == pytest.approx(14.0, abs=0.5)


# ── open / close / explode / merge ───────────────────────────────────────────


def test_close_and_open_polylines(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]])
    v.set_selection([0])
    assert v.close_selected_polylines() == 1
    pts = v._entities[0].points
    assert pts[0] == pts[-1]
    assert v.open_selected_polylines() == 1
    pts = v._entities[0].points
    assert pts[0] != pts[-1]


def test_explode_then_merge_rectangle(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    assert v.explode_selected_to_segments() == 4
    assert v.poly_count == 4
    v.select_all()
    assert v.merge_selected_segments_to_objects() >= 1
    assert v.poly_count == 1
    pts = v._entities[0].points
    assert pts[0] == pts[-1]  # closed again
    assert len(set(map(tuple, pts))) == 4


# ── draw mode ────────────────────────────────────────────────────────────────


def test_draw_polyline_commit_and_undo(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    assert v.get_mode() == "draw"
    for wx, wy in [(100.0, 100.0), (120.0, 100.0), (120.0, 120.0)]:
        click_world(v, wx, wy)
    key(v, Qt.Key.Key_Return)
    assert v.poly_count == 4
    drawn = v._entities[3].points
    assert len(drawn) == 3
    assert drawn[0] == pytest.approx((100.0, 100.0))
    assert v.undo()
    assert v.poly_count == 3


def test_draw_escape_cancels_points(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    click_world(v, 100.0, 100.0)
    click_world(v, 110.0, 100.0)
    key(v, Qt.Key.Key_Escape)
    assert v.poly_count == 3
    assert v._draw_pts == []


def test_draw_rectangle_primitive(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    v._set_draw_primitive("rectangle")
    click_world(v, 100.0, 100.0)
    click_world(v, 130.0, 120.0)
    assert v.poly_count == 4
    e = v._entities[3]
    assert e.kind == "rectangle"
    x0, y0, x1, y1 = bbox(e.points)
    assert (x1 - x0, y1 - y0) == (pytest.approx(30.0), pytest.approx(20.0))


def test_draw_circle_primitive(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    v._set_draw_primitive("circle")
    click_world(v, 100.0, 100.0)
    click_world(v, 110.0, 100.0)
    assert v.poly_count == 4
    e = v._entities[3]
    assert e.kind == "circle"
    x0, y0, x1, y1 = bbox(e.points)
    assert x1 - x0 == pytest.approx(20.0, abs=0.1)


# ── edit mode ────────────────────────────────────────────────────────────────


def test_edit_drag_vertex(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_mode("edit")
    drag_world(v, 0.0, 0.0, -5.0, -5.0)  # grab corner vertex, move out
    pts = v._entities[0].points
    moved = [p for p in pts if p == pytest.approx((-5.0, -5.0), abs=0.5)]
    assert moved, f"no vertex moved to (-5,-5): {pts}"
    assert v.undo()
    assert v._entities[0].points[0] == pytest.approx((0.0, 0.0))


# ── rubber band ──────────────────────────────────────────────────────────────


def test_shift_drag_rubber_band_selects_contained(qapp):
    v = make_view(qapp, THREE_SQUARES)
    drag_world(v, -5.0, -5.0, 45.0, 15.0, mods=SHIFT)
    assert v.get_selection_indices() == [0, 1]


# ── modes / misc ─────────────────────────────────────────────────────────────


def test_mode_switching_resets_interaction_state(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    click_world(v, 100.0, 100.0)
    v.set_mode("select")
    assert v._draw_pts == []
    assert v.get_mode() == "select"


def test_measure_toggle(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.toggle_measure()
    assert v._measure_mode
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    assert v._measure_locked
    v.toggle_measure()
    assert not v._measure_mode


def test_status_summary_counts(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0, 2])
    s = v.get_status_summary()
    assert s["object_count"] == 3
    assert s["selected_count"] == 2


def test_zoom_helpers(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.fit()
    z0 = v.get_zoom_percent()
    v._zoom_by(2.0)
    assert v.get_zoom_percent() == pytest.approx(z0 * 2, rel=0.05)
    v.set_selection([0])
    assert v.fit_selection()


# ── DxfCanvas (quick shapes) ─────────────────────────────────────────────────


def test_quick_shape_drag_creates_rect(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_quick_shape_enabled(True)
    c.set_quick_shape_mode("rectangle", flash=False)
    drag_world(c, 100.0, 100.0, 140.0, 130.0)
    assert c.poly_count == 4
    e = c._entities[3]
    x0, y0, x1, y1 = bbox(e.points)
    assert x1 - x0 == pytest.approx(40.0, abs=0.5)
    assert y1 - y0 == pytest.approx(30.0, abs=0.5)
    assert c.undo()
    assert c.poly_count == 3


def test_text_entity_add_and_records(qapp):
    from PySide6.QtGui import QFontDatabase

    v = make_view(qapp, [])
    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    n = v.add_text_at(10.0, 20.0, text="Hi", family=family, height_mm=8.0)
    if n == 0:
        pytest.skip("no usable font on offscreen platform")
    assert v.poly_count == n
    if n > 1:
        # glyph contours are grouped so text moves as one object
        gids = {e.group for e in v._entities}
        assert len(gids) == 1 and None not in gids
    assert v.undo()
    assert v.poly_count == 0


# ── command registry ─────────────────────────────────────────────────────────


def test_command_registry_keymap_matches_events(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0])
    key(v, Qt.Key.Key_Delete)
    assert v.poly_count == 2
    key(v, Qt.Key.Key_Z, mods=Qt.KeyboardModifier.ControlModifier)
    assert v.poly_count == 3
    key(
        v,
        Qt.Key.Key_Z,
        mods=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert v.poly_count == 2


def test_command_registry_single_letter_commands(qapp):
    v = make_view(qapp, THREE_SQUARES)
    assert not v._grid_visible
    key(v, Qt.Key.Key_G)
    assert v._grid_visible
    key(v, Qt.Key.Key_M)
    assert v._measure_mode
    key(v, Qt.Key.Key_M)
    key(v, Qt.Key.Key_D)
    assert v.get_mode() == "draw"
    key(v, Qt.Key.Key_D)
    assert v.get_mode() == "select"


def test_command_registry_group_shortcut(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0, 1])
    key(v, Qt.Key.Key_G, mods=Qt.KeyboardModifier.ControlModifier)
    assert v._entities[0].group is not None
    key(
        v,
        Qt.Key.Key_G,
        mods=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert all(e.group is None for e in v._entities)


def test_command_registry_no_duplicate_shortcuts(qapp):
    from src.ui.canvas import commands

    seen = {}
    for c in commands.COMMANDS:
        for spec in (c.shortcut, *c.aliases):
            if not spec:
                continue
            combo = commands._combo(spec)
            assert combo not in seen, f"{c.id} and {seen[combo]} share {spec}"
            seen[combo] = c.id


def test_shortcut_reference_rows_nonempty(qapp):
    from src.ui.canvas import commands

    rows = commands.shortcut_reference_rows()
    labels = [r[0] for r in rows]
    assert "Edit" in labels and "View" in labels
    assert any("Undo" in r[0] and r[1] for r in rows)


def test_quick_shape_keys_and_radial_toggle(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    key(c, Qt.Key.Key_R, mods=SHIFT)
    assert c.quick_shape_mode == "rectangle" and c.quick_shape_enabled
    key(c, Qt.Key.Key_C, mods=SHIFT)
    assert c.quick_shape_mode == "circle"
    key(c, Qt.Key.Key_Q)
    assert c._radial_active
    key(c, Qt.Key.Key_Q)
    assert not c._radial_active
    key(c, Qt.Key.Key_Escape)
    assert not c.quick_shape_enabled


def test_radial_menu_is_a_rebindable_command_not_a_hardcoded_key(qapp):
    """"Q" must resolve through the canvas Command registry (so it shows up
    in the Keybindings dialog and is rebindable), not a check the tool's
    key() hook shadows before the registry ever sees it."""
    from src.ui.canvas import commands as canvas_commands

    cmd = canvas_commands.get("canvas.radial_menu")
    assert cmd.shortcut == "Q"

    c = make_canvas(qapp, THREE_SQUARES)
    key(c, Qt.Key.Key_Q)
    assert c._radial_active


def test_radial_menu_hover_highlights_the_wedge_under_the_cursor(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c._cursor_wx, c._cursor_wy = c._c2w(400, 300)
    key(c, Qt.Key.Key_Q)
    assert c._radial_active
    center = c._radial_center_c
    n = len(c._radial_tools)
    slice_deg = 360.0 / n

    # Slice 0 is always centered on angle 0 (straight right of center).
    move(c, center.x() + 60, center.y())
    assert c._radial_hover_index == 0

    # Slice 2's center angle, wherever that lands for the current wedge count.
    ang = math.radians(2 * slice_deg)
    move(c, center.x() + math.cos(ang) * 60, center.y() - math.sin(ang) * 60)
    assert c._radial_hover_index == 2

    # Inside the inner hole / outside the outer ring: no slice hovered.
    move(c, center.x() + 5, center.y())
    assert c._radial_hover_index is None


def test_radial_menu_hover_resets_on_close(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c._cursor_wx, c._cursor_wy = c._c2w(400, 300)
    key(c, Qt.Key.Key_Q)
    center = c._radial_center_c
    move(c, center.x() + 60, center.y())
    assert c._radial_hover_index == 0

    key(c, Qt.Key.Key_Escape)
    assert c._radial_hover_index is None


@pytest.mark.parametrize(
    "idx,expected_mode,expected_primitive",
    [
        (0, "draw", "polyline"),
        (1, "draw", "rectangle"),
        (2, "draw", "circle"),
        (3, "draw", "polygon"),
        (4, "draw", "line"),
        (5, "draw", "arc"),
        (6, "pen", None),
    ],
)
def test_radial_menu_wedges_are_the_draw_primitive_tools(
    qapp, idx, expected_mode, expected_primitive
):
    """The wheel is a shape-tool picker now, not a duplicate of D/E/M/F —
    each wedge switches to draw mode with the matching primitive (or the
    bezier Pen mode), not the old mode-toggle/quick-shape/size actions."""
    c = make_canvas(qapp, THREE_SQUARES)
    c._execute_radial_action(idx)
    assert c.get_mode() == expected_mode
    if expected_primitive is not None:
        assert c._draw_primitive == expected_primitive


def test_radial_menu_opens_and_dismisses_from_any_mode(qapp):
    """Previously the menu only opened in select mode (its press/move/paint
    lived on DxfSelectTool); it must now work from draw/edit/pen too, since
    it lives at the DxfCanvas level ahead of tool dispatch."""
    for mode in ("draw", "edit", "pen", "select"):
        c = make_canvas(qapp, THREE_SQUARES)
        c.set_mode(mode)
        key(c, Qt.Key.Key_Q)
        assert c._radial_active, f"radial menu did not open in {mode!r} mode"
        key(c, Qt.Key.Key_Q)
        assert not c._radial_active


def test_radial_menu_tools_are_customizable(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(["canvas.circle", "canvas.arc", "mode.pen"])
    assert c._radial_tools == ["canvas.circle", "canvas.arc", "mode.pen"]
    c._execute_radial_action(0)
    assert c.get_mode() == "draw" and c._draw_primitive == "circle"
    c._execute_radial_action(2)
    assert c.get_mode() == "pen"


def test_radial_menu_tools_include_the_full_command_registry(qapp):
    """The pool is "every canvas Command", not just draw primitives — this
    is the point of the redesign (many more options than the original 6)."""
    from src.ui.canvas import commands as canvas_commands

    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(["edit.undo", "clipboard.copy", "boolean.union"])
    assert c._radial_tools == ["edit.undo", "clipboard.copy", "boolean.union"]
    non_hidden = [cmd.id for cmd in canvas_commands.COMMANDS if not cmd.hidden]
    assert len(non_hidden) > 20  # the pool really is much bigger now


def test_radial_menu_tools_falls_back_below_minimum(qapp):
    from src.settings import DEFAULT_RADIAL_MENU_TOOLS

    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(["canvas.circle", "canvas.arc"])  # only 2 — below the minimum
    assert c._radial_tools == list(DEFAULT_RADIAL_MENU_TOOLS)


def test_radial_menu_tools_drops_unknown_and_dedupes(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(
        ["canvas.circle", "bogus", "canvas.circle", "canvas.arc", "mode.pen"]
    )
    assert c._radial_tools == ["canvas.circle", "canvas.arc", "mode.pen"]


def test_radial_menu_label_width_budget_never_exceeds_the_disc(qapp):
    """The chord-based width cap _paint_radial_menu elides labels against
    must itself never claim more room than the disc actually has — this is
    the guarantee that stops a long label (e.g. "Duplicate with Offset")
    from spilling past the wheel's outer edge."""
    c = make_canvas(qapp, THREE_SQUARES)
    outer = c._RADIAL_OUTER
    cy = 300.0
    for dy in (0.0, outer * 0.5, outer * 0.95, outer, outer * 1.5):
        chord_half = c._radial_chord_half(cy + dy, cy, outer)
        assert 0.0 <= chord_half <= outer
    # Directly above/below center the full diameter is available; near the
    # top/bottom pole it shrinks to ~0 rather than going negative or over.
    assert c._radial_chord_half(cy, cy, outer) == pytest.approx(outer)
    assert c._radial_chord_half(cy + outer, cy, outer) == pytest.approx(0.0, abs=1e-6)


def test_radial_menu_paints_long_labels_without_raising(qapp):
    """Smoke-check the actual paint path with labels long enough to have
    previously overflowed ("Duplicate with Offset", "Grid Array", ...)."""
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(
        ["edit.duplicate_offset", "edit.array_grid", "boolean.subtract", "select.invert"]
    )
    c._cursor_wx, c._cursor_wy = c._c2w(400, 300)
    key(c, Qt.Key.Key_Q)
    assert c._radial_active
    pix = c.grab()  # forces a real paintEvent through _paint_radial_menu
    assert pix.width() > 0 and pix.height() > 0


def test_undo_store_deltas_only_touch_changed_entities(qapp):
    """Delta undo: a move records only the moved entity, not the document."""
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0])
    v._nudge_selected(1.0, 0.0)
    store = v._undo_store
    # finalize by starting a different (non-coalescing) op
    v.set_selection([1])
    v.rotate_selected(90.0)
    assert len(store._undo) >= 1
    first = store._undo[0]
    assert len(first.back_changed) == 1  # only square 0 stored
    assert first.back_len == 3 and first.fwd_len == 3


def test_undo_after_load_is_unavailable(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([0])
    v.delete_selected()
    v.load([square(0, 0)])
    assert not v.undo()  # fresh document, fresh history


def test_undo_redo_deep_sequence(qapp):
    v = make_view(qapp, [square(0, 0)])
    for i in range(5):
        v.set_selection([0])
        v._duplicate_selected()
    assert v.poly_count == 6
    for _ in range(5):
        assert v.undo()
    assert v.poly_count == 1
    for _ in range(5):
        assert v.redo()
    assert v.poly_count == 6
    assert not v.redo()


def test_snap_engine_vertex_and_guides(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.fit()
    # near the (10,10) corner in screen space → vertex snap
    cx, cy = v._w2c(10.2, 9.9)
    res = v._resolve_snap(cx, cy, 10.2, 9.9)
    assert res is not None
    assert res[0] == pytest.approx(10.0) and res[1] == pytest.approx(10.0)

    # guide lines participate in snapping
    v._guides.append(("v", 20.0))
    cx, cy = v._w2c(20.05, 30.0)
    res = v._resolve_snap(cx, cy, 20.05, 30.0)
    assert res is not None and res[2] == "guide"
    assert res[0] == pytest.approx(20.0)


# ── marquee semantics (window vs crossing) ───────────────────────────────────


def test_marquee_no_shift_on_empty_space(qapp):
    v = make_view(qapp, THREE_SQUARES)
    drag_world(v, -5.0, -5.0, 15.0, 15.0)  # plain drag, no Shift
    assert v.get_selection_indices() == [0]


def test_marquee_window_requires_full_enclosure(qapp):
    v = make_view(qapp, THREE_SQUARES)
    # left→right drag covering all of square 0 but only half of square 1
    drag_world(v, -5.0, -5.0, 36.0, 15.0)
    assert v.get_selection_indices() == [0]


def test_marquee_crossing_selects_touched(qapp):
    v = make_view(qapp, THREE_SQUARES)
    # right→left drag covering all of square 0 and part of square 1
    drag_world(v, 36.0, 15.0, -5.0, -5.0)
    assert v.get_selection_indices() == [0, 1]


def test_marquee_crossing_hits_segment_without_vertices(qapp):
    # long horizontal line; marquee crosses its middle, no endpoint inside
    v = make_view(qapp, [[(0.0, 0.0), (100.0, 0.0)]])
    drag_world(v, 55.0, 10.0, 45.0, -10.0)  # right→left box over the middle
    assert v.get_selection_indices() == [0]
    v.deselect_all()
    # window drag over the middle must NOT select (not fully enclosed)
    drag_world(v, 45.0, -10.0, 55.0, 10.0)
    assert v.get_selection_indices() == []


def test_marquee_pulls_whole_group(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([1, 2])
    v._group_selected()
    v.deselect_all()
    drag_world(v, 45.0, 15.0, 25.0, -5.0)  # crossing box over square 1 only
    assert v.get_selection_indices() == [1, 2]


def test_hover_pre_highlight_tracks_target(qapp):
    v = make_view(qapp, THREE_SQUARES)
    cx, cy = v._w2c(5.0, 0.0)
    move(v, cx, cy, button=Qt.MouseButton.NoButton)
    assert v._hover_poly == 0
    cx, cy = v._w2c(45.0, -20.0)  # empty space
    move(v, cx, cy, button=Qt.MouseButton.NoButton)
    assert v._hover_poly is None


# ── rulers and guides ────────────────────────────────────────────────────────


def test_drag_guide_from_ruler_and_delete(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_rulers_visible(True)
    # press inside the top ruler, drag down into the canvas
    press(c, 300.0, 10.0)
    move(c, 300.0, 200.0)
    release(c, 300.0, 200.0)
    assert len(c._guides) == 1
    orient, coord = c._guides[0]
    assert orient == "h"
    _, wy = c._c2w(300.0, 200.0)
    assert coord == pytest.approx(wy, abs=1e-6)

    # guides persist through view state
    st = c.get_view_state()
    c2 = make_canvas(qapp, THREE_SQUARES)
    c2.set_view_state(st)
    assert c2._guides == c._guides

    # drag the guide back onto the ruler to delete it
    gy = c._w2c(0.0, coord)[1]
    press(c, 300.0, gy)
    move(c, 300.0, 8.0)
    release(c, 300.0, 8.0)
    assert c._guides == []


def test_vertical_guide_from_left_ruler(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_rulers_visible(True)
    press(c, 10.0, 300.0)
    move(c, 250.0, 300.0)
    release(c, 250.0, 300.0)
    assert len(c._guides) == 1
    assert c._guides[0][0] == "v"


# ── 8-handle selection frame ─────────────────────────────────────────────────


def _paint_once(view):
    view.grab()  # forces a full paintEvent so overlay hit-rects populate


def test_handle_scale_corner_uniform(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    _paint_once(v)
    handles = dict(v._gizmo_handle_rects)
    assert set(handles) == {"nw", "n", "ne", "e", "se", "s", "sw", "w"}
    rect = handles["e"]  # right edge: axis-only scale
    cx, cy = rect.center().x(), rect.center().y()
    tx, ty = v._w2c(20.0, 5.0)  # drag right edge from x=10 to x=20
    press(v, cx, cy)
    move(v, tx, ty)
    release(v, tx, ty)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 - x0 == pytest.approx(20.0, abs=0.3)
    assert y1 - y0 == pytest.approx(10.0, abs=0.3)  # height untouched
    assert v.undo()


def test_handle_scale_corner_keeps_anchor(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    _paint_once(v)
    handles = dict(v._gizmo_handle_rects)
    rect = handles["se"]  # world (max x, min y); anchor = world (0, 10)
    cx, cy = rect.center().x(), rect.center().y()
    tx, ty = v._w2c(20.0, -10.0)
    press(v, cx, cy)
    move(v, tx, ty)
    release(v, tx, ty)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    # uniform scale ×2 anchored at the opposite corner (0, 10)
    assert x0 == pytest.approx(0.0, abs=0.3)
    assert y1 == pytest.approx(10.0, abs=0.3)
    assert x1 - x0 == pytest.approx(20.0, abs=0.5)
    assert y1 - y0 == pytest.approx(20.0, abs=0.5)


def test_hud_prompt_offset_inline(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    v._prompt_offset_selected()
    assert v._hud_prompt_edit is not None
    v._hud_prompt_edit.setText("2")
    v._hud_prompt_edit.returnPressed.emit()
    assert v._hud_prompt_edit is None
    assert v.poly_count >= 2  # offset ring created without any dialog
    # Escape dismisses a fresh prompt without running the action
    v._prompt_offset_selected()
    key(v, Qt.Key.Key_Escape)
    assert v._hud_prompt_edit is None


def test_zoom_bounds_and_presets(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.fit()
    for _ in range(200):
        v._zoom_by(2.0)
    from src.ui.canvas.view import _MAX_SCALE

    assert v._scale == _MAX_SCALE  # clamped, no runaway float
    v.set_zoom_percent(100)
    assert v.get_zoom_percent() == pytest.approx(100, abs=1)
    v.set_zoom_percent(200)
    assert v.get_zoom_percent() == pytest.approx(200, abs=2)


def test_double_click_empty_fits(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.fit()
    v._zoom_by(4.0)
    z = v.get_zoom_percent()
    ev = _mouse_event(QEvent.Type.MouseButtonDblClick, 5.0, 5.0)
    v.mouseDoubleClickEvent(ev)
    assert v.get_zoom_percent() < z


# ── parametric text ──────────────────────────────────────────────────────────


def test_text_carries_params_and_rebuilds(qapp):
    from PySide6.QtGui import QFontDatabase

    v = make_view(qapp, [])
    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    n = v.add_text_at(10.0, 20.0, text="Hi", family=family, height_mm=8.0)
    if n == 0:
        pytest.skip("no usable font on offscreen platform")
    params = v.text_params_at(0)
    assert params is not None
    assert params == {
        "text": "Hi",
        "family": family,
        "height_mm": 8.0,
        "bold": False,
        "italic": False,
    }
    # anchor stays put across a rebuild with different content
    old_min_x = min(x for e in v._entities for x, _ in e.points)
    old_min_y = min(y for e in v._entities for _, y in e.points)
    assert v.rebuild_text(0, {**params, "text": "Hello", "height_mm": 8.0})
    params_after_rebuild = v.text_params_at(0)
    assert params_after_rebuild is not None
    assert params_after_rebuild["text"] == "Hello"
    new_min_x = min(x for e in v._entities for x, _ in e.points)
    new_min_y = min(y for e in v._entities for _, y in e.points)
    assert new_min_x == pytest.approx(old_min_x, abs=0.5)
    assert new_min_y == pytest.approx(old_min_y, abs=0.5)
    assert v.undo()
    params_after_undo = v.text_params_at(0)
    assert params_after_undo is not None
    assert params_after_undo["text"] == "Hi"


def test_selected_circle_drags_as_move_not_vertex_edit(qapp):
    """Dragging a circle by its rim must move it, not distort its points —
    every rim point is a 'vertex', which used to hijack the drag."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("circle")
    click_world(v, 50.0, 50.0)
    click_world(v, 60.0, 50.0)
    v.set_mode("select")
    click_world(v, 60.0, 50.0)  # select it (click on rim)
    assert v.get_selection_indices() == [0]
    before = bbox(v._entities[0].points)
    drag_world(v, 60.0, 50.0, 75.0, 65.0)  # grab the rim, drag
    after = bbox(v._entities[0].points)
    # same size (moved, not distorted)
    assert after[2] - after[0] == pytest.approx(before[2] - before[0], abs=1e-6)
    assert after[3] - after[1] == pytest.approx(before[3] - before[1], abs=1e-6)
    assert after[0] != pytest.approx(before[0])


def test_handle_corner_free_resize(qapp):
    """Corners resize X and Y independently by default."""
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    _paint_once(v)
    rect = dict(v._gizmo_handle_rects)["se"]
    cx, cy = rect.center().x(), rect.center().y()
    tx, ty = v._w2c(30.0, -5.0)  # 3x in X, 1.5x in Y from anchor (0, 10)
    press(v, cx, cy)
    move(v, tx, ty)
    release(v, tx, ty)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 - x0 == pytest.approx(30.0, abs=0.5)
    assert y1 - y0 == pytest.approx(15.0, abs=0.5)


def test_handle_corner_shift_keeps_aspect(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    _paint_once(v)
    rect = dict(v._gizmo_handle_rects)["se"]
    cx, cy = rect.center().x(), rect.center().y()
    tx, ty = v._w2c(30.0, -5.0)
    press(v, cx, cy)
    move(v, tx, ty, mods=SHIFT)
    release(v, tx, ty, mods=SHIFT)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 - x0 == pytest.approx(30.0, abs=0.5)  # dominant axis
    assert y1 - y0 == pytest.approx(30.0, abs=0.5)  # aspect locked


def test_move_drag_snaps_by_shape_vertices(qapp):
    """Dragging by the shape's interior still snaps its corners to other
    shapes' vertices."""
    v = make_view(qapp, [square(0, 0), square(30, 0)])
    v.set_selection([0])
    # grab the first square's bottom edge midpoint (not a corner) and drag
    # near the second square: the dragged square's own corners find the
    # nearest static candidate (here: an edge) even though the grab point
    # is nowhere near it
    drag_world(v, 5.0, 0.0, 24.8, -0.3, steps=8)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 == pytest.approx(30.0, abs=1e-6)  # snapped flush exactly
    assert abs(y0 + 0.3) < 0.35  # y stays within drag tolerance


def test_move_drag_snaps_to_guides_by_geometry(qapp):
    v = make_view(qapp, [square(0, 0)])
    v._guides.append(("v", 50.0))
    v.set_selection([0])
    drag_world(v, 5.0, 0.0, 44.8, 3.0, steps=6)  # left edge near guide x=50...
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    # one of the square's edges landed exactly on the guide
    assert any(abs(edge - 50.0) < 1e-6 for edge in (x0, x1))
