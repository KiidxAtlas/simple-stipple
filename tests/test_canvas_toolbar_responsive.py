from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_canvas_toolbar_keeps_modes_and_overflows_secondary_actions(qapp):
    from src.ui.widgets.canvas.toolbar import canvas_toolbar

    calls = []
    toolbar, modes, _selection, _guidance = canvas_toolbar(
        calls.append,
        lambda: calls.append("fit"),
        secondary_actions=[("Delete", lambda: calls.append("delete"), "danger")],
    )
    toolbar.resize(760, 40)
    toolbar.show()
    qapp.processEvents()

    assert all(not button.isHidden() for button in modes.values())
    assert not toolbar._overflow.isHidden()
    assert [action.text() for action in toolbar._overflow_menu.actions()] == ["Fit", "Delete"]
    assert all(button.isHidden() for button in toolbar._responsive_buttons)

    toolbar._overflow_menu.actions()[1].trigger()
    assert calls == ["delete"]

    toolbar.resize(1100, 40)
    qapp.processEvents()
    assert toolbar._overflow.isHidden()
    assert all(not button.isHidden() for button in toolbar._responsive_buttons)
    toolbar.close()
