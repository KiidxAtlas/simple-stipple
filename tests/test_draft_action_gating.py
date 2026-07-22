"""Draft page action buttons must never sit enabled with an unmet precondition.

Explode/Merge previously stayed enabled with nothing selected — clicking them
silently did nothing, no feedback at all. Export stayed enabled on an empty
canvas, popping a blocking "Nothing to Export" dialog instead of just being
disabled up front. These pin that each button's enabled state always matches
whether the action can actually do something, mirroring how Repository
already gates Pull/Commit/Push and how Convert now gates its primary action.
"""

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.draft import DraftPage
from tests.test_dimension_tool import square


def test_explode_and_merge_disabled_with_no_selection(qapp):
    page = DraftPage(None, {})
    assert not page._explode_btn.isEnabled()
    assert not page._merge_btn.isEnabled()


def test_explode_enables_with_one_selected_merge_needs_two(qapp):
    page = DraftPage(None, {})
    page._canvas.load([square(0, 0)])
    page._canvas.set_selection([0])
    page._refresh_status()

    assert page._explode_btn.isEnabled()
    assert not page._merge_btn.isEnabled()  # merge needs 2+

    page._canvas.load([square(0, 0), square(20, 0)])
    page._canvas.set_selection([0, 1])
    page._refresh_status()
    assert page._merge_btn.isEnabled()


def test_export_disabled_on_empty_canvas_enabled_once_geometry_exists(qapp):
    page = DraftPage(None, {})
    assert not page._export_btn.isEnabled()

    page._canvas.load([square(0, 0)])
    page._refresh_status()
    assert page._export_btn.isEnabled()
