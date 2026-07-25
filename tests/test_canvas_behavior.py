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
    from src.ui.canvas.view.main import CanvasView

    v = CanvasView()
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


def test_load_without_fit_preserves_viewport(qapp):
    canvas = make_canvas(qapp, [[(0.0, 0.0), (10.0, 10.0)]])
    canvas._scale = 7.5
    canvas._ox = 123.0
    canvas._oy = 234.0

    canvas.load([[(0.0, 0.0), (100.0, 100.0)]], fit=False)

    assert (canvas._scale, canvas._ox, canvas._oy) == (7.5, 123.0, 234.0)


def test_background_image_requires_explicit_selection_and_escape_deselects(qapp):
    from PIL import Image

    canvas = make_canvas(qapp)
    canvas.set_background_image(Image.new("RGBA", (20, 20)), 10.0, 10.0, 0.0, 0.0)
    canvas.set_background_image_editable(True)
    cx, cy = canvas._w2c(5.0, 5.0)

    assert canvas._background_edit_hit(cx, cy) is None
    click_world(canvas, 5.0, 5.0)
    assert canvas.is_background_image_selected()
    assert canvas._background_edit_hit(cx, cy) is not None

    key(canvas, Qt.Key.Key_Escape)
    assert not canvas.is_background_image_selected()


def test_geometry_has_priority_over_unselected_background_image(qapp):
    from PIL import Image

    canvas = make_canvas(qapp, [square(0.0, 0.0)])
    canvas.set_background_image(Image.new("RGBA", (20, 20)), 10.0, 10.0, 0.0, 0.0)
    canvas.set_background_image_editable(True)

    click_world(canvas, 0.0, 5.0)

    assert not canvas.is_background_image_selected()
    assert canvas._sel == {canvas._entities[0].id}


def test_background_image_exposes_rotation_handle_and_selection_signal(qapp):
    from PIL import Image

    canvas = make_canvas(qapp)
    changes = []
    canvas.backgroundSelectionChanged.connect(changes.append)
    canvas.set_background_image(Image.new("RGBA", (20, 10)), 20.0, 10.0, 0.0, 0.0)
    canvas.set_background_image_editable(True)
    canvas.select_background_image(True)

    corners = canvas._background_canvas_corners()
    top_x = (corners["nw"][0] + corners["ne"][0]) / 2.0
    top_y = (corners["nw"][1] + corners["ne"][1]) / 2.0
    center = canvas._w2c(10.0, 5.0)
    length = ((top_x - center[0]) ** 2 + (top_y - center[1]) ** 2) ** 0.5
    handle_x = top_x + (top_x - center[0]) / length * 24.0
    handle_y = top_y + (top_y - center[1]) / length * 24.0

    assert canvas._background_edit_hit(handle_x, handle_y) == "rotate"
    assert changes == [True]
    canvas.select_background_image(False)
    assert changes == [True, False]


def test_precision_state_exposes_all_user_snap_controls(qapp):
    canvas = make_canvas(qapp)
    canvas.set_snap_master(False)
    canvas.set_snap_vertex(False)
    canvas.set_snap_edge(True)
    canvas.set_snap_angle(False)
    canvas.set_grid_snap(True)
    state = canvas.get_precision_state()
    assert state["snap_master"] is False
    assert state["snap_vertex"] is False
    assert state["snap_edge"] is True
    assert state["snap_angle"] is False
    assert state["grid_snap"] is True


def test_geometry_health_overlay_is_toggleable_and_persisted(qapp):
    canvas = make_canvas(qapp, [[(0.0, 0.0), (10.0, 0.0)]])
    assert canvas._geometry_health_visible is False
    canvas.set_geometry_health_visible(True)
    assert canvas._geometry_health_visible is True
    state = canvas.get_view_state()
    other = make_canvas(qapp)
    other.set_view_state(state)
    assert other._geometry_health_visible is True
    other.resize(400, 300)
    assert not other.grab().isNull()


def test_curvature_overlay_is_toggleable_and_persisted(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (5, 0), (6, 1), (6, 6)]])
    canvas.set_curvature_visible(True)
    assert canvas._curvature_visible
    other = make_canvas(qapp)
    other.set_view_state(canvas.get_view_state())
    assert other._curvature_visible
    assert not canvas.grab().isNull()


def test_semantic_selection_filters(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {"points": [(0, 0), (1, 0)], "kind": "polyline"},
            {
                "points": [],
                "kind": "circle",
                "meta": {"center": (5, 5), "radius": 2},
            },
            {"points": [(0, 2), (1, 2)], "kind": "line", "construction": True},
        ]
    )
    assert canvas.select_geometry_category("parametric") == 1
    assert canvas._sel == {canvas._entities[1].id}
    assert canvas.select_geometry_category("construction") == 1
    assert canvas._sel == {canvas._entities[2].id}


def test_selection_geometry_reports_length_area_and_circle_diameter(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 0), (10, 5), (0, 5), (0, 0)]])
    canvas.set_selection([canvas._entities[0].id])
    info = canvas.selection_geometry()
    assert info is not None
    assert info["length"] == pytest.approx(30)
    assert info["area"] == pytest.approx(50)

    canvas.set_entity_records(
        [{"points": [], "kind": "circle", "meta": {"center": (0, 0), "radius": 4}}]
    )
    canvas.set_selection([canvas._entities[0].id])
    assert canvas.selection_geometry()["diameter"] == pytest.approx(8)


def test_selection_geometry_reports_minimum_clearance(qapp):
    canvas = make_canvas(
        qapp,
        [
            [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)],
            [(5, 0), (7, 0), (7, 2), (5, 2), (5, 0)],
            [(20, 0), (22, 0), (22, 2), (20, 2), (20, 0)],
        ],
    )
    canvas.set_selection([canvas._entities[0].id, canvas._entities[1].id, canvas._entities[2].id])
    info = canvas.selection_geometry()
    assert info is not None
    assert info["clearance"] == pytest.approx(3)


def test_entity_identity_round_trips_through_canvas_records(qapp):
    canvas = make_canvas(qapp, [[(0.0, 0.0), (10.0, 0.0)]])
    entity_id = canvas._entities[0].id
    records = canvas.get_entity_records()
    canvas.set_entity_records(records)
    assert canvas._entities[0].id == entity_id


def test_hidden_geometry_is_not_an_invisible_snap_target(qapp):
    canvas = make_canvas(qapp, [[(0.0, 0.0), (10.0, 0.0)], [(50.0, 50.0), (60.0, 50.0)]])
    canvas.set_hidden_ids([canvas._entities[0].id])
    cx, cy = canvas._w2c(0.0, 0.0)
    assert canvas._snap_engine.query(cx, cy, 0.0, 0.0, allow_grid=False) is None


def test_extension_snap_projects_beyond_segment_endpoint(qapp):
    canvas = make_canvas(qapp, [[(0.0, 0.0), (10.0, 0.0)]])
    cx, cy = canvas._w2c(15.0, 0.1)
    result = canvas._snap_engine.query(cx, cy, 15.0, 0.1, allow_grid=False)
    assert result is not None
    assert result[:2] == pytest.approx((15.0, 0.0))
    assert result[2] == "extension"


def test_tangent_snap_from_reference_point_to_circle(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {
                "points": [],
                "kind": "circle",
                "meta": {"center": (0.0, 0.0), "radius": 5.0},
            }
        ]
    )
    # From (10, 0), tangent points are (2.5, ±sqrt(18.75)).
    tx, ty = 2.5, math.sqrt(18.75)
    cx, cy = canvas._w2c(tx, ty)
    result = canvas._snap_engine.query(
        cx,
        cy,
        tx,
        ty,
        allow_grid=False,
        reference_point=(10.0, 0.0),
    )
    assert result is not None
    assert result[:2] == pytest.approx((tx, ty), abs=1e-6)
    assert result[2] == "tangent"


def test_array_along_path_places_copies_at_both_ends_and_middle(qapp):
    source = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    path = [(10.0, 10.0), (110.0, 10.0)]
    canvas = make_canvas(qapp, [source, path])
    canvas.set_selection([canvas._entities[0].id, canvas._entities[1].id])
    canvas._show_hud_prompt = lambda _label, _default, callback, **_kwargs: callback(3.0)

    canvas._array_duplicate_along_path()

    assert len(canvas._entities) == 5
    centers = []
    for entity_id in canvas._sel:
        entity = next(e for e in canvas._entities if e.id == entity_id)
        points = entity.points
        centers.append(
            (
                (min(x for x, _ in points) + max(x for x, _ in points)) / 2.0,
                (min(y for _, y in points) + max(y for _, y in points)) / 2.0,
            )
        )
    centers.sort()
    assert centers == pytest.approx([(10.0, 10.0), (60.0, 10.0), (110.0, 10.0)])


