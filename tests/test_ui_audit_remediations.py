"""Focused regression checks added by the UI layout and behavior audit."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QIcon, QKeyEvent, QMouseEvent, QPalette, QWheelEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QToolButton, QWidget
from shapely.geometry import Point, Polygon

import simple_stipple.ui.dialogs.customize_dialogs as customize_dialogs
from simple_stipple.canvas.layers.logic import describe_polyline
from simple_stipple.canvas.operations.hud_text import HudTextService, text_to_polylines
from simple_stipple.canvas.snap import SnapEngine
from simple_stipple.canvas.widget import _CONTEXT_STATIC_ACTION_IDS, DxfCanvas
from simple_stipple.canvas.widgets.draw_sidebar import DrawSidebar, _ResizeHandle
from simple_stipple.canvas.widgets.precision_bar import CanvasPrecisionBar
from simple_stipple.canvas.widgets.properties_panel import CanvasPropertiesPanel
from simple_stipple.canvas.widgets.status_strip import CanvasStatusStrip
from simple_stipple.canvas.widgets.toolbar import canvas_toolbar
from simple_stipple.document.model import CanvasDocument, EntityRecord
from simple_stipple.engine.cad.constraints import GeometricConstraint, solve_constraints
from simple_stipple.engine.editing.split import split_paths
from simple_stipple.engine.formats.dxf import polylines_to_outline
from simple_stipple.engine.patterns.fill import FillSpec, apply_fill
from simple_stipple.engine.patterns.processing import PatternProcessor
from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.help import HelpDialog
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.pattern.treatments import treatment_kind
from simple_stipple.features.pattern.workers import compute_preview
from simple_stipple.features.pattern.zones import (
    highlight_zone_on_canvas,
    select_zone_for_canvas_selection,
)
from simple_stipple.platform.settings import (
    DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS,
    MIN_DRAW_SIDEBAR_WIDTH,
)
from simple_stipple.platform.updates import UpdateInfo
from simple_stipple.ui.components.cycle_button import CycleIconButton
from simple_stipple.ui.components.focus import CanvasEscapeRouter
from simple_stipple.ui.components.inputs import NoWheelSlider
from simple_stipple.ui.components.workflow import (
    OperationProgress,
    WorkflowStepper,
    set_status_label,
)
from simple_stipple.ui.dialogs.customize_dialogs import (
    _CONTEXT_ACTION_LABELS,
    ContextMenuActionCustomizeDialog,
    _build_list,
    _checked_keys,
    _ordered_keys,
)
from simple_stipple.ui.dialogs.export_preflight import export_preflight
from simple_stipple.ui.dialogs.import_dialog import VectorImportModeDialog
from simple_stipple.ui.dialogs.settings_dialog import SettingsDialog
from simple_stipple.ui.dialogs.update_dialog import UpdateDialog
from simple_stipple.ui.notifications import notification_history
from simple_stipple.ui.style.theme import STATUS_OK, accessibility_palette, load_app_qss


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


def test_convert_status_labels_are_owned_by_their_subtabs(app: QApplication) -> None:
    """Completion statuses belong in the sticky footer, never a popup window."""
    convert = ConvertPage(settings={})
    for subtab in (
        convert._fvi_subtab,
        convert._fix_subtab,
        convert._svg_subtab,
        convert._svg_dxf_subtab,
    ):
        assert subtab._status.parent() is subtab
    convert.close()


def test_visible_shapes_remain_selectable_across_active_layers(app: QApplication) -> None:
    document = CanvasDocument(
        entities=[
            EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], layer="Cut"),
            EntityRecord(points=[(0.0, 1.0), (1.0, 1.0)], layer="Engrave"),
        ],
        layer_order=["Cut", "Engrave"],
        active_layer="Cut",
    )
    ids = [entity.id for entity in document.entities]
    document.selection = set(ids)

    assert all(document.entity_selectable_by_id(entity_id) for entity_id in ids)
    assert not document.drop_inactive_selection()
    assert document.selection == set(ids)

    canvas = DxfCanvas()
    canvas.set_layer_model(["Cut", "Engrave"], "Cut")
    canvas.add_polylines_state([[(0.0, 0.0), (1.0, 0.0)]])
    canvas.set_active_layer("Engrave")
    canvas.add_polylines_state([[(0.0, 1.0), (1.0, 1.0)]])
    canvas.set_active_layer("Cut")
    canvas.select_all()
    assert len(canvas.get_selected_ids()) == 2
    canvas.close()


def test_outline_transfer_preserves_source_layers(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys(
        [
            {"points": [(0.0, 0.0), (4.0, 0.0)], "layer": "Cut"},
            {"points": [(0.0, 1.0), (4.0, 1.0)], "layer": "Engrave"},
        ],
        source_label="Draft selection",
    )
    assert [page._canvas._entities_by_id[eid].layer for eid in page._outline_ids] == [
        "Cut",
        "Engrave",
    ]
    page.shutdown()
    page.close()


def test_fill_is_primary_pattern_workflow_not_an_advanced_control(app: QApplication) -> None:
    page = PatternPage(settings={"pattern_advanced_mode": False})

    page._set_advanced_mode(False)

    assert not page._fill_section.isHidden()
    assert page._engraving_section.isHidden()
    page.shutdown()
    page.close()


def test_knife_splits_when_endpoints_land_on_closed_shape_boundary() -> None:
    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
    result = split_paths(square, [(0.0, 5.0), (10.0, 5.0)])
    assert result.changed
    assert len(result.paths) == 2
    # A line fully inside a closed region must still not become an invisible cut.
    assert not split_paths(square, [(2.0, 5.0), (8.0, 5.0)]).changed


def test_curve_split_retains_the_drawn_curve_instead_of_using_its_endpoint_chord() -> None:
    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
    curve = [(0.0, 5.0), (2.5, 7.0), (7.5, 7.0), (10.0, 5.0)]

    result = split_paths(square, curve)

    assert result.changed
    assert len(result.paths) == 2
    split_vertices = {point for piece in result.paths for point in piece.points}
    assert (2.5, 7.0) in split_vertices
    assert (7.5, 7.0) in split_vertices


def test_none_pattern_refreshes_and_keeps_export_action_available(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = PatternPage(settings={})
    scheduled: list[bool] = []
    monkeypatch.setattr(page, "_schedule_preview", lambda *_args: scheduled.append(True))

    page._switch_pattern("— None —")
    assert scheduled

    page._update_preview_controls()
    assert page._gen_btn.isEnabled()
    page.shutdown()
    page.close()


def test_pattern_export_format_selection_does_not_execute_export(app: QApplication) -> None:
    """Changing format must not also start a potentially expensive export."""
    page = PatternPage(settings={})
    calls: list[bool] = []
    page._run_remembered_export = lambda: calls.append(True)

    page._select_export_kind("laserstar")

    assert page._export_default == "laserstar"
    assert calls == []
    assert "LaserStar" in page._gen_btn.text()
    page.shutdown()
    page.close()


def test_selection_actions_stay_on_canvas_and_offer_grouping(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (1.0, 0.0)],
            [(0.0, 1.0), (1.0, 1.0)],
        ]
    )
    canvas.select_all()

    actions = canvas.get_context_actions()

    assert [action[0] for action in actions] == ["delete-selection", "group-selection"]
    assert canvas.trigger_context_action("group-selection")
    assert all(entity.group is not None for entity in canvas._entities)
    canvas.close()


def test_vector_import_mode_makes_replace_the_safe_default(app: QApplication) -> None:
    dialog = VectorImportModeDialog(
        "/tmp/example.svg",
        format_name="SVG",
        has_existing_geometry=True,
    )
    assert not dialog.append_mode()
    dialog._append.setChecked(True)
    assert dialog.append_mode()
    dialog.close()


def test_status_readiness_can_keep_import_details_visible(app: QApplication) -> None:
    strip = CanvasStatusStrip()
    strip.set_readiness("Import notes", "warn", "Skipped unsupported SVG arc commands")

    assert strip._readiness_chip.toolTip() == "Skipped unsupported SVG arc commands"
    assert strip._readiness_chip.accessibleDescription() == "Skipped unsupported SVG arc commands"
    strip.close()


def test_inspector_sliders_do_not_consume_scroll_wheel_input(app: QApplication) -> None:
    slider = NoWheelSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(50)
    wheel = QWheelEvent(
        QPointF(4, 4),
        QPointF(4, 4),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    slider.wheelEvent(wheel)
    assert slider.value() == 50


def test_canvas_numeric_controls_meet_the_shared_target_size(app: QApplication) -> None:
    canvas = DxfCanvas()
    panel = CanvasPropertiesPanel(canvas)
    precision = CanvasPrecisionBar(canvas)

    assert panel._x.minimumHeight() >= 30
    assert panel._aspect_lock_btn.minimumHeight() >= 30
    assert precision._pan_btn.minimumHeight() >= 30
    assert precision._spacing_inc.height() >= 30
    canvas.close()
    panel.close()
    precision.close()


def test_canvas_size_hud_uses_semantic_styling_and_recovers_from_invalid_input(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state([[(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]])
    canvas.select_all()

    canvas._show_size_hud()

    assert canvas._size_w_edit is not None
    assert canvas._size_h_edit is not None
    assert canvas._size_w_edit.property("role") == "canvas-hud-input"
    assert canvas._size_h_edit.accessibleName() == "Selected height"

    canvas._size_w_edit.setText("not a number")
    canvas._apply_size_hud()
    assert canvas._size_w_edit.property("error") == "true"
    canvas._clear_size_hud_error("")
    assert canvas._size_w_edit.property("error") is None

    canvas._size_w_edit.setText("0")
    canvas._size_h_edit.setText("2")
    canvas._apply_size_hud()
    assert canvas._size_w_edit.property("error") == "true"
    assert canvas._size_h_edit.property("error") is None
    canvas.close()


def test_canvas_context_actions_are_canvas_owned_and_available_in_status_strip(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.set_mode("draw")
    canvas._draw_pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]
    actions = canvas.get_context_actions()
    assert [action[0] for action in actions] == [
        "undo-point",
        "finish-path",
        "close-path",
        "cancel-draw",
    ]
    assert not canvas.trigger_context_action("export")

    strip = CanvasStatusStrip()
    requested: list[str] = []
    strip.contextActionRequested.connect(requested.append)
    strip.set_context_actions(actions)
    strip._context_buttons[0].click()
    assert requested == ["undo-point"]
    canvas.close()


def test_image_engraving_actions_stay_visible_and_keyboard_reachable(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page._update_preview_controls()
    assert page._engrave_export_btn.isEnabled()
    assert page._export_actions["engraving"].isEnabled()
    assert not page._engrave_remove_btn.isEnabled()

    page._engraving_image_path = "source.png"
    page._refresh_engraving_ui()
    assert page._engrave_remove_btn.isEnabled()
    page.show()
    app.processEvents()
    page._on_engraving_canvas_key("tab")
    assert page._engrave_w.hasFocus()
    page._remove_engraving_image()
    assert page._engraving_image_path == ""
    assert not page._engrave_remove_btn.isEnabled()
    page.shutdown()
    page.close()


def test_image_engraving_has_one_export_terminal_and_safe_clip_choice(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    assert page._engrave_export_btn.text() == "Use engraving export"

    # The clip mask is the region carrying the Engrave treatment — no target
    # combo, and no need to duplicate the shape to use it as both.
    ring = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0), (0.0, 0.0)]
    circle = [(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0), (10.0, 10.0)]
    page.load_outline_polys([ring, circle])
    assert page._engraving_mask_polys() == [list(ring), list(circle)]
    page._treatments[page._outline_ids[1]] = {"kind": "engrave", "pattern": "— None —"}
    assert page._engraving_mask_polys() == [list(circle)]

    page._use_engraving_export()
    assert page._export_default == "engraving"
    assert "Export engraving assets" in page._gen_btn.text()
    page.shutdown()
    page.close()


def test_draw_guidance_describes_the_actual_shape_gesture(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.set_mode("draw")
    canvas._draw_primitive = "rectangle"
    guidance, _tone = canvas.get_command_guidance()
    assert guidance == "Rectangle: drag to size · Esc exits"
    canvas.close()


def test_workflow_strip_is_honest_noninteractive_progress(app: QApplication) -> None:
    stepper = WorkflowStepper(("Input", "Preview", "Export"))
    for button in stepper.findChildren(QToolButton):
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert button.minimumHeight() >= 24 or button.sizeHint().height() >= 24


def test_workflow_error_uses_danger_guidance_without_freezing_wrapped_content(
    app: QApplication,
) -> None:
    stepper = WorkflowStepper(
        ("Input", "Preview", "Export"), description="A description that may wrap in narrow layouts."
    )
    stepper.set_step_states(
        ["complete", "error", "pending"], {1: "Fix the outline, then preview again."}
    )

    assert stepper._labels[1].property("state") == "error"
    assert stepper._guidance is not None
    assert stepper._guidance.property("tone") == "danger"
    assert stepper.maximumHeight() > stepper.sizeHint().height()


def test_operation_progress_uses_semantic_role_and_cancellable_guidance(app: QApplication) -> None:
    progress = OperationProgress()
    assert progress.property("role") == "operation-progress"
    assert progress._cancel.toolTip() == "Cancel the current operation"
    progress.fail("The export could not be written. Choose another folder and try again.")
    assert progress.property("tone") == "danger"
    assert not progress.isHidden()
    assert progress._cancel.isHidden()


def test_canvas_hud_validation_state_clears_when_the_user_corrects_input(app: QApplication) -> None:
    field = QLineEdit()
    field.setProperty("error", True)
    HudTextService._clear_hud_error(field)
    assert field.property("error") is False


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


def test_status_coordinates_follow_canvas_cursor_without_refreshing_page(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    strip = CanvasStatusStrip()
    strip.bind_canvas(canvas)

    canvas.cursorPositionChanged.emit(12.5, -4.25)

    assert strip._cursor_label.text() == "X 12.50  Y -4.25 mm"
    canvas.close()
    strip.close()


def test_compact_toolbar_preserves_guidance(app: QApplication) -> None:
    toolbar, *_ = canvas_toolbar(lambda _mode: None, lambda: None)
    toolbar.set_guidance("Draw · Pick the first point")
    toolbar.resize(900, 44)
    toolbar.show()
    app.processEvents()
    assert toolbar._guidance_chip.isVisible()
    assert "Pick the first point" in toolbar._overflow.accessibleDescription()


def test_context_customizer_rows_have_stable_initial_geometry(app: QApplication) -> None:
    """Checkbox rows must not overlap until the user clicks one."""
    widget = _build_list({"create": "Create shapes", "view": "View"}, ["view"], ())
    widget.show()
    app.processEvents()
    first = widget.visualItemRect(widget.item(0))
    second = widget.visualItemRect(widget.item(1))
    assert first.height() >= 36
    assert second.top() >= first.bottom() + 2
    widget.close()


def test_compact_toolbar_shows_one_guidance_label(app: QApplication) -> None:
    toolbar, *_rest, guidance = canvas_toolbar(lambda _mode: None, lambda: None)
    toolbar.set_guidance("Draw · Pick the first point")
    toolbar.resize(900, 44)
    toolbar.show()
    app.processEvents()
    assert toolbar._guidance_chip.isVisible()
    assert not guidance.isVisible()


def test_multistate_button_uses_native_menu_without_hover_flyout(
    app: QApplication,
) -> None:
    button = CycleIconButton(
        [("a", QIcon(), "A"), ("b", QIcon(), "B"), ("c", QIcon(), "C")],
        lambda _state: None,
    )
    assert not hasattr(button, "_hover_timer")
    button.click()
    app.processEvents()
    assert button._state_menu is not None
    assert "3 options" in button.accessibleDescription()
    assert button.maximumWidth() == 44
    assert button.minimumHeight() >= 40


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


def test_escape_leaves_active_canvas_tool_when_a_panel_input_has_focus(app: QApplication) -> None:
    class CanvasStub(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._mode = "draw"
            self._dimension_mode = False
            self._measure_mode = False
            self.exit_calls = 0

        def exit_to_select(self) -> None:
            self.exit_calls += 1
            self._mode = "select"

    canvas = CanvasStub()
    input_field = QWidget(canvas)
    canvas.show()
    app.processEvents()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

    assert CanvasEscapeRouter(canvas).eventFilter(input_field, event)
    assert canvas.exit_calls == 1
    assert canvas._mode == "select"
    canvas.close()


def test_layer_rows_describe_geometry_without_exposing_internal_ids() -> None:
    label = describe_polyline(
        "a83984d3a7b5",
        [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 0.0)],
    )

    assert label == "Closed path  ·  3 pts  ·  5.0 × 5.0 mm"
    assert "a83984d3a7b5" not in label


def test_theme_resolves_tokenized_stylesheet_and_palette() -> None:
    stylesheet = load_app_qss()

    assert "$" not in stylesheet
    assert accessibility_palette().color(QPalette.ColorRole.Window).isValid()


def test_draw_sidebar_reserves_space_for_its_scrollbar(app: QApplication) -> None:
    def noop(*_args: object) -> None:
        return None

    sidebar = DrawSidebar(
        parent=None,
        on_polyline_family=noop,
        on_shapes_family=noop,
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
    )
    sidebar._apply_width(MIN_DRAW_SIDEBAR_WIDTH)
    sidebar.resize(MIN_DRAW_SIDEBAR_WIDTH, 420)
    sidebar.show()
    app.processEvents()

    viewport = sidebar._scroll.viewport()
    visible_buttons = [
        button for button in sidebar.findChildren(CycleIconButton) if button.isVisibleTo(sidebar)
    ]
    assert visible_buttons
    assert all(
        button.mapTo(viewport, button.rect().topRight()).x() < viewport.width()
        for button in visible_buttons
    )
    sidebar.close()


def test_relationship_snaps_require_a_nearby_reference_and_retain_its_source() -> None:
    near = SimpleNamespace(id="intended", points=[(0.0, 0.0), (10.0, 0.0)])
    far = SimpleNamespace(id="unrelated", points=[(0.0, 100.0), (10.0, 100.0)])
    view = SimpleNamespace(
        _entities=[near, far],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=False,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    result = engine._relationship_candidate(5.0, 2.0, 5.0, 2.0, (0.0, 2.0))
    assert result is not None
    assert result[2] == "parallel"
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "intended"

    # Moving far from the source does not lose an intentional relationship
    # while the drawn segment still satisfies it.
    result = engine._relationship_candidate(5.0, 200.0, 5.0, 200.0, (0.0, 200.0))
    assert result is not None
    assert result[2] == "parallel"
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "intended"

    view._entities = [far]
    result = engine._relationship_candidate(5.0, 2.0, 5.0, 2.0, (0.0, 2.0))
    assert result is None
    assert engine.last_relationship_reference is None


def test_spline_controls_do_not_acquire_line_relationship_snaps() -> None:
    source = SimpleNamespace(id="line", kind="line", points=[(0.0, 0.0), (10.0, 0.0)])
    view = SimpleNamespace(
        _draw_primitive="spline",
        _entities=[source],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    assert engine._relationship_candidate(10.0, 10.0, 10.0, 10.0, (0.0, 0.0)) is None
    assert engine.last_relationship_reference is None


def test_curve_control_polygons_are_not_line_relationship_references() -> None:
    spline = SimpleNamespace(
        id="curve",
        kind="spline",
        points=[(0.0, 0.0), (10.0, 0.0), (12.0, 4.0)],
    )
    line = SimpleNamespace(id="line", kind="line", points=[(0.0, 5.0), (10.0, 5.0)])
    view = SimpleNamespace(
        _entities=[spline, line],
        _snap_angle_enabled=False,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    result = engine._relationship_candidate(10.0, 0.0, 10.0, 0.0, (0.0, 0.0))
    assert result == (10.0, 0.0, "equal_length")
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "line"


def test_equal_length_can_reference_an_earlier_segment_of_active_drawing() -> None:
    view = SimpleNamespace(
        _entities=[],
        _draw_pts=[(0.0, 0.0), (10.0, 0.0)],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    result = engine._relationship_candidate(10.0, 9.0, 10.0, 9.0, (10.0, 0.0))

    assert result == (10.0, 10.0, "perpendicular_equal_length")
    assert engine.last_relationship_type == "perpendicular_equal_length"
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "__active_draw__"


def test_equal_length_ignores_distant_canvas_segments_during_acquisition() -> None:
    local = SimpleNamespace(id="local", points=[(0.0, 5.0), (10.0, 5.0)])
    unrelated = SimpleNamespace(id="unrelated", points=[(0.0, 300.0), (30.0, 300.0)])
    view = SimpleNamespace(
        _entities=[local, unrelated],
        _draw_pts=[],
        _snap_angle_enabled=False,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    result = engine._relationship_candidate(30.0, 0.0, 30.0, 0.0, (0.0, 0.0))

    # The distant 30 mm segment would land exactly under the cursor, but a
    # locally relevant 10 mm reference must be selected instead.
    assert result == (10.0, 0.0, "equal_length")
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "local"


def test_parallel_and_equal_length_beat_an_extension_at_the_same_point() -> None:
    source = SimpleNamespace(id="vertical", points=[(0.0, 0.0), (0.0, 10.0)])
    view = SimpleNamespace(
        _entities=[source],
        _draw_pts=[],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    result = engine._relationship_candidate(20.0, 9.7, 20.0, 9.7, (20.0, 0.0))

    assert result == (20.0, 10.0, "parallel_equal_length")
    chosen = engine._pick_better(
        20.0,
        10.0,
        result,
        (20.0, 10.0, "extension"),
    )
    assert chosen == result


def test_combined_relationship_snap_has_a_practical_magnetic_range() -> None:
    source = SimpleNamespace(id="vertical", points=[(0.0, 0.0), (0.0, 10.0)])
    view = SimpleNamespace(
        _entities=[source],
        _draw_pts=[],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    # The exact target is (20, 10). A 25px miss still acquires the useful
    # two-constraint snap rather than forcing pixel-perfect placement.
    acquired = engine._relationship_candidate(20.0, 35.0, 20.0, 35.0, (20.0, 0.0))
    assert acquired == (20.0, 10.0, "parallel_equal_length")

    # Once acquired, retain it through a slightly wider band so it does not
    # flicker while the pointer is moving.
    retained = engine._relationship_candidate(20.0, 43.0, 20.0, 43.0, (20.0, 0.0))
    assert retained == (20.0, 10.0, "parallel_equal_length")


def test_single_relationship_snaps_have_magnetic_acquisition_and_retention() -> None:
    source = SimpleNamespace(id="vertical", points=[(0.0, 0.0), (0.0, 10.0)])
    view = SimpleNamespace(
        _entities=[source],
        _draw_pts=[],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    # Equal length resolves to an exact endpoint and must not require a
    # pixel-perfect hit.
    equal = engine._relationship_candidate(20.0, 35.0, 20.0, 35.0, (20.0, 0.0))
    assert equal == (20.0, 10.0, "parallel_equal_length")

    # Turn off the paired directional relation to exercise equal-length by
    # itself, then verify it keeps the acquired source while moving.
    view._snap_angle_enabled = False
    engine.clear_relationship_reference()
    equal = engine._relationship_candidate(20.0, 35.0, 20.0, 35.0, (20.0, 0.0))
    assert equal == (20.0, 10.0, "equal_length")
    retained = engine._relationship_candidate(20.0, 43.0, 20.0, 43.0, (20.0, 0.0))
    assert retained == (20.0, 10.0, "equal_length")

    # A single directional relationship is a construction line, so it has a
    # smaller but still usable acquisition band.
    view._snap_angle_enabled = True
    view._snap_equal_length_enabled = False
    engine.clear_relationship_reference()
    parallel = engine._relationship_candidate(36.0, 90.0, 36.0, 90.0, (20.0, 0.0))
    assert parallel is not None
    assert parallel[0] == pytest.approx(20.0)
    assert parallel[2] == "parallel"
    retained = engine._relationship_candidate(42.0, 90.0, 42.0, 90.0, (20.0, 0.0))
    assert retained is not None
    assert retained[0] == pytest.approx(20.0)
    assert retained[2] == "parallel"


def test_held_parallel_relationship_promotes_to_parallel_equal_length() -> None:
    source = SimpleNamespace(id="vertical", points=[(0.0, 0.0), (0.0, 10.0)])
    view = SimpleNamespace(
        _entities=[source],
        _draw_pts=[],
        _snap_angle_enabled=True,
        _snap_equal_length_enabled=True,
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
    )
    engine = SnapEngine(view)

    # First acquire a long parallel line. The source stays locked while the
    # endpoint moves, rather than being reselected from unrelated geometry.
    held = engine._relationship_candidate(20.0, 50.0, 20.0, 50.0, (20.0, 0.0))
    assert held is not None
    assert held[0] == pytest.approx(20.0)
    assert held[1] == pytest.approx(50.0)
    assert held[2] == "parallel"

    # When the cursor reaches the source length, promote the same lock to the
    # combined relationship instead of extending the single parallel snap.
    promoted = engine._relationship_candidate(20.0, 19.0, 20.0, 19.0, (20.0, 0.0))
    assert promoted == (20.0, 10.0, "parallel_equal_length")


def test_snap_menu_has_independent_plain_language_toggles(app: QApplication) -> None:
    canvas = DxfCanvas()
    precision = CanvasPrecisionBar(canvas)

    assert list(precision._snap_actions) == [
        "snap_vertex",
        "snap_midpoint",
        "snap_intersection",
        "snap_parallel",
        "snap_perpendicular",
        "snap_equal_length",
        "snap_align_x",
        "snap_align_y",
        "grid_snap",
    ]
    precision._snap_actions["snap_midpoint"].trigger()
    precision._snap_actions["snap_parallel"].trigger()
    precision._snap_actions["snap_align_x"].trigger()
    state = canvas.get_precision_state()
    assert state["snap_midpoint"] is False
    assert state["snap_parallel"] is False
    assert state["snap_align_x"] is False
    assert state["snap_align_y"] is True
    precision._snap_strength_slider.setValue(60)
    assert canvas.get_precision_state()["snap_strength"] == pytest.approx(0.6)
    assert precision._snap_strength_value.text() == "60%"
    precision._snap_strength_slider.setValue(0)
    assert canvas.get_precision_state()["snap_strength"] == 0.0
    assert precision._snap_strength_value.text() == "0%"
    canvas.close()
    precision.close()


def test_context_menu_customizer_persists_individual_command_toggles(app: QApplication) -> None:
    dialog = ContextMenuActionCustomizeDialog(
        profiles={"draft": {"items": ["constraint.horizontal", "constraint.vertical"]}}
    )
    assert dialog._list.count() > 20
    for index in range(dialog._list.count()):
        item = dialog._list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == "constraint.vertical":
            item.setCheckState(Qt.CheckState.Unchecked)
            break
    dialog._apply()
    profile = dialog.get_profiles()["draft"]
    assert "constraint.horizontal" in profile["items"]
    assert "constraint.vertical" not in profile["items"]
    dialog.close()


def test_context_menu_customizer_initializes_draft_profile_without_rebuilding_lists(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the dialog must not clear and recreate its initial Qt list items.

    A redundant first profile load previously called ``QListWidget.clear()``
    after both lists had been populated.  That native teardown can segfault
    under PySide, before the dialog is even shown.
    """
    original_fill_list = customize_dialogs._fill_list
    calls: list[object] = []

    def record_fill_list(*args, **kwargs) -> None:
        calls.append(args[0])
        original_fill_list(*args, **kwargs)

    monkeypatch.setattr(customize_dialogs, "_fill_list", record_fill_list)
    dialog = ContextMenuActionCustomizeDialog(
        profiles={
            "draft": {
                "action_items_configured": ["yes"],
                "items": ["constraint.horizontal"],
                "overflow_items": [],
            }
        }
    )

    assert calls == [dialog._list, dialog._overflow_list]
    assert _checked_keys(dialog._list) == ["constraint.horizontal"]
    dialog.close()


