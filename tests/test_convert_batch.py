"""Batch behavior for Convert > Fix DXF."""

from __future__ import annotations

import threading

import ezdxf
import pytest

from src.backend.dxf.fix import fix_dxf
from src.backend.dxf.svg_dxf import svg_to_dxf
from src.ui.pages.convert import ConvertPage, FixerSubTab, FviSubTab


def _write_dxf(path, points) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_lwpolyline(points)
    doc.saveas(path)


def test_safe_fix_preserves_lwpolyline_bulges_and_vertex_widths(tmp_path):
    source = tmp_path / "curved.dxf"
    output = tmp_path / "fixed.dxf"
    doc = ezdxf.new("R2010")
    entity = doc.modelspace().add_lwpolyline(
        [
            (0, 0, 1, 2, 0.75),
            (10, 0, 3, 4, 0),
            (10, 10, 5, 6, 0),
            (0.005, 0.005, 7, 8, 0),
        ],
        format="xyseb",
    )
    entity.dxf.layer = "CUT"
    doc.saveas(source)

    before = source.read_bytes()
    stats = fix_dxf(source, output, mode="safe")

    assert output.read_bytes() == before
    assert stats["changed"] is False
    assert stats["protected_polylines"] == 1
    repaired = ezdxf.readfile(output).modelspace()[0]
    assert list(repaired.get_points(format="xyseb")) == list(
        entity.get_points(format="xyseb")
    )
    assert repaired.dxf.layer == "CUT"
    assert repaired.closed is False


def test_safe_fix_still_repairs_plain_near_open_polyline(tmp_path):
    source = tmp_path / "plain.dxf"
    output = tmp_path / "fixed.dxf"
    _write_dxf(source, [(0, 0), (10, 0), (10, 10), (0.005, 0.005)])

    stats = fix_dxf(source, output, mode="safe")

    repaired = ezdxf.readfile(output).modelspace()[0]
    assert stats["changed"] is True
    assert stats["closed"] == 1
    assert stats["protected_polylines"] == 0
    assert repaired.closed is True


def test_safe_fix_uses_dxf_units_for_closing_tolerance(tmp_path):
    source = tmp_path / "inches.dxf"
    output = tmp_path / "fixed.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0.005, 0.005)])
    doc.saveas(source)

    stats = fix_dxf(source, output, mode="safe")

    assert stats["closed"] == 0
    assert ezdxf.readfile(output).modelspace()[0].closed is False


def test_safe_fix_does_not_simplify_intentional_small_deviations(tmp_path):
    source = tmp_path / "detail.dxf"
    output = tmp_path / "fixed.dxf"
    points = [(0, 0), (5, 0.0005), (10, 0)]
    _write_dxf(source, points)

    stats = fix_dxf(source, output, mode="safe")

    repaired = ezdxf.readfile(output).modelspace()[0]
    assert stats["simplified"] == 0
    assert [(float(p[0]), float(p[1])) for p in repaired.get_points()] == points


def test_folder_fix_discovers_dxf_case_insensitively_and_repairs_each(qapp, tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "fixed"
    source.mkdir()
    output.mkdir()
    _write_dxf(source / "a.dxf", [(0, 0), (10, 0), (10, 10)])
    _write_dxf(source / "B.DXF", [(0, 0), (5, 0), (5, 5)])
    (source / "notes.txt").write_text("not a drawing")

    tab = FixerSubTab(settings={})
    try:
        log_messages: list[str] = []
        status_messages: list[str] = []
        tab.log_line.connect(log_messages.append)
        tab._status_sig.connect(lambda text, _tone: status_messages.append(text))
        discovered = tab._folder_dxf_files(str(source))
        assert [path.name for path in discovered] == ["a.dxf", "B.DXF"]

        tab._running = True
        tab._fix_batch(str(source), str(output), threading.Event())

        assert (output / "a.dxf").is_file()
        assert (output / "B.DXF").is_file()
        assert not (output / "notes.txt").exists()
        assert not tab._running
        summary = "\n".join(log_messages)
        assert "BATCH SUMMARY" in summary
        assert "Files scanned: 2" in summary
        assert "Files written: 2" in summary
        assert "Files with geometry repairs: 0" in summary
        assert "Files already clean: 2" in summary
        assert "Polylines: 2 input → 2 output" in summary
        assert "Near-open paths closed:" in summary
        assert "Native entities flattened:" in summary
        assert status_messages[-1].startswith("Done — 0 with repairs · 2 already clean")
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_svg_curves_are_reported_instead_of_silently_misread_as_lines(tmp_path):
    source = tmp_path / "curves.svg"
    output = tmp_path / "curves.dxf"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<path d="M 0 0 C 5 10 15 10 20 0"/>'
        '<path d="M 0 5 L 20 5"/>'
        "</svg>"
    )

    stats = svg_to_dxf(source, output)

    assert stats["unsupported_paths"] == 1
    assert stats["polylines"] == 1


