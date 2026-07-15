"""Geometry tests for optional rounded-rectangle and star primitives."""

import math

import pytest

from src.backend.geometry import build_rounded_rect_poly, build_star_poly
from src.backend.shapes import RoundedRectangleShape, ShapeFactory, StarShape


def test_circle_and_ellipse_control_drags_preserve_parametric_metadata():
    from src.ui.canvas.document import EntityRecord
    from src.ui.canvas.geometry_model import move_entity_control_point

    circle = EntityRecord(
        points=[], kind="circle", meta={"center": (0, 0), "radius": 5, "segments": 48}
    )
    assert move_entity_control_point(circle, 17, (8, 0), displayed_point_count=49)
    assert circle.kind == "circle"
    assert circle.meta["radius"] == pytest.approx(8)

    ellipse = EntityRecord(
        points=[],
        kind="ellipse",
        meta={"center": (0, 0), "rx": 5, "ry": 3, "rotation": 0, "segments": 48},
    )
    assert move_entity_control_point(ellipse, 12, (0, 7), displayed_point_count=49)
    assert ellipse.kind == "ellipse"
    assert ellipse.meta["ry"] == pytest.approx(7)


def test_rounded_rectangle_is_closed_and_clamps_large_radius():
    points = build_rounded_rect_poly(0.0, 0.0, 20.0, 10.0, radius=99.0)
    assert points[0] == points[-1]
    assert min(x for x, _ in points) == -10.0
    assert max(x for x, _ in points) == 10.0
    assert min(y for _, y in points) == -5.0
    assert max(y for _, y in points) == 5.0


def test_star_has_alternating_radii_and_is_closed():
    points = build_star_poly(3.0, 4.0, 10.0, points=5, inner_ratio=0.4)
    assert len(points) == 11
    assert points[0] == points[-1]
    radii = [math.hypot(x - 3.0, y - 4.0) for x, y in points[:-1]]
    assert radii[::2] == pytest.approx([10.0] * 5)
    assert radii[1::2] == pytest.approx([4.0] * 5)


def test_new_primitives_round_trip_as_editable_shapes():
    rounded = ShapeFactory.from_meta_dict(
        "rounded_rectangle",
        [],
        {"center": (2, 3), "width": 20, "height": 10, "radius": 2},
    )
    assert isinstance(rounded, RoundedRectangleShape)
    assert rounded.set_parameter("radius", 4)
    assert rounded.to_meta_dict()[1]["radius"] == 4

    star = ShapeFactory.from_meta_dict(
        "star",
        [],
        {"center": (2, 3), "radius": 10, "points": 7, "inner_ratio": 0.4},
    )
    assert isinstance(star, StarShape)
    assert star.set_parameter("points", 9)
    assert star.to_meta_dict()[1]["points"] == 9