def test_context_menu_customizer_supports_none_and_a_reorderable_more_list(
    app: QApplication,
) -> None:
    dialog = ContextMenuActionCustomizeDialog()
    dialog._set_all_visible(False)
    assert not _checked_keys(dialog._list)
    more_item = next(
        dialog._overflow_list.item(index)
        for index in range(dialog._overflow_list.count())
        if dialog._overflow_list.item(index).data(Qt.ItemDataRole.UserRole) == "constraint.horizontal"
    )
    more_item.setCheckState(Qt.CheckState.Checked)
    assert _checked_keys(dialog._list) == ["constraint.horizontal"]
    assert _checked_keys(dialog._overflow_list) == ["constraint.horizontal"]

    vertical_more_item = next(
        dialog._overflow_list.item(index)
        for index in range(dialog._overflow_list.count())
        if dialog._overflow_list.item(index).data(Qt.ItemDataRole.UserRole) == "constraint.vertical"
    )
    vertical_more_item.setCheckState(Qt.CheckState.Checked)
    assert _checked_keys(dialog._overflow_list) == [
        "constraint.horizontal",
        "constraint.vertical",
    ]
    item = dialog._overflow_list.takeItem(0)
    dialog._overflow_list.insertItem(1, item)
    dialog._normalize_more_action_rows()
    dialog._apply()
    profile = dialog.get_profiles()["draft"]
    assert profile["items"][:2] == ["constraint.horizontal", "constraint.vertical"]
    assert profile["overflow_items"] == ["constraint.vertical", "constraint.horizontal"]
    dialog.close()


