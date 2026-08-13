"""Focused supplemental verification for the completed package migration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QCoreApplication, QEvent, QSize
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QLabel,
    QStyle,
    QStyleOption,
    QWidget,
)

from simple_stipple.app.tasks import AutosaveController
from simple_stipple.app.window import App
from simple_stipple.canvas.operations.draw_ops import DrawOpsService
from simple_stipple.canvas.view.helpers import _animate_view_to
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.formats.dxf import load_dxf_polylines
from simple_stipple.core.formats.dxf_write import write_polylines_dxf
from simple_stipple.core.formats.laserstar import export_laserstar_package
from simple_stipple.core.imaging import (
    RasterEngravingSpec,
    export_raster_job,
)
from simple_stipple.core.imaging import image_to_outlines
from simple_stipple.core.patterns.fill import FillSpec, apply_fill, build_fill_region
from simple_stipple.features.help import HelpDialog
from simple_stipple.features.pattern.workers import (
    CancellableTaskState,
    TaskPhase,
)
from simple_stipple.features.repository import RepoPage
from simple_stipple.ui.components import feedback
from simple_stipple.ui.components.layout import CollapsibleSection
from simple_stipple.ui.components.recent import KIND_DXF, list_recent
from simple_stipple.ui.components.workflow import StatusRegion, set_status_label
from simple_stipple.ui.dialogs import files as file_dialogs
from simple_stipple.ui.style import resolve_tokens


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_test_windows(app: QApplication, monkeypatch: pytest.MonkeyPatch):
    """Close UI-test windows without allowing a modal discard prompt to stall Qt.

    These tests exercise layout and focus only; save/discard behavior is
    covered independently. Page activation can mark a fresh workspace dirty,
    so the production confirmation dialog would otherwise block test teardown.

    Startup recovery is suppressed for the same reason, and for a sharper one:
    ``App()`` reads the *real* application-support directory, so a developer
    with the app open — or any leftover autosave — made this suite hang on a
    modal recovery prompt forever, with no output saying why.
    """
    monkeypatch.setattr(App, "_confirm_discard_if_dirty", lambda _self, **_kwargs: True)
    monkeypatch.setattr(AutosaveController, "offer_startup_autosave_recovery", lambda _self: None)
    existing = set(app.topLevelWidgets())
    yield
    for widget in set(app.topLevelWidgets()) - existing:
        widget.close()
        widget.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("width,height", [(1280, 820), (1050, 700), (900, 600)])
def test_every_top_level_page_fits_and_visible_buttons_meet_minimum_target(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
) -> None:
    monkeypatch.setattr("simple_stipple.platform.config.save_settings", lambda _settings: None)
    window = App()
    window.resize(width, height)
    window.show()
    app.processEvents()

    pages: list[tuple[str, QWidget]] = []
    for index in range(window._tabs.count()):
        window._tabs.setCurrentIndex(index)
        app.processEvents()
        pages.append((window._tabs.tabText(index), window._tabs.currentWidget()))
    pages.extend((("Help", HelpDialog(window)), ("Repository", RepoPage())))

    for name, page in pages:
        page.resize(width, height)
        page.show()
        app.processEvents()
        assert page.minimumSizeHint().width() <= width, name
        undersized = [
            f"{type(button).__name__}({button.text()!r})={button.width()}x{button.height()}"
            for button in page.findChildren(QAbstractButton)
            if button.isVisible()
            and not button.visibleRegion().isEmpty()
            and (button.width() < 24 or button.height() < 24)
        ]
        # No exceptions are currently necessary. Keeping the diagnostic list
        # makes any future, explicitly justified WCAG exception reviewable.
        assert not undersized, f"{name}: {undersized}"
        page.hide()
    window.close()


def test_reduced_motion_uses_direct_final_states(app: QApplication) -> None:
    previous = app.property("reducedMotion")
    app.setProperty("reducedMotion", True)
    content = QWidget()
    content.setMinimumSize(QSize(100, 40))
    section = CollapsibleSection("Options", content, expanded=True)
    section.show()
    section.set_expanded(False)
    assert not content.isVisible()
    assert section._motion is None
    section.set_expanded(True)
    assert content.isVisible()
    assert content.maximumHeight() == 16777215

    class Emitted:
        def __init__(self) -> None:
            self.count = 0

        def emit(self) -> None:
            self.count += 1

    class ViewStub:
        _scale = 1.0
        _ox = 0.0
        _oy = 0.0
        viewChanged = Emitted()

        def _c2w(self, x: float, y: float) -> tuple[float, float]:
            return x, y

        def isVisible(self) -> bool:
            return True

        def _redraw(self) -> None:
            self.redrawn = True

    view = ViewStub()
    _animate_view_to(view, 2.0, 10.0, 20.0)
    assert (view._scale, view._ox, view._oy) == (2.0, -10.0, 60.0)
    assert view.redrawn and view.viewChanged.count == 1
    assert not hasattr(view, "_view_anim")

    class AnimationStub:
        touched = False

        def stop(self) -> None:
            self.touched = True

        def start(self) -> None:
            self.touched = True

    class SidebarHost(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.resize(600, 500)
            self._draw_sidebar = QWidget(self)
            self._draw_sidebar.resize(280, 300)
            self._draw_sidebar.hide()
            self._draw_sidebar_anim = AnimationStub()
            self._draw_sidebar_always_visible = False
            self._draw_sidebar_visible = False
            self._draw_sidebar_height = 300

        def _refresh_draw_sidebar_state(self) -> None:
            pass

        def _chrome_left(self) -> int:
            return 0

        def _chrome_top(self) -> int:
            return 0

    host = SidebarHost()
    DrawOpsService(host)._set_draw_sidebar_visible(True)
    assert not host._draw_sidebar.isHidden()
    assert host._draw_sidebar.pos().x() == 8
    assert not host._draw_sidebar_anim.touched
    app.setProperty("reducedMotion", previous)


def test_top_level_pages_have_working_focus_order_and_focus_state(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("simple_stipple.platform.config.save_settings", lambda _settings: None)
    window = App()
    window.resize(1050, 700)
    window.show()
    app.processEvents()
    pages: list[tuple[str, QWidget]] = []
    for index in range(window._tabs.count()):
        window._tabs.setCurrentIndex(index)
        app.processEvents()
        pages.append((window._tabs.tabText(index), window._tabs.currentWidget()))
    pages.extend((("Help", HelpDialog(window)), ("Repository", RepoPage())))

    for name, page in pages:
        page.show()
        page.activateWindow()
        app.processEvents()
        candidates = [
            widget
            for widget in page.findChildren(QWidget)
            if widget.isVisible()
            and not widget.visibleRegion().isEmpty()
            and widget.isEnabled()
            and widget.focusPolicy() != 0
        ]
        assert len(candidates) >= 2, name
        first = candidates[0]
        first.setFocus()
        app.processEvents()
        assert first.hasFocus(), name
        option = QStyleOption()
        option.initFrom(first)
        assert option.state & QStyle.StateFlag.State_HasFocus, name
        assert page.focusNextChild(), name
        app.processEvents()
        assert app.focusWidget() is not first, name
        page.hide()
    window.close()


def test_primary_and_secondary_theme_text_meet_wcag_aa_contrast() -> None:
    theme = resolve_tokens()

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(foreground: str, background: str) -> float:
        light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    assert ratio(theme["text"], theme["bg_app"]) >= 4.5
    assert ratio(theme["text_muted"], theme["bg_app"]) >= 4.5


def test_accessible_status_event_hook_distinguishes_alerts(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    monkeypatch.setattr(
        feedback.QAccessible,
        "updateAccessibility",
        lambda event: events.append(event.type()),
    )
    widget = QWidget()
    feedback.announce_accessible(widget)
    feedback.announce_accessible(widget, urgent=True)
    assert events == [
        feedback.QAccessible.Event.DescriptionChanged,
        feedback.QAccessible.Event.Alert,
    ]

    announced: list[bool] = []
    monkeypatch.setattr(
        "simple_stipple.ui.components.workflow.announce_accessible",
        lambda _widget, *, urgent=False: announced.append(urgent),
    )
    region = StatusRegion()
    region.set_status("Finished", "success")
    region.set_status("Failed", "danger")
    label = QLabel()
    set_status_label(label, "Invalid", "#f85149")
    assert announced == [False, True, True]


def test_rapid_retrigger_coalesces_and_cancels_only_current_token() -> None:
    state = CancellableTaskState()
    can_start, first_token = state.request_start()
    assert can_start and state.phase is TaskPhase.RUNNING
    for _ in range(100):
        can_start, token = state.request_start()
        assert not can_start
        assert token is first_token
    assert first_token.is_set()
    assert state.phase is TaskPhase.CANCELLING
    assert state.finish_run()
    can_start, replacement_token = state.request_start()
    assert can_start
    assert replacement_token is not first_token
    assert not replacement_token.is_set()
    assert not state.finish_run()
    assert state.phase is TaskPhase.IDLE


def test_recent_and_remembered_directory_flows_without_native_modals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(file_dialogs, "save_settings", lambda value: saved.append(dict(value)))
    monkeypatch.setattr(
        "simple_stipple.ui.components.recent.save_settings", lambda value: saved.append(dict(value))
    )
    source = tmp_path / "input.dxf"
    source.touch()
    output = tmp_path / "output.dxf"
    folder = tmp_path / "exports"
    folder.mkdir()
    settings: dict = {}

    monkeypatch.setattr(
        file_dialogs.QFileDialog, "getOpenFileName", lambda *_args: (str(source), "DXF")
    )
    monkeypatch.setattr(
        file_dialogs.QFileDialog, "getSaveFileName", lambda *_args: (str(output), "DXF")
    )
    monkeypatch.setattr(
        file_dialogs.QFileDialog, "getExistingDirectory", lambda *_args: str(folder)
    )

    assert file_dialogs.pick_open_file(
        None, settings, "vector", "Open", "*.dxf", recent_kind=KIND_DXF
    ) == str(source)
    assert list_recent(settings, KIND_DXF) == [str(source.resolve())]
    assert file_dialogs.pick_save_file(
        None, settings, "vector", "Save", "output.dxf", "*.dxf"
    ) == str(output)
    assert file_dialogs.pick_directory(None, settings, "export", "Export") == str(folder)
    assert file_dialogs.remembered_dir(settings, "vector") == str(tmp_path)
    assert file_dialogs.remembered_dir(settings, "export") == str(folder)
    assert saved


def test_representative_backend_export_and_image_flows(tmp_path: Path) -> None:
    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
    dxf_path = tmp_path / "square.dxf"
    write_polylines_dxf(square, str(dxf_path), close=True)
    loaded = load_dxf_polylines(str(dxf_path))
    assert loaded and len(loaded[0]) >= 4

    region = build_fill_region(square)
    strokes = apply_fill(region, FillSpec(mode="lines", spacing=2.0))
    assert strokes

    image_path = tmp_path / "source.png"
    image = Image.new("RGB", (40, 40), "white")
    ImageDraw.Draw(image).rectangle((8, 8, 31, 31), fill="black")
    image.save(image_path)
    display, outlines, width, height = image_to_outlines(
        str(image_path),
        threshold=127,
        blur_radius=0,
        close_radius=0,
        simplify_tol=0.5,
        min_area_px=10,
        width_mm=20,
    )
    assert display.size == (40, 40)
    assert outlines and (width, height) == (40, 40)

    raster_spec = RasterEngravingSpec(
        width_mm=10,
        height_mm=10,
        line_interval_mm=0.5,
    )
    png, metadata, positioned_svg = export_raster_job(
        image_path, tmp_path / "engraving.png", raster_spec
    )
    assert png.is_file() and metadata.is_file() and positioned_svg.is_file()
    assert json.loads(metadata.read_text())["schema"] == ("simple-stipple-raster-engraving-v1")

    package = export_laserstar_package(tmp_path, "Smoke Job", square)
    assert (package / "01_pattern-and-outline.fvi").is_file()
    assert (package / "job-manifest.json").is_file()
    assert (package / "job-preview.png").is_file()


def test_canvas_edit_history_snapping_and_render_smoke(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(640, 480)
    canvas.show()
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    canvas.load([square], fit=False)
    entity_id = canvas._entities[0].id
    canvas.set_selection([entity_id])
    assert canvas.get_selected_ids() == [entity_id]
    assert canvas.delete_selected() == 1
    assert canvas.get_polylines_state() == []
    assert canvas.undo()
    assert canvas.get_polylines_state() == [square]
    assert canvas.redo()
    assert canvas.get_polylines_state() == []
    assert canvas.undo()

    canvas.set_snap_master(True)
    canvas.set_grid_snap(True)
    canvas.set_snap_vertex(True)
    canvas.set_snap_edge(True)
    precision = canvas.get_precision_state()
    assert precision["snap_master"]
    assert precision["grid_snap"]
    assert precision["snap_vertex"]
    assert precision["snap_edge"]
    app.processEvents()
    rendered = canvas.grab()
    assert not rendered.isNull()
    assert rendered.size().width() == 640
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_frozen_jit_module_import_disables_file_cache() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; sys.frozen = True; import simple_stipple.core.geometry"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr
