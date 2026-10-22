"""Add Reference: a bounding rectangle on its own new layer, undone as one step."""

from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from simple_stipple.canvas.widget import DxfCanvas


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def canvas(app: QApplication):
    view = DxfCanvas()
    view.resize(800, 600)
    yield view
    view.close()


def _references(view: DxfCanvas) -> list:
    return [e for e in view._entities if (e.layer or "").startswith("Reference")]


def _context_action(view: DxfCanvas, cx: float, cy: float):
    menu = QMenu(view)
    view._build_selection_actions(menu, lambda _section: True, lambda f: f(), None, cx, cy)
    return next(action for action in menu.actions() if action.text() == "Add Reference")


def test_grouped_hit_without_selection_frames_the_whole_group(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0), (0.0, 0.0)],
            [(10.0, 5.0), (12.0, 5.0), (12.0, 9.0), (10.0, 9.0), (10.0, 5.0)],
            [(50.0, 50.0), (51.0, 50.0), (51.0, 51.0), (50.0, 50.0)],
        ]
    )
    first, second, outsider = (entity.id for entity in canvas._entities)
    canvas.group_entities([first, second])
    canvas._sel = set()
    cx, cy = canvas._w2c(0.0, 1.0)  # on the left edge of the first member

    assert set(canvas.reference_target_ids(cx, cy)) == {first, second}
    action = _context_action(canvas, cx, cy)
    assert action.isEnabled()
    before = deepcopy(canvas._entities)
    action.trigger()

    reference = _references(canvas)
    assert len(reference) == 1
    assert reference[0].layer == "Reference 1"
    assert reference[0].kind == "rectangle"
    assert reference[0].points == [(0.0, 0.0), (12.0, 0.0), (12.0, 9.0), (0.0, 9.0), (0.0, 0.0)]
    assert tuple(reference[0].meta["center"]) == (6.0, 4.5)
    assert (reference[0].meta["width"], reference[0].meta["height"]) == (12.0, 9.0)
    assert [(e.id, e.points, e.group, e.layer) for e in canvas._entities[:3]] == [
        (e.id, e.points, e.group, e.layer) for e in before
    ]
    assert outsider not in canvas._sel


def test_multiple_selected_shapes_are_framed_and_selection_is_kept(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state(
        [
            [(-3.0, 1.0), (-1.0, 1.0), (-1.0, 2.0)],
            [(5.0, -4.0), (7.0, 6.0)],
            [(100.0, 100.0), (101.0, 101.0)],
        ]
    )
    ids = [entity.id for entity in canvas._entities[:2]]
    canvas._sel = set(ids)

    assert canvas.add_reference_box(canvas.reference_target_ids(0.0, 0.0)) == "Reference 1"

    (reference,) = _references(canvas)
    assert reference.points[0] == (-3.0, -4.0)
    assert reference.points[2] == (7.0, 6.0)
    assert canvas._sel == set(ids)


def test_reference_does_not_touch_locked_originals(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state([[(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]])
    canvas._entities[0].locked = True
    original = deepcopy(canvas._entities[0])
    canvas._sel = {original.id}

    assert canvas.add_reference_box(canvas._selected_ids()) is not None

    assert canvas._entities[0] == original
    assert canvas._entities[0].locked
    assert not _references(canvas)[0].locked


def test_layer_and_rectangle_are_one_undo_and_redo_step(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state([[(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]])
    entity_id = canvas._entities[0].id
    layers_before = canvas.layer_names()
    depth = canvas._canvas_service.undo_depth()

    assert canvas.add_reference_box([entity_id]) == "Reference 1"
    assert canvas._canvas_service.undo_depth() == depth + 1
    assert "Reference 1" in canvas.layer_names()

    canvas._canvas_service.undo()
    assert not _references(canvas)
    assert canvas.layer_names() == layers_before

    canvas._canvas_service.redo()
    assert len(_references(canvas)) == 1
    assert "Reference 1" in canvas.layer_names()


def test_repeated_references_use_unique_layers(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state([[(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]])
    entity_id = canvas._entities[0].id

    names = [canvas.add_reference_box([entity_id]) for _ in range(3)]

    assert names == ["Reference 1", "Reference 2", "Reference 3"]
    assert len({e.layer for e in _references(canvas)}) == 3
    canvas._canvas_service.undo()
    assert canvas.add_reference_box([entity_id]) == "Reference 3"


def test_degenerate_or_empty_target_is_rejected_without_a_layer(canvas: DxfCanvas) -> None:
    canvas.add_polylines_state([[(0.0, 0.0), (5.0, 0.0)]])
    entity_id = canvas._entities[0].id
    layers = canvas.layer_names()
    depth = canvas._canvas_service.undo_depth()

    assert canvas.add_reference_box([entity_id]) is None
    assert canvas.add_reference_box([]) is None
    assert canvas.add_reference_box(["missing"]) is None

    assert canvas.layer_names() == layers
    assert len(canvas._entities) == 1
    assert canvas._canvas_service.undo_depth() == depth
    canvas._sel = set()
    cx, cy = canvas._w2c(100.0, 100.0)
    assert not _context_action(canvas, cx, cy).isEnabled()