def test_context_menu_defaults_place_secondary_selection_actions_under_more(
    app: QApplication,
) -> None:
    dialog = ContextMenuActionCustomizeDialog()

    assert set(DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS).issubset(
        _checked_keys(dialog._overflow_list)
    )
    assert "view.fit" in _checked_keys(dialog._overflow_list)
    dialog.close()


def test_canvas_uses_action_menu_defaults_with_secondary_actions_under_more(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.set_context_menu_profiles({"draft": {"items": []}})

    assert canvas._context_menu_actions_configured
    assert canvas._context_menu_item_order
    assert canvas._context_menu_overflow_items == set(DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS)
    canvas.close()


def test_procedural_shapes_use_the_same_drag_creation_path_as_quick_shapes(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)

    canvas.set_quick_shape_mode("ring")
    ring_paths = canvas._build_drag_shapes("ring", 0.0, 0.0, 30.0, 20.0)
    gear_paths = canvas._build_drag_shapes("gear", 0.0, 0.0, 30.0, 20.0)

    assert canvas.quick_shape_enabled
    assert len(ring_paths) == 2
    assert len(gear_paths) == 1

    canvas._start_shape_drag("ring", QPointF(120.0, 120.0))
    canvas._finish_shape_drag(QPoint(260, 220))

    assert len(canvas._entities) == 2
    assert {entity.kind for entity in canvas._entities} == {"ring"}
    canvas.close()


def test_procedural_shapes_use_standard_draw_dimensions_workflow(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)

    canvas.activate_procedural_draw("ring")

    assert canvas.get_mode() == "draw"
    assert canvas._draw_primitive == "ring"
    assert canvas._shape_primitive_active()
    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = (0.0, 0.0)
    canvas._draw_shape_cursor_w = (30.0, 20.0)
    canvas._show_shape_dim_inputs()
    assert canvas._draw_shape_w_edit is not None
    assert canvas._draw_shape_h_edit is not None
    assert canvas._commit_shape_preview()
    assert len(canvas._entities) == 2
    canvas.close()


def test_snap_strength_defaults_to_fifty_percent(app: QApplication) -> None:
    canvas = DxfCanvas()
    precision = CanvasPrecisionBar(canvas)

    assert canvas.get_precision_state()["snap_strength"] == 0.5
    assert precision._snap_strength_slider.value() == 50
    assert precision._snap_strength_value.text() == "50%"
    canvas.close()
    precision.close()


def test_context_menu_customizer_filters_catalogue_and_covers_static_actions(
    app: QApplication,
) -> None:
    dialog = ContextMenuActionCustomizeDialog()

    dialog._filter.setText("dovetail")
    visible = [
        item.data(Qt.ItemDataRole.UserRole)
        for index in range(dialog._list.count())
        if not (item := dialog._list.item(index)).isHidden()
    ]
    assert visible == ["context.create.dovetail_box"]
    assert set(_CONTEXT_STATIC_ACTION_IDS.values()).issubset(_CONTEXT_ACTION_LABELS)
    assert "context.share.move_to_layer" in _CONTEXT_ACTION_LABELS
    assert "context.pattern_cell.repeat" in _CONTEXT_ACTION_LABELS
    assert not any(key.startswith("context.outline_role.") for key in _CONTEXT_ACTION_LABELS)
    dialog.close()


def test_context_menu_customizer_prioritizes_enabled_actions_and_saves_drag_order(
    app: QApplication,
) -> None:
    dialog = ContextMenuActionCustomizeDialog(
        profiles={"draft": {"items": [], "action_items_configured": ["yes"]}}
    )
    items = {
        str(dialog._list.item(index).data(Qt.ItemDataRole.UserRole)): dialog._list.item(index)
        for index in range(dialog._list.count())
    }
    items["constraint.horizontal"].setCheckState(Qt.CheckState.Checked)
    items["constraint.vertical"].setCheckState(Qt.CheckState.Checked)
    assert _ordered_keys(dialog._list)[:2] == ["constraint.horizontal", "constraint.vertical"]

    item = dialog._list.takeItem(0)
    dialog._list.insertItem(1, item)
    dialog._normalize_action_rows()
    dialog._apply()

    assert dialog.get_profiles()["draft"]["items"][:2] == [
        "constraint.vertical",
        "constraint.horizontal",
    ]
    dialog.close()


def test_context_menu_applies_individual_order_and_more_placement(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas._context_menu_item_order = ["constraint.horizontal", "transform.rotate_cw"]
    canvas._context_menu_overflow_items = {"transform.rotate_cw"}
    menu = QMenu()
    menu.addAction("Horizontal")
    transform = menu.addMenu("Transform")
    rotate = transform.addAction("Rotate +90°")
    rotate.setProperty("context_item", "rotate_cw")

    canvas._apply_context_menu_overflow(menu)

    assert menu.actions()[0].text() == "Horizontal"
    assert menu.actions()[1].text() == "More actions…"
    more_actions = menu.actions()[1].menu().actions()
    assert more_actions[0].text() == "Transform"
    assert more_actions[0].menu().actions()[0].text() == "Rotate +90°"
    menu.close()
    canvas.close()


def test_context_menu_hides_every_action_for_an_intentionally_empty_profile(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.set_context_menu_profiles(
        {"draft": {"items": [], "action_items_configured": ["yes"]}}
    )
    menu = QMenu()
    menu.addAction("Fit View  [F]")
    menu.addAction("Copy  [⌘C]")

    canvas._apply_context_menu_overflow(menu)

    assert not menu.actions()
    menu.close()
    canvas.close()


def test_context_menu_recognizes_shortcut_actions_and_respects_disabled_fit(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas._context_menu_item_order = ["clipboard.copy", "edit.delete"]
    menu = QMenu()
    menu.addAction("Cut  [⌘X]")
    menu.addAction("Copy  [⌘C]")
    menu.addAction("Paste  [⌘V]")
    menu.addAction("Delete Selected  [⌦]")
    menu.addAction("Fit View  [F]")

    canvas._apply_context_menu_overflow(menu)

    assert [action.text() for action in menu.actions()] == ["Copy  [⌘C]", "Delete Selected  [⌦]"]
    menu.close()
    canvas.close()


def test_context_menu_action_customization_flattens_nested_submenus_safely(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas._context_menu_item_order = ["transform.rotate_cw"]
    menu = QMenu()
    transform = menu.addMenu("Transform")
    rotate = transform.addAction("Rotate +90°")
    rotate.setProperty("context_item", "rotate_cw")

    canvas._apply_context_menu_overflow(menu)

    assert [action.text() for action in menu.actions()] == ["Transform"]
    assert menu.actions()[0].menu().actions()[0].text() == "Rotate +90°"
    menu.close()
    canvas.close()


def test_context_menu_groups_shape_actions_without_losing_individual_visibility(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas._context_menu_item_order = ["context.create.rectangle"]
    menu = QMenu()
    shapes = menu.addMenu("Create shape")
    shapes.addAction("Rectangle (drag)")
    shapes.addAction("Circle (drag)")

    canvas._apply_context_menu_overflow(menu)

    assert [action.text() for action in menu.actions()] == ["Shapes"]
    assert [action.text() for action in menu.actions()[0].menu().actions()] == ["Rectangle (drag)"]
    menu.close()
    canvas.close()


def test_context_menu_customization_keeps_every_destination_layer(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas._context_menu_item_order = ["context.share.move_to_layer"]
    menu = QMenu()
    move_menu = menu.addMenu("Move selected to layer")
    for layer in ("Cut", "Engrave"):
        action = move_menu.addAction(layer)
        action.setProperty("context_item_id", "context.share.move_to_layer")

    canvas._apply_context_menu_overflow(menu)

    assert [action.text() for action in menu.actions()] == ["Move selected to layer"]
    assert [
        action.text() for action in menu.actions()[0].menu().actions()
    ] == ["Cut", "Engrave"]
    menu.close()
    canvas.close()


def test_intersection_snap_beats_midpoint_and_remains_independent() -> None:
    horizontal = SimpleNamespace(id="horizontal", points=[(-20.0, 0.0), (20.0, 0.0)])
    vertical = SimpleNamespace(id="vertical", points=[(0.0, -20.0), (0.0, 20.0)])
    view = SimpleNamespace(
        _snap_master_enabled=True,
        _entities=[horizontal, vertical],
        _flagged=lambda _flag: set(),
        _snap_vertex_enabled=False,
        _snap_midpoint_enabled=False,
        _snap_intersection_enabled=True,
        _snap_edge_enabled=True,
        _snap_tangent_enabled=False,
        _snap_extension_enabled=False,
        _snap_parallel_enabled=True,
        _snap_perpendicular_enabled=True,
        _snap_equal_length_enabled=True,
        _snap_align_x_enabled=True,
        _snap_align_y_enabled=True,
        _grid_snap=False,
        _grid_spacing=5.0,
        _scale=1.0,
        _w2c=lambda x, y: (x, y),
        _c2w=lambda x, y: (x, y),
        _poly_bounds=lambda points: (
            min(x for x, _y in points),
            min(y for _x, y in points),
            max(x for x, _y in points),
            max(y for _x, y in points),
        ),
        _is_poly_closed=lambda _points: False,
        _segment_intersection_point=lambda _a, _b, _c, _d: (0.0, 0.0),
        _mode="draw",
        _draw_pts=[],
        _snap_shapes=lambda: {},
        _guides=[],
    )
    engine = SnapEngine(view)

    assert engine.query(0.0, 0.0, 0.0, 0.0, reference_point=(0.0, 10.0)) == (
        0.0,
        0.0,
        "intersection",
    )


def test_relationship_snaps_beat_grid_but_not_explicit_geometry() -> None:
    view = SimpleNamespace(_w2c=lambda x, y: (x, y))
    engine = SnapEngine(view)
    relationship = (20.0, 10.0, "parallel_equal_length")

    assert engine._pick_better(20.0, 35.0, (20.0, 35.0, "grid"), relationship) == relationship
    assert engine._pick_better(20.0, 10.0, relationship, (20.0, 10.0, "vertex")) == (
        20.0,
        10.0,
        "vertex",
    )


def test_inferred_line_snaps_use_a_more_reachable_band_than_exact_geometry() -> None:
    source = SimpleNamespace(id="vertical", points=[(0.0, 0.0), (0.0, 10.0)])
    view = SimpleNamespace(
        _entities=[source],
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
        _c2w=lambda x, y: (x, y),
        _is_poly_closed=lambda _points: False,
        _snap_extension_enabled=True,
    )
    engine = SnapEngine(view)

    assert engine._axis_alignment_candidate(16.0, 30.0, 16.0, 30.0) == (0.0, 30.0, "axis_x")
    assert engine._extension_candidate(16.0, 26.0) == (0.0, 26.0, "extension")


def test_inferred_snaps_ignore_remote_sources_when_drawing_a_stroke() -> None:
    source = SimpleNamespace(id="remote", points=[(0.0, 300.0), (0.0, 310.0)])
    view = SimpleNamespace(
        _entities=[source],
        _flagged=lambda _flag: set(),
        _w2c=lambda x, y: (x, y),
        _c2w=lambda x, y: (x, y),
        _is_poly_closed=lambda _points: False,
        _snap_extension_enabled=True,
        _snap_align_x_enabled=True,
        _snap_align_y_enabled=True,
    )
    engine = SnapEngine(view)

    assert engine._axis_alignment_candidate(15.0, 0.0, 15.0, 0.0, reference=(15.0, 0.0)) is None
    assert engine._extension_candidate(0.0, 0.0, reference=(15.0, 0.0)) is None


def test_help_search_indexes_body_text_and_reports_results(app: QApplication) -> None:
    dialog = HelpDialog()
    dialog._search_box.setText("MOVEDIST")

    visible = [
        dialog._toc_list.item(index).text()
        for index in range(dialog._toc_list.count())
        if not dialog._toc_list.item(index).isHidden()
    ]
    assert visible
    assert dialog._search_status.text() != "0 topics"
    assert dialog._search_box.property("error") is False

    dialog._search_box.setText("definitely-not-a-help-topic")
    assert dialog._search_status.text() == "0 topics"
    assert dialog._search_box.property("error") is True
    dialog.close()


def test_help_search_ranks_user_vocabulary_instead_of_substring_noise(
    app: QApplication,
) -> None:
    dialog = HelpDialog()
    dialog._search_box.setText("round")
    current = dialog._toc_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole) == "precision-editing"
    assert all(
        "Recent Features" not in dialog._toc_list.item(index).text()
        for index in range(dialog._toc_list.count())
        if not dialog._toc_list.item(index).isHidden()
    )

    dialog._search_box.setText("engrave")
    current = dialog._toc_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole) == "pattern-page"

    dialog._search_box.setText("rounded corners")
    current = dialog._toc_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole) == "precision-editing"
    dialog.close()


@pytest.mark.parametrize(
    ("query", "expected_section"),
    [
        ("round", "precision-editing"),
        ("fillet", "precision-editing"),
        ("recover lost work", "files-recovery"),
        ("autosave", "files-recovery"),
        ("change units", "settings-updates"),
        ("update app", "settings-updates"),
    ],
)
def test_help_search_routes_common_goals_to_task_guides(
    app: QApplication, query: str, expected_section: str
) -> None:
    dialog = HelpDialog()
    dialog._search_box.setText(query)
    current = dialog._toc_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole) == expected_section
    dialog.close()


def test_settings_shows_every_card_by_default_and_supports_navigation(
    app: QApplication,
) -> None:
    dialog = SettingsDialog(settings={})
    dialog.show()
    app.processEvents()
    assert dialog._category_combo.currentText() == "All settings"
    assert all(card.isVisibleTo(dialog) for card in dialog._settings_cards.values())

    dialog._category_combo.setCurrentText("Trace")
    app.processEvents()
    visible = {title for title, card in dialog._settings_cards.items() if card.isVisibleTo(dialog)}
    assert visible == {"Trace Defaults"}


def test_pattern_defaults_to_basic_controls_and_can_reveal_advanced(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page.resize(1100, 760)
    page.show()
    app.processEvents()
    assert not page._advanced_mode_cb.isChecked()
    # Zones are always available because they are part of the primary
    # workflow, even when secondary advanced controls are hidden.
    assert page._zone_scroll.isVisibleTo(page)
    assert not page._zones_section.is_expanded()

    page._advanced_mode_cb.setChecked(True)
    app.processEvents()
    assert page._zone_scroll.isVisibleTo(page)
    page.close()


def test_pattern_zone_highlight_and_preview_selection_use_canvas_entity_ids(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    polys = [
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)],
        [(6.0, 0.0), (10.0, 0.0), (10.0, 4.0), (6.0, 0.0)],
        [(12.0, 0.0), (16.0, 0.0), (16.0, 4.0), (12.0, 0.0)],
    ]
    page._canvas.load(polys, fit=False)
    entity_ids = page._canvas.get_entity_ids()
    page._outline_ids = entity_ids[:2]
    page._edit_polys = polys[:2]
    page._treatments = {
        entity_ids[0]: {"kind": "pattern", "pattern": "Lines"},
        entity_ids[1]: {"kind": "pattern", "pattern": "Dots"},
    }
    page._refresh_zone_list()

    highlight_zone_on_canvas(page, 1)
    assert page._canvas._accent_polys == {entity_ids[1]: "#f5a623"}

    page._canvas.set_selection([entity_ids[1]])
    select_zone_for_canvas_selection(page)
    assert page._zone_list.currentRow() == 1
    page.shutdown()
    page.close()


def test_pattern_transfer_preserves_outline_ids_for_zone_assignment(app: QApplication) -> None:
    page = PatternPage(settings={})
    polys = [
        [(0.0, 0.0), (12.0, 0.0), (12.0, 12.0), (0.0, 0.0)],
        [(3.0, 3.0), (6.0, 3.0), (6.0, 6.0), (3.0, 3.0)],
    ]
    page.load_outline_polys(polys)
    assert page._outline_ids == page._canvas.get_entity_ids()
    page._canvas.set_selection([page._outline_ids[1]])
    assert page._canvas.get_selected_ids() == [page._outline_ids[1]]
    page.shutdown()
    page.close()


def test_pattern_direct_drawing_preserves_canvas_ids_for_zone_assignment(
    app: QApplication,
) -> None:
    """Rectangle/circle drawn in-place must remain assignable as zones."""
    page = PatternPage(settings={})
    rectangle = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 0.0)]
    circle = [(5.0, 10.0), (6.0, 12.0), (5.0, 14.0), (4.0, 12.0), (5.0, 10.0)]

    page._canvas.add_polylines_state([rectangle])
    app.processEvents()
    page._canvas.add_polylines_state([circle])
    app.processEvents()

    entity_ids = page._canvas.get_entity_ids()
    assert page._outline_ids == entity_ids
    page._canvas.set_selection([entity_ids[1]])
    app.processEvents()
    assert [eid for eid in page._canvas.get_selected_ids() if eid in page._outline_ids] == [
        entity_ids[1]
    ]
    page.shutdown()
    page.close()


def test_pattern_canvas_selection_is_non_destructive(app: QApplication) -> None:
    page = PatternPage(settings={})
    assert page._canvas._selection_drag_edits is False
    page.shutdown()
    page.close()


def test_pattern_undo_redo_restores_canvas_geometry(app: QApplication) -> None:
    page = PatternPage(settings={})
    poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page._canvas.add_polylines_state([poly])
    assert len(page._canvas.get_entity_ids()) == 1
    assert page._canvas.undo()
    assert page._canvas.get_entity_ids() == []
    assert page._canvas.redo()
    assert len(page._canvas.get_entity_ids()) == 1
    page.shutdown()
    page.close()


def test_invalid_zone_geometry_is_repaired_before_crosshatch_clipping() -> None:
    bowtie = Polygon([(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)])
    strokes = apply_fill(bowtie, FillSpec(mode="crosshatch", spacing=1.0))
    assert strokes


def test_custom_tile_open_strokes_do_not_polygonize_across_repetitions() -> None:
    """An open motif must not create accidental fill cells with its neighbours."""
    outline = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
    # A three-sided tile: adjacent zero-gap repetitions share enough edges
    # to create rectangles if all open strokes are polygonized together.
    open_u = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)]
    fill: list[list[tuple[float, float]]] = []

    PatternProcessor().build_pattern_polys(
        [outline],
        pattern="Custom Tile",
        params={"tile_polys": [open_u], "gap": 0.0},
        scale=(30.0, 30.0),
        orig_w=30.0,
        orig_h=30.0,
        fill_options={"mode": "lines", "spacing": 1.0, "target_pattern": True},
        fill_polys_out=fill,
    )

    assert fill == []


