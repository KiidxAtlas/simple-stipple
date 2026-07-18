"""StarFX/FiberStar FVI parsing, generation, and export UI."""

from __future__ import annotations

import math

import ezdxf
import pytest

from src.backend.dxf.fvi import (
    FviExportOptions,
    FviNoGeometryError,
    convert_fvi_to_dxf,
    parse_fvi,
    read_fvi,
    render_fvi,
    write_fvi,
)


def test_representative_fvi_import_has_expected_geometry() -> None:
    document = parse_fvi(
        "; sample exported by StarFX\n"
        "MOVEDIST 10,20\n"
        "DRAWLINE 10,0\n"
        "DRAWARC 10,10,0,10\n"
        "MOVEDIST 5,5\n"
        "DRAWLINE 0,-5\n"
    )
    assert len(document.paths) == 2
    assert document.report.draw_line_count == 2
    assert document.report.draw_arc_count == 1
    assert not document.report.has_issues
    assert document.bounds is not None
    min_x, min_y, max_x, max_y = document.bounds
    assert min_x == pytest.approx(2.54, abs=1e-5)
    assert min_y == pytest.approx(5.08, abs=1e-5)
    assert max_x == pytest.approx(8.89, abs=1e-5)
    assert max_y == pytest.approx(8.89, abs=1e-5)


def test_parser_reports_hardware_and_malformed_commands() -> None:
    document = parse_fvi("MOVEDIST 1,2\nDRAWLINE 3,4\nBITOUT 1,1\nDRAWLINE nope,2\n")
    assert len(document.paths) == 1
    assert document.report.ignored_commands == ("BITOUT",)
    assert document.report.malformed_lines == (4,)


def test_export_round_trip_preserves_polyline_coordinates() -> None:
    records = [{"polyline": [(10.0, 20.0), (12.54, 20.0), (12.54, 22.54)]}]
    text, report = render_fvi(
        records,
        FviExportOptions(origin="preserve", optimize_travel=False, include_comments=False),
    )
    parsed = parse_fvi(text)
    assert report.draw_line_count == 2
    assert len(parsed.paths) == 1
    for actual, expected in zip(parsed.paths[0], records[0]["polyline"]):
        assert math.dist(actual, expected) < 1e-5


def test_export_lower_left_origin_and_margin() -> None:
    records = [{"polyline": [(-2.0, 7.0), (3.0, 9.0)]}]
    text, report = render_fvi(
        records,
        FviExportOptions(origin="lower_left", margin_mm=1.5, optimize_travel=False),
    )
    parsed = parse_fvi(text)
    assert parsed.paths[0][0] == pytest.approx((1.5, 1.5), abs=1e-5)
    assert report.bounds_mm == pytest.approx((1.5, 1.5, 6.5, 3.5), abs=1e-5)


def test_native_arc_is_emitted_and_importable() -> None:
    records = [
        {
            "kind": "arc",
            "polyline": [(10.0, 0.0), (7.071, 7.071), (0.0, 10.0)],
            "meta": {"center": (0.0, 0.0), "start_angle": 0.0, "end_angle": 90.0},
        }
    ]
    text, report = render_fvi(
        records,
        FviExportOptions(origin="preserve", optimize_travel=False, include_comments=False),
    )
    assert "DRAWARC" in text
    assert report.draw_arc_count == 1
    parsed = parse_fvi(text)
    assert math.dist(parsed.paths[0][0], (10.0, 0.0)) < 1e-5
    assert math.dist(parsed.paths[0][-1], (0.0, 10.0)) < 1e-5


def test_invalid_fvi_arc_is_reported_instead_of_warped() -> None:
    document = parse_fvi("MOVEDIST 0,0\nDRAWARC 10,0,0,2\n")
    assert document.paths == ()
    assert document.report.malformed_lines == (2,)
    assert document.report.draw_arc_count == 0


