import math

import pytest

from src.backend.path_ops import (
    fit_circle,
    fit_line,
    morph_paths,
    resample_by_count,
    resample_by_spacing,
    reverse_path,
    set_closed_start,
)
from tests.test_canvas_behavior import make_canvas


def test_reverse_and_set_closed_start_preserve_closure():
    square = [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]
    reversed_square = reverse_path(square)
    assert reversed_square[0] == reversed_square[-1]
    assert reversed_square[1] == (2, 2)
    rotated = set_closed_start(square, 2)
    assert rotated[0] == (2, 2)
    assert rotated[-1] == rotated[0]


def test_resampling_is_uniform_and_preserves_endpoints():
    path = [(0, 0), (10, 0), (10, 10)]
    sampled = resample_by_count(path, 5)
    assert sampled == pytest.approx([(0, 0), (5, 0), (10, 0), (10, 5), (10, 10)])
    by_spacing = resample_by_spacing(path, 5)
    assert len(by_spacing) == 5
    assert by_spacing[0] == (0, 0) and by_spacing[-1] == (10, 10)


def test_line_and_circle_fitting():
    line = fit_line([(0, 0.1), (5, -0.1), (10, 0.05)])
    assert line is not None
    assert math.dist(*line) == pytest.approx(10, abs=0.1)
    circle_points = [
        (3 + 4 * math.cos(i * math.pi / 8), 2 + 4 * math.sin(i * math.pi / 8)) for i in range(16)
    ]
    circle = fit_circle(circle_points)
    assert circle is not None
    assert circle[0] == pytest.approx((3, 2))
    assert circle[1] == pytest.approx(4)


def test_canvas_reverse_resample_and_fit(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (5, 0), (10, 0.1)]])
    canvas.set_selection([0])
    assert canvas.reverse_selected_paths() == 1
    assert canvas._entities[0].points[0] == (10, 0.1)
    assert canvas.resample_selected_paths(7, by_count=True) == 1
    assert len(canvas._entities[0].points) == 7
    assert canvas.fit_selected_to_primitive("line") == 1
    assert canvas._entities[0].kind == "line"


def test_canvas_set_start_uses_cursor_nearest_vertex(qapp):
    square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    canvas = make_canvas(qapp, [square])
    canvas.set_selection([0])
    canvas._cursor_wx, canvas._cursor_wy = 9.9, 10.1
    assert canvas.set_selected_path_start()
    assert canvas._entities[0].points[0] == (10, 10)


def test_morph_paths_resamples_and_preserves_closed_topology():
    first = [(0, 0), (10, 0), (10, 10), (0, 0)]
    second = [(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)]
    result = morph_paths(first, second, 0.5)
    assert result[0] == result[-1]
    assert len(result) == 5


def test_canvas_morph_creates_new_path_as_one_operation(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 0)], [(0, 10), (10, 10), (20, 10)]])
    canvas.set_selection([0, 1])
    canvas.prompt_morph_selected_paths()
    canvas._hud_prompt_edit.setText("50")
    canvas._hud_prompt_edit.returnPressed.emit()
    assert len(canvas._entities) == 3
    assert canvas._entities[2].points == pytest.approx([(0, 5), (7.5, 5), (15, 5)])
    result = canvas._last_operation_result
    assert result.changed
    assert result.metadata == {"amount": 0.5}
    assert result.created_ids == result.selected_ids == (canvas._entities[2].id,)
    assert canvas.undo()
    assert len(canvas._entities) == 2
