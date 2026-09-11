"""The full application keeps compact Pattern drawers and export reachable."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QWidget

from simple_stipple.app import window as window_module
from simple_stipple.app.tasks import TaskController
from simple_stipple.app.window import App
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.platform import settings as settings_module


@pytest.fixture
def compact_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    palette, font, stylesheet = app.palette(), app.font(), app.styleSheet()
    properties = {
        bytes(name).decode(): app.property(bytes(name).decode())
        for name in app.dynamicPropertyNames()
    }
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings_module, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(TaskController, "startup", lambda self, **kwargs: None)
    monkeypatch.setattr(App, "_confirm_discard_if_dirty", lambda self, **kwargs: True)
    monkeypatch.setattr(PatternPage, "_start_preview_thread", lambda self: None)
    windows = []

    def create(scale: float, width: int, height: int) -> App:
        settings = {"appearance": "dark", "ui_scale": scale, "reduced_motion": True}
        monkeypatch.setattr(window_module, "load_settings", lambda: copy.deepcopy(settings))
        window = App()
        windows.append(window)
        window.resize(width, height)
        window.show()
        window._page_runtime.switch_to("pattern")
        _settle(app)
        return window

    yield app, create
    for window in windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle(app)
    app.setPalette(palette)
    app.setFont(font)
    app.setStyleSheet(stylesheet)
    for name in app.dynamicPropertyNames():
        app.setProperty(bytes(name).decode(), properties.get(bytes(name).decode()))


def _settle(app: QApplication) -> None:
    for _ in range(8):
        app.processEvents()


def _rect_in(widget: QWidget, parent: QWidget):
    rect = widget.rect()
    rect.moveTopLeft(widget.mapTo(parent, QPoint()))
    return rect


def _assert_drawer_controls(page: PatternPage) -> None:
    splitters = (page._splitter, page._canvas_splitter)
    visible = [s._drawer_toggle for s in splitters if s._drawer_toggle.isVisible()]
    for button in visible:
        assert not button.visibleRegion().isEmpty()
    if len(visible) == 2:
        assert not _rect_in(visible[0], page).intersects(_rect_in(visible[1], page))
    for splitter in splitters:
        opened = splitter.sizes()[splitter._responsive_secondary] >= splitter.MIN_DRAWER_WIDTH
        assert splitter._drawer_toggle.text().startswith("Hide " if opened else "Show ")


@pytest.mark.parametrize("scale", [1.0, 1.25])
@pytest.mark.parametrize("width,height", [(900, 600), (1280, 820)])
def test_pattern_drawers_and_export_stay_reachable(compact_app, scale, width, height):
    app, create = compact_app
    window = create(scale, width, height)
    page = window._page_runtime.get("pattern")
    assert isinstance(page, PatternPage)
    assert (window.width(), window.height()) == (width, height)
    _assert_drawer_controls(page)
    page.load_outline_polys([[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 0.0)]])
    _settle(app)

    # Both controls remain usable with geometry and the canvas toolbar present.
    for splitter in (page._splitter, page._canvas_splitter):
        splitter._set_drawer_open(False)
        splitter._drawer_toggle.click()
        _settle(app)
        assert splitter.sizes()[splitter._responsive_secondary] >= 220
    _assert_drawer_controls(page)
    assert not page._gen_btn.visibleRegion().isEmpty()
    assert not _rect_in(page._gen_btn, page).intersects(_rect_in(page._status, page))
    assert not _rect_in(page._gen_btn, page).intersects(_rect_in(page._details_scroll, page))
    if height == 600:
        assert page._details_scroll.verticalScrollBar().maximum() > 0

    # Handle drags must synchronize the label and reopening must recover space.
    for splitter in (page._canvas_splitter, page._splitter):
        index = splitter._responsive_secondary
        sizes = [splitter.width(), 0] if index == 1 else [0, splitter.width()]
        splitter.setSizes(sizes)
        splitter.splitterMoved.emit(0, 1)
        _settle(app)
        _assert_drawer_controls(page)
        splitter._drawer_toggle.click()
        _settle(app)
        assert splitter.sizes()[index] >= 220
    window.resize(1500, 900)
    _settle(app)
    _assert_drawer_controls(page)
    window.resize(width, height)
    _settle(app)
    _assert_drawer_controls(page)


@pytest.mark.parametrize("page_id", ["draft", "trace"])
def test_shared_drawers_keep_overlay_controls_reachable(compact_app, page_id):
    app, create = compact_app
    window = create(1.25, 900, 600)
    window._page_runtime.switch_to(page_id)
    _settle(app)
    page = window._page_runtime.get(page_id)
    from simple_stipple.ui.components.layout import ResponsiveContentSplitter

    splitters = page.findChildren(ResponsiveContentSplitter)
    assert splitters
    for splitter in splitters:
        if splitter._responsive_secondary is None or not splitter._drawer_toggle.isVisible():
            continue
        splitter._set_drawer_open(False)
        splitter._drawer_toggle.click()
        _settle(app)
        assert splitter.sizes()[splitter._responsive_secondary] >= 220
        assert not splitter._drawer_toggle.visibleRegion().isEmpty()
        assert splitter._drawer_toggle.text().startswith("Hide ")


def test_collapsed_pattern_groups_preserve_document_and_region_values(compact_app):
    from simple_stipple.features.pattern.form import collect_form_state

    app, create = compact_app
    window = create(1.0, 1280, 820)
    page = window._page_runtime.get("pattern")
    page.load_outline_polys(
        [
            [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 0.0)],
            [(50.0, 0.0), (90.0, 0.0), (90.0, 30.0), (50.0, 0.0)],
        ]
    )
    page._zone_list.setCurrentRow(0)
    page._pattern_combo.setCurrentText("Honeycomb")
    page._hex_r.setText("3.2")
    page._lattice_origin_x.setText("2.5")
    page._lattice_origin_y.setText("1.75")
    page._lattice_seed.setText("19")
    expected = collect_form_state(page)
    treatment = copy.deepcopy(page._treatments)
    for section in (page._pattern_grid_section, page._presets_section):
        section.set_expanded(True)
        _settle(app)
        section.set_expanded(False)
    assert collect_form_state(page) == expected
    assert page._treatments == treatment

    page._zone_list.setCurrentRow(1)
    page._pattern_combo.setCurrentText("Voronoi")
    page._zone_list.setCurrentRow(0)
    assert page._pattern_combo.currentText() == "Honeycomb"
    assert page._hex_r.text() == "3.2"
    assert page._lattice_origin_x.text() == "2.5"
    assert page._lattice_seed.text() == "19"
    assert page._pattern_props_scope.text().startswith("Region 1")

    state = page.get_workspace_state()
    restored = create(1.0, 1280, 820)._page_runtime.get("pattern")
    restored.apply_workspace_state(state)
    restored._zone_list.setCurrentRow(0)
    assert restored._hex_r.text() == "3.2"
    assert restored._lattice_origin_y.text() == "1.75"
    assert restored._lattice_seed.text() == "19"


@pytest.mark.parametrize("initial_pattern", ["— None —", "Image"])
def test_presets_remain_usable_without_a_generator(compact_app, initial_pattern):
    from PySide6.QtWidgets import QPushButton

    app, create = compact_app
    page = create(1.0, 1280, 820)._page_runtime.get("pattern")
    page._pattern_combo.setCurrentText(initial_pattern)
    page._presets["Fill only"] = {
        "pattern": "— None —",
        "fill_mode": "lines",
        "fill_spacing": "0.37",
        "fill_target_outline": True,
        "lattice_origin_x": "8.5",
        "lattice_seed": "23",
    }
    page._refresh_preset_combo()
    page._presets_section.set_expanded(True)
    _settle(app)
    assert page._preset_combo.isVisibleTo(page)
    page._preset_combo.setCurrentText("Fill only")
    apply = next(
        button
        for button in page._presets_section.findChildren(QPushButton)
        if button.accessibleName() == "Apply selected preset"
    )
    apply.click()
    assert page._pattern_combo.currentText() == "— None —"
    assert page._fill_mode_combo.currentData() == "lines"
    assert page._fill_spacing.text() == "0.37"
    assert page._lattice_origin_x.text() == "8.5"
    assert page._lattice_seed.text() == "23"
    assert not page._tile_library_widget.isVisibleTo(page)


def test_custom_tile_group_follows_saved_tile_restoration(compact_app):
    app, create = compact_app
    page = create(1.0, 1280, 820)._page_runtime.get("pattern")
    tile = [[(0.0, 0.0), (2.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]
    page._tile_motifs["Polish fixture"] = tile
    page._tile_settings["Polish fixture"] = {"rotation": "31", "size_percent": "125"}
    page._refresh_pattern_choices()
    page._pattern_combo.setCurrentText("Custom · Polish fixture")
    _settle(app)
    assert page._tile_library_widget.isVisibleTo(page)
    assert page._custom_tile_polys == tile
    assert page._pattern_rotation.text() == "31"
    assert page._pattern_size_percent.text() == "125"
    page._pattern_combo.setCurrentText("Honeycomb")
    assert not page._tile_library_widget.isVisibleTo(page)
    page._pattern_combo.setCurrentText("Custom · Polish fixture")
    assert page._tile_library_widget.isVisibleTo(page)
    assert page._custom_tile_polys == tile
    assert page._pattern_rotation.text() == "31"

    # A region may reference a saved tile whose snapshot has an older grid.
    page._tile_settings["Polish fixture"].update(
        {"lattice_origin_x": "0", "lattice_origin_y": "0", "lattice_seed": "1"}
    )
    page.load_outline_polys(
        [
            [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 0.0)],
            [(50.0, 0.0), (90.0, 0.0), (90.0, 30.0), (50.0, 0.0)],
        ]
    )
    page._zone_list.setCurrentRow(0)
    page._pattern_combo.setCurrentText("Custom · Polish fixture")
    page._pattern_rotation.setText("32")
    page._lattice_origin_x.setText("8.5")
    page._lattice_seed.setText("23")
    page._zone_list.setCurrentRow(1)
    page._pattern_combo.setCurrentText("Honeycomb")
    page._zone_list.setCurrentRow(0)
    assert page._pattern_combo.currentText() == "Custom · Polish fixture"
    assert page._lattice_origin_x.text() == "8.5"
    assert page._lattice_seed.text() == "23"