def test_export_rejects_precision_that_collapses_segments() -> None:
    records = [{"polyline": [(0.0, 0.0), (0.1016, 0.0)]}]
    with pytest.raises(ValueError, match="precision is too low"):
        render_fvi(
            records,
            FviExportOptions(
                origin="preserve", precision=0, optimize_travel=False, include_comments=False
            ),
        )


def test_reversed_native_curve_is_reported_as_tessellated() -> None:
    records = [{
        "kind": "arc",
        "polyline": [(10.0, 0.0), (5.0, 5.0), (0.0, 0.0)],
        "meta": {"center": (5.0, 0.0)},
    }]
    _text, report = render_fvi(records, FviExportOptions(origin="preserve"))
    assert any("reversed" in warning for warning in report.warnings)


def test_write_fvi_is_atomic_and_readable(tmp_path) -> None:
    destination = tmp_path / "job.fvi"
    report = write_fvi([{"polyline": [(0.0, 0.0), (2.54, 0.0)]}], destination)
    assert destination.is_file()
    assert report.path_count == 1
    assert read_fvi(destination).report.draw_line_count == 1


def test_empty_fvi_has_a_specific_non_geometry_error(tmp_path) -> None:
    source = tmp_path / "empty.fvi"
    source.write_bytes(b"")
    with pytest.raises(FviNoGeometryError, match="empty"):
        convert_fvi_to_dxf(source, tmp_path / "output.dxf")


def test_fvi_to_dxf_joins_segments_and_marks_closed_paths(tmp_path) -> None:
    source = tmp_path / "closed.fvi"
    output = tmp_path / "closed.dxf"
    source.write_text(
        "MOVEDIST 0,0\n"
        "DRAWLINE 10,0\n"
        "DRAWLINE 0,10\n"
        "DRAWLINE -10.001,-10\n"
        "MOVEDIST 20,0\n"
        "DRAWLINE 5,0\n"
    )

    report = convert_fvi_to_dxf(source, output)
    entities = list(ezdxf.readfile(output).modelspace())

    assert report.path_count == 2
    assert [entity.dxftype() for entity in entities] == ["LWPOLYLINE", "LWPOLYLINE"]
    assert entities[0].closed is True
    assert len(entities[0]) == 3
    assert entities[1].closed is False
    assert len(entities[1]) == 2


def test_export_dialog_exposes_current_options(qapp) -> None:
    from src.ui.widgets.dialogs.fvi_dialog import FviExportDialog

    dialog = FviExportDialog([{"polyline": [(0.0, 0.0), (1.0, 1.0)]}])
    try:
        assert dialog.options().origin == "lower_left"
        assert "1 paths" in dialog._summary.text()
        dialog._flip_y.setChecked(True)
        assert dialog.options().flip_y
    finally:
        dialog.close()


def test_draft_uses_one_vector_import_entry_point(qapp) -> None:
    from PySide6.QtWidgets import QToolButton

    from src.ui.pages.draft import DraftPage

    page = DraftPage(settings={})
    try:
        buttons = page.findChildren(QToolButton)
        button = next(widget for widget in buttons if widget.text() == "Import Vector")
        assert button.menu() is not None
        assert [action.text() for action in button.menu().actions()] == [
            "Import vector into drawing (add)…"
        ]
    finally:
        page.close()


def test_svg_can_replace_or_append_through_vector_import(qapp, tmp_path) -> None:
    from src.ui.pages.draft import DraftPage

    first = tmp_path / "first.svg"
    first.write_text(
        '<svg viewBox="0 0 20 20"><rect x="1" y="2" width="4" height="5"/></svg>',
        encoding="utf-8",
    )
    second = tmp_path / "second.svg"
    second.write_text(
        '<svg viewBox="0 0 20 20"><line x1="10" y1="10" x2="15" y2="10"/></svg>',
        encoding="utf-8",
    )
    page = DraftPage(settings={})
    try:
        page._load_vector(str(first))
        assert page._canvas.poly_count == 1
        page._load_vector(str(second), append=True)
        assert page._canvas.poly_count == 2
    finally:
        page.close()
