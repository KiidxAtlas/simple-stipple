"""Draw sidebar revamp: grouped CycleIconButton controls, slot tool,
polygon side count, and independent snap-category toggles.

Reuses the make_view/click_world/bbox helpers from test_canvas_behavior.py
rather than duplicating the synthesized-event plumbing.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import bbox, click_world, make_view

# ── Polyline / Shapes family sidebar cycling ─────────────────────────────────


def test_polyline_family_buttons_select_draw_primitive_directly(qapp):
    """Path icons are direct-select, not cycling: clicking a tool's own
    icon selects that exact primitive regardless of what's currently
    active."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    assert v._draw_primitive == "polyline"

    sb._polyline_buttons["spline"].click()
    assert v._draw_primitive == "spline"
    sb._polyline_buttons["arc"].click()
    assert v._draw_primitive == "arc"
    # clicking the already-active tool re-selects it, it does not cycle on
    sb._polyline_buttons["arc"].click()
    assert v._draw_primitive == "arc"


def test_polyline_family_bezier_button_stays_in_draw_mode(qapp):
    """Bezier is a draw-mode primitive like polyline/spline/arc, not a
    separate mode — selecting it must not hide the sidebar or leave draw
    mode (regression: it used to be a separate pen mode that silently hid
    the panel)."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    sb._polyline_buttons["bezier"].click()
    assert v.get_mode() == "draw"
    assert v._draw_primitive == "bezier"
    assert v._draw_sidebar_visible is True


def test_polyline_family_each_button_selects_its_own_primitive(qapp):
    """Regression test: selecting any Path icon must stay in draw mode and
    keep the sidebar open — bezier used to be a separate "pen" mode that
    silently hid the sidebar."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar

    for want in ("spline", "arc", "bezier", "polyline"):
        sb._polyline_buttons[want].click()
        assert v.get_mode() == "draw"
        assert v._draw_primitive == want
        assert v._draw_sidebar_visible is True


