"""Focused regression checks added by the UI layout and behavior audit."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import QApplication, QToolButton, QWidget
from shapely.geometry import Polygon

from simple_stipple.canvas.snap import SnapEngine
from simple_stipple.canvas.widgets.draw_sidebar import DrawSidebar, _ResizeHandle
from simple_stipple.canvas.widgets.status_strip import CanvasStatusStrip
from simple_stipple.canvas.widgets.toolbar import canvas_toolbar
from simple_stipple.engine.patterns.fill import FillSpec, apply_fill
from simple_stipple.engine.patterns.processing import PatternProcessor
from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.help import HelpDialog
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.pattern.zones import (
    _restore_preview_poly_to_source,
    highlight_zone_on_canvas,
    select_zone_for_canvas_selection,
)
from simple_stipple.platform.settings import MIN_DRAW_SIDEBAR_WIDTH
from simple_stipple.platform.updates import UpdateInfo
from simple_stipple.ui.components.cycle_button import CycleIconButton
from simple_stipple.ui.components.workflow import WorkflowStepper, set_status_label
from simple_stipple.ui.dialogs.export_preflight import export_preflight
from simple_stipple.ui.dialogs.settings_dialog import SettingsDialog
from simple_stipple.ui.dialogs.update_dialog import UpdateDialog
from simple_stipple.ui.notifications import notification_history
from simple_stipple.ui.style.theme import STATUS_OK


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
    assert not hasattr(button, "_hover_timer")
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

    assert result == (10.0, 10.0, "equal_length")
    assert engine.last_relationship_type == "equal_length"
    assert engine.last_relationship_reference is not None
    assert engine.last_relationship_reference[0] == "__active_draw__"


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
    page._zones = [
        {"outline_ids": [entity_ids[0]], "pattern": "Lines"},
        {"outline_ids": [entity_ids[1]], "pattern": "Dots"},
    ]
    page._refresh_zone_list()

    highlight_zone_on_canvas(page, 1)
    assert page._canvas._accent_polys == {entity_ids[1]: "#f5a623"}

    page._showing_preview = True
    page._preview_zone_owners = [0, 1, 1]
    page._canvas.set_selection([entity_ids[2]])
    select_zone_for_canvas_selection(page, preview=True)
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


def test_invalid_zone_exclusion_overlay_does_not_abort_preview() -> None:
    processor = PatternProcessor()
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    exclusion = [(2.0, 2.0), (8.0, 8.0), (2.0, 8.0), (8.0, 2.0), (2.0, 2.0)]
    fill: list[list[tuple[float, float]]] = []
    processor.build_pattern_polys(
        [outline],
        pattern="— None —",
        params={},
        scale=(1.0, 1.0),
        orig_w=10.0,
        orig_h=10.0,
        exclusion_polys=[exclusion],
        fill_options={"mode": "lines", "spacing": 1.0, "target_outline": True},
        fill_polys_out=fill,
    )
    assert isinstance(fill, list)


def test_nested_zone_exclusion_uses_repaired_coverage() -> None:
    processor = PatternProcessor()
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    # The child touches the parent boundary; this is a valid clipped preview
    # cell and must still be excluded from the parent's generated treatment.
    child = [(0.0, 5.0), (4.0, 5.0), (4.0, 9.0), (0.0, 9.0), (0.0, 5.0)]
    zones = [{"polys": [outer]}, {"polys": [child]}]
    assert processor._zone_nested_exclusions(zones, 0) == [child]
    assert processor._zone_nested_exclusions(zones, 1) == []


def test_preview_cell_promotion_restores_zone_source_coordinates() -> None:
    outer = [(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0), (10.0, 10.0)]
    page = SimpleNamespace(
        _preview_categories={"outline": [outer], "zone_owners": [0, 0]},
        _zones=[{"outline_ids": ["outer"], "scale": (40.0, 40.0)}],
        _orig_w=20.0,
        _orig_h=20.0,
        _outline_ids=["outer"],
        _edit_polys=[outer],
    )
    # Preview coordinates are scaled from the source bbox by 2x.
    preview_cell = [(10.0, 10.0), (18.0, 10.0), (18.0, 18.0), (10.0, 18.0)]
    restored = _restore_preview_poly_to_source(page, "cell", preview_cell, {"cell": 1})
    assert restored == [(10.0, 10.0), (14.0, 10.0), (14.0, 14.0), (10.0, 14.0)]


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
    outline = next(row for row in rows if row["name"] == "pattern_active")
    assert len(outline["shapes"]) == 1
    assert tuple(outline["shapes"][0]["key"]) == tuple(ids)
    page.shutdown()
    page.close()


def test_pattern_preview_layer_tree_uses_selectable_canvas_ids(app: QApplication) -> None:
    page = PatternPage(settings={})
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    pattern = [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0)]
    fill = [(4.0, 4.0), (5.0, 4.0), (5.0, 5.0), (4.0, 4.0)]
    page._preview_categories = {"outline": [outline], "pattern": [pattern], "fill": [fill]}
    page._preview_polys_cache = [outline, pattern, fill]
    page._showing_preview = True
    page._canvas.load(page._preview_polys_cache, fit=False)
    page._refresh_canvas_panels()

    ids = page._canvas.get_entity_ids()
    rows = page._layer_module.controller._build_rows(page._layer_module.state)
    assert [row["key"] for row in rows[1]["shapes"]] == [ids[1]]
    pattern_item = page._layers_tree._tree.topLevelItem(1).child(0)
    assert pattern_item is not None
    page._layers_tree._tree.clearSelection()
    pattern_item.setSelected(True)
    page._layers_tree._emit_selection_request()
    assert page._canvas.get_selected_ids() == [ids[1]]
    page.shutdown()
    page.close()


def test_pattern_preview_outline_rows_keep_source_order_and_ids(app: QApplication) -> None:
    page = PatternPage(settings={})
    outlines = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)],
        [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 0.0)],
    ]
    pattern = [[(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0)]]
    page._preview_categories = {"outline": outlines, "pattern": pattern, "fill": []}
    page._preview_polys_cache = outlines + pattern
    page._showing_preview = True
    page._canvas.load(page._preview_polys_cache, fit=False)
    page._refresh_canvas_panels()
    ids = page._canvas.get_entity_ids()
    rows = page._layer_module.controller._build_rows(page._layer_module.state)
    assert [shape["key"] for shape in rows[0]["shapes"]] == ids[:2]
    assert [shape["label"] for shape in rows[0]["shapes"]] == [
        "Outline 1  ·  3 pts",
        "Outline 2  ·  3 pts",
    ]
    outline_item = page._layers_tree._tree.topLevelItem(0).child(1)
    assert outline_item is not None
    page._layers_tree._tree.clearSelection()
    outline_item.setSelected(True)
    page._layers_tree._emit_selection_request()
    assert page._canvas.get_selected_ids() == [ids[1]]
    page.shutdown()
    page.close()


def test_pattern_tree_remains_selectable_across_repeated_preview_toggles(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    page.load_outline_polys([outline])
    page._preview_categories = {"outline": [outline], "pattern": [], "fill": []}
    page._preview_polys_cache = [outline]
    source_id = page._outline_ids[0]
    for _ in range(3):
        page._on_preview_toggled(True)
        page._refresh_canvas_panels()
        preview_child = page._layers_tree._tree.topLevelItem(0).child(0)
        assert preview_child is not None
        preview_child.setSelected(True)
        page._layers_tree._emit_selection_request()
        assert page._canvas.get_selected_ids()
        page._on_preview_toggled(False)
        page._refresh_canvas_panels()
        edit_child = page._layers_tree._tree.topLevelItem(0).child(0)
        assert edit_child is not None
        edit_child.setSelected(True)
        page._layers_tree._emit_selection_request()
        assert page._canvas.get_selected_ids() == [source_id]
    page.shutdown()
    page.close()


def test_assigning_preview_outline_reuses_source_without_duplicate(app: QApplication) -> None:
    page = PatternPage(settings={})
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    cell = [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0)]
    page.load_outline_polys([outline])
    source_id = page._outline_ids[0]
    page._preview_categories = {"outline": [outline], "pattern": [cell], "fill": []}
    page._preview_polys_cache = [outline, cell]
    page._showing_preview = True
    page._canvas.load([outline, cell], fit=False)
    page._canvas.set_selection([page._canvas.get_entity_ids()[0]])
    page._assign_zone()
    assert page._outline_ids == [source_id]
    page._on_preview_toggled(False)
    assert page._canvas.get_entity_ids() == [source_id]
    page._canvas.set_selection([source_id])
    page._refresh_canvas_panels()
    page._layers_tree.select_shape_keys([source_id])
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
    page._on_preview_toggled(False)
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


def test_preview_cell_zone_materializes_base_without_dropping_pattern(app: QApplication) -> None:
    page = PatternPage(settings={})
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 0.0)]
    cell = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 4.0)]
    page.load_outline_polys([outer])
    page._advanced_mode_cb.setChecked(True)
    page._pattern_combo.setCurrentText("Lines")
    page._preview_categories = {"outline": [outer], "pattern": [cell], "fill": []}
    page._preview_polys_cache = [outer, cell]
    page._showing_preview = True
    page._canvas.load([outer, cell], fit=False)
    page._canvas.set_selection([page._canvas.get_entity_ids()[1]])
    page._assign_zone()
    assert len(page._zones) == 2
    assert page._zones[0]["outline_ids"] == [page._outline_ids[0]]
    assert page._zones[1]["outline_ids"] == [page._outline_ids[1]]
    page.shutdown()
    page.close()


def test_pattern_zone_list_preserves_editor_scope_and_allows_outline_only_zone(
    app: QApplication,
) -> None:
    page = PatternPage(settings={})
    page._advanced_mode_cb.setChecked(True)
    poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page._canvas.load([poly], fit=False)
    entity_id = page._canvas.get_entity_ids()[0]
    page._edit_polys = [poly]
    page._outline_ids = [entity_id]
    page._zones = [
        {"outline_ids": [entity_id], "pattern": "Lines"},
        {"outline_ids": [entity_id], "pattern": "Dots"},
    ]
    page._refresh_zone_list()
    page._zone_list.setCurrentRow(1)
    page._refresh_zone_list()
    assert page._zone_list.currentRow() == 1

    page.shutdown()
    page.close()


def test_pattern_zone_list_delete_key_removes_selected_zone(app: QApplication) -> None:
    page = PatternPage(settings={})
    page._advanced_mode_cb.setChecked(True)
    ids = ["zone-a", "zone-b"]
    page._zones = [
        {"outline_ids": [ids[0]], "pattern": "Lines"},
        {"outline_ids": [ids[1]], "pattern": "Dots"},
    ]
    page._refresh_zone_list()
    page._zone_list.setCurrentRow(1)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    page._zone_list.keyPressEvent(event)
    assert event.isAccepted()
    assert len(page._zones) == 1
    assert page._zones[0]["outline_ids"] == [ids[0]]
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
