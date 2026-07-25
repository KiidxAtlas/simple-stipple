"""Boolean operations on closed shapes."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import bbox, make_view, square  # noqa: E402

OVERLAPPING = [square(0, 0), square(5, 5)]  # two 10mm squares overlapping


def test_union_welds(qapp):
    v = make_view(qapp, OVERLAPPING)
    source_ids = {entity.id for entity in v._entities}
    v.select_all()
    assert v.boolean_selected("union") == 1
    assert v.poly_count == 1
    result = v._last_operation_result
    assert result.changed
    assert set(result.removed_ids) == source_ids
    assert result.created_ids == result.selected_ids
    assert {entity.id for entity in v._entities if entity.id in v._sel} == set(result.selected_ids)
    first = next(entity for entity in v._entities if entity.id in v._sel)
    x0, y0, x1, y1 = bbox(first.points)
    assert (x1 - x0, y1 - y0) == (pytest.approx(15.0), pytest.approx(15.0))
    assert v.undo()
    assert v.poly_count == 2


def test_subtract_cuts_from_first(qapp):
    v = make_view(qapp, OVERLAPPING)
    v.select_all()
    assert v.boolean_selected("subtract") == 1
    import shapely.geometry as sg

    pg = sg.Polygon(v._entities[0].points[:-1])
    assert pg.area == pytest.approx(100 - 25)  # square minus overlap


def test_intersect_keeps_common(qapp):
    v = make_view(qapp, OVERLAPPING)
    v.select_all()
    assert v.boolean_selected("intersect") == 1
    x0, y0, x1, y1 = bbox(v._entities[0].points)
    assert (x1 - x0, y1 - y0) == (pytest.approx(5.0), pytest.approx(5.0))


def test_divide_splits_faces(qapp):
    v = make_view(qapp, OVERLAPPING)
    v.select_all()
    assert v.boolean_selected("divide") == 3  # A-only, B-only, overlap
    assert v.poly_count == 3


def test_subtract_full_containment_makes_hole(qapp):
    outer = square(0, 0, 20.0)
    inner = square(5, 5, 10.0)
    v = make_view(qapp, [outer, inner])
    v.select_all()
    assert v.boolean_selected("subtract") == 2  # exterior + hole loop
    assert v.poly_count == 2


def test_boolean_requires_two_closed(qapp):
    v = make_view(qapp, [square(0, 0), [(30.0, 0.0), (40.0, 0.0)]])
    v.select_all()
    assert v.boolean_selected("union") == 0
    assert v.poly_count == 2
