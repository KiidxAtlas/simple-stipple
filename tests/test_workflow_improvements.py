"""User workflows retain job ownership, originals, and actionable results."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from simple_stipple.app.window import App
from simple_stipple.features.convert import tasks
from simple_stipple.features.convert.page import ConvertPage
from simple_stipple.features.pattern import export as export_module
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.trace import page as trace_module
from simple_stipple.features.trace.page import TracePage
from simple_stipple.ui.style import _render_app_qss, load_app_qss


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_widgets(app, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("simple_stipple.platform.settings.save_settings", lambda _s: None)
    monkeypatch.setattr("simple_stipple.features.convert.page.save_settings", lambda _s: None)
    existing = set(app.topLevelWidgets())
    yield
    for widget in set(app.topLevelWidgets()) - existing:
        shutdown = getattr(widget, "shutdown", None)
        if shutdown:
            shutdown()
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_unchanged_theme_does_not_restyle_application():
    # Global Qt styles also touch closed widgets retained by earlier tests.
    # Keep the real setters and assertions, but give them a fresh application.
    script = (
        "import runpy, pytest\n"
        f"checks = runpy.run_path({str(Path(__file__).resolve())!r})\n"
        "app = checks['QApplication']([])\n"
        "with pytest.MonkeyPatch.context() as monkeypatch:\n"
        "    checks['_assert_theme_updates'](app, monkeypatch)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_theme_updates(app, monkeypatch):
    palette, font, qss = app.palette(), app.font(), app.styleSheet()
    properties = {
        bytes(key).decode(): app.property(bytes(key).decode()) for key in app.dynamicPropertyNames()
    }
    settings = {"appearance": "dark", "ui_scale": 1.0}
    host = SimpleNamespace(_settings=settings)
    try:
        App._apply_accessibility_settings(host)
        style_setter = Mock(wraps=app.setStyleSheet)
        palette_setter = Mock(wraps=app.setPalette)
        font_setter = Mock(wraps=app.setFont)
        monkeypatch.setattr(app, "setStyleSheet", style_setter)
        monkeypatch.setattr(app, "setPalette", palette_setter)
        monkeypatch.setattr(app, "setFont", font_setter)
        App._apply_accessibility_settings(host)
        assert [style_setter.call_count, palette_setter.call_count, font_setter.call_count] == [
            0,
            0,
            0,
        ]
        settings["reduced_motion"] = True
        App._apply_accessibility_settings(host)
        assert style_setter.call_count == 0
        settings["appearance"] = "light"
        App._apply_accessibility_settings(host)
        assert style_setter.call_count == 1
        assert palette_setter.call_count == 1
        settings["ui_scale"] = 1.25
        App._apply_accessibility_settings(host)
        assert style_setter.call_count == 2
        assert font_setter.call_count == 1
        before = _render_app_qss.cache_info().hits
        load_app_qss()
        load_app_qss()
        assert _render_app_qss.cache_info().hits > before
    finally:
        app.setPalette(palette)
        app.setFont(font)
        app.setStyleSheet(qss)
        for key in app.dynamicPropertyNames():
            name = bytes(key).decode()
            app.setProperty(name, properties.get(name))


def test_old_trace_progress_cannot_overwrite_a_restarted_or_finished_job(app, monkeypatch):
    captured = []

    def thread(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(start=lambda: None, is_alive=lambda: False)

    monkeypatch.setattr(trace_module.threading, "Thread", thread)
    page = TracePage(settings={})
    page._img_path = "source.png"
    page._width_mm.setText("100")
    page._start_trace_thread()
    old_progress = captured[-1]["args"][1]["on_progress"]
    page._force_reload_trace()
    current_progress = captured[-1]["args"][1]["on_progress"]
    current_progress(40, "Current trace")
    assert page._progress.value() == 40
    old_progress(90, "Old trace")
    assert page._progress.value() == 40
    page._running = False
    current_progress(99, "Late trace")
    assert page._progress.value() == 40
    page._running = True
    page._shutting_down = True
    current_progress(100, "Closed trace")
    assert page._progress.value() == 40


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["prepare", "publish"])
def test_export_failure_preserves_the_previous_complete_set(
    tmp_path, monkeypatch, existing, failure
):
    target = tmp_path / "part.dxf"
    sidecar = tmp_path / "part-engraving.png"
    if existing:
        target.write_text("old vector")
        sidecar.write_text("old raster")

    def prepare(output_path, *_args, **_kwargs):
        staged = Path(output_path)
        staged.write_text("new vector")
        if failure == "prepare":
            raise OSError("raster failed")
        raster = staged.with_name(sidecar.name)
        raster.write_text("new raster")
        return [staged, raster]

    monkeypatch.setattr(export_module, "_write_document_files", prepare)
    replace = export_module.os.replace

    def publish(source, destination):
        if Path(destination) == sidecar:
            raise OSError("destination unavailable")
        return replace(source, destination)

    monkeypatch.setattr(export_module.os, "replace", publish)
    with pytest.raises(OSError):
        export_module.export_document_file(str(target), "dxf", [])
    if existing:
        assert target.read_text() == "old vector"
        assert sidecar.read_text() == "old raster"
    else:
        assert not target.exists() and not sidecar.exists()
    assert not list(tmp_path.glob(".part-export-*"))


def test_geometry_check_lists_selectable_findings_without_changing_paths(app):
    page = PatternPage(settings={})
    line = [[(0.0, 0.0), (20.0, 10.0)]]
    page.load_outline_polys(line)
    assert page._geometry_findings.isHidden()
    assert page._canvas._issue_markers == ()
    page._check_geometry_btn.click()
    assert page._geometry_findings.count() == 2
    assert page._canvas.get_view_state()["geometry_health_visible"]
    page._geometry_findings.setCurrentRow(1)
    assert page._canvas.get_selected_ids() == page._outline_ids
    page._geometry_markers_cb.setChecked(False)
    assert not page._canvas.get_view_state()["geometry_health_visible"]
    assert page._canvas.get_polylines_state() == line
    page.load_outline_polys([[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]])
    page._refresh_preflight_markers()
    assert page._geometry_findings.count() == 1
    assert page._geometry_findings.item(0).text() == "No geometry findings"


@pytest.mark.parametrize(
    "tab_class, method_name",
    [
        (tasks.FviSubTab, "convert_fvi_to_dxf"),
        (tasks.SvgSubTab, "dxf_to_svg"),
        (tasks.SvgToDxfSubTab, "svg_to_dxf"),
    ],
)
def test_batch_results_retry_only_failed_files_with_original_destinations(
    app, monkeypatch, tmp_path, tab_class, method_name
):
    source = tmp_path / "input"
    source.mkdir()
    suffix = {tasks.FviSubTab: ".fvi", tasks.SvgSubTab: ".dxf", tasks.SvgToDxfSubTab: ".svg"}[
        tab_class
    ]
    for name in ("good", "bad"):
        (source / (name + suffix)).write_text("input")
    output = tmp_path / "output"
    calls = []
    fail = True

    def convert(src, dst):
        calls.append((Path(src), Path(dst)))
        if Path(src).stem == "bad" and fail:
            raise ValueError("invalid source")
        Path(dst).write_text("converted")
        return {"polylines": 1, "width_mm": 10, "height_mm": 10}

    monkeypatch.setattr(tasks.DxfService, method_name, convert)
    monkeypatch.setattr(tasks.DxfService, "summarize_fvi_import", lambda _report: "")
    tab = tab_class(settings={})
    event = tab._start_job()
    worker = tab._convert if tab_class is tasks.FviSubTab else tab._convert_batch
    worker(str(source), str(output), event)
    app.processEvents()
    assert sorted(result.status for result in tab._results.values()) == ["Done", "Failed"]
    original_failed_output = next(
        result.output for result in tab._results.values() if result.status == "Failed"
    )
    tab._out_edit.setText(str(tmp_path / "edited-output"))
    fail = False
    tab.retry_failed()
    tab._thread.join(timeout=5)
    assert not tab._thread.is_alive()
    app.processEvents()
    assert [path.stem for path, _out in calls].count("good") == 1
    assert [path.stem for path, _out in calls].count("bad") == 2
    assert calls[-1][1] == original_failed_output
    assert all(result.status == "Done" for result in tab._results.values())


def test_conversion_results_are_owned_by_the_active_tool(app, monkeypatch, tmp_path):
    page = ConvertPage(settings={})
    first, second = page._fvi_subtab, page._svg_subtab
    first._start_job()
    output = tmp_path / "done.dxf"
    first._execute_conversion(tmp_path / "in.fvi", output, lambda: output.write_text("done"))
    first._running = False
    page._refresh_file_results()
    assert page._file_results.count() == 1
    page._file_results.setCurrentRow(0)
    assert page._open_result_btn.isEnabled()
    opened = []
    monkeypatch.setattr(
        "simple_stipple.features.convert.page.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )
    page._open_result_btn.click()
    assert opened[0].toLocalFile() == str(output)
    page._on_tool_changed(2)
    assert page._tool_stack.currentWidget() is second
    assert page._file_results.count() == 0
    assert not page._open_result_btn.isEnabled()


def test_repair_retry_keeps_original_mode_and_can_be_cancelled(app, monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a", "b"):
        (source / f"{name}.dxf").write_text("original")
    modes = []
    should_fail = True

    def fix(src, out, *, mode):
        modes.append(mode)
        if should_fail:
            raise ValueError("repair failed")
        Path(out).write_text("repaired")
        return {
            "polylines_in": 1,
            "polylines_out": 1,
            "closed": 0,
            "simplified": 0,
            "discarded": 0,
            "changed": False,
        }

    monkeypatch.setattr(tasks, "fix_dxf", fix)
    tab = tasks.FixerSubTab(settings={})
    event = tab._start_job()
    tab._fix_batch(str(source), str(tmp_path / "out"), event, repair_mode="flatten")
    failed = list(tab._results.values())
    assert len(failed) == 2 and all(item.status == "Failed" for item in failed)
    should_fail = False
    tab._repair_mode.setCurrentIndex(0)
    event = tab._start_job(preserve_results=True)
    event.set()
    tab._retry_results(failed, event)
    assert modes == ["flatten", "flatten"]
    assert not tab._running
    event = tab._start_job(preserve_results=True)
    tab._retry_results(failed, event)
    assert modes == ["flatten"] * 4
    assert all(item.status == "Done" for item in tab._results.values())


def test_rollback_failure_keeps_recoverable_originals(tmp_path, monkeypatch):
    target = tmp_path / "part.dxf"
    target.write_text("old vector")

    def prepare(output_path, *_args, **_kwargs):
        staged = Path(output_path)
        staged.write_text("new vector")
        sidecar = staged.with_suffix(".png")
        sidecar.write_text("new raster")
        return [staged, sidecar]

    monkeypatch.setattr(export_module, "_write_document_files", prepare)
    replace = export_module.os.replace

    def broken_replace(source, destination):
        if Path(destination).suffix == ".png" or Path(source).parent.name == "originals":
            raise OSError("disk unavailable")
        return replace(source, destination)

    monkeypatch.setattr(export_module.os, "replace", broken_replace)
    with pytest.raises(OSError, match="Original files are preserved"):
        export_module.export_document_file(str(target), "dxf", [])
    backups = list(tmp_path.glob(".part-export-*/originals/part.dxf"))
    assert len(backups) == 1 and backups[0].read_text() == "old vector"