def test_custom_tile_nested_paths_fill_as_one_compound_cell() -> None:
    outline = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
    outer = [(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0), (-5.0, -5.0)]
    inner = [(-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0), (-2.0, -2.0)]
    fill: list[list[tuple[float, float]]] = []

    PatternProcessor().build_pattern_polys(
        [outline],
        pattern="Custom Tile",
        params={"tile_polys": [outer, inner], "gap": 0.0},
        scale=(30.0, 30.0),
        orig_w=30.0,
        orig_h=30.0,
        fill_options={"mode": "lines", "spacing": 0.5, "target_pattern": True},
        fill_polys_out=fill,
    )

    assert fill
    inner_core = Polygon(inner).buffer(-0.1)
    assert not any(inner_core.contains(Point(point)) for stroke in fill for point in stroke)


def test_invalid_zone_exclusion_overlay_does_not_abort_preview() -> None:
    processor = PatternProcessor()
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    exclusion = [(2.0, 2.0), (8.0, 8.0), (2.0, 8.0), (8.0, 2.0), (2.0, 2.0)]
    fill: list[list[tuple[float, float]]] = []
    processor.build_pattern_polys(
        [outline],
        pattern="— None —",
        params={},
        scale=(10.0, 10.0),
        orig_w=10.0,
        orig_h=10.0,
        exclusion_polys=[exclusion],
        fill_options={"mode": "lines", "spacing": 1.0, "target_outline": True},
        fill_polys_out=fill,
    )
    assert isinstance(fill, list)


