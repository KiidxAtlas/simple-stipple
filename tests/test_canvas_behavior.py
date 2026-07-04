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

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
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
    if polys is not None:
        c.load(polys)
        c.fit()
    return c


NO_MOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
LMB = Qt.MouseButton.LeftButton


def _mouse_event(etype, cx, cy, button=LMB, mods=NO_MOD):
    buttons = (
        Qt.MouseButton.NoButton
        if etype == QEvent.Type.MouseButtonRelease
        else button
    )
    return QMouseEvent(etype, QPointF(cx, cy), QPointF(cx, cy), button, buttons, mods)


def press(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, cx, cy, button, mods))


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
