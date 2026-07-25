import math

import pytest

from src.backend.cad.construction import (
    angle_bisector,
    centerline,
    circumcircle,
    common_circle_tangents,
    tangents_from_point,
)
from tests.test_canvas_behavior import make_canvas


def test_circumcircle_and_bisector_geometry():
    circle = circumcircle((1, 0), (0, 1), (-1, 0))
    assert circle is not None
    assert circle[0] == pytest.approx((0, 0))
    assert circle[1] == pytest.approx(1)
    bisector = angle_bisector(((0, 0), (10, 0)), ((0, 0), (0, 10)))
    assert bisector is not None
    assert bisector[0] == pytest.approx((0, 0))
    assert bisector[1] == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))


def test_centerline_and_tangent_construction():
    assert centerline(((0, 0), (10, 0)), ((0, 4), (10, 4))) == ((0, 2), (10, 2))
    tangents = tangents_from_point((10, 0), (0, 0), 5)
    assert len(tangents) == 2
    for start, end in tangents:
        assert start == (10, 0)
        assert math.dist(end, (0, 0)) == pytest.approx(5)
    assert len(common_circle_tangents((0, 0), 2, (10, 0), 2)) == 4


def test_canvas_creates_infinite_line_ray_and_circle(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 0)]])
    canvas.set_selection([canvas._entities[0].id])
    assert canvas.construction_line_from_selection() == 1
    xline = canvas._entity_for_id(next(iter(canvas._sel)))
    assert xline is not None
    assert xline.kind == "xline" and xline.construction
    assert math.dist(*xline.points) > 1_000_000
    assert canvas._bbox() == (0, 0, 10, 0)

    canvas.set_selection([canvas._entities[0].id])
    assert canvas.construction_line_from_selection(ray=True) == 1
    ray = canvas._entity_for_id(next(iter(canvas._sel)))
    assert ray is not None
    assert ray.kind == "ray"

    canvas = make_canvas(qapp, [[(1, 0), (0, 1), (-1, 0)]])
    canvas.set_selection([canvas._entities[0].id])
    assert canvas.create_circle_through_three_points() == 1
    entity = canvas._entity_for_id(next(iter(canvas._sel)))
    assert entity is not None
    assert entity.kind == "circle" and entity.construction


def test_canvas_creates_derived_bisector_centerline_and_tangents(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 0)], [(0, 0), (0, 10)]])
    canvas.set_selection([canvas._entities[0].id, canvas._entities[1].id])
    assert canvas.create_angle_bisector() == 1

    canvas = make_canvas(qapp, [[(0, 0), (10, 0)], [(0, 4), (10, 4)]])
    canvas.set_selection([canvas._entities[0].id, canvas._entities[1].id])
    assert canvas.create_centerline() == 1
    entity = canvas._entity_for_id(next(iter(canvas._sel)))
    assert entity is not None
    assert entity.points == [(0, 2), (10, 2)]

    canvas = make_canvas(qapp)
    canvas.set_entity_records(
        [
            {"points": [], "kind": "circle", "meta": {"center": (0, 0), "radius": 2}},
            {"points": [], "kind": "circle", "meta": {"center": (10, 0), "radius": 2}},
        ]
    )
    canvas.set_selection([canvas._entities[0].id, canvas._entities[1].id])
    assert canvas.create_common_circle_tangents() == 4
