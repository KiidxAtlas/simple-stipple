"""Shared input behavior."""

from PySide6.QtWidgets import QLineEdit, QToolButton

from src.ui.components import make_resettable_line_edit


def test_resettable_line_edit_x_restores_default_without_blocking_manual_blank(qapp):
    edit = make_resettable_line_edit(QLineEdit("12"), "5")
    edit.show()
    try:
        edit.clear()
        qapp.processEvents()
        assert edit.text() == ""

        edit.setText("12")
        clear_button = edit.findChild(QToolButton)
        assert clear_button is not None
        clear_button.click()
        qapp.processEvents()
        assert edit.text() == "5"
    finally:
        edit.deleteLater()
        qapp.processEvents()
