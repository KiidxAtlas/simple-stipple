"""Spline offset must follow the contour rendered by the canvas."""

from __future__ import annotations

import os
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.cad.geometry import build_spline_poly
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.core.editing.boolean import offset_polyline


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def test_spline_offset_preview_and_commit_follow_rendered_contour(app: QApplication) -> None:
    app.processEvents()
    canvas = DxfCanvas()
    control_points = [(0.0, 0.0), (10.0, 15.0), (20.0, -15.0), (30.0, 0.0)]
    segments = 64
    distance = 3.0
    entity = EntityRecord(
        points=control_points,
        kind="spline",
        meta={
            "control_points": control_points,
            "degree": 3,
            "segments": segments,
            "closed": False,
        },
    )
    created = canvas._canvas_service.create_entities([entity])
    canvas._sel = set(created.created_ids)

    expected = offset_polyline(build_spline_poly(control_points, segments=segments), distance)
    sparse_control_offset = offset_polyline(control_points, distance)
    assert expected is not None
    assert sparse_control_offset is not None
    assert len(expected) > len(sparse_control_offset)

    view = cast(Any, canvas)
    view._selection_service._preview_offset_selected(distance)
    assert view._operation_preview_polys == [expected]

    assert canvas.offset_selected(distance) == 1
    result = view._last_operation_result
    assert result is not None and result.created_ids
    offset_entity = canvas._entity_for_id(result.created_ids[0])
    assert offset_entity is not None
    assert offset_entity.points == expected
    canvas.close()
