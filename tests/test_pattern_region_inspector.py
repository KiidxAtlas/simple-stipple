"""Behavioral coverage for the pattern region inspector.

The Pattern/Fill controls exist once. They edit the selected region, or the
document defaults when nothing is selected, and the header says which.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.pattern.regions.treatments import treatment_kind

OUTER = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
CIRCLE = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_pattern_and_fill_controls_exist_exactly_once(app: QApplication) -> None:
    page = PatternPage(settings={})
    for duplicate in (
        "_zone_pattern_combo",
        "_zone_fill_mode",
        "_zone_fill_spacing",
        "_zone_fill_angle",
        "_zone_fill_inset",
        "_zone_fill_target_outline",
        "_zone_fill_target_pattern",
        "_zone_param_inputs",
        "_zone_rotation",
        "_zone_size_percent",
    ):
        assert not hasattr(page, duplicate), f"{duplicate} is a second copy of an existing control"
    page.shutdown()
    page.close()


def test_the_same_widgets_edit_a_region_or_the_document(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    outer_id, circle_id = page._outline_ids

    # Nothing selected: the inspector is the document's, and says so.
    page._zone_list.setCurrentRow(-1)
    assert "Document defaults" in page._pattern_props_scope.text()
    page._pattern_combo.setCurrentText("Honeycomb")
    assert page._treatments == {}

    # Select a region: the same combo now writes that region's treatment,
    # and the header names it. Selecting alone changes nothing — an edit does.
    page._zone_list.setCurrentRow(1)
    assert page._pattern_props_scope.text().startswith("Region 2")
    assert page._treatments == {}
    page._pattern_combo.setCurrentText("Voronoi")
    assert treatment_kind(page, circle_id) in {"pattern", "pattern_fill"}
    assert treatment_kind(page, outer_id) == "none"
    page.shutdown()
    page.close()


def test_selecting_a_region_reloads_its_settings_into_the_inspector(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])

    page._zone_list.setCurrentRow(0)
    page._pattern_combo.setCurrentText("Honeycomb")
    page._zone_list.setCurrentRow(1)
    page._pattern_combo.setCurrentText("— None —")

    page._zone_list.setCurrentRow(0)
    assert page._pattern_combo.currentText() == "Honeycomb"
    page._zone_list.setCurrentRow(1)
    assert page._pattern_combo.currentText() == "— None —"
    page.shutdown()
    page.close()


def test_the_workflow_strip_is_gone(app: QApplication) -> None:
    import simple_stipple.ui.components.workflow as workflow

    assert not hasattr(workflow, "workflow_strip")
    assert not hasattr(workflow, "WorkflowStepper")
    page = PatternPage(settings={})
    assert not hasattr(page, "_workflow_strip")
    page.shutdown()
    page.close()


def test_empty_canvas_offers_buttons_not_numbered_prose(app: QApplication) -> None:
    page = PatternPage(settings={})
    bar = page._canvas._empty_actions_bar
    assert bar is not None
    labels = [button.text() for button in bar.findChildren(type(bar.children()[1]))]
    assert "Import outline…" in labels

    page._canvas.sync_empty_actions()
    assert bar.isVisibleTo(page._canvas)

    # Once there is geometry the buttons get out of the way.
    page.load_outline_polys([OUTER])
    page._canvas.sync_empty_actions()
    assert not bar.isVisibleTo(page._canvas)
    page.shutdown()
    page.close()