def test_rounding_parametric_rectangle_demotes_stale_metadata(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {
                "points": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
                "kind": "rectangle",
                "meta": {"center": (5.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            }
        ]
    )
    assert canvas._round_vertex(0, 1, 2.0)
    entity = canvas._entities[0]
    assert entity.kind == "polyline"
    assert entity.meta is None
    assert canvas._flattened_points(entity.id) == entity.points


@pytest.mark.parametrize("tool", ["rounded_rectangle", "star"])
def test_new_shape_sidebar_tools_can_create_geometry(qapp, tool):
    canvas = make_canvas(qapp)
    canvas.set_mode("draw")
    canvas._set_draw_primitive(tool)
    assert canvas._draw_primitive == tool
    click(canvas, *canvas._w2c(100.0, 100.0))
    click(canvas, *canvas._w2c(120.0, 112.0))
    assert len(canvas._entities) == 1
    assert canvas._entities[0].kind == tool
    assert len(canvas._entities[0].points) > 8


def test_ellipse_quadrant_snap_has_all_four_cardinal_points(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {
                "points": [],
                "kind": "ellipse",
                "meta": {"center": (0.0, 0.0), "rx": 5.0, "ry": 2.0, "rotation": 0.0},
            }
        ]
    )
    cx, cy = canvas._w2c(5.0, 0.0)
    result = canvas._snap_engine.query(cx, cy, 5.0, 0.0, allow_grid=False)
    assert result is not None
    assert result[:2] == pytest.approx((5.0, 0.0))
    assert result[2] == "ellipse_east"

    cx, cy = canvas._w2c(-5.0, 0.0)
    result = canvas._snap_engine.query(cx, cy, -5.0, 0.0, allow_grid=False)
    assert result is not None
    assert result[:2] == pytest.approx((-5.0, 0.0))
    assert result[2] == "ellipse_west"


def test_arc_only_exposes_quadrants_on_its_sweep(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {
                "points": [],
                "kind": "arc",
                "meta": {
                    "center": (0.0, 0.0),
                    "radius": 5.0,
                    "start_angle": 20.0,
                    "end_angle": 200.0,
                },
            }
        ]
    )
    from src.ui.canvas.snap import ShapeSnapEngine

    candidates = ShapeSnapEngine.get_snap_candidates(next(iter(canvas._snap_shapes().values())))
    roles = {role for _x, _y, role in candidates}
    assert "quadrant_north" in roles
    assert "quadrant_west" in roles
    assert "quadrant_south" not in roles


def test_circle_nearest_point_snap_is_analytic_not_tessellation_limited(qapp):
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [{"points": [], "kind": "circle", "meta": {"center": (0.0, 0.0), "radius": 5.0}}]
    )
    angle = math.radians(17.0)
    expected = (5.0 * math.cos(angle), 5.0 * math.sin(angle))
    cx, cy = canvas._w2c(expected[0] + 0.03, expected[1] + 0.03)
    result = canvas._snap_engine.query(
        cx, cy, expected[0] + 0.03, expected[1] + 0.03, allow_grid=False
    )
    assert result is not None
    assert result[:2] == pytest.approx(expected, abs=0.05)
    assert result[2] == "edge"


def test_intersection_snap_works_while_dragging(qapp):
    # Segments deliberately don't have their midpoint/endpoints at the
    # crossing point (5, 5), so a passing result actually exercises the
    # intersection candidate rather than coincidentally matching midpoint.
    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {"points": [(0.0, 5.0), (9.0, 5.0)], "kind": "polyline"},
            {"points": [(5.0, 0.0), (5.0, 9.0)], "kind": "polyline"},
        ]
    )
    cx, cy = canvas._w2c(5.05, 5.05)
    result = canvas._snap_engine.query(
        cx, cy, 5.05, 5.05, drag=True, allow_grid=False, allow_edge=False
    )
    assert result is not None
    assert result[:2] == pytest.approx((5.0, 5.0))
    assert result[2] == "intersection"


NO_MOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
LMB = Qt.MouseButton.LeftButton


def _mouse_event(etype, cx, cy, button=LMB, mods=NO_MOD):
    buttons = Qt.MouseButton.NoButton if etype == QEvent.Type.MouseButtonRelease else button
    return QMouseEvent(etype, QPointF(cx, cy), QPointF(cx, cy), button, buttons, mods)


def press(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, cx, cy, button, mods))


def move(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, cx, cy, button, mods))


def release(view, cx, cy, button=LMB, mods=NO_MOD):
    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, cx, cy, button, mods))


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


def test_add_polylines_state_preserves_existing_entities(qapp):
    """add_polylines_state (used by cross-tab "send selection to Draft")
    must append, not replace — unlike set_polylines_state, which is a
    fresh load and legitimately wipes everything."""
    v = make_view(qapp, THREE_SQUARES)
    assert v.poly_count == 3
    v.add_polylines_state([square(100, 100)], fit=True)
    assert v.poly_count == 4
    assert v.get_selected_ids() == [v._entities[3].id]
    # the original three squares are untouched
    assert [tuple(p) for p in v._entities[0].points] == THREE_SQUARES[0]


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
    v.set_hidden_ids([v._entities[1].id])
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
    assert v.get_selected_ids() == [v._entities[0].id]
    click_world(v, 45.0, -20.0)  # empty space
    assert v.get_selected_ids() == []


def test_shift_click_adds_to_selection(qapp):
    v = make_view(qapp, THREE_SQUARES)
    click_world(v, 5.0, 0.0)
    click_world(v, 35.0, 0.0, mods=SHIFT)
    assert sorted(v.get_selected_ids()) == sorted([v._entities[0].id, v._entities[1].id])


def test_select_all_and_invert(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.select_all()
    assert sorted(v.get_selected_ids()) == sorted([v._entities[0].id, v._entities[1].id, v._entities[2].id])
    v.set_selection([v._entities[0].id])
    v._invert_selection()
    assert sorted(v.get_selected_ids()) == sorted([v._entities[1].id, v._entities[2].id])
    v.deselect_all()
    assert v.get_selected_ids() == []


def test_hidden_poly_not_clickable_locked_not_moved(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_hidden_ids([v._entities[0].id])
    click_world(v, 5.0, 0.0)
    assert v.get_selected_ids() == []

    v.set_hidden_ids([])
    v.set_locked_ids([v._entities[1].id])
    before = [tuple(p) for p in v._entities[1].points]
    drag_world(v, 35.0, 0.0, 45.0, 15.0)  # try to drag the locked square
    assert [tuple(p) for p in v._entities[1].points] == before


def test_group_click_selects_whole_group(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id, v._entities[1].id])
    v._group_selected()
    v.deselect_all()
    click_world(v, 5.0, 0.0)
    assert sorted(v.get_selected_ids()) == sorted([v._entities[0].id, v._entities[1].id])
    v._ungroup_selected()
    assert all(e.group is None for e in v._entities)


# ── undo / redo ──────────────────────────────────────────────────────────────


def test_delete_undo_redo_preserves_flags(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v._entities[2].hidden = True
    v._entities[1].group = 3
    v.set_selection([v._entities[0].id])
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
    assert v.get_selected_ids() == [v._entities[0].id]
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


def test_each_nudge_is_a_reversible_command(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id])
    before = [tuple(p) for p in v._entities[0].points]
    key(v, Qt.Key.Key_Right)
    key(v, Qt.Key.Key_Right)
    after = [tuple(p) for p in v._entities[0].points]
    assert after[0][0] > before[0][0]
    assert after[0][1] == pytest.approx(before[0][1])
    assert v.undo()
    assert [tuple(p) for p in v._entities[0].points] != before
    assert v.undo()
    assert [tuple(p) for p in v._entities[0].points] == before


# ── clipboard ────────────────────────────────────────────────────────────────


def test_copy_paste_and_duplicate(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id])
    v._copy_selected()
    v._paste_clipboard()
    assert v.poly_count == 4
    # paste selects the new entity
    assert v.get_selected_ids() == [v._entities[3].id]
    v.set_selection([v._entities[1].id])
    v.duplicate_selected()
    assert v.poly_count == 5
    assert v.undo() and v.undo()
    assert v.poly_count == 3


