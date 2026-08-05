"""Phase 6 — one output, continuous validation.

The document produces operations, not an export "kind". Preflight runs while
the design is being made and is drawn on the part, not summarised at export.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.engine.cad.preflight import GeometryIssue
from simple_stipple.features.pattern.output import density_issues, document_operations
from simple_stipple.features.pattern.page import PatternPage

OUTER = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
CIRCLE = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_the_reference_scenario_is_one_job_of_three_operations(app: QApplication) -> None:
    """Honeycomb in the ring, a logo engraved in the circle, an outer cut."""
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    ring_id, circle_id = page._outline_ids
    page._treatments[ring_id] = {
        "kind": "pattern",
        "pattern": "Honeycomb",
        "pattern_label": "Honeycomb",
        "params": {},
    }
    page._treatments[circle_id] = {
        "kind": "engrave",
        "pattern": "Image",
        "params": {},
        "engraving": {"path": "/tmp/logo.png", "x": 0, "y": 0, "width": 10, "height": 10},
    }

    operations = document_operations(page)
    assert [op.kind for op in operations] == ["engrave", "mark", "cut"]
    assert "logo.png" in operations[0].subject
    assert "Honeycomb" == operations[1].subject
    page.shutdown()
    page.close()


def test_run_order_is_engrave_then_mark_then_cut(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    outer_id, circle_id = page._outline_ids
    page._treatments[outer_id] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._treatments[circle_id] = {
        "kind": "engrave",
        "pattern": "Image",
        "params": {},
        "engraving": {"path": "logo.png"},
    }
    assert [op.kind for op in document_operations(page)] == ["engrave", "cut"]
    page.shutdown()
    page.close()


def test_rows_reorder_and_switch_off_without_touching_the_treatment(
    app: QApplication,
) -> None:
    from PySide6.QtCore import Qt

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    for region_id in page._outline_ids:
        page._treatments[region_id] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._refresh_output_panel()
    assert page._output_list.count() == 2

    before = dict(page._treatments)
    page._output_list.setCurrentRow(1)
    page._move_output_row(-1)
    assert page._treatments == before

    page._output_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert len(page._enabled_operations()) == 1
    assert page._treatments == before
    page.shutdown()
    page.close()


def test_preflight_marks_the_canvas_while_drawing_not_at_export(
    app: QApplication,
) -> None:
    """An open path that should be closed is a marker on the part, now."""
    page = PatternPage(settings={})
    page.load_outline_polys([[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]])
    page._refresh_preflight_markers()

    markers = page._canvas._issue_markers
    assert markers, "an unclosed path produced no finding"
    assert all(hasattr(m, "point") and hasattr(m, "severity") for m in markers)
    assert "finding" in page._output_preflight.text()

    # Clicking one selects the path it belongs to.
    assert page._on_issue_marker_clicked(markers[0]) is True
    assert page._canvas.get_selected_ids() == [page._outline_ids[0]]
    page.shutdown()
    page.close()


def test_a_clean_document_reports_no_findings(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER])
    page._refresh_preflight_markers()
    assert page._canvas._issue_markers == ()
    assert "no findings" in page._output_preflight.text()
    page.shutdown()
    page.close()


def test_density_below_the_machine_minimum_is_a_design_time_warning() -> None:
    jobs = [
        {"polys": [OUTER], "fill": {"mode": "lines", "spacing": 0.05}},
        {"polys": [CIRCLE], "fill": {"mode": "lines", "spacing": 1.0}},
    ]
    issues = density_issues(jobs, minimum_spacing_mm=0.2)
    assert len(issues) == 1
    assert isinstance(issues[0], GeometryIssue)
    assert issues[0].severity == "warning"
    assert issues[0].point == OUTER[0]
    # A zero minimum is the off switch, not a check that flags everything.
    assert density_issues(jobs, minimum_spacing_mm=0.0) == ()
