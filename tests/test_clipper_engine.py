from __future__ import annotations

import pytest

from src.backend.editing.clipper_engine import (
    clipper_difference,
    clipper_intersection,
    clipper_offset,
    clipper_union,
)

SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
OVERLAP = [(1.0, 0.0), (3.0, 0.0), (3.0, 2.0), (1.0, 2.0), (1.0, 0.0)]


@pytest.mark.parametrize(
    ("operation", "expected_area"),
    ((clipper_union, 6.0), (clipper_intersection, 2.0), (clipper_difference, 2.0)),
)
def test_boolean_wrappers_preserve_expected_area(operation, expected_area):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    result = operation([SQUARE], [OVERLAP]) if operation is not clipper_union else operation([SQUARE, OVERLAP])
    assert unary_union([Polygon(path) for path in result]).area == pytest.approx(expected_area)


def test_offset_expands_closed_polygon():
    from shapely.geometry import Polygon

    result = clipper_offset(SQUARE, 0.5)
    assert result
    assert max(Polygon(path).area for path in result) > Polygon(SQUARE).area
