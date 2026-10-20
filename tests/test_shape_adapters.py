"""Editing goes through Shape objects instead of reaching into ``meta``."""

from __future__ import annotations

import math

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.cad.shape_factory import ShapeFactory
from simple_stipple.core.document.geometry import (
    bezier_for_entity,
    demote_to_path,
    entity_local_extent,
    geometry_for_entity,
    write_bezier_handles,
)
from simple_stipple.core.document.kinds import EntityKind
from simple_stipple.core.document.model import EntityRecord


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _bezier_record(node_types: list[str]) -> EntityRecord:
    shape = ShapeFactory.bezier(
        control_points=[(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        tangents=[(2.0, 0.0), (3.0, 0.0), (2.0, 0.0)],
        node_types=node_types,
    )
    kind, meta = shape.to_meta_dict()
    return EntityRecord(points=list(shape.control_points), kind=EntityKind.coerce(kind), meta=meta)


def test_symmetric_handle_mirrors_its_partner() -> None:
    record = _bezier_record(["symmetric"] * 3)
    shape = bezier_for_entity(record)
    assert shape is not None
    assert shape.set_handle(1, "out", (10.0, 4.0))
    write_bezier_handles(record, shape)
    assert record.meta is not None
    assert record.meta["handles_out"][1] == (0.0, 4.0)
    assert record.meta["handles_in"][1] == (-0.0, -4.0)
    assert record.meta["tangents"] == record.meta["handles_out"]


def test_smooth_handle_keeps_partner_length_and_break_pair_makes_a_corner() -> None:
    record = _bezier_record(["smooth"] * 3)
    shape = bezier_for_entity(record)
    assert shape is not None
    shape.set_handle(1, "out", (10.0, 6.0))
    assert math.isclose(math.hypot(*shape.handles_in[1]), 3.0)
    assert shape.handles_in[1][1] < 0
    shape.set_handle(1, "in", (5.0, 5.0), break_pair=True)
    assert shape.node_types[1] == "corner"
    assert shape.handles_out[1] == (0.0, 6.0)


def test_node_type_change_invents_a_handle_for_a_zero_length_anchor() -> None:
    shape = ShapeFactory.bezier(control_points=[(0.0, 0.0), (10.0, 0.0)], node_types=["corner"] * 2)
    assert shape.set_node_type(0, "symmetric")
    assert shape.handles_out[0] == (1.0, 0.0)
    assert shape.handles_in[0] == (-1.0, -0.0)
    assert not shape.set_node_type(0, "bogus")


def test_handles_need_bezier_metadata() -> None:
    assert bezier_for_entity(EntityRecord(points=[(0.0, 0.0)], kind=EntityKind.BEZIER)) is None
    assert bezier_for_entity(EntityRecord(points=[(0.0, 0.0)])) is None


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        (
            ShapeFactory.rectangle(center=(1.0, 2.0), width=8.0, height=4.0, rotation=30.0),
            ((1.0, 2.0), 30.0, 8.0, 4.0, "width", "height"),
        ),
        (
            ShapeFactory.circle(center=(0.0, 0.0), radius=5.0),
            ((0.0, 0.0), 0.0, 10.0, 10.0, "radius", "radius"),
        ),
        (
            ShapeFactory.ellipse(center=(3.0, 3.0), rx=4.0, ry=2.0),
            ((3.0, 3.0), 0.0, 8.0, 4.0, "rx", "ry"),
        ),
    ],
)
def test_local_extent_reads_the_shape_definition(shape, expected) -> None:
    kind, meta = shape.to_meta_dict()
    record = EntityRecord(points=list(shape.points), kind=kind, meta=meta)
    extent = entity_local_extent(record)
    assert extent is not None
    assert (
        extent.center,
        extent.rotation,
        extent.width,
        extent.height,
        extent.x_key,
        extent.y_key,
    ) == expected


def test_demoted_records_have_no_extent() -> None:
    shape = ShapeFactory.circle(center=(0.0, 0.0), radius=5.0)
    kind, meta = shape.to_meta_dict()
    record = EntityRecord(points=list(shape.points), kind=kind, meta=meta)
    demote_to_path(record)
    assert record.kind is EntityKind.POLYLINE and record.meta is None
    assert entity_local_extent(record) is None


def test_fit_to_curve_stores_a_curved_closed_bezier(app: QApplication) -> None:
    canvas = DxfCanvas()
    circle = [
        (10.0 * math.cos(2 * math.pi * i / 40), 10.0 * math.sin(2 * math.pi * i / 40))
        for i in range(40)
    ]
    canvas.add_polylines_state([[*circle, circle[0]]])
    canvas._sel = {canvas._entities[0].id}
    assert canvas.fit_selected_to_curve(tolerance=0.5) == 1
    entity = canvas._entities[0]
    assert entity.kind is EntityKind.BEZIER
    assert entity.meta is not None and entity.meta["closed"] is True
    assert any(math.hypot(*tangent) > 0 for tangent in entity.meta["tangents"])
    outline = geometry_for_entity(entity).tessellate()
    # A straight-segment polygon through the anchors would sit inside the
    # circle between anchors; the fitted curve bulges back out toward it.
    assert len(outline) > len(entity.points) * 4
    assert min(math.hypot(x, y) for x, y in outline) > 9.0
    canvas.close()
