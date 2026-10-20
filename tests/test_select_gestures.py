"""Select-mode pointer gestures driven through real Qt mouse events."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from simple_stipple.canvas.widget import DxfCanvas

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _canvas(app: QApplication) -> DxfCanvas:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
            [(30.0, 0.0), (40.0, 0.0), (40.0, 10.0), (30.0, 10.0), (30.0, 0.0)],
        ]
    )
    canvas.fit()
    canvas.grab()
    app.processEvents()
    return canvas


def _point(canvas: DxfCanvas, wx: float, wy: float) -> QPoint:
    x, y = canvas._w2c(wx, wy)
    return QPoint(int(round(x)), int(round(y)))


def _drag_to(canvas: DxfCanvas, target: QPoint) -> None:
    # QTest.mouseMove does not carry pressed buttons, so send the event directly.
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(target),
        QPointF(target),
        Qt.MouseButton.NoButton,
        LEFT,
        NO_MOD,
    )
    QApplication.sendEvent(canvas, event)


def _escape(canvas: DxfCanvas) -> None:
    QApplication.sendEvent(canvas, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, NO_MOD))


def _xs(canvas: DxfCanvas) -> list[float]:
    return [canvas._entities[0].points[0][0], canvas._entities[1].points[0][0]]


def test_window_box_selects_only_enclosed_shapes(app: QApplication) -> None:
    canvas = _canvas(app)
    first = canvas._entities[0].id
    QTest.mousePress(canvas, LEFT, NO_MOD, _point(canvas, -2.0, 12.0))
    assert canvas._marquee is not None and canvas._marquee.is_box
    end = _point(canvas, 15.0, -2.0)
    _drag_to(canvas, end)
    QTest.mouseRelease(canvas, LEFT, NO_MOD, end)
    assert canvas._sel == {first}
    assert canvas._marquee is None
    canvas.close()


def test_lasso_is_one_shot_and_selects_crossed_shapes(app: QApplication) -> None:
    canvas = _canvas(app)
    canvas.arm_lasso_selection()
    QTest.mousePress(canvas, LEFT, NO_MOD, _point(canvas, 35.0, 15.0))
    assert canvas._marquee is not None and canvas._marquee.is_lasso
    for wx, wy in ((45.0, 15.0), (45.0, -5.0), (25.0, -5.0)):
        _drag_to(canvas, _point(canvas, wx, wy))
    end = _point(canvas, 25.0, 15.0)
    QTest.mouseRelease(canvas, LEFT, NO_MOD, end)
    assert canvas._sel == {canvas._entities[1].id}
    assert canvas._marquee is None
    assert not canvas._lasso_select_enabled
    canvas.close()


def test_escape_cancels_marquee_without_changing_selection(app: QApplication) -> None:
    canvas = _canvas(app)
    canvas.set_selection([canvas._entities[1].id])
    QTest.mousePress(canvas, LEFT, NO_MOD, _point(canvas, -2.0, 12.0))
    _drag_to(canvas, _point(canvas, 15.0, -2.0))
    assert canvas._marquee is not None
    _escape(canvas)
    assert canvas._marquee is None
    assert canvas._sel == {canvas._entities[1].id}
    canvas.close()


def test_move_drag_commits_one_undo_step(app: QApplication) -> None:
    canvas = _canvas(app)
    canvas.set_selection([canvas._entities[0].id])
    QTest.mousePress(canvas, LEFT, NO_MOD, _point(canvas, 10.0, 5.0))
    assert canvas._move_gesture is not None and not canvas._move_gesture.dragging
    end = _point(canvas, 15.0, 5.0)
    _drag_to(canvas, end)
    assert canvas._move_gesture is not None and canvas._move_gesture.dragging
    QTest.mouseRelease(canvas, LEFT, NO_MOD, end)
    assert canvas._move_gesture is None
    assert _xs(canvas)[0] > 2.0
    assert _xs(canvas)[1] == 30.0
    depth = canvas.undo_depth()
    assert canvas.undo()
    assert _xs(canvas) == [0.0, 30.0]
    assert canvas.undo_depth() == depth - 1
    canvas.close()


def test_escape_restores_geometry_mid_move(app: QApplication) -> None:
    canvas = _canvas(app)
    depth = canvas.undo_depth()
    canvas.set_selection([canvas._entities[0].id])
    QTest.mousePress(canvas, LEFT, NO_MOD, _point(canvas, 10.0, 5.0))
    _drag_to(canvas, _point(canvas, 20.0, 5.0))
    assert _xs(canvas)[0] != 0.0
    _escape(canvas)
    assert canvas._move_gesture is None
    assert _xs(canvas) == [0.0, 30.0]
    assert canvas.undo_depth() == depth
    canvas.close()
