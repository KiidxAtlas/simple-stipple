import math

import pytest

from src.backend.cad.coordinates import parse_coordinate


def test_absolute_relative_and_polar_coordinate_syntax():
    assert parse_coordinate("10,5", origin=(100, 100)) == (10, 5)
    assert parse_coordinate("@10,-5", origin=(100, 100)) == (110, 95)
    assert parse_coordinate("@20<30", origin=(2, 3)) == pytest.approx(
        (2 + 20 * math.cos(math.radians(30)), 3 + 20 * math.sin(math.radians(30)))
    )


def test_coordinate_parser_applies_display_unit_scale():
    assert parse_coordinate("@1,2", origin=(0, 0), scale=25.4) == pytest.approx((25.4, 50.8))


@pytest.mark.parametrize("value", ["", "10", "@4<", "4<90", "1,2,3"])
def test_invalid_coordinate_syntax_is_rejected(value):
    with pytest.raises(ValueError):
        parse_coordinate(value)


def test_canvas_coordinate_entry_adds_relative_draw_point(qapp):
    from tests.test_canvas_behavior import make_canvas

    canvas = make_canvas(qapp)
    canvas.set_mode("draw")
    canvas._draw_primitive = "polyline"
    canvas._draw_pts = [(10.0, 10.0)]
    canvas.show_coordinate_entry("@5,2")
    canvas._hud_prompt_edit.returnPressed.emit()
    assert canvas._draw_pts[-1] == pytest.approx((15.0, 12.0))


def test_canvas_coordinate_entry_moves_selection_absolute(qapp):
    from tests.test_canvas_behavior import make_canvas

    canvas = make_canvas(qapp, [[(1, 1), (3, 1)]])
    canvas.set_selection([0])
    canvas.show_coordinate_entry("10,20")
    canvas._hud_prompt_edit.returnPressed.emit()
    assert canvas._entities[0].points[0] == pytest.approx((10.0, 20.0))
