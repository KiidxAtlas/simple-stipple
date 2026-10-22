"""Geometry health repairs preserve linework and are one reversible edit."""

from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QMenu
from shapely.geometry import LineString
from shapely.ops import unary_union

from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.cad.preflight import analyze_geometry
from simple_stipple.core.editing.repair import repair_geometry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(app):
    view = DxfCanvas()
    view.resize(900, 650)
    yield view
    view.close()


def _flatten(repairs):
    return [path for replacements in repairs for path in replacements]


def test_repairs_vertices_gaps_and_reversed_duplicates_without_mutating_input():
    ring = [(0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0.0005)]
    duplicate = [(10, 10), (10, 0), (0, 0), (0, 10), (10, 10)]
    paths = [ring, duplicate, [], [(3, 3), (3, 3)]]
    original = deepcopy(paths)
    result = repair_geometry(paths)
    assert paths == original
    assert result == [[[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]], [], [], []]
    assert analyze_geometry(_flatten(result)).ready
    assert _flatten(repair_geometry(_flatten(result))) == _flatten(result)


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0), (4, 4), (0, 4), (4, 0), (0, 0)],
        [(0, 0), (2, 0), (2, 2), (2, 0), (4, 0)],
    ],
)
def test_splits_crossings_and_branches_without_losing_linework(points):
    result = _flatten(repair_geometry([points]))
    assert len(result) > 1
    assert analyze_geometry(result).ready
    assert unary_union([LineString(path) for path in result]).equals(LineString(points))


def test_preserves_intentional_open_paths_tiny_features_and_nested_rings():
    paths = [
        [(0, 0), (10, 0)],
        [(20, 20), (20.00001, 20)],
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        [(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)],
    ]
    assert _flatten(repair_geometry(paths)) == paths


def test_nonfinite_coordinates_are_not_guessed():
    path = [(float("inf"), 0), (1, 1)]
    assert repair_geometry([path]) == [[path]]


@pytest.mark.parametrize("overflow", [False, True])
def test_context_action_repairs_in_one_undo_step_and_redoes(canvas, overflow):
    canvas.add_polylines_state(
        [
            [(0, 0), (4, 4), (0, 4), (4, 0), (0, 0)],
            [(10, 0), (10, 0), (15, 0)],
        ]
    )
    original = deepcopy(canvas._entities)
    depth = canvas._canvas_service.undo_depth()
    from simple_stipple.canvas.operations.interactions import handle_right_click

    canvas.set_context_menu_profiles({})
    if overflow:
        canvas._context_menu_overflow_items.add("path.auto_fix_geometry")
    assert handle_right_click(canvas, 450, 325)
    menu, action = next(
        (menu, action)
        for menu in canvas.findChildren(QMenu)
        for action in menu.actions()
        if action.property("context_item_id") == "path.auto_fix_geometry"
    )
    assert action.isEnabled()
    assert (menu.title() == "More actions…") == overflow
    menu.hide()
    action.trigger()
    repaired = deepcopy(canvas._entities)
    assert canvas._canvas_service.undo_depth() == depth + 1
    assert analyze_geometry([e.points for e in repaired]).ready
    assert len({e.id for e in repaired}) == len(repaired)
    assert canvas.get_view_state()["geometry_health_visible"]
    canvas._canvas_service.undo()
    assert canvas._entities == original
    canvas._canvas_service.redo()
    assert canvas._entities == repaired
    assert canvas.auto_fix_geometry() == 0
    assert canvas._canvas_service.undo_depth() == depth + 1


def test_selection_scope_locked_hidden_and_explicit_empty_targets(canvas):
    dirty = [(0, 0), (0, 0), (5, 0)]
    canvas.add_polylines_state([dirty, dirty, dirty, dirty])
    canvas._entities[1].locked = True
    canvas._entities[2].hidden = True
    first, locked, hidden, outside = deepcopy(canvas._entities)
    canvas.set_selection([first.id, locked.id])
    assert canvas.auto_fix_geometry([]) == 0
    assert canvas.auto_fix_geometry() == 1
    assert canvas._entities[0].points == [(0, 0), (5, 0)]
    assert canvas._entities[1:] == [locked, hidden, outside]