def test_nested_boundary_outlines_are_filled_until_explicitly_marked_cutout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = PatternProcessor()
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    inner = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0)]
    captured = {}

    def capture_region(region, _pattern, _params):
        captured["region"] = region
        return []

    monkeypatch.setattr(processor, "_gen_pattern", capture_region)
    processor.build_pattern_polys(
        [outer, inner],
        pattern="— None —",
        params={},
        scale=(10.0, 10.0),
        orig_w=10.0,
        orig_h=10.0,
        fill_options=None,
    )
    assert captured["region"].covers(Point(5.0, 5.0))

    cutout_processor = PatternProcessor()
    cutout_capture = {}

    def capture_cutout_region(region, _pattern, _params):
        cutout_capture["region"] = region
        return []

    monkeypatch.setattr(
        cutout_processor,
        "_gen_pattern",
        capture_cutout_region,
    )
    cutout_processor.build_pattern_polys(
        [outer],
        pattern="— None —",
        params={},
        scale=(10.0, 10.0),
        orig_w=10.0,
        orig_h=10.0,
        exclusion_polys=[inner],
    )
    assert not cutout_capture["region"].covers(Point(5.0, 5.0))


def test_outline_winding_preserves_text_counters_without_hiding_nested_shapes() -> None:
    """Opposite-winding text counters are holes; same-winding shapes are not."""
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    # The counter runs in the opposite direction, as Qt/SVG font outlines do.
    counter = [(3.0, 3.0), (3.0, 7.0), (7.0, 7.0), (7.0, 3.0), (3.0, 3.0)]
    text_region = polylines_to_outline([outer, counter])
    assert text_region.covers(Point(1.0, 1.0))
    assert not text_region.covers(Point(5.0, 5.0))

    # A separately drawn inner square with the same direction is a filled
    # shape, not an accidental cutout.  Explicit Cutout remains the way to
    # subtract it in Pattern.
    inner_shape = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0), (3.0, 3.0)]
    drawing_region = polylines_to_outline([outer, inner_shape])
    assert drawing_region.covers(Point(5.0, 5.0))


def test_overlapping_compound_outlines_do_not_erase_each_others_fill() -> None:
    """A counter only subtracts from the glyph/object it belongs to."""
    left = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    # Opposite winding: a counter in the left compound outline.
    left_counter = [(3.0, 3.0), (3.0, 7.0), (7.0, 7.0), (7.0, 3.0), (3.0, 3.0)]
    # A separate overlapping shape covers part of that counter's coordinate
    # space.  Its fill must survive the left object's counter subtraction.
    right = [(4.0, 0.0), (12.0, 0.0), (12.0, 10.0), (4.0, 10.0), (4.0, 0.0)]

    region = polylines_to_outline([left, left_counter, right])

    assert region.covers(Point(5.0, 5.0))


def test_text_outline_fill_reaches_every_glyph_and_respects_its_counter(
    app: QApplication,
) -> None:
    """A multi-contour text selection must not fill only its first glyph."""
    contours = text_to_polylines("hey", family="Arial", height_mm=10.0)
    fill: list[list[tuple[float, float]]] = []
    PatternProcessor().build_pattern_polys(
        contours,
        pattern="— None —",
        params={},
        scale=(1.0, 1.0),
        orig_w=1.0,
        orig_h=1.0,
        fill_options={"mode": "lines", "spacing": 0.25, "target_outline": True},
        fill_polys_out=fill,
    )

    # The three clockwise exterior contours are h, e, and y.  Every glyph
    # should receive at least one hatch endpoint, rather than stopping after
    # the first contour in the selected text group.
    exteriors = [Polygon(poly) for poly in contours if Polygon(poly).exterior.is_ccw is False]
    assert len(exteriors) == 3
    fill_points = [Point(point) for stroke in fill for point in stroke]
    assert all(any(exterior.covers(point) for point in fill_points) for exterior in exteriors)

    # The counter in e is anti-clockwise and must remain unfilled.
    counter = next(Polygon(poly) for poly in contours if Polygon(poly).exterior.is_ccw)
    # Hatch clipping can retain a point a few floating-point units inside a
    # curve boundary, but it must never place a stroke through the counter's
    # actual interior.
    assert not any(counter.buffer(-0.05).contains(point) for point in fill_points)