def test_plain_paste_preserves_position_while_offset_paste_moves_five_mm(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    original = list(v._entities[0].points)
    v._copy_selected()
    v._paste_clipboard()
    assert v._entities[1].points == original
    v._paste_clipboard_with_offset(5.0)
    assert v._entities[2].points == [(x + 5.0, y + 5.0) for x, y in original]


def test_clipboard_is_shared_across_canvas_instances(qapp):
    """Copy/paste must work across tabs — each tab has its own PolylineView
    instance, so the clipboard can't be plain per-instance state or a copy
    in one tab silently pastes nothing in another."""
    draft = make_view(qapp, THREE_SQUARES)
    draft.set_selection([draft._entities[0].id])
    draft._copy_selected()

    pattern = make_view(qapp, [])  # a different tab's canvas
    assert pattern.poly_count == 0
    pattern._paste_clipboard()
    assert pattern.poly_count == 1


def test_cut_removes_and_paste_restores_geometry(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[2].id])
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
    v.set_selection([v._entities[0].id])
    assert v.rotate_selected(45.0)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert (x0 + x1) / 2 == pytest.approx(5.0, abs=1e-6)
    assert (y0 + y1) / 2 == pytest.approx(5.0, abs=1e-6)
    # 45° rotated square bbox is sqrt(2) * 10 wide
    assert x1 - x0 == pytest.approx(10 * math.sqrt(2), abs=1e-6)


def test_mirror_selected(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]])
    v.set_selection([v._entities[0].id])
    assert v.mirror_selected("horizontal")
    # mirrored about the selection bbox center (x=5)
    assert v._entities[0].points[0][0] == pytest.approx(10.0)
    assert v._entities[0].points[1][0] == pytest.approx(0.0)


def test_set_selected_width_scales_uniformly(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert v._set_selected_width(20.0)
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert x1 - x0 == pytest.approx(20.0)


def test_offset_selected_adds_offset_copy(qapp):
    v = make_view(qapp, [square(0, 0)])
    source_id = v._entities[0].id
    v.set_selection([v._entities[0].id])
    assert v.offset_selected(2.0) >= 1
    assert v.poly_count >= 2
    result = v._last_operation_result
    assert result.changed
    assert result.metadata == {"distance": 2.0}
    assert result.created_ids == result.selected_ids
    assert source_id not in result.created_ids
    assert {v._entity_for_id(index).id for index in v._sel if v._entity_for_id(index)} == set(
        result.selected_ids
    )
    # outward offset of a 10mm square is a 14mm-wide ring (with round joins)
    x0, y0, x1, y1 = bbox(v._entities[-1].points)
    assert x1 - x0 == pytest.approx(14.0, abs=0.5)


def test_offset_hud_previews_without_mutating_and_cancel_clears(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    original = [list(entity.points) for entity in v._entities]

    v._prompt_offset_selected()
    assert v._operation_preview_polys
    assert [entity.points for entity in v._entities] == original
    v._hud_prompt_edit.setText("3")
    preview_width = bbox(v._operation_preview_polys[0])[2] - bbox(v._operation_preview_polys[0])[0]
    assert preview_width == pytest.approx(16.0, abs=0.5)
    v._dismiss_hud_prompt()
    assert not v._operation_preview_polys
    assert [entity.points for entity in v._entities] == original


# ── open / close / explode / merge ───────────────────────────────────────────


def test_close_and_open_polylines(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]])
    v.set_selection([v._entities[0].id])
    assert v.close_selected_polylines() == 1
    pts = v._entities[0].points
    assert pts[0] == pts[-1]
    assert v.open_selected_polylines() == 1
    pts = v._entities[0].points
    assert pts[0] != pts[-1]


def test_explode_then_merge_rectangle(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert v.explode_selected_to_segments() == 4
    assert v.poly_count == 4
    v.select_all()
    assert v.merge_selected_segments_to_objects() >= 1
    assert v.poly_count == 1
    pts = v._entities[0].points
    assert pts[0] == pts[-1]  # closed again
    assert len(set(map(tuple, pts))) == 4


def test_merge_preserves_source_layer_and_tree_identity(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0)], [(10.0, 0.0), (20.0, 0.0)]])
    for entity in v._entities:
        entity.layer = "Engrave"
    first_id = v._entities[0].id
    v.set_active_layer("Engrave")
    v.set_selection([v._entities[0].id, v._entities[1].id])

    assert v.merge_selected_segments_to_objects() == 1
    assert v.poly_count == 1
    assert v._entities[0].layer == "Engrave"
    assert v._entities[0].id == first_id


def test_crossing_draw_split_consumes_cutter_instead_of_leaving_middle_line(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 10.0)])
    v._draw_split_enabled = True

    assert v._selection_service._commit_drawn_polyline([(-5.0, 5.0), (15.0, 5.0)], primitive="line")
    assert v.poly_count == 2
    assert all(v._is_poly_closed(entity.points) for entity in v._entities)


def test_internal_short_line_does_not_invisibly_extend_and_split_region(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 10.0)])
    v._draw_split_enabled = True

    assert v._selection_service._commit_drawn_polyline([(3.0, 5.0), (7.0, 5.0)], primitive="line")
    assert v.poly_count == 2
    assert sum(v._is_poly_closed(entity.points) for entity in v._entities) == 1


def test_partial_region_intersection_keeps_line_and_does_not_cut(qapp):
    original = square(0.0, 0.0, 10.0)
    v = make_view(qapp, [original])
    v._draw_split_enabled = True

    assert v._selection_service._commit_drawn_polyline([(5.0, 5.0), (15.0, 5.0)], primitive="line")
    assert v.poly_count == 2
    assert v._entities[0].points == original
    assert v._entities[1].points == [(5.0, 5.0), (15.0, 5.0)]


def test_closed_shape_tool_carves_existing_region(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 10.0)])
    v._draw_split_enabled = True
    v._draw_primitive = "rectangle"
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = (3.0, 3.0)
    v._draw_shape_cursor_w = (7.0, 7.0)

    assert v._draw_ops._commit_shape_preview()
    # Outer boundary, carved inner boundary, and the retained editable cutter.
    assert v.poly_count == 3
    assert all(v._is_poly_closed(entity.points) for entity in v._entities)
    sel_entity = next(e for e in v._entities if e.id in v._sel)
    assert sel_entity.kind == "rectangle"


def test_circle_tool_carves_existing_region(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 20.0)])
    v._draw_split_enabled = True
    v._draw_primitive = "circle"
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = (10.0, 10.0)
    v._draw_shape_cursor_w = (14.0, 10.0)

    assert v._draw_ops._commit_shape_preview()
    assert v.poly_count == 3
    assert all(v._is_poly_closed(entity.points) for entity in v._entities)
    sel_entity = next(e for e in v._entities if e.id in v._sel)
    assert sel_entity.kind == "circle"


def test_quick_drag_circle_also_carves_existing_region(qapp):
    v = make_canvas(qapp, [square(0.0, 0.0, 20.0)])
    start = v._w2c(6.0, 6.0)
    end = v._w2c(14.0, 14.0)
    v._draw_split_enabled = True
    v._shape_drag_active = True
    v._shape_drag_mode = "circle"
    v._shape_start_w = (6.0, 6.0)
    v._shape_start_c = QPoint(round(start[0]), round(start[1]))

    v._finish_shape_drag(QPoint(round(end[0]), round(end[1])))

    assert v.poly_count == 3
    assert all(v._is_poly_closed(entity.points) for entity in v._entities)
    sel_entity = next(e for e in v._entities if e.id in v._sel)
    assert sel_entity.kind == "circle"


def test_closed_bezier_carves_existing_region(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 20.0)])
    v._draw_split_enabled = True
    v._pen_pts = [(6.0, 6.0), (14.0, 6.0), (14.0, 14.0), (6.0, 14.0)]
    v._pen_tangents = [(0.0, 0.0)] * 4

    assert v._selection_service._finish_pen(close=True)
    assert v.poly_count == 3
    sel_entity = next(e for e in v._entities if e.id in v._sel)
    assert sel_entity.kind == "bezier"


def test_construction_bezier_never_cuts(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 20.0)])
    v._draw_split_enabled = True
    v._draw_construction_mode = True
    v._pen_pts = [(6.0, 6.0), (14.0, 6.0), (14.0, 14.0), (6.0, 14.0)]
    v._pen_tangents = [(0.0, 0.0)] * 4

    assert v._selection_service._finish_pen(close=True)
    assert v.poly_count == 2
    assert v._entities[0].points == square(0.0, 0.0, 20.0)
    assert v._entities[1].construction


