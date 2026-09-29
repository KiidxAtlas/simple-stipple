"""Regression coverage for Convert's selected-result preview and handoff state."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ezdxf
import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.convert.tasks import ConversionResult


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_selected_results_keep_preview_and_handoff_in_sync(
    app: QApplication, tmp_path: Path
) -> None:
    failed_source = tmp_path / "failed.fvi"
    failed_source.touch()
    successful_source = tmp_path / "success.fvi"
    successful_source.touch()
    output = tmp_path / "success.dxf"
    document = ezdxf.new()
    document.modelspace().add_lwpolyline([(0, 0), (8, 0), (8, 5), (0, 5)], close=True)
    document.saveas(output)

    page = ConvertPage(settings={})
    page._fvi_subtab._results = {
        failed_source: ConversionResult(
            failed_source, tmp_path / "failed.dxf", "Failed", "decode error", lambda: None
        ),
        successful_source: ConversionResult(successful_source, output, "Done", "", lambda: None),
    }
    page._refresh_file_results()
    app.processEvents()

    assert page._right_stack.currentIndex() == 1
    assert page._file_results.currentRow() == 0
    assert "decode error" in page._result_detail.text()
    assert page._preview_canvas.poly_count == 0
    assert not page._open_draft_btn.isEnabled()
    assert not page._open_pattern_btn.isEnabled()
    page.show()
    app.processEvents()
    assert page._file_results.isVisible() and page._preview_canvas.isVisible()

    page._file_results.setCurrentRow(1)
    app.processEvents()
    assert str(output) in page._result_detail.text()
    assert page._preview_canvas.poly_count == 1
    assert page._open_result_btn.isEnabled()
    assert page._open_draft_btn.isEnabled()
    assert page._open_pattern_btn.isEnabled()

    page._file_results.setCurrentRow(0)
    app.processEvents()
    assert page._preview_canvas.poly_count == 0
    assert not page._open_pattern_btn.isEnabled()