def test_pattern_transfer_expands_a_selected_text_contour_to_its_full_group(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas._text_service.add_text_at(
        0.0,
        0.0,
        text="hey",
        family="Arial",
        height_mm=10.0,
    )
    canvas.set_selection([canvas._entities[0].id])
    received: list[list[dict]] = []
    canvas._send_selected_to_pattern_cb = received.append

    canvas._editing._send_selected_to_pattern()

    assert len(received) == 1
    assert len(received[0]) == len(canvas._entities)
    canvas.close()


def test_self_touching_closed_outline_is_repaired_for_pattern_fill() -> None:
    # A bow-tie stands in for the self-touching contours emitted by some font
    # glyphs.  It should become two valid fillable islands, not a hard error.
    bow_tie = [(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    processor = PatternProcessor()
    assert processor.validate_outline_inputs([bow_tie]) is None
    fill: list[list[tuple[float, float]]] = []
    processor.build_pattern_polys(
        [bow_tie],
        pattern="— None —",
        params={},
        scale=(10.0, 10.0),
        orig_w=10.0,
        orig_h=10.0,
        fill_options={"mode": "lines", "spacing": 1.0, "target_outline": True},
        fill_polys_out=fill,
    )
    assert fill


def test_preview_fill_row_budget_bounds_dense_hatching_without_changing_export() -> None:
    outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    processor = PatternProcessor()
    full = processor.build_preview_polys(
        [outline],
        pattern="— None —",
        params={},
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        border_polys=None,
        fill_options={"mode": "lines", "spacing": 0.1, "target_outline": True},
    )
    bounded = processor.build_preview_polys(
        [outline],
        pattern="— None —",
        params={},
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        border_polys=None,
        fill_options={
            "mode": "lines",
            "spacing": 0.1,
            "target_outline": True,
            "preview_max_rows": 120,
        },
    )

    assert len(full["fill"]) > 900
    assert len(bounded["fill"]) <= 120


def test_canvas_preview_keeps_every_fill_stroke(app: QApplication) -> None:
    outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    completed: list[tuple] = []
    errors: list[tuple] = []

    compute_preview(
        [outline],
        "— None —",
        {},
        (100.0, 100.0),
        None,
        pattern_service=PatternProcessor(),
        orig_w=100.0,
        orig_h=100.0,
        on_done=completed.append,
        on_error=errors.append,
        fill_options={"mode": "lines", "spacing": 0.1, "target_outline": True},
    )

    assert errors == []
    assert len(completed) == 1
    _token, display, _count, categories = completed[0]
    assert len(categories["fill"]) > 900
    assert display == categories["display"]
    assert len(display) == sum(len(categories[name]) for name in ("outline", "pattern", "fill"))


def test_dense_canvas_preview_reuses_exact_paths_until_visual_state_changes(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.resize(640, 480)
    canvas.load(
        [[(float(index), 0.0), (float(index), 100.0)] for index in range(2_000)],
        fit=True,
    )
    canvas.set_dense_preview_render(True)
    canvas.show()
    app.processEvents()

    first = canvas._renderer._dense_preview_batches
    assert first is not None
    assert sum(path.elementCount() for path in first.values()) >= 4_000
    raster = canvas._renderer._dense_preview_raster
    assert raster is not None

    canvas._ox += 24.0
    canvas.update()
    app.processEvents()
    assert canvas._renderer._dense_preview_batches is first
    assert canvas._renderer._dense_preview_raster is raster

    canvas._zoom_at(320.0, 240.0, 1.5)
    app.processEvents()
    assert canvas._renderer._dense_preview_raster is not raster
    assert canvas._renderer._dense_preview_raster_scale == canvas._scale

    canvas._find_poly_at = lambda *_args: pytest.fail("dense preview hover must not hit-test")
    canvas._tools["select"].move(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(320.0, 240.0),
            QPointF(320.0, 240.0),
            QPointF(320.0, 240.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    render_only_id = canvas._entities[1].id
    canvas.set_render_only_entity_ids({render_only_id})
    assert not canvas._entity_selectable(render_only_id)
    canvas.set_selection([render_only_id])
    assert canvas.get_selected_ids() == []

    canvas.set_selection([canvas._entities[0].id])
    assert canvas._renderer._dense_preview_batches is None
    canvas.close()


def test_nested_zone_exclusion_uses_repaired_coverage() -> None:
    processor = PatternProcessor()
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    # The child touches the parent boundary; this is a valid clipped preview
    # cell and must still be excluded from the parent's generated treatment.
    child = [(0.0, 5.0), (4.0, 5.0), (4.0, 9.0), (0.0, 9.0), (0.0, 5.0)]
    zones = [{"polys": [outer]}, {"polys": [child]}]
    assert processor._zone_nested_exclusions(zones, 0) == [child]
    assert processor._zone_nested_exclusions(zones, 1) == []


def test_preview_fill_quality_does_not_change_fill_geometry() -> None:
    base = FillSpec.from_dict({"mode": "lines", "spacing": 2.0})
    fast = FillSpec.from_dict(
        {"mode": "lines", "spacing": 2.0, "_preview_quality": "fast"}
    )
    balanced = FillSpec.from_dict(
        {"mode": "lines", "spacing": 2.0, "_preview_quality": "balanced"}
    )
    assert base.spacing == 2.0
    assert fast.spacing == base.spacing
    assert balanced.spacing == base.spacing


def test_pattern_layer_tree_delete_dispatches_to_canvas(app: QApplication) -> None:
    page = PatternPage(settings={})
    poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page.load_outline_polys([poly])
    page._refresh_canvas_panels()
    tree = page._layers_tree
    shape = tree._tree.topLevelItem(0).child(0)
    assert shape is not None
    entity_id = page._outline_ids[0]
    tree._tree.setCurrentItem(shape)
    tree._delete_current_layer()
    assert entity_id not in page._canvas.get_entity_ids()
    page.shutdown()
    page.close()


def test_pattern_layer_tree_group_move_keeps_group_and_rows_in_sync(app: QApplication) -> None:
    page = PatternPage(settings={})
    polys = [
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)],
        [(6.0, 0.0), (10.0, 0.0), (10.0, 4.0), (6.0, 0.0)],
    ]
    page.load_outline_polys(polys)
    ids = list(page._outline_ids)
    assert page._canvas.group_entities(ids) == 2
    group_id = page._canvas._entity_for_id(ids[0]).group
    page._refresh_canvas_panels()
    controller = page._layer_module.controller
    controller.on_shapes_move_requested("pattern_active", [tuple(ids)], "moved")
    assert {page._canvas._entity_for_id(eid).group for eid in ids} == {group_id}
    assert {page._canvas._entity_for_id(eid).layer for eid in ids} == {"moved"}
    rows = controller._build_rows(controller.state)
    moved = next(row for row in rows if row["name"] == "moved")
    assert len(moved["shapes"]) == 1
    assert tuple(moved["shapes"][0]["key"]) == tuple(ids)
    page.shutdown()
    page.close()


def test_pattern_outline_layers_survive_showing_and_hiding_the_pattern(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys(
        [
            {"points": [(0.0, 0.0), (4.0, 0.0), (0.0, 0.0)], "layer": "Cut"},
            {"points": [(6.0, 0.0), (10.0, 0.0), (6.0, 0.0)], "layer": "Mark"},
        ]
    )
    assert [page._canvas._entity_for_id(entity_id).layer for entity_id in page._outline_ids] == [
        "Cut",
        "Mark",
    ]
    page._preview_polys_cache = [[(0.0, 0.0), (1.0, 0.0)]]
    # Showing and hiding the solved pattern is visibility only — it must never
    # disturb the entities or their layers.
    page._set_result_visible(True)
    page._set_result_visible(False)
    assert [page._canvas._entity_for_id(entity_id).layer for entity_id in page._outline_ids] == [
        "Cut",
        "Mark",
    ]
    assert {row["name"] for row in page._layer_module.controller._build_rows({})} == {
        "Cut",
        "Mark",
    }
    page.shutdown()
    page.close()


def test_properties_aspect_lock_is_opt_in_and_scales_both_dimensions(
    app: QApplication,
) -> None:
    canvas = DxfCanvas(selectable=True)
    canvas.load([[(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0), (0.0, 0.0)]])
    canvas.set_selection(canvas.get_entity_ids())
    assert canvas._aspect_ratio_locked is False
    canvas.set_aspect_ratio_locked(True)
    assert canvas._set_selected_height(4.0)
    geometry = canvas.selection_geometry()
    assert geometry is not None
    assert geometry["w"] == pytest.approx(8.0)
    assert geometry["h"] == pytest.approx(4.0)
    canvas.set_aspect_ratio_locked(False)
    assert canvas._set_selected_width(4.0)
    geometry = canvas.selection_geometry()
    assert geometry is not None
    assert geometry["w"] == pytest.approx(4.0)
    assert geometry["h"] == pytest.approx(4.0)
    canvas.close()


def test_constraints_accept_polyline_edges_and_two_edit_vertices(app: QApplication) -> None:
    canvas = DxfCanvas(selectable=True)
    canvas.load([[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]])
    entity_id = canvas.get_entity_ids()[0]
    canvas._constraint_segment_refs = [
        {"entity_id": entity_id, "segment_index": 0},
        {"entity_id": entity_id, "segment_index": 1},
    ]
    assert canvas.add_geometric_constraint("equal_length") == 1
    assert canvas._entities_by_id[entity_id].points[-1] == pytest.approx((4.0, 4.0))

    canvas.load([[(0.0, 0.0), (1.0, 0.0)], [(5.0, 3.0), (6.0, 3.0)]])
    first, second = canvas.get_entity_ids()
    canvas._edit_selected_verts = {(first, 1), (second, 0)}
    assert canvas.add_geometric_constraint("coincident") == 1
    points = [canvas._entities_by_id[first].points[1], canvas._entities_by_id[second].points[0]]
    assert points[0] == points[1]
    canvas.close()


def test_merging_a_spline_uses_its_visible_curve_not_control_polygon(app: QApplication) -> None:
    canvas = DxfCanvas(selectable=True)
    spline = EntityRecord(
        points=[(0.0, 0.0), (10.0, 30.0), (20.0, 0.0)],
        kind="spline",
        meta={"segments": 64, "control_points": [(0.0, 0.0), (10.0, 30.0), (20.0, 0.0)]},
    )
    continuation = EntityRecord(points=[(20.0, 0.0), (30.0, 0.0)], kind="line")
    canvas._canvas_service.create_entities([spline, continuation])
    canvas.set_selection([spline.id, continuation.id])

    assert canvas.merge_selected_segments_to_objects() == 1
    merged = canvas._entity_for_id(next(iter(canvas._sel)))
    assert merged is not None
    assert merged.kind == "polyline"
    assert len(merged.points) > len(spline.points) + len(continuation.points)
    assert max(y for _x, y in merged.points) > 10.0
    canvas.close()


def test_extended_line_and_point_constraints_are_persistent_and_deterministic() -> None:
    geometry = {
        "axis": [(0.0, 0.0), (10.0, 0.0)],
        "other": [(2.0, 3.0), (5.0, 8.0)],
        "point": [(7.0, 7.0), (8.0, 7.0)],
        "cross_a": [(0.0, 0.0), (10.0, 10.0)],
        "cross_b": [(0.0, 10.0), (10.0, 0.0)],
        "curve_a": [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (3.0, 1.0)],
        "curve_b": [(8.0, 8.0), (9.0, 8.0), (10.0, 8.0), (11.0, 8.0)],
    }
    constraints = [
        GeometricConstraint(
            kind="collinear",
            entity_ids=("axis", "other"),
        ),
        GeometricConstraint(
            kind="midpoint",
            entity_ids=("axis", "point"),
            parameters={"point_vertex": 0},
        ),
        GeometricConstraint(
            kind="intersection",
            entity_ids=("cross_a", "cross_b", "point"),
            parameters={"point_vertex": 1},
        ),
        GeometricConstraint(
            kind="smooth",
            entity_ids=("curve_a", "curve_b"),
            parameters={"curve": True},
        ),
    ]

    solved = solve_constraints(geometry, constraints)

    assert solved["other"][0][1] == pytest.approx(0.0)
    assert solved["other"][1][1] == pytest.approx(0.0)
    assert solved["point"][0] == pytest.approx((5.0, 0.0))
    assert solved["point"][1] == pytest.approx((5.0, 5.0))
    assert solved["curve_b"][:3] == pytest.approx([(3.0, 1.0), (4.0, 1.0), (5.0, 0.0)])


def test_pattern_tree_remains_selectable_across_repeated_show_hide(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    page.load_outline_polys([outline])
    page._preview_categories = {"outline": [outline], "pattern": [], "fill": []}
    page._preview_polys_cache = [outline]
    source_id = page._outline_ids[0]
    for _ in range(3):
        for visible in (True, False):
            page._set_result_visible(visible)
            page._refresh_canvas_panels()
            child = page._layers_tree._tree.topLevelItem(0).child(0)
            assert child is not None
            child.setSelected(True)
            page._layers_tree._emit_selection_request()
            # The outline stays the selectable thing whether the pattern is
            # shown or hidden — there is no second entity set to switch to.
            assert page._canvas.get_selected_ids() == [source_id]
    page.shutdown()
    page.close()


def test_edit_outline_layer_row_selects_shapes_on_canvas(app: QApplication) -> None:
    page = PatternPage(settings={})
    outlines = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)],
        [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 0.0)],
    ]
    page.load_outline_polys(outlines)
    page._refresh_canvas_panels()
    tree = page._layers_tree
    layer = tree._tree.topLevelItem(0)
    assert layer is not None
    tree._tree.clearSelection()
    layer.setSelected(True)
    tree._tree.setCurrentItem(layer)
    tree._emit_selection_request()
    assert set(page._canvas.get_selected_ids()) == set(page._outline_ids)
    assert tree._tree.topLevelItem(0).child(0).text(0).startswith("Outline 1")
    page.shutdown()
    page.close()


def test_post_zone_edit_tree_child_selection_and_delete(app: QApplication) -> None:
    page = PatternPage(settings={})
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    page.load_outline_polys([poly])
    entity_id = page._outline_ids[0]
    page._canvas.set_selection([entity_id])
    page._assign_zone()
    page._refresh_canvas_panels()
    tree = page._layers_tree
    child = tree._tree.topLevelItem(0).child(0)
    assert child is not None and child.text(0).startswith("Outline 1")
    tree._tree.clearSelection()
    child.setSelected(True)
    tree._tree.setCurrentItem(child)
    tree._emit_selection_request()
    assert page._canvas.get_selected_ids() == [entity_id]
    tree._delete_current_layer()
    assert entity_id not in page._canvas.get_entity_ids()
    page.shutdown()
    page.close()


def test_layer_tree_refresh_mutates_existing_rows_when_structure_is_unchanged(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page.load_outline_polys([poly])
    page._refresh_canvas_panels()
    tree = page._layers_tree
    before = tree._tree.topLevelItem(0).child(0)
    assert before is not None
    tree._tree.setCurrentItem(before)
    page._refresh_canvas_panels()
    after = tree._tree.topLevelItem(0).child(0)
    assert after is before
    assert after.isSelected()
    page.shutdown()
    page.close()


def test_pattern_zone_list_preserves_editor_scope_and_allows_outline_only_zone(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page._advanced_mode_cb.setChecked(True)
    polys = [
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)],
        [(6.0, 0.0), (10.0, 0.0), (10.0, 4.0), (6.0, 0.0)],
    ]
    page._canvas.load(polys, fit=False)
    entity_ids = page._canvas.get_entity_ids()
    page._edit_polys = polys
    page._outline_ids = entity_ids
    page._treatments = {
        entity_ids[0]: {"kind": "pattern", "pattern": "Lines"},
        entity_ids[1]: {"kind": "cut", "pattern": "— None —"},
    }
    page._refresh_zone_list()
    page._zone_list.setCurrentRow(1)
    page._refresh_zone_list()
    assert page._zone_list.currentRow() == 1

    page.shutdown()
    page.close()


def test_regions_list_scrolls_and_keeps_every_row_reachable(app: QApplication) -> None:
    """The list holds one row per region, so it must scroll, not clip.

    Sizing it to its contents with the scrollbar forced off hid rows as soon
    as a document had more shapes than fit, and selecting a later row scrolled
    the earlier ones permanently out of reach.
    """
    page = PatternPage(settings={})
    squares = [
        [(i * 10.0, 0.0), (i * 10.0 + 8, 0.0), (i * 10.0 + 8, 8.0), (i * 10.0, 8.0), (i * 10.0, 0.0)]
        for i in range(12)
    ]
    page.load_outline_polys(squares)
    widget = page._zone_list
    assert widget.count() == 12
    assert widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert widget.height() <= widget.maximumHeight()

    # Selecting a late row must not remove earlier rows from the list.
    widget.setCurrentRow(11)
    assert widget.count() == 12
    widget.setCurrentRow(0)
    assert widget.count() == 12
    page.shutdown()
    page.close()


def test_a_region_can_be_treated_from_either_selection_surface(app: QApplication) -> None:
    """Both the canvas and the Regions list must be able to treat a region.

    The Treatment combo is how a region stops being untreated, so it cannot
    require the region to already carry a treatment, and Apply cannot require
    a canvas selection when the list is the surface the user just used.
    """
    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]
    page = PatternPage(settings={})
    page.load_outline_polys([outer, circle])
    outer_id, circle_id = page._outline_ids

    assert not page._assign_zone_btn.isEnabled()

    # Regions list → choose a pattern. This is the flow that did nothing.
    page._zone_list.setCurrentRow(1)
    assert page._assign_zone_btn.isEnabled()
    page._zone_pattern_combo.setCurrentText("Honeycomb")
    assert treatment_kind(page, circle_id) == "pattern_fill"

    # "None" still clears it.
    page._zone_output_combo.setCurrentIndex(page._zone_output_combo.findData("none"))
    assert treatment_kind(page, circle_id) == "none"

    # Canvas selection → Apply.
    page._canvas.set_selection([outer_id])
    page._on_sel_change(1)
    page._zone_pattern_combo.setCurrentText("Honeycomb")
    page._zone_output_combo.setCurrentIndex(page._zone_output_combo.findData("pattern"))
    page._assign_zone()
    assert treatment_kind(page, outer_id) == "pattern"

    # Apply with only a list row selected.
    page._canvas.deselect_all()
    page._on_sel_change(0)
    page._zone_list.setCurrentRow(1)
    page._zone_output_combo.setCurrentIndex(page._zone_output_combo.findData("engrave"))
    page._assign_zone()
    assert treatment_kind(page, circle_id) == "engrave"
    page.shutdown()
    page.close()


def test_multi_region_canvas_selection_shows_every_row(app: QApplication) -> None:
    """A two-shape canvas selection must read as two regions in the list."""
    polys = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
        [(120.0, 0.0), (160.0, 0.0), (160.0, 25.0), (120.0, 25.0), (120.0, 0.0)],
    ]
    page = PatternPage(settings={})
    page.load_outline_polys(polys)
    ids = list(page._outline_ids)

    page._canvas.set_selection([ids[1], ids[2]])
    page._on_sel_change(2)
    assert sorted(i.row() for i in page._zone_list.selectedIndexes()) == [1, 2]

    # Apply reaches every selected region, not just the current row.
    page._zone_pattern_combo.setCurrentText("Honeycomb")
    page._zone_output_combo.setCurrentIndex(page._zone_output_combo.findData("pattern"))
    page._assign_zone()
    assert treatment_kind(page, ids[1]) == "pattern"
    assert treatment_kind(page, ids[2]) == "pattern"
    page.shutdown()
    page.close()


