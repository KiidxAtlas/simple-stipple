"""Trim and extend tools."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.test_canvas_behavior import (
    click_world,
    key,  # noqa: E402
    make_view,
    square,
)


def cross():
    # horizontal + vertical line crossing at (0, 0)
    return [[(-10.0, 0.0), (10.0, 0.0)], [(0.0, -10.0), (0.0, 10.0)]]


def test_trim_hover_previews_exact_removed_piece(qapp):
    v = make_view(qapp, [
        [(-10, 0), (10, 0)],
        [(-5, -5), (-5, 5)],
        [(5, -5), (5, 5)],
    ])
    cx, cy = v._w2c(0, 0)
    v.preview_trim_at(cx, cy)
    assert len(v._operation_preview_polys) == 1
    assert v._operation_preview_polys[0][0] == pytest.approx((-5, 0))
    assert v._operation_preview_polys[0][-1] == pytest.approx((5, 0))


def test_trim_removes_clicked_piece(qapp):
    v = make_view(qapp, cross())
    v.set_mode("trim")
    click_world(v, 5.0, 0.0)  # right half of the horizontal line
    # horizontal line now ends at the intersection (0,0)
    pts = v._entities[0].points
    xs = sorted(x for x, _ in pts)
    assert xs[0] == pytest.approx(-10.0)
    assert xs[-1] == pytest.approx(0.0, abs=1e-6)
    assert v.undo()
    xs = sorted(x for x, _ in v._entities[0].points)
    assert xs[-1] == pytest.approx(10.0)


def test_trim_middle_piece_leaves_two(qapp):
    polys = cross() + [[(4.0, -10.0), (4.0, 10.0)]]  # second vertical at x=4
    v = make_view(qapp, polys)
    v.set_mode("trim")
    click_world(v, 2.0, 0.0)  # piece between x=0 and x=4
    assert v.poly_count == 4  # horizontal became two pieces
    lengths = sorted(
        abs(v._entities[i].points[-1][0] - v._entities[i].points[0][0]) for i in (0, 3)
    )
    assert lengths[0] == pytest.approx(6.0, abs=1e-6)  # x=4..10
    assert lengths[1] == pytest.approx(10.0, abs=1e-6)  # x=-10..0


def test_extend_reaches_next_shape(qapp):
    v = make_view(
        qapp,
        [[(0.0, 0.0), (5.0, 0.0)], [(20.0, -10.0), (20.0, 10.0)]],
    )
    v.set_mode("extend")
    click_world(v, 5.0, 0.0)  # near the open end at (5, 0)
    pts = v._entities[0].points
    assert pts[-1][0] == pytest.approx(20.0, abs=1e-6)
    assert v.undo()
    assert v._entities[0].points[-1][0] == pytest.approx(5.0)


def test_trim_mode_shortcut_and_escape(qapp):
    v = make_view(qapp, cross())
    key(v, Qt.Key.Key_K)
    assert v.get_mode() == "trim"
    key(v, Qt.Key.Key_Escape)
    assert v.get_mode() == "select"
    key(v, Qt.Key.Key_L)
    assert v.get_mode() == "extend"


def test_extend_works_clicking_anywhere_on_line(qapp):
    """Clicking the middle of an open polyline extends its nearer end."""
    v = make_view(
        qapp,
        [[(20.0, 0.0), (30.0, 0.0)], [(50.0, -10.0), (50.0, 10.0)]],
    )
    v.set_mode("extend")
    click_world(v, 27.0, 0.0)  # mid-line, closer to the right end
    assert v._entities[0].points[-1][0] == pytest.approx(50.0, abs=1e-6)


def test_r_rounds_hovered_corner_in_edit_mode(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_mode("edit")
    cx, cy = v._w2c(10.0, 0.0)
    from tests.test_canvas_behavior import move as move_ev

    move_ev(v, cx, cy, button=Qt.MouseButton.NoButton)
    assert v._hover_vert is not None
    key(v, Qt.Key.Key_R)
    assert v._hud_prompt_edit is not None  # inline radius prompt opened
    v._hud_prompt_edit.setText("2")
    v._hud_prompt_edit.returnPressed.emit()
    assert len(v._entities[0].points) > 5  # corner replaced by an arc


def test_rulers_toggle_moved_off_plain_r(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_rulers_visible(False)
    key(v, Qt.Key.Key_R)  # select mode: R no longer flips rulers
    assert not v._rulers_visible
    key(v, Qt.Key.Key_R, mods=Qt.KeyboardModifier.ControlModifier)
    assert v._rulers_visible
