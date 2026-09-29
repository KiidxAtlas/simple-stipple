"""Focused behavior checks for the discoverable Draw palette and dimension keys."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.canvas.widgets.draw_sidebar import DrawSidebar


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_sidebar(
    parent: QWidget | None = None,
    *,
    sections: list[str] | None = None,
    on_shapes=None,
) -> DrawSidebar:
    def noop(*_args):
        return None

    return DrawSidebar(
        parent=parent,
        on_polyline_family=noop,
        on_shapes_family=on_shapes or noop,
        on_text=noop,
        on_arc_mode=noop,
        on_constraint=noop,
        on_split=noop,
        on_dimension=noop,
        on_smoothing_method=noop,
        on_finish_open=noop,
        on_close_edit=noop,
        on_undo_point=noop,
        on_cancel_draw=noop,
        on_back_to_select=noop,
        sections=sections,
    )


def test_default_draw_sidebar_stays_compact_and_marks_the_active_tool(
    app: QApplication,
) -> None:
    sidebar = _make_sidebar()
    sidebar.show()
    app.processEvents()

    headings = [
        label.text()
        for label in sidebar.findChildren(QLabel)
        if label.property("role") == "section-title" and label.isVisibleTo(sidebar)
    ]
    assert headings == ["Path", "Shapes", "Text", "Mode"]

    assert len(sidebar._path_buttons) == 4
    assert len(sidebar._shape_buttons) == 7
    assert all(button.isVisibleTo(sidebar) for button in sidebar._path_buttons.values())
    assert all(button.isVisibleTo(sidebar) for button in sidebar._shape_buttons.values())
    sidebar.set_active_tool("rounded_rectangle")
    assert sidebar._shape_buttons["rounded_rectangle"].isChecked()
    assert not sidebar._shape_buttons["rectangle"].isChecked()
    sidebar.close()


def test_custom_draw_section_visibility_and_order_are_preserved(app: QApplication) -> None:
    sidebar = _make_sidebar(sections=["shapes", "path"])
    sidebar.resize(sidebar.width(), 500)
    sidebar.show()
    app.processEvents()

    headings = [
        label.text()
        for label in sidebar.findChildren(QLabel)
        if label.property("role") == "section-title" and label.isVisibleTo(sidebar)
    ]
    assert headings == ["Shapes", "Path"]
    assert sidebar._sections == ["shapes", "path"]
    sidebar.close()


def test_shape_icons_are_direct_and_restore_canvas_focus(app: QApplication) -> None:
    parent = QWidget()
    parent.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    selected: list[str] = []
    sidebar = _make_sidebar(parent, on_shapes=selected.append)
    parent.resize(260, 800)
    sidebar.resize(220, 780)
    sidebar.show()
    parent.show()
    app.processEvents()

    button = sidebar._shape_buttons["rectangle"]
    parent.setFocus()
    app.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    app.processEvents()
    assert selected == ["rectangle"]
    assert QApplication.focusWidget() is parent

    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    app.processEvents()
    assert selected == ["rectangle", "rectangle"]
    assert QApplication.focusWidget() is parent
    sidebar.close()
    parent.close()


def test_tool_click_does_not_steal_focus_back_from_an_editor(app: QApplication) -> None:
    parent = QWidget()
    parent.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    sidebar = _make_sidebar(parent)
    editor = QLineEdit(parent)
    parent.resize(260, 800)
    sidebar.resize(220, 780)
    editor.move(0, 0)
    sidebar.show()
    parent.show()
    editor.show()
    app.processEvents()

    editor.setFocus()
    app.processEvents()
    button = sidebar._shape_buttons["circle"]
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    app.processEvents()
    assert QApplication.focusWidget() is not parent
    sidebar.close()
    parent.close()


def test_draw_shape_tab_and_backtab_switch_between_live_size_fields(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.show()
    app.processEvents()
    canvas.set_mode("draw")
    canvas._draw_primitive = "rectangle"
    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = (0.0, 0.0)
    canvas._draw_shape_cursor_w = (30.0, 20.0)
    canvas._show_shape_dim_inputs()
    width = canvas._draw_shape_w_edit
    height = canvas._draw_shape_h_edit
    assert width is not None and height is not None

    width.setFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Backtab)
    assert height.hasFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Tab)
    assert width.hasFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Tab)
    assert height.hasFocus()
    canvas.close()


def test_draw_line_tab_and_backtab_switch_between_length_and_angle(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.show()
    app.processEvents()
    canvas.set_mode("draw")
    canvas._draw_primitive = "polyline"
    canvas._draw_pts = [(0.0, 0.0)]
    canvas._show_dim_inputs()
    length = canvas._dim_distance_edit
    angle = canvas._dim_angle_edit
    assert length is not None and angle is not None

    length.setFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Backtab)
    assert angle.hasFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Tab)
    assert length.hasFocus()
    canvas.close()


def test_a_focuses_line_angle_without_overwriting_existing_navigation(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.show()
    app.processEvents()
    canvas.set_mode("draw")
    canvas._draw_primitive = "polyline"
    canvas._draw_pts = [(0.0, 0.0)]

    QTest.keyClick(canvas, Qt.Key.Key_A)
    app.processEvents()
    assert canvas._dim_angle_edit is not None
    assert canvas._dim_angle_edit.hasFocus()
    canvas.close()


def test_dxf_canvas_sidebar_selection_returns_focus_to_canvas(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(900, 650)
    canvas.show()
    app.processEvents()
    canvas.set_mode("draw")
    canvas.setFocus()
    app.processEvents()

    button = canvas._draw_sidebar._shape_buttons["rectangle"]
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    app.processEvents()
    assert canvas._draw_primitive == "rectangle"
    assert QApplication.focusWidget() is canvas
    canvas.close()


@pytest.mark.parametrize(
    "anchor,cursor,rotation",
    [
        ((0.0, 0.0), (96.0, 24.0), 0.0),
        ((96.0, 24.0), (0.0, 0.0), 0.0),
        ((0.0, 0.0), (24.0, 96.0), 90.0),
        ((24.0, 96.0), (0.0, 0.0), 90.0),
    ],
)
def test_slot_drag_commits_vertical_and_horizontal_in_all_drag_directions(
    app: QApplication, anchor: tuple[float, float], cursor: tuple[float, float], rotation: float
) -> None:
    canvas = DxfCanvas()
    canvas.set_mode("draw")
    canvas._draw_primitive = "slot"
    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = anchor
    canvas._draw_shape_cursor_w = cursor

    assert canvas._commit_shape_preview()
    entity = canvas._entities[-1]
    xs = [x for x, _ in entity.points]
    ys = [y for _, y in entity.points]
    assert max(xs) - min(xs) == pytest.approx(abs(cursor[0] - anchor[0]))
    assert max(ys) - min(ys) == pytest.approx(abs(cursor[1] - anchor[1]))

    assert entity.meta is not None
    meta = entity.meta
    assert meta["length"] == pytest.approx(
        max(abs(cursor[0] - anchor[0]), abs(cursor[1] - anchor[1]))
    )
    assert meta["width"] == pytest.approx(
        min(abs(cursor[0] - anchor[0]), abs(cursor[1] - anchor[1]))
    )
    assert meta["rotation"] == rotation
    canvas.close()