def test_treatment_undo_reaches_every_undo_route(app: QApplication) -> None:
    """Undo is invoked from the Edit menu, palette and radial menu, all of
    which call straight into the canvas. A page-level shortcut alone left
    those routes reporting "Nothing to undo" after a treatment change."""
    from simple_stipple.canvas import commands as canvas_commands

    polys = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
    ]
    page = PatternPage(settings={})
    page.load_outline_polys(polys)
    region_id = page._outline_ids[0]
    page._zone_list.setCurrentRow(0)
    page._zone_pattern_combo.setCurrentText("Honeycomb")
    assert treatment_kind(page, region_id) == "pattern_fill"

    canvas_commands.run(page._canvas, "edit.undo")
    assert treatment_kind(page, region_id) == "none"
    canvas_commands.run(page._canvas, "edit.redo")
    assert treatment_kind(page, region_id) == "pattern_fill"

    # A geometry edit made afterwards still undoes first.
    page._canvas.set_selection([page._outline_ids[1]])
    page._canvas._ctx_delete_poly(page._outline_ids[1])
    page._canvas.undo()
    assert treatment_kind(page, region_id) == "pattern_fill"
    page._canvas.undo()
    assert treatment_kind(page, region_id) == "none"
    page.shutdown()
    page.close()


def test_treatment_changes_undo_and_redo(app: QApplication) -> None:
    """Applying a treatment must be undoable; canvas undo never saw it."""
    polys = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
    ]
    page = PatternPage(settings={})
    page.load_outline_polys(polys)
    ids = list(page._outline_ids)

    page._canvas.set_selection([ids[0]])
    page._on_sel_change(1)
    page._zone_pattern_combo.setCurrentText("Honeycomb")
    page._zone_output_combo.setCurrentIndex(page._zone_output_combo.findData("pattern"))
    page._assign_zone()
    assert treatment_kind(page, ids[0]) == "pattern"

    page._undo_pattern()
    assert treatment_kind(page, ids[0]) == "none"
    page._redo_pattern()
    assert treatment_kind(page, ids[0]) == "pattern"

    # Undo survives repeated application and leaves the region list intact.
    page._undo_pattern()
    assert page._zone_list.count() == 2
    page.shutdown()
    page.close()


def test_drawing_shapes_populates_the_regions_list(app: QApplication) -> None:
    """A region is derived from geometry, so drawing a shape creates one.

    The list only refreshed when a treatment already existed, so a fresh
    document sat on "No closed regions yet" no matter how much was drawn.
    """
    polys = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
        [(120.0, 0.0), (160.0, 0.0), (160.0, 25.0), (120.0, 25.0), (120.0, 0.0)],
    ]
    page = PatternPage(settings={})
    page.load_outline_polys(polys)
    assert not page._treatments  # nothing treated yet

    page._canvas.load(polys, fit=False)
    page._on_canvas_geometry_change()
    assert page._zone_list.count() == 3
    assert "No closed regions" not in page._zone_list.item(0).text()
    page.shutdown()
    page.close()


def test_editing_applies_to_every_region_selected_in_the_list(app: QApplication) -> None:
    """Selecting three regions and changing the pattern must change three."""
    polys = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
        [(120.0, 0.0), (160.0, 0.0), (160.0, 25.0), (120.0, 25.0), (120.0, 0.0)],
    ]
    page = PatternPage(settings={})
    page.load_outline_polys(polys)
    ids = list(page._outline_ids)

    page._zone_list.setCurrentRow(0)
    for row in range(3):
        item = page._zone_list.item(row)
        if item is not None:
            item.setSelected(True)

    page._zone_pattern_combo.setCurrentText("Honeycomb")
    assert [treatment_kind(page, i) for i in ids] == ["pattern_fill"] * 3

    # One undo step for the whole multi-region edit, not one per region.
    page._undo_pattern()
    assert [treatment_kind(page, i) for i in ids] == ["none"] * 3
    page.shutdown()
    page.close()


def test_selection_change_never_desyncs_outline_ids_from_polys(app: QApplication) -> None:
    """Tearing down a preview clears the flag before the canvas is reloaded.

    For that moment the page thinks it is editing while the canvas still holds
    generated geometry. Mirroring it into ``_edit_polys`` desynced the parallel
    id list, and the next outline load died on ``zip(..., strict=True)``.
    """
    outlines = [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 25.0), (0.0, 25.0), (0.0, 0.0)],
        [(60.0, 0.0), (100.0, 0.0), (100.0, 25.0), (60.0, 25.0), (60.0, 0.0)],
    ]
    cells = [[(i, 0.0), (i + 1.0, 0.0), (i + 1.0, 1.0), (i, 1.0)] for i in range(5)]
    page = PatternPage(settings={})
    page.load_outline_polys(outlines)

    page._canvas.load(outlines + cells, fit=False)
    page._on_sel_change(1)
    assert len(page._edit_polys) == len(page._outline_ids) == 2

    page._load_outline_canvas(fit=False)  # used to raise ValueError

    # A desync from any other source reconciles instead of crashing.
    page._edit_polys = outlines + [outlines[0]]
    page._load_outline_canvas(fit=False)
    assert len(page._outline_ids) == len(page._edit_polys) == 3
    page.shutdown()
    page.close()


def test_every_pattern_builds_its_form_fields(app: QApplication) -> None:
    """Adding a pattern must not require editing a second hand-written list.

    The pattern widgets were built from a hardcoded list of names. A pattern
    added to PARAM_SPECS but missed there got no widgets, and the first preview
    died in collect_pattern_params on the missing page attribute.
    """
    from simple_stipple.features.pattern.form_spec import PARAM_SPECS

    page = PatternPage(settings={})
    missing = [
        (name, field.attr)
        for name, fields in PARAM_SPECS.items()
        for field in fields
        if not hasattr(page, field.attr)
    ]
    assert not missing, f"patterns with unbuilt form fields: {missing}"
    assert set(page._pattern_widgets) == set(PARAM_SPECS)
    page.shutdown()
    page.close()


