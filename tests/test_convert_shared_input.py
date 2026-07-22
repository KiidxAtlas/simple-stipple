from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.convert import ConvertPage, FviSubTab


@pytest.fixture()
def convert_page(qapp):
    page = ConvertPage(None, {})
    yield page
    page.shutdown()
    page.deleteLater()
    qapp.processEvents()


def test_shared_input_tracks_each_conversion_task(convert_page, tmp_path):
    page = convert_page
    fvi = tmp_path / "job.fvi"
    fvi.write_text("", encoding="utf-8")
    svg = tmp_path / "shape.svg"
    svg.write_text("<svg/>", encoding="utf-8")

    page._set_shared_source(str(fvi))
    assert page._fvi_subtab._src_edit.text() == str(fvi)

    page._on_tool_changed(3)
    assert page._shared_input_edit.text() == ""
    assert page._shared_input_hint.text() == "SVG file or folder"
    page._set_shared_source(str(svg))
    assert page._svg_dxf_subtab._src_edit.text() == str(svg)

    page._on_tool_changed(0)
    assert page._shared_input_edit.text() == str(fvi)


@pytest.mark.parametrize(("suffix", "index"), [(".fvi", 0), (".dxf", 1), (".svg", 3)])
def test_external_repair_input_selects_matching_task(convert_page, tmp_path, suffix, index):
    source = tmp_path / f"broken{suffix}"
    source.write_text("", encoding="utf-8")

    convert_page.open_repair_input(str(source))

    assert convert_page._tool_stack.currentIndex() == index
    assert convert_page._shared_input_edit.text() == str(source)


def test_inline_result_actions_handoff_preview_geometry(convert_page):
    page = convert_page
    square = [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 0.0)]
    draft: list = []
    pattern: list = []
    page.openInDraftRequested.connect(draft.append)
    page.openInPatternRequested.connect(pattern.append)
    page._preview_canvas.set_polylines_state([square])
    page._refresh_preview_ui()

    assert page._open_draft_btn.isEnabled()
    assert page._open_pattern_btn.isEnabled()
    page._open_preview_in_draft()
    page._open_preview_in_pattern()

    assert draft == [[square]]
    assert pattern == [[square]]


def test_pattern_handoff_excludes_open_conversion_paths(convert_page):
    page = convert_page
    open_path = [(0.0, 0.0), (3.0, 0.0)]
    page._preview_canvas.set_polylines_state([open_path])
    page._refresh_preview_ui()

    assert not page._open_pattern_btn.isEnabled()


def test_batch_progress_reports_phase_count_file_and_elapsed(qapp, monkeypatch):
    tab = FviSubTab(settings={})
    statuses: list[tuple[str, str]] = []
    tab._status_sig.connect(lambda text, tone: statuses.append((text, tone)))
    times = iter((100.0, 125.0))
    monkeypatch.setattr("src.ui.pages.convert.time.monotonic", lambda: next(times))
    tab._start_job()
    tab._begin_batch(8)
    tab._report_batch_progress(3, "Converting", "part.fvi")

    assert statuses[-1][0] == "Converting 3/8 — part.fvi · elapsed 0:25"
    tab.cancel()


def test_batch_cancellation_reports_retained_partial_outputs(qapp, monkeypatch):
    tab = FviSubTab(settings={})
    statuses: list[str] = []
    logs: list[str] = []
    tab._status_sig.connect(lambda text, _tone: statuses.append(text))
    tab.log_line.connect(logs.append)
    times = iter((100.0, 110.0))
    monkeypatch.setattr("src.ui.pages.convert.time.monotonic", lambda: next(times))
    tab._start_job()
    tab._begin_batch(5)
    tab._record_batch_item(2)

    tab._finish_cancelled()

    assert statuses[-1] == "Cancelled — 2/5 completed output(s) retained · elapsed 0:10"
    assert "2/5 completed output(s) retained" in logs[-1]
