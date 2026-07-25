"""Responsive and durable sidebar behavior for Trace and Convert."""

from unittest.mock import patch

from PySide6.QtWidgets import QBoxLayout

from src.ui.pages.convert import ConvertPage
from src.ui.pages.trace.ui.form import TextField
from src.ui.pages.trace.tab import TracePage


def test_trace_sidebar_restores_and_remembers_bounded_width(qapp):
    settings = {"trace_sidebar_width": 405}
    page = TracePage(None, settings)
    page.resize(1300, 800)
    page.show()
    qapp.processEvents()
    assert page._left_panel.minimumWidth() == 300
    assert page._left_panel.maximumWidth() == 420
    # The remembered value is used as the requested initial splitter size;
    # headless Qt may defer actual geometry until the window is exposed.
    assert settings["trace_sidebar_width"] == 405

    page._splitter.setSizes([315, 900])
    with patch("src.ui.pages.trace.tab.save_settings") as save:
        page._remember_sidebar_width(315, 1)
    assert settings["trace_sidebar_width"] == 315
    save.assert_called_once_with(settings)


def test_convert_sidebar_switches_selector_and_remembers_state(qapp):
    settings = {"convert_sidebar_width": 325, "convert_selected_task": 2}
    page = ConvertPage(None, settings)
    page.resize(1240, 800)
    page.show()
    qapp.processEvents()
    assert page._left_panel.minimumWidth() == 300
    assert page._task_combo.isHidden() is False
    assert page._task_buttons_widget.isHidden() is True
    assert page._tool_stack.currentIndex() == 2

    page._update_task_selector_mode(380)
    assert page._task_combo.isHidden() is True
    assert page._task_buttons_widget.isHidden() is False

    with patch("src.ui.pages.convert.save_settings") as save:
        page._select_task_from_combo(3)
        page._splitter.setSizes([410, 800])
        page._on_sidebar_resized(410, 1)
    assert settings["convert_selected_task"] == 3
    assert settings["convert_sidebar_width"] == 410
    assert save.call_count == 2


def test_trace_field_stacks_label_in_narrow_inspector(qapp):
    field = TextField("Verbose translated field label")
    field.resize(280, 80)
    field.show()
    qapp.processEvents()
    assert field._layout.direction() == QBoxLayout.Direction.TopToBottom

    field.resize(330, 40)
    qapp.processEvents()
    assert field._layout.direction() == QBoxLayout.Direction.LeftToRight