def test_duplicates_stay_separate_across_layers_and_construction_roles(canvas):
    line = [(0, 0), (5, 0)]
    canvas.add_polylines_state([line, line, line, line])
    canvas._entities[0].layer = "Cut"
    canvas._entities[1].layer = "Engrave"
    canvas._entities[2].layer = "Cut"
    canvas._entities[2].construction = True
    canvas._entities[3].layer = "Cut"
    originals = deepcopy(canvas._entities[:3])
    assert canvas.auto_fix_geometry() == 1
    assert canvas._entities == originals


def test_repaired_native_text_retains_edit_metadata(canvas):
    canvas.add_polylines_state([[(0, 0), (0, 0), (5, 0)]])
    entity = canvas._entities[0]
    entity.meta = {"text_params": {"text": "Test", "family": "Tahoma", "height_mm": 4}}
    metadata = deepcopy(entity.meta)
    assert canvas.auto_fix_geometry() == 1
    assert canvas._entities[0].meta == metadata


def test_pattern_button_repairs_outlines_and_updates_health_findings(app):
    from simple_stipple.features.pattern.page import PatternPage

    page = PatternPage(settings={})
    try:
        original = [[(0, 0), (4, 4), (0, 4), (4, 0), (0, 0)]]
        page.load_outline_polys(original)
        depth = page._canvas._canvas_service.undo_depth()
        page._auto_fix_geometry_btn.click()
        assert len(page._edit_polys) > 1
        assert analyze_geometry(page._edit_polys).ready
        assert len(page._outline_ids) == len(page._edit_polys)
        assert page._canvas._canvas_service.undo_depth() == depth + 1
        assert not page._geometry_findings.isHidden()
        assert page._canvas.undo()
        assert page._edit_polys == original
        assert page._canvas.redo()
        assert analyze_geometry(page._edit_polys).ready
    finally:
        page.shutdown()
        page.close()


def test_self_crossing_ring_retains_both_closed_lobes():
    result = _flatten(repair_geometry([[(0, 0), (4, 4), (0, 4), (4, 0), (0, 0)]]))
    assert len(result) == 2
    assert analyze_geometry(result).closed == 2
    assert all(path[0] == path[-1] for path in result)


def test_three_point_nearly_closed_path_is_repaired_not_left_invalid():
    paths = [[(0, 0), (10, 0), (0, 0.0001)]]
    result = _flatten(repair_geometry(paths, remove_tiny=True))
    assert result == [[(0, 0), (10, 0)]]
    assert analyze_geometry(result).ready


def test_thorough_repair_removes_tiny_lobes_but_keeps_normal_lobes_and_holes():
    paths = [
        [(0, 0), (10, 10), (0, 10), (0.00001, 0), (0, 0)],
        [(1, 6), (2, 6), (2, 7), (1, 7), (1, 6)],
        [(20, 20), (20.00001, 20)],
    ]
    assert analyze_geometry(_flatten(repair_geometry(paths))).tiny_paths > 0
    result = _flatten(repair_geometry(paths, remove_tiny=True))
    assert len(result) == 2
    assert paths[1] in result
    assert analyze_geometry(result).ready
    assert _flatten(repair_geometry(result, remove_tiny=True)) == result


def test_canvas_auto_fix_clears_remaining_tiny_and_invalid_findings_and_undoes(canvas):
    canvas.add_polylines_state(
        [
            [(0, 0), (10, 0), (0, 0.0001)],
            [(20, 20), (20.00001, 20)],
            [(30, 30), (30.00001, 30), (30, 30.00001), (30, 30)],
            [(0, 0), (10, 10), (0, 10), (0.00001, 0), (0, 0)],
        ]
    )
    original = deepcopy(canvas._entities)
    depth = canvas.undo_depth()
    assert not analyze_geometry([e.points for e in original]).ready
    assert canvas.auto_fix_geometry() == 4
    assert analyze_geometry([e.points for e in canvas._entities]).ready
    assert canvas.undo_depth() == depth + 1
    repaired = deepcopy(canvas._entities)
    assert canvas.undo()
    assert canvas._entities == original
    assert canvas.redo()
    assert canvas._entities == repaired