def test_compound_ring_cutter_preserves_center_island(qapp):
    from src.backend.cad.geometry import build_circle_poly

    v = make_view(qapp, [square(-20.0, -20.0, 40.0)])
    outer = build_circle_poly(0.0, 0.0, 10.0)
    inner = build_circle_poly(0.0, 0.0, 5.0)

    changed, count = v._editing._carve_geometry_with_shapes([outer, inner])
    assert changed and count == 1
    # Remaining material is represented by the outside boundary, the annular
    # cut boundary, and the preserved center island boundary.
    assert v.poly_count == 3


def test_enclosing_shape_does_not_erase_contained_regions(qapp):
    inner_a = square(2.0, 2.0, 3.0)
    inner_b = square(10.0, 10.0, 4.0)
    v = make_view(qapp, [inner_a, inner_b])
    v._draw_split_enabled = True
    enclosing = square(0.0, 0.0, 20.0)

    assert v._selection_service._commit_drawn_polyline(enclosing, primitive="polyline", close=True)
    assert v.poly_count == 3
    assert v._entities[0].points == inner_a
    assert v._entities[1].points == inner_b
    assert v._entities[2].points == enclosing
    assert v._sel == {v._entities[2].id}


@pytest.mark.parametrize(
    ("primitive", "points", "minimum_samples"),
    [
        ("arc", [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)], 100),
        ("spline", [(0.0, 0.0), (5.0, 8.0), (10.0, 0.0)], 100),
    ],
)
def test_curved_cutters_use_rendered_curve_not_control_polygon(
    qapp, monkeypatch, primitive, points, minimum_samples
):
    v = make_view(qapp, [])
    v._draw_split_enabled = True
    captured = []

    def capture(cutter):
        captured.extend(cutter)
        return False, 0, 0

    monkeypatch.setattr(v, "_split_geometry_with_line", capture)
    assert v._selection_service._commit_drawn_polyline(points, primitive=primitive)
    assert len(captured) >= minimum_samples


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
    assert sorted(v.get_selected_ids()) == sorted([v._entities[0].id, v._entities[1].id])


def test_edit_mode_band_select_does_not_require_shift(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("edit")
    drag_world(v, -5.0, -5.0, 15.0, 15.0)  # encloses square(0, 0) only, no modifier
    first_entity_id = v._entities[0].id
    assert {eid for eid, _ in v._edit_selected_verts} == {first_entity_id}
    assert len(v._edit_selected_verts) >= 4


def test_edit_mode_multi_vertex_delete_does_not_corrupt_small_polygon(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_mode("edit")
    drag_world(v, -5.0, -5.0, 15.0, 15.0)  # band-select all 4 corners, no shift
    assert len(v._edit_selected_verts) >= 4
    key(v, Qt.Key.Key_Delete)
    pts = v._entities[0].points
    # Deleting every corner of a quad must not strip it below a valid
    # closed triangle (3 unique points + duplicated closing point).
    assert len(pts) >= 4
    assert pts[0] == pts[-1]


def test_edit_mode_delete_vertex_keeps_open_polyline_open(qapp):
    open_line = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (20.0, 10.0)]
    v = make_view(qapp, [open_line])
    v.set_mode("edit")
    click_world(v, 10.0, 0.0)  # select the interior vertex at index 1
    key(v, Qt.Key.Key_Delete)
    pts = v._entities[0].points
    assert len(pts) == 3
    assert pts[0] != pts[-1]


# ── modes / misc ─────────────────────────────────────────────────────────────


def test_mode_switching_resets_interaction_state(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_mode("draw")
    click_world(v, 100.0, 100.0)
    v.set_mode("select")
    assert v._draw_pts == []
    assert v.get_mode() == "select"


def test_scale_toggle(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.toggle_measure()
    assert v._measure_mode
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    assert v._measure_locked
    v.toggle_measure()
    assert not v._measure_mode


def test_visible_precision_tool_buttons_activate_scale_and_angular_dimension(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.show()
    qapp.processEvents()
    v.grab()  # force chrome painting and hit-rectangle layout

    mx1, my1, mx2, my2 = v._mbtn_rect
    click(v, (mx1 + mx2) / 2, (my1 + my2) / 2)
    assert v._measure_mode

    ax1, ay1, ax2, ay2 = v._adbtn_rect
    click(v, (ax1 + ax2) / 2, (ay1 + ay2) / 2)
    assert not v._measure_mode
    assert v._dimension_mode
    assert v._dimension_kind == "angle"


def test_scale_by_reference_affects_only_selection_and_keeps_first_point_fixed(qapp):
    from PySide6.QtWidgets import QLineEdit

    v = make_view(qapp, [square(10, 0), square(100, 0)])
    v.set_selection([v._entities[0].id])
    v._measure_anchor = (10.0, 0.0)
    v._measure_end = (20.0, 0.0)
    v._measure_edit = QLineEdit(v)
    v._measure_edit.setText("20")

    v._apply_measure_scale()

    assert v._entities[0].points[0] == pytest.approx((10.0, 0.0))
    assert v._entities[0].points[1] == pytest.approx((30.0, 0.0))
    assert v._entities[1].points == square(100, 0)
    assert v.undo()
    assert v._entities[0].points == square(10, 0)


def test_scale_by_reference_does_not_fall_back_to_everything_for_locked_selection(qapp):
    v = make_view(qapp, [square(0, 0), square(20, 0)])
    v.set_locked_ids([v._entities[0].id])
    v.set_selection([v._entities[0].id])

    assert not v.scale_by_reference(2.0, (0.0, 0.0))
    assert v._entities[0].points == square(0, 0)
    assert v._entities[1].points == square(20, 0)


def test_scale_rejects_zero_length_reference_without_locking(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.toggle_measure()
    click_world(v, 0.0, 0.0)
    click_world(v, 0.0, 0.0)

    assert not v._measure_locked
    assert v._measure_end is None


def test_scale_right_click_steps_back_before_closing_tool(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.toggle_measure()
    click_world(v, 0.0, 0.0)
    assert v._measure_anchor is not None

    v._rightclick_cb(*v._w2c(0.0, 0.0))
    assert v._measure_mode
    assert v._measure_anchor is None
    v._rightclick_cb(*v._w2c(0.0, 0.0))
    assert not v._measure_mode


def test_measure_editor_stays_inside_canvas_at_edge(qapp):
    v = make_view(qapp, size=(320, 220))
    v._measure_anchor = v._c2w(300, 205)
    v._measure_end = v._c2w(318, 218)

    v._show_measure_edit()

    edit = v._measure_edit
    assert edit is not None
    assert edit.x() >= 8 and edit.y() >= 8
    assert edit.x() + edit.width() <= v.width() - 8
    assert edit.y() + edit.height() <= v.height() - 8


def test_selection_dimension_editor_stays_inside_canvas_at_edge(qapp):
    from PySide6.QtCore import QRectF

    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0)]], size=(320, 220))
    v.set_selection([v._entities[0].id])

    v._show_sel_dim_editor("l", QRectF(305, 210, 40, 18))

    edit = v._sel_dim_edit
    assert edit is not None
    assert edit.x() + edit.width() <= v.width() - 8
    assert edit.y() + edit.height() <= v.height() - 8


def test_size_hud_stays_grouped_and_inside_canvas(qapp):
    c = make_canvas(qapp, [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]])
    c.resize(320, 220)
    c.set_selection([c._entities[0].id])
    c._ox = 310
    c._oy = 210

    c._show_size_hud()

    assert c._size_w_edit is not None and c._size_h_edit is not None
    assert c._size_w_edit.y() == c._size_h_edit.y()
    assert c._size_h_edit.x() - c._size_w_edit.x() == 106
    assert c._size_w_edit.x() >= 8 and c._size_w_edit.y() >= 8
    assert c._size_h_edit.x() + c._size_h_edit.width() <= c.width() - 8


def test_radial_menu_shifts_inward_near_canvas_edge(qapp):
    c = make_canvas(qapp, size=(500, 400))
    c._cursor_wx, c._cursor_wy = c._c2w(3, 397)

    c._toggle_radial_menu()

    outer, _inner = c._radial_menu._radial_geometry(len(c._radial_tools))
    center = c._radial_center_c
    assert center.x() - outer >= 0
    assert center.y() + outer <= c.height()


def test_escape_exits_idle_measure_mode(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.toggle_measure()
    assert v._measure_mode
    key(v, Qt.Key.Key_Escape)
    assert not v._measure_mode


def test_escape_exits_locked_scale_mode_even_when_target_input_has_focus(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.toggle_measure()
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    assert v._measure_locked
    assert v._measure_edit is not None
    v._measure_edit.setFocus()

    key(v, Qt.Key.Key_Escape)

    assert not v._measure_mode
    assert not v._measure_locked
    assert v._measure_anchor is None
    assert v._measure_end is None
    assert v._measure_edit is None


def test_status_summary_counts(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id, v._entities[2].id])
    s = v.get_status_summary()
    assert s["object_count"] == 3
    assert s["selected_count"] == 2


def test_zoom_helpers(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.fit()
    z0 = v.get_zoom_percent()
    v._zoom_by(2.0)
    assert v.get_zoom_percent() == pytest.approx(z0 * 2, rel=0.05)
    v.set_selection([v._entities[0].id])
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
    v.set_selection([v._entities[0].id])
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
    key(v, Qt.Key.Key_P)
    assert v.get_mode() == "pan"
    key(v, Qt.Key.Key_P)
    assert v.get_mode() == "select"


def test_pan_tool_left_drag_moves_view_without_editing_geometry(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id])
    original_polys = v.get_polylines_state()
    original_selection = v.get_selected_ids()
    ox, oy = v._ox, v._oy

    v.set_mode("pan")
    press(v, 100, 120)
    move(v, 135, 145)
    release(v, 135, 145)

    assert (v._ox, v._oy) == pytest.approx((ox + 35, oy + 25))
    assert v.get_polylines_state() == original_polys
    assert v.get_selected_ids() == original_selection
    assert v._lmb_prev is None


def test_command_registry_group_shortcut(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id, v._entities[1].id])
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
    """ "Q" must resolve through the canvas Command registry (so it shows up
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
        (6, "draw", "bezier"),
    ],
)
def test_radial_menu_wedges_are_the_draw_primitive_tools(
    qapp, idx, expected_mode, expected_primitive
):
    """The wheel is a shape-tool picker now, not a duplicate of D/E/M/F —
    each wedge switches to draw mode with the matching primitive (bezier
    pen included — it's a draw primitive, not its own mode), not the old
    mode-toggle/quick-shape/size actions."""
    c = make_canvas(qapp, THREE_SQUARES)
    c._execute_radial_action(idx)
    assert c.get_mode() == expected_mode
    if expected_primitive is not None:
        assert c._draw_primitive == expected_primitive


def test_radial_menu_opens_and_dismisses_from_any_mode(qapp):
    """Previously the menu only opened in select mode (its press/move/paint
    lived on DxfSelectTool); it must now work from draw/edit too, since
    it lives at the DxfCanvas level ahead of tool dispatch."""
    for mode in ("draw", "edit", "select"):
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
    assert c.get_mode() == "draw" and c._draw_primitive == "bezier"


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
    from src.core.settings import DEFAULT_RADIAL_MENU_TOOLS

    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(["canvas.circle", "canvas.arc"])  # only 2 — below the minimum
    assert c._radial_tools == list(DEFAULT_RADIAL_MENU_TOOLS)


def test_radial_menu_tools_drops_unknown_and_dedupes(qapp):
    c = make_canvas(qapp, THREE_SQUARES)
    c.set_radial_menu_tools(["canvas.circle", "bogus", "canvas.circle", "canvas.arc", "mode.pen"])
    assert c._radial_tools == ["canvas.circle", "canvas.arc", "mode.pen"]


def test_radial_menu_label_width_budget_never_exceeds_the_disc(qapp):
    """The chord-based width cap _paint_radial_menu elides labels against
    must itself never claim more room than the disc actually has — this is
    the guarantee that stops a long label (e.g. "Duplicate with Offset")
    from spilling past the wheel's outer edge."""
    c = make_canvas(qapp, THREE_SQUARES)
    outer = c._radial_menu._RADIAL_OUTER
    cy = 300.0
    for dy in (0.0, outer * 0.5, outer * 0.95, outer, outer * 1.5):
        chord_half = c._radial_menu._radial_chord_half(cy + dy, cy, outer)
        assert 0.0 <= chord_half <= outer
    # Directly above/below center the full diameter is available; near the
    # top/bottom pole it shrinks to ~0 rather than going negative or over.
    assert c._radial_menu._radial_chord_half(cy, cy, outer) == pytest.approx(outer)
    assert c._radial_menu._radial_chord_half(cy + outer, cy, outer) == pytest.approx(0.0, abs=1e-6)


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


def test_command_history_targets_only_changed_entities(qapp):
    """A move command references only the moved entity, not the document."""
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id])
    v._nudge_selected(1.0, 0.0)
    history = v._canvas_service.documents.history
    assert len(history._undo_commands) == 1
    command, inverse = history._undo_commands[0]
    assert command.entity_ids == inverse.entity_ids
    assert command.entity_ids == (v._entities[0].id,)


