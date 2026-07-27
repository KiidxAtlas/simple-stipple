"""Focused regression checks added by the UI layout and behavior audit."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from simple_stipple.canvas.widgets.draw_sidebar import _ResizeHandle
from simple_stipple.canvas.widgets.status_strip import CanvasStatusStrip
from simple_stipple.canvas.widgets.toolbar import canvas_toolbar
from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.ui.components.cycle_button import CycleIconButton
from simple_stipple.ui.components.workflow import WorkflowStepper


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width,height", [(1280, 820), (1050, 700), (900, 600)])
def test_responsive_pages_preserve_primary_content(
    app: QApplication, width: int, height: int
) -> None:
    convert = ConvertPage(settings={})
    convert.resize(width, height)
    convert.show()
    app.processEvents()
    assert convert.sizeHint().width() <= width
    assert convert._left_panel.maximumWidth() <= 320
    if width < convert._splitter.COMPACT_WIDTH:
        assert convert._splitter.sizes()[0] == 0
        assert convert._splitter._drawer_toggle.isVisible()
    convert.close()

    pattern = PatternPage(settings={})
    pattern.resize(width, height)
    pattern.show()
    app.processEvents()
    sizes = pattern._canvas_splitter.sizes()
    assert sizes[1] == 0 or sizes[0] / max(1, sum(sizes)) >= 0.60
    if width < pattern._canvas_splitter.COMPACT_WIDTH:
        assert pattern._canvas_splitter._drawer_toggle.isVisible()
    pattern.close()


def test_workflow_strip_is_honest_noninteractive_progress(app: QApplication) -> None:
    stepper = WorkflowStepper(("Input", "Preview", "Export"))
    for button in stepper.findChildren(QToolButton):
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert button.minimumHeight() >= 24 or button.sizeHint().height() >= 24


def test_compact_status_keeps_selection_and_exposes_details(app: QApplication) -> None:
    strip = CanvasStatusStrip()
    strip.resize(700, 40)
    strip.set_snapshot(
        mode="select",
        selected_count=2,
        object_count=8,
        precision_text="Grid snap",
        readiness_text="Ready",
    )
    strip.show()
    app.processEvents()
    assert strip._selection_label.isVisible()
    assert strip._details_button.isVisible()
    assert "8 obj" in strip._details_button.accessibleDescription()
    assert "2 sel" in strip._details_button.accessibleDescription()


def test_compact_toolbar_preserves_guidance(app: QApplication) -> None:
    toolbar, *_ = canvas_toolbar(lambda _mode: None, lambda: None)
    toolbar.set_guidance("Draw · Pick the first point")
    toolbar.resize(900, 44)
    toolbar.show()
    app.processEvents()
    assert toolbar._guidance_chip.isVisible()
    assert "Pick the first point" in toolbar._overflow.accessibleDescription()


def test_multistate_button_uses_native_menu_without_hover_flyout(
    app: QApplication,
) -> None:
    button = CycleIconButton(
        [("a", QIcon(), "A"), ("b", QIcon(), "B"), ("c", QIcon(), "C")],
        lambda _state: None,
    )
    assert not button._hover_timer.isActive()
    button.click()
    app.processEvents()
    assert button._state_menu is not None
    assert "3 options" in button.accessibleDescription()


def test_draw_resize_handle_meets_target_and_keyboard_contract(
    app: QApplication,
) -> None:
    class SidebarStub(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.resize(280, 300)
            self.committed = False

        def _apply_width(self, width: int) -> None:
            self.resize(width, self.height())

        def _on_width_committed(self) -> None:
            self.committed = True

    sidebar = SidebarStub()
    handle = _ResizeHandle(sidebar)  # type: ignore[arg-type]
    assert handle.width() >= 24
    assert handle.focusPolicy() == Qt.FocusPolicy.StrongFocus
    handle.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.ShiftModifier,
        )
    )
    assert sidebar.width() == 304
    assert sidebar.committed
