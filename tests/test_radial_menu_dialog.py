"""Customize-the-radial-menu dialog: checked items (in list order) become
the tool list; unchecking below the minimum falls back to defaults.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from src.infra.settings import DEFAULT_RADIAL_MENU_TOOLS  # noqa: E402
from src.ui.widgets.customize_dialogs import _POOL, RadialMenuDialog  # noqa: E402


def test_pool_spans_far_more_than_just_draw_primitives():
    """The whole point of the redesign: many more options than the original
    handful of shape tools — undo/redo, clipboard, booleans, view/grid, ..."""
    assert len(_POOL) > 20
    assert "edit.undo" in _POOL
    assert "boolean.union" in _POOL
    assert "canvas.rectangle" in _POOL
    assert "canvas.radial_menu" not in _POOL  # can't open itself from itself


def test_dialog_preselects_the_current_tool_list(qapp):
    dlg = RadialMenuDialog(None, tools=["canvas.circle", "canvas.arc", "mode.pen"])
    checked = [
        dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dlg._list.count())
        if dlg._list.item(i).checkState() == Qt.CheckState.Checked
    ]
    assert checked == ["canvas.circle", "canvas.arc", "mode.pen"]


def test_unchecking_an_item_and_applying_removes_it(qapp):
    dlg = RadialMenuDialog(None, tools=list(DEFAULT_RADIAL_MENU_TOOLS))
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "canvas.arc":
            item.setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert "canvas.arc" not in dlg.get_tools()
    assert len(dlg.get_tools()) == len(DEFAULT_RADIAL_MENU_TOOLS) - 1


def test_applying_below_minimum_falls_back_to_defaults(qapp):
    dlg = RadialMenuDialog(None, tools=list(DEFAULT_RADIAL_MENU_TOOLS))
    for i in range(dlg._list.count()):
        dlg._list.item(i).setCheckState(Qt.CheckState.Unchecked)
    # Re-check just one — below the 3-item minimum.
    dlg._list.item(0).setCheckState(Qt.CheckState.Checked)
    dlg._apply()
    assert dlg.get_tools() == list(DEFAULT_RADIAL_MENU_TOOLS)


def test_reset_restores_default_checked_set(qapp):
    dlg = RadialMenuDialog(None, tools=["canvas.circle", "canvas.arc", "mode.pen"])
    dlg._reset()
    dlg._apply()
    assert dlg.get_tools() == list(DEFAULT_RADIAL_MENU_TOOLS)


def test_invalid_tools_are_ignored_on_open(qapp):
    dlg = RadialMenuDialog(None, tools=["bogus", "not-a-tool"])
    dlg._apply()
    assert dlg.get_tools() == list(DEFAULT_RADIAL_MENU_TOOLS)


def test_filter_hides_non_matching_rows(qapp):
    dlg = RadialMenuDialog(None, tools=list(DEFAULT_RADIAL_MENU_TOOLS))
    dlg._filter.setText("undo")
    visible = [
        dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dlg._list.count())
        if not dlg._list.item(i).isHidden()
    ]
    assert visible == ["edit.undo"]
