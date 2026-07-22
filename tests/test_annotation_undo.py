"""Guides and dimensions are undoable on the same stack as geometry.

Integration-level proof, driven through the real interaction handlers, that
annotation edits round-trip through undo/redo. Each of these would have failed
before guides/dimensions became document state: the mutations bypassed the
command system, so undo silently ignored them.
"""

import pytest

from tests.test_canvas_behavior import make_canvas, make_view, move, press, release
from tests.test_dimension_tool import _place_dimension


def _square(ox=0.0, oy=0.0, s=10.0):
    return [(ox, oy), (ox + s, oy), (ox + s, oy + s), (ox, oy)]


def test_drag_guide_from_ruler_is_undoable(qapp):
    c = make_canvas(qapp, [_square()])
    c.set_rulers_visible(True)
    press(c, 300.0, 10.0)
    move(c, 300.0, 200.0)
    release(c, 300.0, 200.0)
    assert len(c._guides) == 1

    assert c.undo()
    assert c._guides == []  # the whole drag-out gesture reverts as one step
    assert c.poly_count == 1  # geometry untouched

    assert c.redo()
    assert len(c._guides) == 1  # and comes back on redo


def test_moving_a_guide_is_undoable(qapp):
    c = make_canvas(qapp, [_square()])
    c.set_rulers_visible(True)
    press(c, 300.0, 10.0)
    move(c, 300.0, 200.0)
    release(c, 300.0, 200.0)
    original = c._guides[0][1]

    gy = c._w2c(0.0, original)[1]
    press(c, 300.0, gy)
    move(c, 300.0, 260.0)
    release(c, 300.0, 260.0)
    assert c._guides[0][1] != pytest.approx(original)

    assert c.undo()  # the move reverts on its own, guide stays
    assert len(c._guides) == 1
    assert c._guides[0][1] == pytest.approx(original)


def test_dimension_precision_change_is_undoable(qapp):
    v = make_view(qapp, [_square(0, 0, 30)])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    assert v._dimensions[0]["precision"] == 2

    v._set_dimension_precision(0, 5)
    assert v._dimensions[0]["precision"] == 5

    assert v.undo()
    assert v._dimensions[0]["precision"] == 2
