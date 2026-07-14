"""Tests for the canvas polymorphic geometry boundary."""

import pytest

from src.backend.geometry import build_polygon_poly
from src.backend.shapes import BezierShape, CircleShape, SlotShape
from src.ui.canvas.document import EntityRecord
from src.ui.canvas.geometry_model import (
    PolylineGeometry,
    ShapeGeometry,
    entity_shows_point_handles,
    geometry_for_entity,
    update_entity_parameter,
)


def test_generic_entity_uses_polyline_geometry():
    geometry = geometry_for_entity(EntityRecord(points=[(0.0, 0.0), (2.0, 0.0)]))
    assert isinstance(geometry, PolylineGeometry)
    assert geometry.tessellate() == [(0.0, 0.0), (2.0, 0.0)]


def test_circle_entity_uses_shape_geometry():
    record = EntityRecord(
        points=[],
        kind="circle",
        meta={"center": (2.0, 3.0), "radius": 4.0, "segments": 32},
    )
    geometry = geometry_for_entity(record)
    assert isinstance(geometry, ShapeGeometry)
    assert isinstance(geometry.shape, CircleShape)
    assert len(geometry.tessellate()) == 33


def test_bezier_entity_uses_bezier_shape_and_preserves_tangents():
    record = EntityRecord(
        points=[(0.0, 0.0), (10.0, 0.0)],
        kind="bezier",
        meta={"tangents": [(3.0, 4.0), (-3.0, 4.0)], "segments": 12},
    )
    geometry = geometry_for_entity(record)
    assert isinstance(geometry, ShapeGeometry)
    assert isinstance(geometry.shape, BezierShape)
    assert len(geometry.tessellate()) > len(record.points)


def test_shape_geometry_transform_updates_parametric_shape():
    geometry = geometry_for_entity(
        EntityRecord(points=[], kind="circle", meta={"center": (1.0, 2.0), "radius": 3.0})
    )
    assert isinstance(geometry, ShapeGeometry)
    geometry.translate(4.0, -1.0)
    assert geometry.shape.center == (5.0, 1.0)  # type: ignore[attr-defined]


def test_polygon_parameter_updates_through_shape_behavior():
    entity = EntityRecord(
        points=[],
        kind="polygon",
        meta={"center": (2.0, 3.0), "radius": 4.0, "sides": 6, "rotation": 15.0},
    )
    assert update_entity_parameter(entity, "sides", 8)
    assert entity.meta is not None
    assert entity.meta["sides"] == 8
    assert entity.meta["rotation"] == 15.0
    assert len(entity.points) == 9


def test_ellipse_parameter_update_preserves_rotation():
    entity = EntityRecord(
        points=[],
        kind="ellipse",
        meta={"center": (0.0, 0.0), "rx": 5.0, "ry": 2.0, "rotation": 37.0},
    )
    assert update_entity_parameter(entity, "ry", 3.0)
    assert entity.meta is not None
    assert entity.meta["rotation"] == 37.0
    assert entity.meta["ry"] == 3.0


def test_slot_entity_uses_slot_shape_not_a_dumb_polyline():
    # A drawn slot used to collapse into a generic polyline the instant it
    # was committed, permanently losing its editable length/width/rotation.
    record = EntityRecord(
        points=[],
        kind="slot",
        meta={"center": (0.0, 0.0), "length": 20.0, "width": 8.0, "rotation": 0.0},
    )
    geometry = geometry_for_entity(record)
    assert isinstance(geometry, ShapeGeometry)
    assert isinstance(geometry.shape, SlotShape)
    # Bounding box matches the length x width footprint (rounded ends
    # inscribed within it).
    x0, y0, x1, y1 = geometry.shape.bounds
    assert (x1 - x0, y1 - y0) == (20.0, 8.0)
    # Not point-handle-editable, same as circle/ellipse.
    assert entity_shows_point_handles(record) is False


def test_slot_parameter_update_and_round_trip():
    entity = EntityRecord(
        points=[],
        kind="slot",
        meta={"center": (1.0, 2.0), "length": 20.0, "width": 8.0, "rotation": 10.0},
    )
    assert update_entity_parameter(entity, "length", 30.0)
    assert entity.meta is not None
    assert entity.meta["length"] == 30.0
    assert entity.meta["width"] == 8.0
    assert entity.meta["rotation"] == 10.0

    geometry = geometry_for_entity(entity)
    assert isinstance(geometry, ShapeGeometry)
    geometry.translate(1.0, -1.0)
    assert geometry.shape.center == (2.0, 1.0)  # type: ignore[attr-defined]


def test_polygon_shape_snap_geometry_matches_stored_vertices():
    points = build_polygon_poly(4.0, 6.0, 10.0, 7)
    entity = EntityRecord(
        points=points,
        kind="polygon",
        meta={"center": (4.0, 6.0), "radius": 10.0, "sides": 7, "rotation": 0.0},
    )
    geometry = geometry_for_entity(entity)
    assert isinstance(geometry, ShapeGeometry)
    assert geometry.tessellate() == pytest.approx(points)
