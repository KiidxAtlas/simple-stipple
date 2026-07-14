import math

import pytest

from src.backend.recognition import recognize_polyline


def test_recognizes_rotated_rectangle():
    angle = math.radians(30)
    cosine, sine = math.cos(angle), math.sin(angle)
    points = []
    for x, y in [(-5, -2), (5, -2), (5, 2), (-5, 2), (-5, -2)]:
        points.append((x * cosine - y * sine + 3, x * sine + y * cosine + 4))
    result = recognize_polyline(points)
    assert result is not None
    assert result.kind == "rectangle"
    assert result.metadata["center"] == pytest.approx((3, 4))
    assert result.metadata["width"] == pytest.approx(10)


def test_recognizes_dense_circle_and_regular_polygon():
    circle = [
        (2 + 5 * math.cos(i * 2 * math.pi / 32), 3 + 5 * math.sin(i * 2 * math.pi / 32))
        for i in range(32)
    ]
    circle.append(circle[0])
    result = recognize_polyline(circle)
    assert result is not None and result.kind == "circle"

    hexagon = [
        (5 * math.cos(-math.pi / 2 + i * math.pi / 3), 5 * math.sin(-math.pi / 2 + i * math.pi / 3))
        for i in range(6)
    ]
    hexagon.append(hexagon[0])
    result = recognize_polyline(hexagon)
    assert result is not None and result.kind == "polygon"
    assert result.metadata["sides"] == 6


def test_rejects_irregular_closed_path():
    assert recognize_polyline([(0, 0), (5, 0), (4, 4), (0, 3), (0, 0)]) is None


def test_canvas_recognition_restores_editable_metadata(qapp):
    from tests.test_canvas_behavior import make_canvas

    rectangle = [(0, 0), (10, 0), (10, 4), (0, 4), (0, 0)]
    canvas = make_canvas(qapp, [rectangle])
    canvas.set_selection([0])
    assert canvas.recognize_selected_shapes() == 1
    entity = canvas._entities[0]
    assert entity.kind == "rectangle"
    assert entity.meta is not None
    assert entity.meta["width"] == pytest.approx(10)
    assert canvas.set_shape_param(0, "width", 20)