def test_undo_after_load_is_unavailable(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[0].id])
    v.delete_selected()
    v.load([square(0, 0)])
    assert not v.undo()  # fresh document, fresh history


def test_undo_redo_deep_sequence(qapp):
    v = make_view(qapp, [square(0, 0)])
    for i in range(5):
        v.set_selection([v._entities[0].id])
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
    assert v.get_selected_ids() == [v._entities[0].id]


def test_marquee_window_requires_full_enclosure(qapp):
    v = make_view(qapp, THREE_SQUARES)
    # left→right drag covering all of square 0 but only half of square 1
    drag_world(v, -5.0, -5.0, 36.0, 15.0)
    assert v.get_selected_ids() == [v._entities[0].id]


def test_marquee_crossing_selects_touched(qapp):
    v = make_view(qapp, THREE_SQUARES)
    # right→left drag covering all of square 0 and part of square 1
    drag_world(v, 36.0, 15.0, -5.0, -5.0)
    assert sorted(v.get_selected_ids()) == sorted([v._entities[0].id, v._entities[1].id])


def test_marquee_crossing_hits_segment_without_vertices(qapp):
    # long horizontal line; marquee crosses its middle, no endpoint inside
    v = make_view(qapp, [[(0.0, 0.0), (100.0, 0.0)]])
    drag_world(v, 55.0, 10.0, 45.0, -10.0)  # right→left box over the middle
    assert v.get_selected_ids() == [v._entities[0].id]
    v.deselect_all()
    # window drag over the middle must NOT select (not fully enclosed)
    drag_world(v, 45.0, -10.0, 55.0, 10.0)
    assert v.get_selected_ids() == []


def test_marquee_pulls_whole_group(qapp):
    v = make_view(qapp, THREE_SQUARES)
    v.set_selection([v._entities[1].id, v._entities[2].id])
    v._group_selected()
    v.deselect_all()
    drag_world(v, 45.0, 15.0, 25.0, -5.0)  # crossing box over square 1 only
    assert sorted(v.get_selected_ids()) == sorted([v._entities[1].id, v._entities[2].id])


def test_hover_pre_highlight_tracks_target(qapp):
    v = make_view(qapp, THREE_SQUARES)
    cx, cy = v._w2c(5.0, 0.0)
    move(v, cx, cy, button=Qt.MouseButton.NoButton)
    assert v._hover_poly == v._entities[0].id
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
    v.set_selection([v._entities[0].id])
    _paint_once(v)
    handles = dict(v._gizmo_handle_rects)
    assert set(handles) == {"nw", "n", "ne", "e", "se", "s", "sw", "w"}
    assert all(rect.width() >= 28 and rect.height() >= 28 for rect in handles.values())
    assert v._gizmo_rotate_rect.width() >= 32
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


def test_hud_prompt_anchors_near_world_cursor(qapp):
    v = make_view(qapp, [square(0, 0)])
    v._cursor_wx, v._cursor_wy = 5.0, 5.0
    anchor_x, anchor_y = v._w2c(5.0, 5.0)
    v._show_hud_prompt("Value", 1.0, lambda _value: None, is_length=False)
    edit = v._hud_prompt_edit
    assert edit is not None
    assert edit.x() == pytest.approx(anchor_x + 20, abs=1.0)
    assert edit.y() == pytest.approx(anchor_y + 18, abs=1.0)


def test_transient_feedback_anchors_to_world_cursor(qapp):
    v = make_view(qapp, [square(0, 0)])
    v._cursor_wx, v._cursor_wy = 5.0, 5.0
    expected = v._w2c(5.0, 5.0)
    v._show_flash("Updated", 1000)
    assert v._flash_anchor_c == pytest.approx(expected)