def test_editing_a_region_parameter_keeps_the_field_alive(app: QApplication) -> None:
    """Typing must not destroy the field being typed into.

    Committing an edit refreshed the whole Regions list, which re-entered
    on_zone_selected and rebuilt every parameter widget — so the caret was
    lost on every keystroke. Only the row text needs restating.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    poly = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0), (0.0, 0.0)]
    page = PatternPage(settings={})
    page.show()
    page.load_outline_polys([poly])
    page._zone_list.setCurrentRow(0)
    page._zone_pattern_combo.setCurrentText("Honeycomb")

    key = next(iter(page._zone_param_inputs))
    field = page._zone_param_inputs[key]
    field.setFocus(Qt.FocusReason.MouseFocusReason)
    QTest.keyClicks(field, "5")

    # Widget identity is the real guard: a rebuilt field cannot hold the caret.
    # hasFocus() itself is not asserted — it depends on the window being active,
    # which is not reliable under a batch test run.
    assert page._zone_param_inputs[key] is field, "parameter widget was rebuilt mid-edit"
    assert not field.isHidden()
    # The row label still reflects the change.
    assert "Honeycomb" in page._zone_list.item(0).text()
    page.shutdown()
    page.close()


def test_engraving_preview_is_shown_whole_and_clipped_only_on_export(
    app: QApplication, tmp_path
) -> None:
    """The canvas shows the whole image; the region clips it at export.

    Masking the preview cropped the artwork to its region on screen, which hid
    the edges you need in order to move and resize it.
    """
    from PIL import Image

    source = tmp_path / "swatch.png"
    Image.new("RGB", (64, 64), (200, 80, 40)).save(source)
    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]

    page = PatternPage(settings={})
    page.load_outline_polys([outer, circle])
    page._zone_list.setCurrentRow(1)
    page._engraving_image_path = str(source)
    page._attach_image_to_selected_region(str(source))
    page._update_engraving_overlay()

    # The canvas holds the image uncropped — a uniform alpha, no cut-out.
    assert page._canvas._bg_pil is not None
    assert page._canvas._bg_pil.getchannel("A").getextrema() == (125, 125)
    # The export mask is still the region that owns it.
    assert page._engraving_mask_polys() == [list(circle)]
    page.shutdown()
    page.close()


def test_image_belongs_to_a_region_and_is_undoable(app: QApplication) -> None:
    """The image is owned by the region that masks it.

    It used to be page-global — one path and one placement for the whole
    document, with a separate combo to pick a target. Attaching it to a region
    removes that choice, allows more than one engraved region, and makes the
    whole thing undoable like every other treatment change.
    """
    from simple_stipple.features.pattern.treatments import region_engraving, treatment_kind

    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]
    page = PatternPage(settings={})
    page.load_outline_polys([outer, circle])
    region_id = page._outline_ids[1]

    page._zone_list.setCurrentRow(1)
    page._engrave_x.setValue(30.0)
    page._engrave_y.setValue(30.0)
    page._engrave_w.setValue(40.0)
    page._engrave_h.setValue(40.0)
    page._attach_image_to_selected_region("/tmp/logo.png")

    # Choosing an image makes the region an Engrave region and masks to it.
    assert treatment_kind(page, region_id) == "engrave"
    assert region_engraving(page, region_id)["path"] == "/tmp/logo.png"
    assert page._engraving_mask_polys() == [list(circle)]

    # Selecting another region and coming back re-points the placement fields.
    page._zone_list.setCurrentRow(0)
    page._zone_list.setCurrentRow(1)
    assert page._engrave_x.value() == 30.0
    assert page._engrave_w.value() == 40.0

    page._undo_pattern()
    assert treatment_kind(page, region_id) == "none"
    assert region_engraving(page, region_id) is None
    page._redo_pattern()
    assert treatment_kind(page, region_id) == "engrave"
    page.shutdown()
    page.close()


def test_image_flow_is_region_owned_end_to_end(app: QApplication, tmp_path) -> None:
    """Attach, drag, undo, export, save/reload and legacy migration."""
    from PIL import Image

    from simple_stipple.features.pattern.treatments import (
        engraving_regions,
        region_engraving,
        treatment_kind,
    )

    source = tmp_path / "logo.png"
    Image.new("RGB", (64, 64), (200, 80, 40)).save(source)
    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]

    page = PatternPage(settings={})
    page.load_outline_polys([outer, circle])
    region_id = page._outline_ids[1]
    page._zone_list.setCurrentRow(1)
    for widget, value in (
        (page._engrave_x, 30.0),
        (page._engrave_y, 30.0),
        (page._engrave_w, 40.0),
        (page._engrave_h, 40.0),
    ):
        widget.setValue(value)
    page._attach_image_to_selected_region(str(source))

    assert treatment_kind(page, region_id) == "engrave"
    assert page._engraving_mask_polys() == [list(circle)]

    # Dragging on canvas is a placement edit on the region, and undoes.
    page._on_engraving_canvas_transform(35.0, 35.0, 30.0, 30.0, 0.0)
    assert region_engraving(page, region_id)["x"] == 35.0
    page._undo_pattern()
    assert region_engraving(page, region_id)["x"] == 30.0

    # Export targets the region that owns the image.
    assert page._active_engraving()[0] == region_id

    # It survives a workspace round-trip.
    restored = PatternPage(settings={})
    restored.apply_workspace_state(page.get_workspace_state())
    assert len(engraving_regions(restored)) == 1

    # A pre-region workspace stored one page-global image; it lands on the
    # innermost region containing it rather than being dropped.
    legacy = PatternPage(settings={})
    legacy.apply_workspace_state(
        {
            "edit_polys": [outer, circle],
            "orig_polys": [outer, circle],
            "outline_ids": ["a", "b"],
            "orig_w": 100.0,
            "orig_h": 100.0,
            "zones": [
                {
                    "outline_ids": ["b"],
                    "pattern": "— None —",
                    "output_mode": "outline",
                    "scale": (100.0, 100.0),
                }
            ],
            "engraving_image_path": str(source),
            "engraving_options": {"x": 30, "y": 30, "width": 40, "height": 40},
        }
    )
    assert treatment_kind(legacy, "b") == "engrave"
    assert legacy._engraving_mask_polys() == [list(circle)]
    for instance in (page, restored, legacy):
        instance.shutdown()
        instance.close()


def test_image_is_a_pattern_choice_not_a_sidebar_section(app: QApplication) -> None:
    """A region either carries a generated pattern or an image, so both live in
    the same dropdown and the image controls belong to the selected region."""
    from simple_stipple.features.pattern.treatments import IMAGE_PATTERN, treatment_kind

    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]
    page = PatternPage(settings={})
    page.show()
    page._zones_section.set_expanded(True)
    page.load_outline_polys([outer, circle])
    region_id = page._outline_ids[1]

    items = [page._zone_pattern_combo.itemText(i) for i in range(page._zone_pattern_combo.count())]
    assert IMAGE_PATTERN in items

    # The image controls are mounted inside the region editor, not the sidebar.
    assert page._engraving_section.isHidden()

    page._zone_list.setCurrentRow(1)
    page._zone_pattern_combo.setCurrentText(IMAGE_PATTERN)
    assert treatment_kind(page, region_id) == "engrave"
    assert not page._engraving_section.isHidden()
    # Image is a UI choice; the engine emits the region outline, not a pattern.
    assert page._zones[0]["pattern"] == "— None —"

    page._zone_list.setCurrentRow(0)
    assert page._engraving_section.isHidden()
    page._zone_list.setCurrentRow(1)
    assert not page._engraving_section.isHidden()

    page._undo_pattern()
    assert treatment_kind(page, region_id) == "none"
    page.shutdown()
    page.close()


def test_image_lands_at_natural_size_and_stays_masked(app: QApplication, tmp_path) -> None:
    """The image arrives at the size it actually is.

    Auto-fitting it to the outline silently rescaled the artwork before the
    user had seen it. The region still masks the image; only the sizing is
    the user's to choose.
    """
    from PIL import Image

    source = tmp_path / "art.png"
    Image.new("RGB", (300, 150), (9, 9, 9)).save(source, dpi=(300, 300))  # 25.4 x 12.7 mm

    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    page = PatternPage(settings={})
    page.load_outline_polys([outer])

    # PIL round-trips the DPI rational as 299.9994, so allow 0.01 mm.
    assert page._natural_image_size_mm(str(source)) == pytest.approx((25.4, 12.7), abs=0.01)

    page._zone_list.setCurrentRow(0)
    page._engraving_image_path = str(source)
    width_mm, height_mm = page._natural_image_size_mm(str(source))
    page._engrave_w.setValue(width_mm)
    page._engrave_h.setValue(height_mm)
    page._attach_image_to_selected_region(str(source))

    # Not stretched to the 100 mm outline.
    assert page._engrave_w.value() == pytest.approx(25.4, abs=0.01)
    assert page._engrave_h.value() == pytest.approx(12.7, abs=0.01)
    # Still clipped to the region that owns it.
    assert page._engraving_mask_polys() == [list(outer)]
    page.shutdown()
    page.close()


def test_pre_phase1_workspace_migrates_zones_and_cutouts(app: QApplication) -> None:
    """A workspace written before region treatments opens with the same output."""
    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
    circle = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]
    page = PatternPage(settings={})
    page.apply_workspace_state(
        {
            "edit_polys": [outer, circle],
            "orig_polys": [outer, circle],
            "outline_ids": ["outer", "circle"],
            "orig_w": 100.0,
            "orig_h": 100.0,
            "outline_roles": {"outer": "boundary", "circle": "cutout"},
            "zones": [
                {
                    "outline_ids": ["outer"],
                    "pattern": "Honeycomb",
                    "params": {"r": 4.0, "gap": 0.5},
                    "scale": (100.0, 100.0),
                    "output_mode": "pattern",
                }
            ],
            "exclusion_ids": ["circle"],
        }
    )

    assert treatment_kind(page, "outer") == "pattern"
    # A cutout always meant "subtract this area but do not fill it".
    assert treatment_kind(page, "circle") == "cut"
    zones = page._zones
    assert zones[0]["pattern"] == "Honeycomb"
    assert [zone["outline_ids"] for zone in zones] == [["outer"], ["circle"]]

    # Round-tripping keeps the treatments verbatim rather than re-migrating.
    assert page.get_workspace_state()["treatments"]["circle"]["kind"] == "cut"
    page.shutdown()
    page.close()


def test_pattern_zone_list_delete_key_removes_selected_zone(app: QApplication) -> None:
    page = PatternPage(settings={})
    page._advanced_mode_cb.setChecked(True)
    polys = [
        [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)],
        [(6.0, 0.0), (10.0, 0.0), (10.0, 4.0), (6.0, 0.0)],
    ]
    page._canvas.load(polys, fit=False)
    ids = page._canvas.get_entity_ids()
    page._edit_polys = polys
    page._outline_ids = ids
    page._treatments = {
        ids[0]: {"kind": "pattern", "pattern": "Lines"},
        ids[1]: {"kind": "pattern", "pattern": "Dots"},
    }
    page._refresh_zone_list()
    page._zone_list.setCurrentRow(1)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    page._zone_list.keyPressEvent(event)
    assert event.isAccepted()
    assert len(page._zones) == 1
    assert page._zones[0]["outline_ids"] == [ids[0]]
    # The region itself survives; only its treatment was cleared.
    assert page._zone_list.count() == 2
    page.shutdown()
    page.close()


def test_clean_export_preflight_proceeds_without_prompt(app: QApplication) -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    parent = QWidget()
    proceed, report = export_preflight(parent, [square], action="Export", allow_open_paths=False)
    assert proceed
    assert report.ready


def test_significant_status_outcomes_enter_notification_history(
    app: QApplication,
) -> None:
    from PySide6.QtWidgets import QLabel

    before = len(notification_history())
    label = QLabel()
    set_status_label(label, "Export completed", STATUS_OK)
    assert len(notification_history()) == before + 1
    assert notification_history()[-1][1] == "Export completed"


def test_common_dimension_fields_display_two_decimal_places(app: QApplication) -> None:
    page = PatternPage(settings={})
    page._update_dims_from_polys([[(0.0, 0.0), (12.3456, 7.8912)]])
    assert page._scale_w.text() == "12.35"
    assert page._scale_h.text() == "7.89"


def test_update_result_replaces_content_without_deleting_dialog_actions(
    app: QApplication,
) -> None:
    current = UpdateInfo("0.3.4", "https://example.test/app.exe", "", False, "abc")
    newer = UpdateInfo("0.3.5", "https://example.test/app.exe", "Fixes", True, "abc")
    dialog = UpdateDialog(update_info=current)
    close_button = dialog._close_btn

    dialog._on_check_complete(newer, dialog._content_layout)
    app.processEvents()

    assert close_button is dialog._close_btn
    assert close_button is not None
    assert close_button.text() == "Close"
    assert dialog._download_btn is not None
