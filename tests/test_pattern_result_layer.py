"""Behavioral coverage for the continuous pattern result layer.

There is no preview toggle to leave: the result renders continuously and its
visibility is a row in the layer tree like every other layer.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.features.pattern.page import PatternPage

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _page_with_result(cells: list[list[tuple[float, float]]]) -> PatternPage:
    page = PatternPage(settings={})
    page.load_outline_polys([{"points": SQUARE, "layer": "Outline"}])
    page._preview_polys_cache = list(cells)
    page._preview_categories = {"outline": [], "pattern": list(cells), "fill": []}
    page._canvas.set_result_polylines(cells, pattern_span=(0, len(cells)))
    return page


def test_result_layer_row_carries_visibility(app: QApplication) -> None:
    cell = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 1.0)]
    page = _page_with_result([cell])

    rows = {row["name"]: row for row in page._build_layer_tree_rows({})}
    assert "pattern_result" in rows
    assert rows["pattern_result"]["visible"] is True
    assert rows["pattern_result"]["shapes"] == []

    page._on_pattern_layer_visibility_changed("pattern_result", False)
    assert page._canvas.result_visible() is False
    rows = {row["name"]: row for row in page._build_layer_tree_rows({})}
    assert rows["pattern_result"]["visible"] is False

    # Hiding the result is visibility only — the document is untouched.
    assert page._canvas.get_entity_ids() == page._outline_ids
    page.shutdown()
    page.close()


def test_no_result_row_without_solved_geometry(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([{"points": SQUARE, "layer": "Outline"}])
    assert "pattern_result" not in {row["name"] for row in page._build_layer_tree_rows({})}
    page.shutdown()
    page.close()


def test_convert_to_outline_promotes_a_generated_cell(app: QApplication) -> None:
    cell = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 1.0)]
    page = _page_with_result([cell])
    before = len(page._outline_ids)

    page._on_result_cell_convert(0)

    assert len(page._outline_ids) == before + 1
    assert page._edit_polys[-1] == [tuple(point) for point in cell]
    assert page._canvas.get_entity_ids() == page._outline_ids
    # Out-of-range indices are a no-op, not a crash.
    page._on_result_cell_convert(99)
    assert len(page._outline_ids) == before + 1
    page.shutdown()
    page.close()


def test_cancel_solve_is_a_noop_when_nothing_is_solving(app: QApplication) -> None:
    page = PatternPage(settings={})
    assert page._preview_task.running is False
    page._cancel_solve()  # must not raise
    page.shutdown()
    page.close()