def test_shapes_family_button_selects_slot_directly(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    sb._shapes_buttons["slot"].click()
    assert v._draw_primitive == "slot"

    click_world(v, 100.0, 100.0)
    click_world(v, 130.0, 110.0)
    assert v.poly_count == 1
    e = v._entities[0]
    assert e.kind == "slot"
    x0, y0, x1, y1 = bbox(e.points)
    assert (x1 - x0, y1 - y0) == (pytest.approx(30.0, abs=0.5), pytest.approx(10.0, abs=0.5))


def test_new_shape_tools_remain_visible_with_an_old_custom_list(qapp):
    v = make_view(qapp, [])
    v.set_draw_sidebar_shape_tools(["circle"])
    tools = v._draw_sidebar._shape_tools
    assert tools[:2] == ["rounded_rectangle", "star"]
    assert "circle" in tools


def test_shape_sidebar_uses_distinct_matching_icons(qapp):
    """Rounded Rectangle and Star must not reuse Rectangle/Polygon glyphs."""
    from src.ui.components import tool_icon

    def pixels(icon):
        image = icon.pixmap(20, 20).toImage()
        return bytes(image.constBits())

    assert pixels(tool_icon("rounded_rectangle")) != pixels(tool_icon("rectangle"))
    assert pixels(tool_icon("star")) != pixels(tool_icon("polygon"))


def test_shapes_family_selecting_polygon_opens_no_prompt(qapp):
    """Regression test: selecting Polygon must not pop any HUD prompt —
    side count is now a live stepper shown during the actual drag, not a
    modal fired the instant the tool is selected."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    sb._shapes_buttons["polygon"].click()
    assert v._draw_primitive == "polygon"
    assert getattr(v, "_hud_prompt_edit", None) is None


def test_polygon_side_count_used_when_drawing(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._draw_polygon_sides = 8
    v._set_draw_primitive("polygon")
    click_world(v, 100.0, 100.0)
    click_world(v, 110.0, 110.0)
    e = v._entities[0]
    assert e.kind == "polygon"
    assert e.meta["sides"] == 8
    # closed polygon points list repeats the first vertex at the end.
    assert len(e.points) - 1 == 8


def test_polygon_is_drawn_center_first_like_circle(qapp):
    """Regression test: polygon used to be corner-to-corner (bounding-box
    midpoint as center); it must now behave like circle — first click is
    the center, drag distance is the radius."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("polygon")
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    e = v._entities[0]
    assert e.meta["center"] == pytest.approx((0.0, 0.0), abs=1e-6)
    assert e.meta["radius"] == pytest.approx(10.0, abs=1e-6)
    for x, y in e.points[:-1]:
        assert math.hypot(x, y) == pytest.approx(10.0, abs=1e-6)


def test_polygon_sides_spinbox_live_updates_without_committing(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("polygon")
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = (0.0, 0.0)
    v._draw_shape_cursor_w = (10.0, 0.0)
    v._show_shape_dim_inputs()

    spin = v._draw_shape_sides_spin
    assert spin is not None
    assert spin.value() == v._draw_polygon_sides

    spin.setValue(9)
    assert v._draw_polygon_sides == 9
    # changing the spinbox must not finalize the shape
    assert v.poly_count == 0
    assert v._draw_shape_preview_active is True


def test_star_point_count_is_independent_and_used_when_drawing(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("star")
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = (100.0, 100.0)
    v._draw_shape_cursor_w = (110.0, 100.0)
    v._show_shape_dim_inputs()
    spin = v._draw_shape_sides_spin
    assert spin is not None
    assert spin.value() == 5
    spin.setValue(8)
    assert v._draw_star_points == 8
    v._commit_shape_preview()
    entity = v._entities[0]
    assert entity.kind == "star"
    assert entity.meta["points"] == 8
    assert len(entity.points) == 17


def test_slot_gets_size_hud_fields_like_rectangle(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("slot")
    assert v._shape_primitive_active() is True
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = (0.0, 0.0)
    v._draw_shape_cursor_w = (20.0, 10.0)
    v._show_shape_dim_inputs()
    assert v._draw_shape_w_edit is not None
    assert v._draw_shape_h_edit is not None
    # slot has a fixed regular width/length, not a per-vertex side count
    assert v._draw_shape_sides_spin is None


# ── Constraint (snap master/grid/vertex/edge/angle toggles now live only
# in the Precision bar — see test_precision_bar.py) ──────────────────────────


def test_constraint_button_selects_free_h_v_45_directly(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("polyline")
    sb = v._draw_sidebar
    assert v._draw_constraint_lock is None

    sb._constraint_button._select_state(1)
    assert v._draw_constraint_lock == "H"
    sb._constraint_button._select_state(2)
    assert v._draw_constraint_lock == "V"
    sb._constraint_button._select_state(3)
    assert v._draw_constraint_lock == "45"
    sb._constraint_button._select_state(0)
    assert v._draw_constraint_lock is None


def test_constraint_button_disabled_outside_line_and_polyline(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    v._set_draw_primitive("rectangle")
    assert v._draw_sidebar._constraint_button.isEnabled() is False


# ── Split / dimension (construction/measure now live only in the
# Precision bar — see test_precision_bar.py) ─────────────────────────────────


def test_split_button_toggles_view_state(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar

    before_split = v._draw_split_enabled
    sb._split_button.click()
    assert v._draw_split_enabled is (not before_split)


def test_dimension_button_toggles_its_mode(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar

    assert v._dimension_mode is False
    sb._dimension_button.click()
    assert v._dimension_mode is True


# ── Contextual polyline-editing action row ───────────────────────────────────


def test_polyline_action_buttons_enable_state_tracks_point_count(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    assert sb._finish_button.isEnabled() is False
    assert sb._close_button.isEnabled() is False
    assert sb._undo_button.isEnabled() is False

    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    assert sb._undo_button.isEnabled() is True
    assert sb._finish_button.isEnabled() is True
    click_world(v, 10.0, 10.0)
    assert sb._close_button.isEnabled() is True


def test_hover_flyout_shows_beside_sidebar_not_overlapping_it(qapp):
    """Regression test: the flyout used to be centered under the button,
    which is narrower than most of its labels, so it spilled over and
    covered neighboring sidebar content. It must now sit fully to the
    right of the whole panel."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    v.show()
    sb = v._draw_sidebar
    sb.show()

    btn = sb._constraint_button
    btn._show_flyout()
    assert btn._flyout is not None
    panel_rect = btn._panel_global_rect()
    assert btn._flyout.x() >= panel_rect.right()
    screen = qapp.screenAt(panel_rect.center()) or qapp.primaryScreen()
    available = screen.availableGeometry()
    assert btn._flyout.x() >= available.left()
    assert btn._flyout.y() >= available.top()
    assert btn._flyout.x() + btn._flyout.width() <= available.right() + 1
    assert btn._flyout.y() + btn._flyout.height() <= available.bottom() + 1


def test_flyout_survives_mouse_passing_through_the_gap_to_reach_it(qapp):
    """Regression test: real mouse movement from the button to the flyout
    crosses empty space belonging to neither widget (the flyout sits
    beside the panel, not flush against the button). An immediate
    leaveEvent check used to hide the flyout while the cursor was still
    mid-transit; it must now survive as long as the cursor reaches the
    button or flyout within a short grace period."""
    from PySide6.QtCore import QEvent, QEventLoop, QTimer
    from PySide6.QtGui import QCursor

    v = make_view(qapp, [])
    v.set_mode("draw")
    v.show()
    sb = v._draw_sidebar
    sb.show()
    btn = sb._constraint_button
    btn._show_flyout()
    flyout = btn._flyout
    assert flyout is not None

    QCursor.setPos(0, 0)  # cursor leaves into the gap between button and flyout
    btn.leaveEvent(QEvent(QEvent.Type.Leave))
    assert btn._flyout is not None  # not torn down immediately

    QCursor.setPos(flyout.mapToGlobal(flyout.rect().center()))  # arrives in time
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert btn._flyout is not None


def test_flyout_hides_once_grace_period_elapses_with_cursor_still_away(qapp):
    from PySide6.QtCore import QEvent, QEventLoop, QTimer
    from PySide6.QtGui import QCursor

    v = make_view(qapp, [])
    v.set_mode("draw")
    v.show()
    sb = v._draw_sidebar
    sb.show()
    btn = sb._constraint_button
    btn._show_flyout()
    assert btn._flyout is not None

    QCursor.setPos(0, 0)
    btn.leaveEvent(QEvent(QEvent.Type.Leave))
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert btn._flyout is None


def test_sidebar_width_is_draggable_and_clamped(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    from src.core.settings import MAX_DRAW_SIDEBAR_WIDTH, MIN_DRAW_SIDEBAR_WIDTH

    changes = []
    v.drawSidebarWidthChanged.connect(changes.append)

    sb._apply_width(160)
    sb._on_width_committed()
    assert sb.width() == 160
    assert v._draw_sidebar_width == 160
    assert changes == [160]

    sb._apply_width(MAX_DRAW_SIDEBAR_WIDTH + 500)
    assert sb.width() == MAX_DRAW_SIDEBAR_WIDTH
    sb._apply_width(MIN_DRAW_SIDEBAR_WIDTH - 50)
    assert sb.width() == MIN_DRAW_SIDEBAR_WIDTH


def test_resize_handle_drag_uses_stable_delta_from_press(qapp):
    """Regression test: the handle used to compute delta as
    (event global x) - (handle's own global x), which drifts every time the
    sidebar itself resizes (the handle moves with it), making the drag
    non-functional in practice. It must track delta from the fixed
    press-time anchor instead."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from src.ui.widgets.canvas.draw_sidebar import _ResizeHandle

    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    handle = next(c for c in sb.children() if isinstance(c, _ResizeHandle))
    assert handle.width() >= 12
    start_width = sb.width()

    def move_event(global_x: float) -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(0, 0),
            QPointF(global_x, 0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(0, 0),
        QPointF(500.0, 0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    handle.mousePressEvent(press)
    handle.mouseMoveEvent(move_event(540.0))
    assert sb.width() == start_width + 40
    # a second move from the SAME press must add to the press-time width,
    # not compound on top of the previous move (which the old "distance
    # from handle's own origin" math effectively did once the handle moved).
    handle.mouseMoveEvent(move_event(520.0))
    assert sb.width() == start_width + 20


def test_sidebar_groups_use_whitespace_not_nested_frames(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QWidget

    v = make_view(qapp, [])
    v.set_mode("draw")
    section = v._draw_sidebar._polyline_buttons["polyline"].parentWidget().parentWidget()
    assert isinstance(section, QWidget)
    assert not isinstance(section, QFrame)
    assert v._draw_sidebar._polyline_buttons["polyline"].size().width() >= 44
    assert v._draw_sidebar._polyline_buttons["polyline"].size().height() >= 40
    qapp.processEvents()
    assert (
        v._draw_sidebar._content.minimumSizeHint().width()
        <= v._draw_sidebar._scroll.viewport().width()
    )
    assert (
        v._draw_sidebar._scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def test_shape_dimension_fields_stay_inside_canvas_near_edges(qapp):
    v = make_view(qapp, [])
    v.resize(500, 320)
    v.set_mode("draw")
    v._set_draw_primitive("polygon")
    v._draw_shape_preview_active = True
    v._draw_shape_anchor_w = v._c2w(490, 310)
    v._draw_shape_cursor_w = v._c2w(498, 318)

    v._show_shape_dim_inputs()

    controls = [v._draw_shape_w_edit, v._draw_shape_h_edit, v._draw_shape_sides_spin]
    assert all(control is not None for control in controls)
    assert all(control.x() >= 8 and control.y() >= 8 for control in controls)
    assert all(control.x() + control.width() <= v.width() - 8 for control in controls)
    assert all(control.y() + control.height() <= v.height() - 8 for control in controls)


def test_sidebar_sections_can_be_hidden_and_reordered(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    original = v._draw_sidebar

    v.set_draw_sidebar_sections(["shapes", "path"])
    rebuilt = v._draw_sidebar
    assert rebuilt is not original
    assert v._draw_sidebar_sections == ["shapes", "path"]
    # buttons for hidden sections ("editing" etc.) still exist and are
    # safely callable — state-sync methods must never crash just because
    # a section isn't shown.
    rebuilt.set_polyline_actions_enabled(can_finish=True, can_close=False, can_undo=True)
    assert rebuilt._finish_button.isEnabled() is True


def test_customize_dialog_applies_unchecked_and_reordered_sections(qapp):
    from PySide6.QtCore import Qt

    from src.ui.widgets.dialogs.customize_dialogs import DrawSidebarCustomizeDialog

    dlg = DrawSidebarCustomizeDialog(sections=["path", "shapes", "text"])
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "text":
            item.setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert dlg.get_sections() == ["path", "shapes"]


def test_customize_dialog_falls_back_to_defaults_without_path_and_shapes(qapp):
    from PySide6.QtCore import Qt

    from src.core.settings import DEFAULT_DRAW_SIDEBAR_SECTIONS
    from src.ui.widgets.dialogs.customize_dialogs import DrawSidebarCustomizeDialog

    dlg = DrawSidebarCustomizeDialog(sections=["path"])
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) != "path":
            item.setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert dlg.get_sections() == list(DEFAULT_DRAW_SIDEBAR_SECTIONS)


def test_customize_dialog_hides_and_reorders_individual_path_shape_icons(qapp):
    """The per-icon lists (not just whole sections) must round-trip through
    the dialog and drive which Path/Shapes icons the sidebar actually
    builds sections from."""
    from PySide6.QtCore import Qt

    from src.ui.widgets.dialogs.customize_dialogs import DrawSidebarCustomizeDialog

    dlg = DrawSidebarCustomizeDialog(
        path_tools=["polyline", "spline", "arc", "bezier"],
        shape_tools=["rectangle", "slot", "circle", "ellipse", "polygon"],
    )
    for i in range(dlg._path_list.count()):
        item = dlg._path_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "bezier":
            item.setCheckState(Qt.CheckState.Unchecked)
    for i in range(dlg._shape_list.count()):
        item = dlg._shape_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "ellipse":
            item.setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert "bezier" not in dlg.get_path_tools()
    assert "ellipse" not in dlg.get_shape_tools()

    v = make_view(qapp, [])
    v.set_mode("draw")
    v.set_draw_sidebar_path_tools(dlg.get_path_tools())
    v.set_draw_sidebar_shape_tools(dlg.get_shape_tools())
    sb = v._draw_sidebar
    assert "bezier" not in sb._path_tools
    assert "ellipse" not in sb._shape_tools
    # the button objects still exist (state-sync must never crash), they
    # just aren't added to the section's layout.
    assert "bezier" in sb._polyline_buttons
    assert "ellipse" in sb._shapes_buttons


def test_customize_dialog_path_shape_lists_require_at_least_one_checked(qapp):
    from PySide6.QtCore import Qt

    from src.core.settings import (
        DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
        DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    )
    from src.ui.widgets.dialogs.customize_dialogs import DrawSidebarCustomizeDialog

    dlg = DrawSidebarCustomizeDialog()
    for i in range(dlg._path_list.count()):
        dlg._path_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    for i in range(dlg._shape_list.count()):
        dlg._shape_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert dlg.get_path_tools() == list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
    assert dlg.get_shape_tools() == list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)


def test_context_menu_customize_can_hide_sections_but_keeps_view(qapp):
    from PySide6.QtCore import Qt

    from src.ui.widgets.dialogs.customize_dialogs import ContextMenuCustomizeDialog

    dlg = ContextMenuCustomizeDialog(sections=["create", "transform", "view"])
    for i in range(dlg._list.count()):
        dlg._list.item(i).setCheckState(Qt.CheckState.Unchecked)
    dlg._apply()
    assert dlg.get_sections() == ["view"]


def test_smoothing_button_cycles_and_stays_in_sync_with_settings(qapp):
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar
    assert v._smoothing_method == "chaikin"
    assert sb._smoothing_button.current_state_id == "chaikin"

    sb._smoothing_button._select_state(1)
    assert v._smoothing_method == "gaussian"
    sb._smoothing_button._select_state(2)
    assert v._smoothing_method == "catmull_rom"

    # setting it the way Settings' own combo box does must also update
    # the sidebar button, not just the other direction.
    v.set_smoothing_method("chaikin")
    assert sb._smoothing_button.current_state_id == "chaikin"


def test_sidebar_smoothing_change_emits_persistence_signal(qapp):
    """Regression test: picking a method from the sidebar used to call the
    silent set_smoothing_method() directly, so nothing ever told app.py to
    save it to settings.json or echo it to other tabs. The sidebar's
    callback must fire smoothingMethodChanged so app.py can persist it."""
    v = make_view(qapp, [])
    v.set_mode("draw")
    sb = v._draw_sidebar

    seen: list[str] = []
    v.smoothingMethodChanged.connect(seen.append)

    sb._smoothing_button._select_state(1)
    assert v._smoothing_method == "gaussian"
    assert seen == ["gaussian"]

    # the plain settings-apply path (used at startup / echoed from other
    # tabs) must NOT re-emit the signal, or persisting would loop forever.
    v.set_smoothing_method("catmull_rom")
    assert seen == ["gaussian"]


def test_draw_sidebar_always_visible_keeps_panel_shown_outside_draw_mode(qapp):
    v = make_view(qapp, [])
    assert v._draw_sidebar_visible is False

    v.set_draw_sidebar_always_visible(True)
    assert v._draw_sidebar_visible is True

    v.set_mode("draw")
    assert v._draw_sidebar_visible is True
    v.set_mode("select")
    assert v._draw_sidebar_visible is True  # stays shown, override active

    v.set_draw_sidebar_always_visible(False)
    assert v._draw_sidebar_visible is False  # not in draw mode, so it hides
