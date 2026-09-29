"""Trace page: the source picture can be moved, scaled, rotated, and removed.

Drives the real canvas with mouse/key events; the traced outlines must stay
glued to the picture, survive a re-trace, and persist in the workspace.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from simple_stipple.features.trace import TracePage

SQUARE = [(10.0, 5.0), (30.0, 5.0), (30.0, 15.0), (10.0, 15.0), (10.0, 5.0)]


def _send(canvas, kind: QEvent.Type, point: QPointF, buttons, modifiers) -> None:
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, point, canvas.mapToGlobal(point), button, buttons, modifiers)
    QApplication.sendEvent(canvas, event)


def _drag(canvas, start_world, end_world, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    start = QPointF(*canvas._w2c(*start_world))
    end = QPointF(*canvas._w2c(*end_world))
    held = Qt.MouseButton.LeftButton
    _send(canvas, QEvent.Type.MouseButtonPress, start, held, modifiers)
    for step in range(1, 5):
        t = step / 4
        mid = QPointF(start.x() + (end.x() - start.x()) * t, start.y() + (end.y() - start.y()) * t)
        _send(canvas, QEvent.Type.MouseMove, mid, held, modifiers)
    _send(canvas, QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton, modifiers)


def _outline(page: TracePage) -> list[tuple[float, float]]:
    (poly,) = page._canvas.get_polylines_state()
    return poly


def _assert_points(actual, expected, tol: float = 0.05) -> None:
    assert len(actual) == len(expected)
    for (ax, ay), (ex, ey) in zip(actual, expected, strict=True):
        assert math.isclose(ax, ex, abs_tol=tol) and math.isclose(ay, ey, abs_tol=tol), (
            actual,
            expected,
        )


@pytest.fixture
def traced_page(qapp: QApplication, tmp_path: Path):
    """A Trace page showing a 40×20 mm picture with one traced square."""
    source = tmp_path / "source.png"
    Image.new("RGB", (200, 100), "white").save(source)
    page = TracePage(settings={})
    page.resize(1400, 900)
    page.show()
    page._img_path = str(source)
    page._img_edit.setText(str(source))
    page._load_thumbnail(str(source))
    page._handle_trace_done(
        (page._trace_revision, Image.open(source).convert("RGB"), [list(SQUARE)], 200, 100, 40.0)
    )
    page._canvas.set_view_state({"scale": 10.0, "ox": 200.0, "oy": 500.0})
    qapp.processEvents()
    page._adjust_image_btn.click()
    assert page._canvas._bg_selected
    yield page
    page.shutdown()
    page.close()


def test_dragging_the_picture_moves_the_outlines_with_it(traced_page: TracePage) -> None:
    _drag(traced_page._canvas, (5.0, 10.0), (15.0, 13.0))

    _assert_points(_outline(traced_page), [(x + 10.0, y + 3.0) for x, y in SQUARE])
    assert (traced_page._image_x_mm, traced_page._image_y_mm) == pytest.approx((10.0, 3.0))
    assert not traced_page._preview_timer.isActive()  # a move needs no re-trace


def test_rotating_the_picture_rotates_the_outlines_about_its_centre(
    traced_page: TracePage,
) -> None:
    canvas = traced_page._canvas
    top_mid = (20.0, 20.0)
    handle_c = QPointF(*canvas._w2c(*top_mid)) + QPointF(0.0, -24.0)
    handle_w = canvas._c2w(handle_c.x(), handle_c.y())
    # Centre is (20, 10): dragging the top handle round to the left is +90°.
    _drag(canvas, handle_w, (20.0 - (handle_w[1] - 10.0), 10.0))

    assert traced_page._image_rotation_deg == pytest.approx(90.0, abs=0.5)
    rotated = [(20.0 - (y - 10.0), 10.0 + (x - 20.0)) for x, y in SQUARE]
    _assert_points(_outline(traced_page), rotated, tol=0.2)


def test_scaling_a_corner_keeps_proportions_and_retraces_at_the_new_size(
    traced_page: TracePage,
) -> None:
    _drag(traced_page._canvas, (40.0, 20.0), (80.0, 25.0))

    _assert_points(_outline(traced_page), [(x * 2.0, y * 2.0) for x, y in SQUARE])
    assert traced_page._canvas._bg_w_mm == pytest.approx(80.0)
    assert traced_page._canvas._bg_h_mm == pytest.approx(40.0)
    assert traced_page._width_mm.text() == "80.00"
    assert traced_page._preview_timer.isActive()


def test_a_retrace_lands_where_the_picture_was_moved(traced_page: TracePage) -> None:
    _drag(traced_page._canvas, (5.0, 10.0), (15.0, 10.0))
    source = Image.new("RGB", (200, 100), "white")

    traced_page._handle_trace_done(
        (traced_page._trace_revision, source, [list(SQUARE)], 200, 100, 40.0)
    )

    _assert_points(_outline(traced_page), [(x + 10.0, y) for x, y in SQUARE])
    assert traced_page._canvas._bg_x_mm == pytest.approx(10.0)


def test_delete_removes_the_selected_picture_and_keeps_the_outlines(
    traced_page: TracePage,
) -> None:
    QTest.keyClick(traced_page._canvas, Qt.Key.Key_Delete)

    assert traced_page._img_path is None
    assert traced_page._canvas._bg_pil is None
    assert traced_page._canvas.poly_count == 1
    assert not traced_page._adjust_image_btn.isEnabled()


def test_picture_placement_survives_a_workspace_round_trip(traced_page: TracePage) -> None:
    _drag(traced_page._canvas, (5.0, 10.0), (15.0, 13.0))
    state = traced_page.get_workspace_state()

    restored = TracePage(settings={})
    restored.apply_workspace_state(state)

    assert restored._canvas._bg_x_mm == pytest.approx(10.0)
    assert restored._canvas._bg_y_mm == pytest.approx(3.0)
    _assert_points(restored._canvas.get_polylines_state()[0], _outline(traced_page))
    restored.shutdown()
    restored.close()


# ── Typed placement (Image panel, like Draft's Properties) ────────────────


def _type(edit, text: str) -> None:
    edit.setText(text)
    edit.editingFinished.emit()


def test_typing_a_position_moves_picture_and_outlines(traced_page: TracePage) -> None:
    panel = traced_page._image_panel
    _type(panel._x, "12")
    _type(panel._y, "1in")  # expressions and units, as in Draft's Properties

    _assert_points(_outline(traced_page), [(x + 12.0, y + 25.4) for x, y in SQUARE])
    assert traced_page._canvas._bg_x_mm == pytest.approx(12.0)
    assert traced_page._canvas._bg_y_mm == pytest.approx(25.4)
    assert not traced_page._preview_timer.isActive()


def test_typing_a_rotation_turns_the_picture_about_its_centre(traced_page: TracePage) -> None:
    _type(traced_page._image_panel._rotation, "90")

    rotated = [(20.0 - (y - 10.0), 10.0 + (x - 20.0)) for x, y in SQUARE]
    _assert_points(_outline(traced_page), rotated)
    assert traced_page._canvas._bg_rotation_deg == pytest.approx(90.0)
    traced_page._image_panel._rotate_by(90.0)
    traced_page._image_panel._rotate_by(90.0)
    assert traced_page._image_rotation_deg == pytest.approx(-90.0)  # 270° shown as -90°
    assert traced_page._image_panel._rotation.text() == "-90.00"


def test_typing_a_width_keeps_proportions_and_retraces(traced_page: TracePage) -> None:
    _type(traced_page._image_panel._w, "80")

    assert traced_page._image_panel._h.text() == "40.000"
    _assert_points(_outline(traced_page), [(x * 2.0, y * 2.0) for x, y in SQUARE])
    assert traced_page._width_mm.text() == "80.00"
    assert traced_page._preview_timer.isActive()


def test_the_panel_follows_a_canvas_drag(traced_page: TracePage) -> None:
    _drag(traced_page._canvas, (5.0, 10.0), (15.0, 13.0))

    assert traced_page._image_panel._x.text() == "10.000"
    assert traced_page._image_panel._y.text() == "3.000"


def test_invalid_input_is_kept_and_marked_until_corrected(traced_page: TracePage) -> None:
    field = traced_page._image_panel._x
    _type(field, "abc")

    assert field.text() == "abc"
    assert field.property("error")
    assert "25/2" in field.toolTip()
    _assert_points(_outline(traced_page), SQUARE)
    traced_page._refresh_image_panel()  # an unrelated refresh keeps the attempt
    assert field.text() == "abc"

    _type(field, "5")
    assert not field.property("error")
    _assert_points(_outline(traced_page), [(x + 5.0, y) for x, y in SQUARE])


def test_a_size_of_zero_is_rejected_with_its_reason(traced_page: TracePage) -> None:
    field = traced_page._image_panel._w
    _type(field, "0")

    assert field.text() == "0"
    assert "greater than zero" in field.toolTip()
    _assert_points(_outline(traced_page), SQUARE)


def test_placement_stays_editable_while_the_picture_is_hidden(traced_page: TracePage) -> None:
    traced_page._bg_visible_cb.setChecked(False)

    assert not traced_page._adjust_image_btn.isEnabled()
    _type(traced_page._image_panel._x, "12")
    _assert_points(_outline(traced_page), [(x + 12.0, y) for x, y in SQUARE])


def test_fit_to_bed_keeps_proportions_and_centres_the_turned_picture(
    traced_page: TracePage,
) -> None:
    traced_page._settings.update(machine_bed_width_mm=100.0, machine_bed_height_mm=60.0)
    traced_page._image_panel._rotate_by(90.0)  # 40×20 turned: 20 wide × 40 tall
    traced_page._image_panel.fitToBedRequested.emit()

    # min(100 / 20, 60 / 40) = 1.5 → 60×30, centred on the 100×60 bed.
    assert (traced_page._last_width_mm, traced_page._last_height_mm) == pytest.approx((60, 30))
    assert (traced_page._image_x_mm, traced_page._image_y_mm) == pytest.approx((20, 15))
    assert traced_page._preview_timer.isActive()


def test_centre_on_bed_moves_without_resizing(traced_page: TracePage) -> None:
    traced_page._settings.update(machine_bed_width_mm=100.0, machine_bed_height_mm=60.0)
    traced_page._image_panel.centerOnBedRequested.emit()

    _assert_points(_outline(traced_page), [(x + 30.0, y + 20.0) for x, y in SQUARE])
    assert not traced_page._preview_timer.isActive()
