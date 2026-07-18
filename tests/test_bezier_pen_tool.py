"""Bezier pen tool: build_bezier_poly geometry + interactive placement."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.test_canvas_behavior import click, drag_world, key, make_view  # noqa: E402


def _assert_points_approx(actual, expected, abs_tol=1e-6):
    assert len(actual) == len(expected)
    for (ax, ay), (ex, ey) in zip(actual, expected):
        assert ax == pytest.approx(ex, abs=abs_tol)
        assert ay == pytest.approx(ey, abs=abs_tol)


# ── build_bezier_poly (pure geometry) ───────────────────────────────────────


def test_straight_segment_when_tangents_are_zero():
    from src.backend.cad.geometry import build_bezier_poly

    poly = build_bezier_poly([(0.0, 0.0), (10.0, 0.0)], [(0.0, 0.0), (0.0, 0.0)])
    # A zero-handle cubic degenerates to the straight line — every sampled
    # point should sit exactly on segment y=0.
    assert all(y == pytest.approx(0.0, abs=1e-9) for _x, y in poly)
    assert poly[0] == pytest.approx((0.0, 0.0))
    assert poly[-1] == pytest.approx((10.0, 0.0))


def test_curve_bulges_toward_a_nonzero_tangent():
    from src.backend.cad.geometry import build_bezier_poly

    poly = build_bezier_poly([(0.0, 0.0), (10.0, 0.0)], [(0.0, 5.0), (0.0, 5.0)], segments=32)
    ys = [y for _x, y in poly]
    assert max(ys) > 0.5  # curve bulges up, away from the straight segment


def test_closed_flag_adds_a_return_segment():
    from src.backend.cad.geometry import build_bezier_poly

    anchors = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    tangents = [(0.0, 0.0)] * 3
    open_poly = build_bezier_poly(anchors, tangents, closed=False)
    closed_poly = build_bezier_poly(anchors, tangents, closed=True)
    assert len(closed_poly) > len(open_poly)
    assert closed_poly[-1] == pytest.approx(anchors[0], abs=1e-6)


def test_too_few_anchors_returns_input_unchanged():
    from src.backend.cad.geometry import build_bezier_poly

    assert build_bezier_poly([], []) == []
    assert build_bezier_poly([(1.0, 2.0)], [(0.0, 0.0)]) == [(1.0, 2.0)]


def test_independent_handles_control_each_side_without_forced_mirroring():
    from src.backend.cad.geometry import build_bezier_poly

    anchors = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    poly = build_bezier_poly(
        anchors,
        [],
        segments=16,
        handles_out=[(0.0, 6.0), (0.0, 0.0), (0.0, 0.0)],
        handles_in=[(0.0, 0.0), (0.0, 0.0), (0.0, -6.0)],
    )

    assert max(y for _x, y in poly[:17]) > 0.5
    assert min(y for _x, y in poly[16:]) < -0.5


# ── Interactive pen tool ─────────────────────────────────────────────────────


def test_plain_clicks_place_corner_anchors_with_zero_tangent(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    click(v, *v._w2c(0.0, 0.0))
    click(v, *v._w2c(10.0, 0.0))
    click(v, *v._w2c(10.0, 10.0))
    _assert_points_approx(v._pen_pts, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
    _assert_points_approx(v._pen_tangents, [(0.0, 0.0)] * 3)


def test_click_drag_places_a_smooth_anchor_with_a_handle(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    drag_world(v, 0.0, 0.0, 5.0, 5.0)  # drag well past the click threshold
    assert len(v._pen_pts) == 1
    tx, ty = v._pen_tangents[0]
    assert math.hypot(tx, ty) > 1.0  # a real handle was set, not (0, 0)


def test_enter_finalizes_curve_as_bezier_entity(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    click(v, *v._w2c(0.0, 0.0))
    click(v, *v._w2c(10.0, 0.0))
    click(v, *v._w2c(20.0, 10.0))
    key(v, Qt.Key.Key_Return)

    assert v.poly_count == 1
    ent = v._entities[0]
    assert ent.kind == "bezier"
    _assert_points_approx(ent.points, [(0.0, 0.0), (10.0, 0.0), (20.0, 10.0)])
    assert ent.meta is not None
    assert len(ent.meta["tangents"]) == 3
    assert len(ent.meta["handles_in"]) == 3
    assert len(ent.meta["handles_out"]) == 3
    assert ent.meta["node_types"] == ["corner", "corner", "corner"]
    # The tool resets for the next curve.
    assert v._pen_pts == []


def test_escape_cancels_without_creating_an_entity(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    click(v, *v._w2c(0.0, 0.0))
    click(v, *v._w2c(10.0, 0.0))

    key(v, Qt.Key.Key_Escape)
    assert v._pen_pts == []
    assert v.poly_count == 0


def test_bezier_entity_renders_via_build_bezier_poly(qapp):
    """A bezier entity's stored points are the sparse anchors; render.py
    must re-tessellate through meta['tangents'], not draw them as-is."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    drag_world(v, 0.0, 0.0, 0.0, 5.0)
    click(v, *v._w2c(20.0, 0.0))

    key(v, Qt.Key.Key_Return)
    ent = v._entities[0]
    assert len(ent.points) == 2  # sparse anchors only
    assert ent.meta is not None

    from src.backend.cad.geometry import build_bezier_poly

    tessellated = build_bezier_poly(ent.points, ent.meta["tangents"], segments=ent.meta["segments"])
    assert len(tessellated) > len(ent.points)  # actually a curve, not a line


