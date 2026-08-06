"""Phase 6 — one output, continuous validation.

The document produces operations, not an export "kind". Preflight runs while
the design is being made and is drawn on the part, not summarised at export.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.engine.cad.preflight import GeometryIssue
from simple_stipple.features.pattern.output import density_issues, document_operations
from simple_stipple.features.pattern.page import PatternPage

OUTER = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
CIRCLE = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_the_reference_scenario_is_one_job_of_three_operations(app: QApplication) -> None:
    """Honeycomb in the ring, a logo engraved in the circle, an outer cut."""
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    ring_id, circle_id = page._outline_ids
    page._treatments[ring_id] = {
        "kind": "pattern",
        "pattern": "Honeycomb",
        "pattern_label": "Honeycomb",
        "params": {},
    }
    page._treatments[circle_id] = {
        "kind": "engrave",
        "pattern": "Image",
        "params": {},
        "engraving": {"path": "/tmp/logo.png", "x": 0, "y": 0, "width": 10, "height": 10},
    }

    operations = document_operations(page)
    assert [op.kind for op in operations] == ["engrave", "mark", "cut"]
    assert "logo.png" in operations[0].subject
    assert "Honeycomb" == operations[1].subject
    page.shutdown()
    page.close()


def test_run_order_is_engrave_then_mark_then_cut(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    outer_id, circle_id = page._outline_ids
    page._treatments[outer_id] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._treatments[circle_id] = {
        "kind": "engrave",
        "pattern": "Image",
        "params": {},
        "engraving": {"path": "logo.png"},
    }
    assert [op.kind for op in document_operations(page)] == ["engrave", "cut"]
    page.shutdown()
    page.close()


def test_rows_reorder_and_switch_off_without_touching_the_treatment(
    app: QApplication,
) -> None:
    from PySide6.QtCore import Qt

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    for region_id in page._outline_ids:
        page._treatments[region_id] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._refresh_output_panel()
    assert page._output_list.count() == 2

    before = dict(page._treatments)
    page._output_list.setCurrentRow(1)
    page._move_output_row(-1)
    assert page._treatments == before

    page._output_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert len(page._enabled_operations()) == 1
    assert page._treatments == before
    page.shutdown()
    page.close()


def test_preflight_marks_the_canvas_while_drawing_not_at_export(
    app: QApplication,
) -> None:
    """An open path that should be closed is a marker on the part, now."""
    page = PatternPage(settings={})
    page.load_outline_polys([[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]])
    page._refresh_preflight_markers()

    markers = page._canvas._issue_markers
    assert markers, "an unclosed path produced no finding"
    assert all(hasattr(m, "point") and hasattr(m, "severity") for m in markers)
    assert "finding" in page._output_preflight.text()

    # Clicking one selects the path it belongs to.
    assert page._on_issue_marker_clicked(markers[0]) is True
    assert page._canvas.get_selected_ids() == [page._outline_ids[0]]
    page.shutdown()
    page.close()


def test_a_clean_document_reports_no_findings(app: QApplication) -> None:
    page = PatternPage(settings={})
    page.load_outline_polys([OUTER])
    page._refresh_preflight_markers()
    assert page._canvas._issue_markers == ()
    assert "no findings" in page._output_preflight.text()
    page.shutdown()
    page.close()


def _export_and_wait(page, done, *, timeout_ms: int = 8000) -> None:
    """Click Export and pump until the write lands.

    Export always solves at full quality first, so the file arrives once that
    solve completes rather than on the click.
    """
    from PySide6.QtTest import QTest

    page._export_document_job()
    while not done() and timeout_ms > 0:
        QTest.qWait(50)
        timeout_ms -= 50


@pytest.mark.parametrize("export_format,suffix", [("dxf", ".dxf"), ("svg", ".svg"), ("fvi", ".fvi")])
def test_every_single_file_format_exports(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    export_format: str,
    suffix: str,
) -> None:
    """Clicking Export must run end to end in every format on the menu.

    The first cut of the one-Export rewrite removed the helper that waits for
    the solve but left the call, so the primary action raised AttributeError
    on click — and nothing exercised the button, so nothing caught it.
    """
    from simple_stipple.features.pattern import page as page_module

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER])
    page._treatments[page._outline_ids[0]] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._select_export_format(export_format)
    assert page._gen_btn.text() == f"Export {export_format.upper()}"

    target = tmp_path / f"part{suffix}"
    monkeypatch.setattr(page_module, "pick_save_file", lambda *a, **k: str(target))

    _export_and_wait(page, target.exists)

    assert target.exists(), f"{export_format} export wrote nothing"
    assert target.stat().st_size > 0
    page.shutdown()
    page.close()


def test_svg_export_embeds_the_engraving_image(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The single-file answer: outline and image in one SVG."""
    from PIL import Image

    from simple_stipple.features.pattern import page as page_module

    logo = tmp_path / "logo.png"
    Image.new("L", (16, 16), 128).save(logo)

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    ring_id, circle_id = page._outline_ids
    page._treatments[ring_id] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._zone_list.setCurrentRow(1)
    page._engraving_image_path = str(logo)
    page._engrave_x.setValue(30.0)
    page._engrave_y.setValue(30.0)
    page._engrave_w.setValue(40.0)
    page._engrave_h.setValue(40.0)
    page._attach_image_to_selected_region(str(logo))
    assert any(op.kind == "engrave" for op in page._enabled_operations())

    page._select_export_format("svg")
    target = tmp_path / "part.svg"
    monkeypatch.setattr(page_module, "pick_save_file", lambda *a, **k: str(target))

    _export_and_wait(page, target.exists)

    markup = target.read_text()
    assert "<image" in markup, "the image is not in the SVG"
    assert "data:image/png;base64," in markup, "the image is not embedded"
    assert "<path" in markup, "the outlines are not in the SVG"
    page.shutdown()
    page.close()


