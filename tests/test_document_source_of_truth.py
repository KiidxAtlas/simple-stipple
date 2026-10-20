"""The canvas document is the only owner of entity state and its indexes."""

from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.canvas.widget import DxfCanvas


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def test_view_entity_index_is_the_document_index(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state([[(0.0, 0.0), (5.0, 0.0)], [(0.0, 1.0), (5.0, 1.0)]])
    assert canvas._entities_by_id is canvas.document.entities_by_id()
    assert list(canvas._entities_by_id) == canvas.document.entity_ids()
    canvas.close()


def test_snap_shapes_follow_committed_edits_and_undo(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state([[(0.0, 0.0), (5.0, 0.0)]])
    eid = canvas._entities[0].id
    assert canvas._snap_shapes()[eid].points[-1] == (5.0, 0.0)

    moved = deepcopy(canvas._entities[0])
    moved.points = [(0.0, 0.0), (9.0, 0.0)]
    assert canvas._canvas_service.update_entities([moved]).changed
    assert canvas._snap_shapes()[eid].points[-1] == (9.0, 0.0)

    canvas._canvas_service.undo()
    assert canvas._snap_shapes()[eid].points[-1] == (5.0, 0.0)
    canvas.close()
