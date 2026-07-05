"""Grid / radial array duplication (chained HUD prompts -> _paste_records)."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import make_view, square  # noqa: E402


def _submit(v, text: str):
    assert v._hud_prompt_edit is not None
    v._hud_prompt_edit.setText(text)
    v._hud_prompt_edit.returnPressed.emit()


def test_array_grid_creates_expected_copies_and_positions(qapp):
    v = make_view(qapp, [square(0, 0, 10.0)])
    v.set_selection([0])
    assert v.poly_count == 1

    v.array_duplicate_grid()
    _submit(v, "3")  # columns
    _submit(v, "2")  # rows
    _submit(v, "20")  # spacing (mm)

    # 3x2 grid = 6 cells, one of which is the original -> 5 new copies
    assert v.poly_count == 6
    origin_x0, origin_y0, _, _ = _bbox(v._entities[0].points)

    corners = {_bbox(e.points)[:2] for e in v._entities}
    expected = {
        (origin_x0 + col * 20.0, origin_y0 + row * 20.0)
        for row in range(2)
        for col in range(3)
    }
    for ex, ey in expected:
        assert any(
            abs(cx - ex) < 0.01 and abs(cy - ey) < 0.01 for cx, cy in corners
        ), f"missing grid cell at ({ex}, {ey})"


def test_array_grid_1x1_is_a_noop(qapp):
    v = make_view(qapp, [square(0, 0, 10.0)])
    v.set_selection([0])
    v.array_duplicate_grid()
    _submit(v, "1")
    _submit(v, "1")
    _submit(v, "10")
    assert v.poly_count == 1  # nothing duplicated, no stray undo step either
    assert not v.undo()


def test_array_radial_creates_expected_copies_and_positions(qapp):
    v = make_view(qapp, [square(0, 0, 10.0)])
    v.set_selection([0])

    v.array_duplicate_radial()
    _submit(v, "4")  # copies
    _submit(v, "50")  # radius (mm)

    assert v.poly_count == 4
    origin_x0, origin_y0, _, _ = _bbox(v._entities[0].points)
    corners = {_bbox(e.points)[:2] for e in v._entities}
    for i in range(1, 4):
        angle = 2.0 * math.pi * i / 4
        ex = origin_x0 + 50.0 * math.cos(angle)
        ey = origin_y0 + 50.0 * math.sin(angle)
        assert any(
            abs(cx - ex) < 0.01 and abs(cy - ey) < 0.01 for cx, cy in corners
        ), f"missing radial copy at ({ex}, {ey})"


def test_array_duplicate_undoes_as_one_step(qapp):
    v = make_view(qapp, [square(0, 0, 10.0)])
    v.set_selection([0])
    v.array_duplicate_grid()
    _submit(v, "2")
    _submit(v, "2")
    _submit(v, "15")
    assert v.poly_count == 4
    assert v.undo()
    assert v.poly_count == 1


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)