def test_rotate_selected_bezier_also_rotates_its_tangents(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    drag_world(v, 0.0, 0.0, 5.0, 0.0)  # handle pointing along +X
    click(v, *v._w2c(10.0, 0.0))

    key(v, Qt.Key.Key_Return)
    v.set_mode("select")
    v.set_selection([0])

    meta = v._entities[0].meta
    assert meta is not None
    before = meta["tangents"][0]
    assert before == pytest.approx((5.0, 0.0), abs=0.01)

    assert v.rotate_selected(90.0)
    meta = v._entities[0].meta
    assert meta is not None
    after = meta["tangents"][0]
    # A +90 degree rotation of a vector pointing along +X now points +Y.
    assert after == pytest.approx((0.0, 5.0), abs=0.5)
    assert meta["handles_out"][0] == pytest.approx((0.0, 5.0), abs=0.5)


def test_bezier_node_modes_link_or_break_handle_pairs(qapp):
    v = make_view(qapp, [])
    v._append_entity(
        [(0.0, 0.0), (10.0, 0.0)],
        kind="bezier",
        meta={
            "tangents": [(3.0, 0.0), (3.0, 0.0)],
            "handles_out": [(3.0, 0.0), (3.0, 0.0)],
            "handles_in": [(-3.0, 0.0), (-3.0, 0.0)],
            "node_types": ["symmetric", "symmetric"],
            "segments": 16,
            "closed": False,
        },
    )

    assert v._set_bezier_handle(0, 0, "out", (0.0, 4.0))
    meta = v._entities[0].meta
    assert meta is not None
    assert meta["handles_out"][0] == pytest.approx((0.0, 4.0))
    assert meta["handles_in"][0] == pytest.approx((0.0, -4.0))

    assert v._set_bezier_handle(0, 0, "out", (5.0, 1.0), break_pair=True)
    assert meta["node_types"][0] == "corner"
    assert meta["handles_out"][0] == pytest.approx((5.0, 1.0))
    assert meta["handles_in"][0] == pytest.approx((0.0, -4.0))

    assert v.set_bezier_node_type(0, 0, "smooth")
    assert meta["node_types"][0] == "smooth"
    assert math.hypot(*meta["handles_in"][0]) == pytest.approx(4.0)


def test_polyline_family_bezier_entry_stays_in_draw_mode(qapp):
    """Picking "Bezier Pen" from the Polyline-family sidebar cycle is just
    another draw-mode primitive, like polyline/spline/arc — it must not
    switch to a separate mode or hide the draw sidebar."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    assert v.get_mode() == "draw"
    assert v._draw_primitive == "bezier"
    assert v._draw_sidebar_visible is True


def test_escape_in_bezier_exits_to_select_like_other_primitives(qapp):
    """Escape while sketching a bezier curve discards it and exits to
    select mode, matching Escape's behavior for every other draw
    primitive (polyline, arc, ...) rather than a bezier-specific carve-out."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("bezier")
    click(v, *v._w2c(0.0, 0.0))
    click(v, *v._w2c(10.0, 0.0))

    key(v, Qt.Key.Key_Escape)
    assert v._pen_pts == []
    assert v.poly_count == 0
    assert v.get_mode() == "select"