def test_dxf_export_writes_the_image_as_a_sidecar(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """DXF has nowhere to put a raster, so it lands beside the file."""
    from PIL import Image

    from simple_stipple.features.pattern import page as page_module

    logo = tmp_path / "logo.png"
    Image.new("L", (16, 16), 128).save(logo)

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER, CIRCLE])
    page._treatments[page._outline_ids[0]] = {"kind": "cut", "pattern": "— None —", "params": {}}
    page._zone_list.setCurrentRow(1)
    page._engraving_image_path = str(logo)
    page._engrave_w.setValue(20.0)
    page._engrave_h.setValue(20.0)
    page._attach_image_to_selected_region(str(logo))

    page._select_export_format("dxf")
    target = tmp_path / "part.dxf"
    monkeypatch.setattr(page_module, "pick_save_file", lambda *a, **k: str(target))

    _export_and_wait(page, lambda: (tmp_path / "part-engraving.png").exists())

    assert target.exists()
    assert (tmp_path / "part-engraving.png").exists(), "no positioned raster beside the DXF"
    page.shutdown()
    page.close()


def test_an_image_on_the_part_is_never_dropped_from_the_export(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An image added with no region selected still reaches the file.

    It used to belong to no region, so it produced no operation and the export
    wrote a vectors-only file while the image sat visibly on the canvas —
    silent loss, with nothing on screen saying it had happened.
    """
    from PIL import Image

    from simple_stipple.features.pattern import page as page_module

    art = tmp_path / "mountains.png"
    Image.new("L", (64, 32), 90).save(art)

    page = PatternPage(settings={})
    page.load_outline_polys([OUTER])
    page._zone_list.setCurrentRow(-1)  # nothing selected
    page._engraving_image_path = str(art)
    page._engrave_w.setValue(40.0)
    page._engrave_h.setValue(25.0)
    page._attach_image_to_selected_region(str(art))

    engraves = [op for op in page._enabled_operations() if op.kind == "engrave"]
    assert engraves, "the image never showed up in Output"
    assert engraves[0].target == "whole outline"

    page._select_export_format("svg")
    target = tmp_path / "pattern.svg"
    monkeypatch.setattr(page_module, "pick_save_file", lambda *a, **k: str(target))
    _export_and_wait(page, target.exists)

    markup = target.read_text()
    assert markup.count("<image") == 1, "the image is missing from the export"
    assert "<path" in markup, "the outline is missing from the export"
    page.shutdown()
    page.close()


def test_the_format_menu_picks_a_format_and_never_starts_an_export(
    app: QApplication,
) -> None:
    """The dropdown chooses what the file is; Export is the only trigger."""
    from simple_stipple.features.pattern.export_jobs import EXPORT_FORMAT_KEYS

    page = PatternPage(settings={})
    calls: list[bool] = []
    page._perform_document_export = lambda: calls.append(True)

    assert set(page._export_actions) == set(EXPORT_FORMAT_KEYS)
    for key in EXPORT_FORMAT_KEYS:
        page._select_export_format(key)
        assert page._export_format == key
        assert page._export_actions[key].isChecked()
        assert sum(action.isChecked() for action in page._export_actions.values()) == 1
    assert calls == [], "changing format started an export"

    # An unknown format is ignored rather than left half-applied.
    page._select_export_format("nonsense")
    assert page._export_format == EXPORT_FORMAT_KEYS[-1]
    page.shutdown()
    page.close()


def test_export_with_nothing_to_do_explains_instead_of_raising(app: QApplication) -> None:
    page = PatternPage(settings={})
    page._export_document_job()  # no geometry, no operations
    assert "Nothing to export" in page._status.text()
    page.shutdown()
    page.close()


def test_density_below_the_machine_minimum_is_a_design_time_warning() -> None:
    jobs = [
        {"polys": [OUTER], "fill": {"mode": "lines", "spacing": 0.05}},
        {"polys": [CIRCLE], "fill": {"mode": "lines", "spacing": 1.0}},
    ]
    issues = density_issues(jobs, minimum_spacing_mm=0.2)
    assert len(issues) == 1
    assert isinstance(issues[0], GeometryIssue)
    assert issues[0].severity == "warning"
    assert issues[0].point == OUTER[0]
    # A zero minimum is the off switch, not a check that flags everything.
    assert density_issues(jobs, minimum_spacing_mm=0.0) == ()