def test_handle_scale_corner_keeps_anchor(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
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
    v.set_selection([v._entities[0].id])
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


def test_double_click_inside_connected_edges_selects_enclosed_profile(qapp):
    edges = [
        [(0.0, 0.0), (10.0, 0.0)],
        [(10.0, 0.0), (10.0, 10.0)],
        [(10.0, 10.0), (0.0, 10.0)],
        [(0.0, 10.0), (0.0, 0.0)],
    ]
    canvas = make_canvas(qapp, edges)
    cx, cy = canvas._w2c(5.0, 5.0)
    canvas.mouseDoubleClickEvent(_mouse_event(QEvent.Type.MouseButtonDblClick, cx, cy))
    assert canvas._sel == {canvas._entities[i].id for i in range(4)}


def test_alt_click_cycles_overlapping_geometry(qapp):
    canvas = make_canvas(
        qapp,
        [[(0.0, 0.0), (10.0, 0.0)], [(0.0, 0.0), (10.0, 0.0)]],
    )
    click_world(canvas, 5.0, 0.0, Qt.KeyboardModifier.AltModifier)
    first = set(canvas._sel)
    click_world(canvas, 5.0, 0.0, Qt.KeyboardModifier.AltModifier)
    second = set(canvas._sel)
    assert first == {canvas._entities[0].id}
    assert second == {canvas._entities[1].id}


# ── parametric text ──────────────────────────────────────────────────────────


def test_text_carries_params_and_rebuilds(qapp):
    from PySide6.QtGui import QFontDatabase

    v = make_view(qapp, [])
    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    n = v.add_text_at(10.0, 20.0, text="Hi", family=family, height_mm=8.0)
    if n == 0:
        pytest.skip("no usable font on offscreen platform")
    text_id = next(iter(v._sel))
    params = v.text_params_at(text_id)
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
    assert v.rebuild_text(text_id, {**params, "text": "Hello", "height_mm": 8.0})
    new_text_id = next(iter(v._sel))
    params_after_rebuild = v.text_params_at(new_text_id)
    assert params_after_rebuild is not None
    assert params_after_rebuild["text"] == "Hello"
    new_min_x = min(x for e in v._entities for x, _ in e.points)
    new_min_y = min(y for e in v._entities for _, y in e.points)
    assert new_min_x == pytest.approx(old_min_x, abs=0.5)
    assert new_min_y == pytest.approx(old_min_y, abs=0.5)
    assert v.undo()
    params_after_undo = v.text_params_at(next(iter(v._sel)))
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
    assert v.get_selected_ids() == [v._entities[0].id]
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
    v.set_selection([v._entities[0].id])
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
    v.set_selection([v._entities[0].id])
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


def test_multi_shape_gizmo_rotate_updates_parametric_geometry(qapp):
    v = make_view(qapp, [])
    v.set_entity_records(
        [
            {
                "points": square(0, 0),
                "kind": "rectangle",
                "meta": {"center": (5.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            },
            {
                "points": square(20, 0),
                "kind": "rectangle",
                "meta": {"center": (25.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            },
        ]
    )
    v.set_selection([v._entities[0].id, v._entities[1].id])
    assert v._start_gizmo_drag("rotate", 30.0, 5.0)
    v._apply_gizmo_drag(15.0, 20.0, Qt.KeyboardModifier.NoModifier)
    assert [entity.meta["rotation"] for entity in v._entities] == pytest.approx([90.0, 90.0])
    assert [entity.meta["center"] for entity in v._entities] == pytest.approx(
        [(15.0, -5.0), (15.0, 15.0)]
    )
    for entity in v._entities:
        assert bbox(v._flattened_points(entity.id)) == pytest.approx(bbox(entity.points))


def test_shift_snaps_gizmo_rotation_to_fifteen_degree_increment(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (10.0, 0.0)]])
    v.set_selection([v._entities[0].id])
    assert v._start_gizmo_drag("rotate", 10.0, 0.0)
    requested = math.radians(22.0)

    v._apply_gizmo_drag(
        5.0 + 5.0 * math.cos(requested),
        5.0 * math.sin(requested),
        Qt.KeyboardModifier.ShiftModifier,
    )

    (ax, ay), (bx, by) = v._entities[0].points
    actual = math.degrees(math.atan2(by - ay, bx - ax))
    assert actual == pytest.approx(15.0)


def test_multi_shape_nonuniform_gizmo_resize_keeps_transformed_geometry(qapp):
    v = make_view(qapp, [])
    v.set_entity_records(
        [
            {
                "points": square(0, 0),
                "kind": "rectangle",
                "meta": {"center": (5.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            },
            {
                "points": square(20, 0),
                "kind": "rectangle",
                "meta": {"center": (25.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            },
        ]
    )
    v.set_selection([v._entities[0].id, v._entities[1].id])
    assert v._start_gizmo_drag("scale-e", 30.0, 5.0)
    v._apply_gizmo_drag(60.0, 5.0, Qt.KeyboardModifier.NoModifier)
    assert [entity.kind for entity in v._entities] == ["polyline", "polyline"]
    assert all(entity.meta is None for entity in v._entities)
    assert bbox(v._entities[0].points) == pytest.approx((0.0, 0.0, 20.0, 10.0))
    assert bbox(v._entities[1].points) == pytest.approx((40.0, 0.0, 60.0, 10.0))


def test_rotated_rectangle_uses_local_gizmo_and_preserves_parameters(qapp):
    from src.backend.cad.geometry import build_rect_poly

    v = make_view(qapp, [])
    base_points = build_rect_poly(5, 5, 10, 6)
    angle = math.radians(45)
    rotated_points = [
        (
            5 + (x - 5) * math.cos(angle) - (y - 5) * math.sin(angle),
            5 + (x - 5) * math.sin(angle) + (y - 5) * math.cos(angle),
        )
        for x, y in base_points
    ]
    v.set_entity_records(
        [
            {
                "points": rotated_points,
                "kind": "rectangle",
                "meta": {"center": (5, 5), "width": 10, "height": 6, "rotation": 45},
            }
        ]
    )
    v.set_selection([v._entities[0].id])
    _paint_once(v)
    east = dict(v._gizmo_handle_rects)["e"].center()
    target_world = (5 + 15 / math.sqrt(2), 5 + 15 / math.sqrt(2))
    target = v._w2c(*target_world)
    press(v, east.x(), east.y())
    move(v, *target)
    release(v, *target)
    entity = v._entities[0]
    assert entity.kind == "rectangle"
    assert entity.meta["rotation"] == pytest.approx(45)
    assert entity.meta["width"] == pytest.approx(20, abs=0.2)


def test_command_guidance_tracks_active_command_step(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    assert "pick first point" in v.get_command_guidance()[0].lower()
    v._draw_pts = [(0, 0)]
    guidance, tone = v.get_command_guidance()
    assert "pick next point" in guidance.lower()
    assert "Enter finishes" in guidance
    assert tone == "accent"


def test_draw_inference_acquires_parallel_relationship(qapp):
    v = make_view(qapp, [[(0, 0), (10, 10)]])
    v.set_mode("draw")
    v._draw_pts = [(20, 20)]
    target = v._w2c(30, 30.3)
    event = _mouse_event(QEvent.Type.MouseMove, *target, button=Qt.MouseButton.NoButton)
    v.mouseMoveEvent(event)
    assert v._draw_snap_type == "parallel"
    assert v._draw_snap is not None
    assert v._draw_snap[0] - 20 == pytest.approx(v._draw_snap[1] - 20)


def test_draw_inference_acquires_existing_line_length(qapp):
    v = make_view(qapp, [[(0, 0), (10, 0)]])
    v.set_mode("draw")
    v._draw_pts = [(20, 20)]
    target = v._w2c(29.9, 20.0)
    event = _mouse_event(QEvent.Type.MouseMove, *target, button=Qt.MouseButton.NoButton)

    v.mouseMoveEvent(event)

    assert v._draw_snap_type == "equal_length"
    assert v._draw_snap is not None
    assert math.dist(v._draw_pts[-1], v._draw_snap) == pytest.approx(10.0)

    v.set_snap_equal_length(False)
    v.mouseMoveEvent(event)
    assert v._draw_snap_type != "equal_length"


def test_draw_endpoint_axis_alignment_is_independently_toggleable(qapp):
    v = make_view(qapp, [[(0, 0), (10, 0)]])
    v.set_mode("draw")
    v._draw_pts = [(20, 30)]
    target = v._w2c(10.1, 20.0)
    event = _mouse_event(QEvent.Type.MouseMove, *target, button=Qt.MouseButton.NoButton)

    v.mouseMoveEvent(event)
    assert v._draw_snap_type == "axis_x"
    assert v._draw_snap is not None
    assert v._draw_snap[0] == pytest.approx(10.0)

    v.set_snap_axis_alignment(False)
    v.mouseMoveEvent(event)
    assert v._draw_snap_type != "axis_x"


@pytest.mark.parametrize("explicit_type", ["vertex", "edge"])
def test_explicit_geometry_snap_outranks_inference_within_acquisition_radius(qapp, explicit_type):
    v = make_view(qapp, [[(0, 0), (10, 0)]])
    vertex_cx, vertex_cy = v._w2c(10.0, 0.0)
    pointer_cx = vertex_cx + 5.0
    pointer_cy = vertex_cy
    inferred_x, inferred_y = v._c2w(pointer_cx, pointer_cy)

    result = v._snap_engine._pick_better(
        pointer_cx,
        pointer_cy,
        (10.0, 0.0, explicit_type),
        (inferred_x, inferred_y, "equal_length"),
    )

    assert result is not None
    assert result[2] == explicit_type


@pytest.mark.parametrize(
    ("kind", "meta", "click_at"),
    [
        (
            "rectangle",
            {"center": (5.0, 5.0), "width": 10.0, "height": 10.0, "rotation": 0.0},
            (5.0, 0.0),
        ),
        (
            "circle",
            {"center": (5.0, 5.0), "radius": 5.0, "segments": 48},
            (10.0, 5.0),
        ),
    ],
)
def test_edit_double_click_adds_visible_vertex_to_procedural_shape(qapp, kind, meta, click_at):
    v = make_view(qapp, [])
    v.set_entity_records([{"points": square(0, 0), "kind": kind, "meta": meta}])
    before = len(v._flattened_points(v._entities[0].id))
    v.set_mode("edit")
    cx, cy = v._w2c(*click_at)
    event = _mouse_event(QEvent.Type.MouseButtonDblClick, cx, cy)
    v.mouseDoubleClickEvent(event)
    entity = v._entities[0]
    assert entity.kind == "polyline"
    assert entity.meta is None
    assert len(entity.points) == before + 1
    assert len(v._edit_selected_verts) == 1
    assert next(iter(v._edit_selected_verts))[0] == entity.id
    assert any(math.dist(point, click_at) < 0.2 for point in entity.points)
    assert v.undo()


def test_move_drag_snaps_by_shape_vertices(qapp):
    """Dragging by the shape's interior still snaps its corners to other
    shapes' vertices."""
    v = make_view(qapp, [square(0, 0), square(30, 0)])
    v.set_selection([v._entities[0].id])
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
    v.set_selection([v._entities[0].id])
    drag_world(v, 5.0, 0.0, 44.8, 3.0, steps=6)  # left edge near guide x=50...
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    # one of the square's edges landed exactly on the guide
    assert any(abs(edge - 50.0) < 1e-6 for edge in (x0, x1))


def test_smooth_selected_default_is_chaikin(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    before_count = len(v._entities[0].points)
    assert v.smooth_selected(iterations=1) == 1
    # Chaikin roughly doubles vertex count per pass on a closed shape.
    assert len(v._entities[0].points) > before_count * 1.5


def test_smooth_selected_gaussian_preserves_vertex_count(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    v.set_smoothing_method("gaussian")
    before_count = len(v._entities[0].points)
    assert v.smooth_selected(iterations=2) == 1
    assert len(v._entities[0].points) == before_count


def test_smooth_selected_catmull_rom_interpolates_original_vertices(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    v.set_smoothing_method("catmull_rom")
    original = [tuple(p) for p in v._entities[0].points[:-1]]  # drop closure dup
    assert v.smooth_selected(iterations=2) == 1
    result = [tuple(p) for p in v._entities[0].points]
    for ox, oy in original:
        assert any(math.hypot(rx - ox, ry - oy) < 1e-6 for rx, ry in result), (
            f"original vertex {(ox, oy)} not found in smoothed result"
        )


def test_set_smoothing_method_rejects_unknown_value(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_smoothing_method("bogus")
    assert v._smoothing_method == "chaikin"


def test_smooth_command_prompt_seeds_from_and_remembers_last_value(qapp):
    """Regression test: the Smooth HUD prompt used to always show a
    hardcoded "2", forcing the user to retype their preferred value every
    time. It must now seed from (and update) v._smooth_iterations."""
    from src.ui.canvas.commands import _smooth_selected

    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert v._smooth_iterations == 2

    _smooth_selected(v)
    assert v._hud_prompt_edit.text() == "2"
    v._hud_prompt_edit.setText("5")
    v._hud_prompt_edit.returnPressed.emit()
    assert v._smooth_iterations == 5

    # next time the prompt opens, it remembers "5" instead of "2".
    v.set_selection([v._entities[0].id])
    _smooth_selected(v)
    assert v._hud_prompt_edit.text() == "5"


def test_simplify_command_prompt_seeds_from_and_remembers_last_value(qapp):
    from src.ui.canvas.commands import _simplify_selected

    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert v._simplify_tolerance == pytest.approx(0.2)

    _simplify_selected(v)
    assert v._hud_prompt_edit.text() == "0.2"
    v._hud_prompt_edit.setText("1.5")
    v._hud_prompt_edit.returnPressed.emit()
    assert v._simplify_tolerance == pytest.approx(1.5)

    v.set_selection([v._entities[0].id])
    _simplify_selected(v)
    assert v._hud_prompt_edit.text() == "1.5"


def test_smooth_iterations_change_emits_persistence_signal(qapp):
    from src.ui.canvas.commands import _smooth_selected

    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    seen: list[int] = []
    v.smoothIterationsChanged.connect(seen.append)

    _smooth_selected(v)
    v._hud_prompt_edit.setText("7")
    v._hud_prompt_edit.returnPressed.emit()
    assert seen == [7]

    # the plain settings-apply path must NOT re-emit, or persisting loops.
    v.set_smooth_iterations(3)
    assert seen == [7]


def test_smooth_selected_chaikin_preserves_sharp_spike(qapp):
    """A deliberate spike (like a lettering serif) should survive a Chaikin
    pass even though ordinary right-angle corners on the same path do not."""
    spike = [(0, 0), (10, 0), (10, 10), (15, 10), (10, 15), (10, 25), (0, 25)]
    v = make_view(qapp, [spike])
    v.set_selection([v._entities[0].id])
    v.smooth_selected(iterations=1)
    result = [tuple(p) for p in v._entities[0].points]
    assert any(math.hypot(x - 15, y - 10) < 1e-6 for x, y in result)
    # the 90-degree corners are not spikes, so they should have been cut
    assert not any(math.hypot(x - 10, y - 10) < 1e-6 for x, y in result)


def test_smooth_selected_catmull_rom_decimates_dense_straight_runs(qapp):
    """Dense, mostly-straight input (typical of a hand/image trace) should
    not balloon into a fixed samples-per-segment multiple of the input
    point count — the near-collinear runs should get decimated back down."""
    dense = [(i * 0.5, 0.0) for i in range(30)] + [(14.5 + i * 0.5, i * 0.5) for i in range(1, 30)]
    dense.append(dense[0])  # close it so it round-trips through load()
    v = make_view(qapp, [dense])
    v.set_selection([v._entities[0].id])
    v.set_smoothing_method("catmull_rom")
    naive_upper_bound = (len(dense) - 1) * 8
    v.smooth_selected(iterations=2)
    assert len(v._entities[0].points) < naive_upper_bound / 4


def test_lasso_selection_picks_touched_geometry_once(qapp):
    v = make_view(qapp, [square(0, 0), square(30, 0)])
    v.arm_lasso_selection()
    corners = [v._w2c(x, y) for x, y in ((-2, -2), (12, -2), (12, 12), (-2, 12))]
    press(v, *corners[0])
    for point in corners[1:]:
        move(v, *point)
    release(v, *corners[0])
    assert v._sel == {v._entities[0].id}
    assert not v._lasso_select_enabled


def test_knife_splits_crossed_open_path_and_undo_restores_it(qapp):
    v = make_view(
        qapp,
        [[(0.0, -10.0), (0.0, 10.0)], [(20.0, -10.0), (20.0, 10.0)]],
    )
    original_ids = [entity.id for entity in v._entities]
    assert v.knife_cut((-5.0, 0.0), (5.0, 0.0))
    assert len(v._entities) == 3
    assert v._sel == {v._entities[0].id, v._entities[1].id}
    assert all(entity.kind == "polyline" for entity in v._entities)
    assert v._entities[0].id == original_ids[0]
    assert v._entities[1].id not in original_ids
    assert v._entities[2].id == original_ids[1]
    assert len({entity.id for entity in v._entities}) == 3
    v.undo()
    assert len(v._entities) == 2
    assert v._entities[0].points == [(0.0, -10.0), (0.0, 10.0)]
    assert [entity.id for entity in v._entities] == original_ids


def test_named_symbol_round_trips_in_view_state_and_inserts_at_cursor(qapp):
    source = make_view(qapp, [square(10, 20)])
    source.set_selection([source._entities[0].id])
    source.create_symbol_from_selection()
    source._hud_prompt_edit.setText("Badge")
    source._hud_prompt_edit.returnPressed.emit()
    state = source.get_view_state()
    assert "Badge" in state["symbols"]

    target = make_view(qapp, [])
    target.set_view_state(state)
    target._cursor_wx, target._cursor_wy = 50.0, 60.0
    target.insert_symbol()
    target._hud_prompt_edit.setText("badge")  # names are case-insensitive
    target._hud_prompt_edit.returnPressed.emit()
    assert len(target._entities) == 1
    xs = [point[0] for point in target._entities[0].points]
    ys = [point[1] for point in target._entities[0].points]
    assert min(xs) == pytest.approx(50.0)
    assert min(ys) == pytest.approx(60.0)


def test_symbols_support_direct_insertion_rename_and_delete(qapp):
    canvas = make_view(qapp, [square(0, 0)])
    canvas.set_selection([canvas._entities[0].id])
    canvas.create_symbol_from_selection()
    canvas._hud_prompt_edit.setText("Badge")
    canvas._hud_prompt_edit.returnPressed.emit()

    canvas._cursor_wx, canvas._cursor_wy = 30.0, 40.0
    assert canvas.insert_symbol_named("badge")
    assert canvas._last_operation_result.metadata == {"symbol": "Badge"}
    assert canvas.rename_symbol("Badge", "Marker")
    assert "Marker" in canvas._symbol_library and "Badge" not in canvas._symbol_library
    assert canvas.delete_symbol("Marker")
    assert not canvas._symbol_library


def test_repeat_last_command_replays_registry_action(qapp):
    from src.ui.canvas import commands

    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert commands.run(v, "edit.duplicate")
    assert len(v._entities) == 2
    # A non-repeatable view toggle must not replace the remembered edit.
    assert commands.run(v, "grid.toggle")
    assert commands.run(v, "edit.repeat_last")
    assert len(v._entities) == 3


def test_repeat_last_reuses_numeric_operation_parameters(qapp):
    from src.ui.canvas import commands

    v = make_view(qapp, [square(0, 0)])
    v.set_selection([v._entities[0].id])
    assert v.offset_selected(2.5) == 1
    first_offset_entity = v._entity_for_id(next(iter(v._sel)))
    assert first_offset_entity is not None
    first_offset_id = first_offset_entity.id

    assert commands.run(v, "edit.repeat_last")
    assert len(v._entities) == 3
    assert v._last_operation_result.metadata == {"distance": 2.5}
    assert first_offset_id not in v._last_operation_result.created_ids


def test_command_availability_accepts_registry_id_for_context_menus(qapp):
    from src.ui.canvas import commands

    v = make_canvas(qapp, [[(0, 0), (10, 0)]])
    v.set_selection([v._entities[0].id])
    assert commands.can_run(v, "path.reverse")
    assert not commands.can_run(v, "path.morph")


def test_context_menu_configuration_removes_optional_sections(qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu

    canvas = make_canvas(qapp, [])
    captured: list[QMenu] = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _point: captured.append(menu))
    canvas.set_context_menu_sections(["view"])
    canvas._rightclick_cb(300.0, 200.0)
    assert captured
    labels = [action.text() for action in captured[0].actions() if not action.isSeparator()]
    assert "Create shape" not in labels
    assert "Insert Symbol…" not in labels
    assert "Arrange" not in labels
    assert "Transform" not in labels
    assert any(label.startswith("Fit view") for label in labels)


def test_context_menu_use_as_outline_passes_selected_polylines(qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu

    from src.ui.canvas.dxf_canvas import DxfCanvas

    received: list[list[list[tuple[float, float]]]] = []
    canvas = DxfCanvas(on_send_selected_to_pattern=received.append)
    canvas.resize(800, 600)
    canvas.load([square(0, 0)])
    canvas.set_selection([canvas._entities[0].id])
    canvas.set_context_menu_sections(["share_diagnostics", "view"])
    captured: list[QMenu] = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _point: captured.append(menu))

    canvas._rightclick_cb(*canvas._w2c(5.0, 0.0))
    action = next(action for action in captured[0].actions() if action.text() == "Use as outline")
    action.trigger()

    assert received == [[square(0, 0)]]


def test_context_menu_builds_for_mixed_editor_states(qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu

    captured: list[QMenu] = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _point: captured.append(menu))
    canvas = make_canvas(qapp, [square(0, 0), [(20.0, 0.0), (30.0, 0.0)]])
    canvas._append_entity(
        [(40.0, 0.0), (50.0, 0.0)],
        kind="bezier",
        meta={
            "tangents": [(3.0, 0.0), (3.0, 0.0)],
            "handles_in": [(-3.0, 0.0), (-3.0, 0.0)],
            "handles_out": [(3.0, 0.0), (3.0, 0.0)],
            "node_types": ["symmetric", "symmetric"],
        },
    )
    canvas._entities[1].locked = True
    canvas._symbol_library["Badge"] = [{"polyline": square(0, 0)}]

    for selection, point in (
        ([], (100.0, 100.0)),
        ([0], canvas._w2c(5.0, 0.0)),
        ([1], canvas._w2c(25.0, 0.0)),
        ([0, 1], canvas._w2c(5.0, 0.0)),
        ([2], canvas._w2c(40.0, 0.0)),
    ):
        canvas.set_selection(selection)
        canvas._rightclick_cb(*point)

    assert len(captured) == 5
    bezier_labels = [action.text() for action in captured[-1].actions()]
    more = next(
        (action.menu() for action in captured[-1].actions() if action.text() == "More actions…"),
        None,
    )
    if more is not None:
        bezier_labels.extend(action.text() for action in more.actions())
    assert "Bézier node" in bezier_labels
    removed = {
        "Next View",
        "Previous View",
        "Geometry Preflight…",
        "Show Geometry Health",
        "Select by geometry",
        "Symbols",
        "Construct",
        "Path Direction & Sampling",
        "Recognize Parametric Shapes",
        "Fit to Curve…",
    }
    assert removed.isdisjoint(bezier_labels)
    visible_actions = list(captured[-1].actions())
    if more is not None:
        visible_actions.extend(more.actions())
    array = next(action.menu() for action in visible_actions if action.text() == "Array")
    array_labels = [action.text() for action in array.actions()]
    assert len(array_labels) == 2
    assert any("grid" in label.lower() for label in array_labels)
    assert any("radial" in label.lower() for label in array_labels)


def test_edit_mode_context_menu_exposes_corner_operations(qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu

    captured: list[QMenu] = []
    monkeypatch.setattr(QMenu, "popup", lambda menu, _point: captured.append(menu))
    canvas = make_canvas(qapp, [square(0, 0)])
    canvas.set_selection([canvas._entities[0].id])
    canvas.set_mode("edit")

    canvas._rightclick_cb(*canvas._w2c(0.0, 0.0))

    assert captured
    actions = captured[0].actions()
    more = next((action.menu() for action in actions if action.text() == "More actions…"), None)
    if more is not None:
        actions = [*actions, *more.actions()]
    corner = next(action.menu() for action in actions if action.text() == "Corner")
    labels = [action.text() for action in corner.actions()]
    assert any(label.startswith("Round Corner") for label in labels)
    assert any(label.startswith("Chamfer Corner") for label in labels)


def test_previous_and_next_view_restore_zoom_transform(qapp):
    canvas = make_canvas(qapp, [square(0, 0)])
    initial = canvas._view_transform()
    canvas._zoom_by(1.5)
    zoomed = canvas._view_transform()
    assert zoomed != initial
    assert canvas.previous_view()
    assert canvas._view_transform() == pytest.approx(initial)
    assert canvas.next_view()
    assert canvas._view_transform() == pytest.approx(zoomed)
