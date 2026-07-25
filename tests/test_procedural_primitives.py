import math

import pytest

from src.backend.cad.primitives import (
    chamfered_star,
    dovetail_box,
    finger_joint_box,
    gear,
    keyhole,
    regular_polygon_from_edge,
    ring,
    rounded_star,
    spiral,
    superellipse,
    tabbed_panel,
    teardrop,
)
from tests.test_canvas_behavior import make_canvas


@pytest.mark.parametrize(
    "factory",
    [
        gear,
        superellipse,
        teardrop,
        keyhole,
        rounded_star,
        chamfered_star,
        finger_joint_box,
        dovetail_box,
        tabbed_panel,
    ],
)
def test_closed_procedural_primitives_are_valid_paths(factory):
    points = factory()
    assert len(points) >= 8
    assert points[0] == pytest.approx(points[-1])
    assert all(math.isfinite(value) for point in points for value in point)


def test_spiral_is_open_and_ring_has_opposite_wound_loops():
    points = spiral()
    assert len(points) > 100 and points[0] != points[-1]
    outer, inner = ring()
    assert outer[0] == outer[-1]
    assert inner[0] == inner[-1]

    def signed_area(path):
        return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(path, path[1:])) / 2

    assert signed_area(outer) * signed_area(inner) < 0


def test_canvas_procedural_creation_groups_multi_loop_ring(qapp):
    canvas = make_canvas(qapp)
    assert canvas.create_procedural_primitive("ring") == 2
    entity_ids = sorted(canvas._sel)
    entity_a = canvas._entity_for_id(entity_ids[0])
    entity_b = canvas._entity_for_id(entity_ids[1])
    assert entity_a is not None
    assert entity_b is not None
    assert entity_a.group == entity_b.group
    for index in entity_ids:
        ent = canvas._entity_for_id(index)
        assert ent is not None
        assert ent.kind == "ring"


@pytest.mark.parametrize(
    "primitive",
    [
        "gear",
        "spiral",
        "superellipse",
        "teardrop",
        "keyhole",
        "rounded_star",
        "chamfered_star",
        "finger_joint_box",
        "dovetail_box",
        "tabbed_panel",
    ],
)
def test_canvas_creates_each_procedural_primitive(qapp, primitive):
    canvas = make_canvas(qapp)
    assert canvas.create_procedural_primitive(primitive) == 1
    entity = canvas._entity_for_id(next(iter(canvas._sel)))
    assert entity is not None
    assert entity.kind == primitive
    assert entity.meta["generator"] == primitive


def test_regular_polygon_from_edge_uses_edge_and_requested_sides(qapp):
    points = regular_polygon_from_edge((0, 0), (10, 0), 5)
    assert len(points) == 6
    assert points[0] == pytest.approx((0, 0))
    assert points[1] == pytest.approx((10, 0))

    canvas = make_canvas(qapp, [[(0, 0), (10, 0)]])
    canvas.set_selection([canvas._entities[0].id])
    assert canvas.create_polygon_from_selected_edge(7) == 1
    selected_entity = canvas._entity_for_id(next(iter(canvas._sel)))
    assert selected_entity is not None
    assert len(selected_entity.points) == 8