def test_svg_nested_transform_and_physical_size_are_applied(tmp_path):
    source = tmp_path / "transformed.svg"
    output = tmp_path / "converted.dxf"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="96px" height="96px" '
        'viewBox="0 0 96 96"><g transform="translate(10 20)">'
        '<line x1="0" y1="0" x2="10" y2="0"/></g></svg>'
    )

    svg_to_dxf(source, output)

    line = ezdxf.readfile(output).modelspace()[0]
    assert float(line.dxf.start.x) == pytest.approx(10 * 25.4 / 96)
    assert float(line.dxf.start.y) == pytest.approx(76 * 25.4 / 96)
    assert float(line.dxf.end.x) == pytest.approx(20 * 25.4 / 96)


def test_malformed_svg_path_is_a_clean_value_error(tmp_path):
    source = tmp_path / "bad.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M 0 0 H"/></svg>')
    with pytest.raises(ValueError, match="H command"):
        svg_to_dxf(source, tmp_path / "bad.dxf")


def test_svg_does_not_import_definition_geometry_and_reports_use(tmp_path):
    source = tmp_path / "defs.svg"
    output = tmp_path / "defs.dxf"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
        '<defs><path id="shape" d="M 0 0 L 10 0"/></defs>'
        '<use href="#shape" x="5" y="5"/></svg>'
    )
    stats = svg_to_dxf(source, output)
    assert stats["polylines"] == 0
    assert stats["unsupported_features"] == ("use",)
    assert len(ezdxf.readfile(output).modelspace()) == 0


def test_folder_fix_recurses_and_preserves_relative_directories(qapp, tmp_path):
    source = tmp_path / "source"
    nested = source / "customer" / "job"
    output = tmp_path / "fixed"
    nested.mkdir(parents=True)
    _write_dxf(nested / "part.dxf", [(0, 0), (10, 0), (10, 10)])

    tab = FixerSubTab(settings={})
    try:
        assert tab._folder_dxf_files(str(source), recursive=False) == []
        assert tab._folder_dxf_files(str(source), recursive=True) == [nested / "part.dxf"]
        tab._running = True
        tab._fix_batch(str(source), str(output), threading.Event(), True)
        assert (output / "customer" / "job" / "part.dxf").is_file()
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_fvi_batch_recurses_and_preserves_relative_directories(qapp, tmp_path):
    source = tmp_path / "source"
    nested = source / "customer" / "job"
    output = tmp_path / "converted"
    nested.mkdir(parents=True)
    (nested / "part.FVI").write_text("MOVEDIST 0,0\nDRAWLINE 10,0\n")

    tab = FviSubTab(settings={})
    try:
        tab._running = True
        tab._convert(str(source), str(output), threading.Event(), False)
        assert not (output / "customer" / "job" / "part.dxf").exists()

        tab._running = True
        tab._convert(str(source), str(output), threading.Event(), True)
        assert (output / "customer" / "job" / "part.dxf").is_file()
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_fvi_batch_skips_empty_placeholder_files_without_errors(qapp, tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "converted"
    source.mkdir()
    (source / "empty.fvi").write_bytes(b"")
    (source / "part.fvi").write_text("MOVEDIST 0,0\nDRAWLINE 10,0\n")
    messages: list[str] = []
    statuses: list[str] = []
    tab = FviSubTab(settings={})
    try:
        tab.log_line.connect(messages.append)
        tab._status_sig.connect(lambda text, _tone: statuses.append(text))
        tab._running = True
        tab._convert(str(source), str(output), threading.Event())
        summary = "\n".join(messages)
        assert "empty.fvi: skipped — The FVI file is empty." in summary
        assert "1 skipped, 0 error(s)" in summary
        assert statuses[-1] == "Done — 1 converted, 1 skipped"
        assert (output / "part.dxf").is_file()
        assert not (output / "empty.dxf").exists()
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_fvi_declined_overwrite_does_not_leave_job_running(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    source = tmp_path / "part.fvi"
    source.write_text("MOVEDIST 0,0\nDRAWLINE 10,0\n")
    source.with_suffix(".dxf").write_text("existing")
    tab = FviSubTab(settings={})
    try:
        tab._src_edit.setText(str(source))
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
        )

        tab._run()

        assert not tab._running
        assert tab._btn.isEnabled()
        assert tab._thread is None
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_batch_log_reveals_results_panel_without_preview(qapp):
    page = ConvertPage(settings={})
    try:
        assert page._right_stack.currentIndex() == 0
        page._fix_subtab.log_line.emit("BATCH SUMMARY\nFiles repaired: 2")
        assert page._right_stack.currentIndex() == 1
        assert "BATCH SUMMARY" in page._log.toPlainText()
    finally:
        page.deleteLater()
        qapp.processEvents()
