"""Round-trip Simple Stipple groups and dimensions through DXF and SVG.

DXF has no group entity, and DIMENSION geometry alone cannot recover the
measurement metadata, so both ride as app-private XDATA under the "SSTP"
appid. SVG paths carry a ``stipple:group`` attribute instead, which flows
through the SVG→DXF conversion into the same XDATA channel. Foreign files
(no SSTP XDATA) must import exactly as before.
"""

from __future__ import annotations

import json

import ezdxf
import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.core.formats.dxf import (
    load_dxf_polylines_by_layer_with_report,
    write_polylines_dxf,
)
from simple_stipple.core.formats.svg import svg_to_dxf, write_document_svg

SQUARE_A = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
SQUARE_B = [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0), (20.0, 0.0)]
LONER = [(40.0, 0.0), (50.0, 0.0)]

DIMENSION = {
    "type": "linear",
    "p1": (0.0, 0.0),
    "p2": (120.0, 0.0),
    "offset": 8.0,
    "precision": 3,
    "layer": "Notes",
    "driving": {"kind": "segment_length", "sources": [{"entity_id": "e-1", "kind": "segment"}]},
}


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dimension_metadata_round_trips_through_dxf(tmp_path) -> None:
    target = tmp_path / "dimension.dxf"
    write_polylines_dxf(
        [[(0.0, 0.0), (120.0, 0.0)]],
        str(target),
        entity_kinds=["dimension"],
        entity_meta=[DIMENSION],
    )

    _by_layer, report = load_dxf_polylines_by_layer_with_report(str(target))

    # JSON normalises tuples to lists; the restored dict must otherwise match
    # the original metadata exactly, including the driving reference.
    assert report.dimensions == [json.loads(json.dumps(DIMENSION))]
    assert "DIMENSION" not in report.unsupported_entities


def test_foreign_dimension_without_xdata_stays_unsupported(tmp_path) -> None:
    """A DIMENSION written by another tool carries no SSTP XDATA — it must
    keep landing in unsupported_entities instead of being guessed from
    defpoints."""
    target = tmp_path / "foreign.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], close=True)
    msp.add_linear_dim(
        base=(0.0, 5.0), p1=(0.0, 0.0), p2=(10.0, 0.0), dimstyle="EZDXF"
    ).render()
    doc.saveas(str(target))

    by_layer, report = load_dxf_polylines_by_layer_with_report(str(target))

    assert report.dimensions == []
    assert report.unsupported_entities.get("DIMENSION") == 1
    assert len(by_layer["0"]) == 1


def test_groups_and_labels_round_trip_through_dxf(tmp_path) -> None:
    target = tmp_path / "grouped.dxf"
    write_polylines_dxf(
        [SQUARE_A, SQUARE_B, LONER],
        str(target),
        entity_groups=[7, 7, None],
        group_labels={7: "flange"},
    )

    by_layer, report = load_dxf_polylines_by_layer_with_report(str(target))

    assert len(by_layer["0"]) == 3
    entries = [entry for entry in report.groups if entry["group"] == 7]
    assert {(entry["layer"], entry["index"]) for entry in entries} == {("0", 0), ("0", 1)}
    assert report.group_labels == {7: "flange"}


def test_plain_export_writes_no_xdata_and_empty_report_fields(tmp_path) -> None:
    target = tmp_path / "plain.dxf"
    write_polylines_dxf([SQUARE_A], str(target))

    assert "SSTP" not in target.read_text()

    _by_layer, report = load_dxf_polylines_by_layer_with_report(str(target))
    assert report.dimensions == []
    assert report.groups == []
    assert report.group_labels == {}


def test_svg_group_marker_flows_into_dxf_import(tmp_path) -> None:
    svg_path = tmp_path / "grouped.svg"
    write_document_svg([SQUARE_A, SQUARE_B, LONER], svg_path, entity_groups=[3, 3, None])
    assert svg_path.read_text().count('stipple:group="3"') == 2

    dxf_path = tmp_path / "grouped.dxf"
    svg_to_dxf(svg_path, dxf_path)

    _by_layer, report = load_dxf_polylines_by_layer_with_report(str(dxf_path))
    entries = [entry for entry in report.groups if entry["group"] == 3]
    assert len(entries) == 2
    assert all(entry["layer"] == "0" for entry in entries)


def test_draft_page_dxf_round_trip_restores_groups_labels_and_dimensions(
    tmp_path, app: QApplication, monkeypatch
) -> None:
    from simple_stipple.features.draft import page as draft_page_module
    from simple_stipple.features.draft.page import DraftPage

    page = DraftPage(settings={})
    canvas = page._canvas
    canvas.set_polylines_state([SQUARE_A, SQUARE_B, LONER])
    entity_ids = canvas.get_entity_ids()
    canvas.group_entities(entity_ids[:2])
    group_id = canvas._grouping_service.group_of(entity_ids[0])
    assert group_id is not None
    canvas.set_group_label(group_id, "Flange")
    page_dimension = {**DIMENSION, "layer": "Layer 1"}
    canvas._append_dimension(page_dimension)

    target = tmp_path / "draft.dxf"
    monkeypatch.setattr(draft_page_module, "pick_save_file", lambda *a, **k: str(target))
    page._export()
    assert target.exists()

    def fail_on_error(_parent, _title, exc) -> None:
        raise AssertionError(f"DXF import surfaced an error dialog: {exc}")

    imported = DraftPage(settings={})
    monkeypatch.setattr(draft_page_module, "show_error", fail_on_error)
    monkeypatch.setattr(
        imported,
        "_review_dxf_import",
        lambda _path, by_layer, _report, *, default_append: (by_layer, False),
    )
    monkeypatch.setattr(imported, "_offer_shape_detection", lambda: None)
    imported._load_dxf(str(target))

    imported_canvas = imported._canvas
    assert len(imported_canvas._entities) == 3

    group_map = imported_canvas._grouping_service.group_map()
    assert len(group_map) == 2
    restored_ids = set(group_map.values())
    assert len(restored_ids) == 1
    assert imported_canvas._group_labels.get(restored_ids.pop()) == "Flange"

    assert len(imported_canvas._dimensions) == 1
    assert imported_canvas._dimensions[0] == json.loads(json.dumps(page_dimension))

    page.close()
    imported.close()


def test_draft_page_dxf_append_import_restores_groups(tmp_path, app, monkeypatch) -> None:
    """Append mode collects created ids inside the document mutation — the
    group mapping must line up there too, not only on the replace path."""
    from simple_stipple.features.draft import page as draft_page_module
    from simple_stipple.features.draft.page import DraftPage

    target = tmp_path / "grouped.dxf"
    write_polylines_dxf(
        [SQUARE_A, SQUARE_B, LONER],
        str(target),
        entity_groups=[5, 5, None],
        group_labels={5: "pair"},
    )

    page = DraftPage(settings={})
    page._canvas.set_polylines_state([[(100.0, 100.0), (110.0, 100.0)]])

    def fail_on_error(_parent, _title, exc) -> None:
        raise AssertionError(f"DXF import surfaced an error dialog: {exc}")

    monkeypatch.setattr(draft_page_module, "show_error", fail_on_error)
    monkeypatch.setattr(
        page,
        "_review_dxf_import",
        lambda _path, by_layer, _report, *, default_append: (by_layer, True),
    )
    monkeypatch.setattr(page, "_offer_shape_detection", lambda: None)
    page._import_dxf_add(str(target))

    canvas = page._canvas
    assert len(canvas._entities) == 4
    group_map = canvas._grouping_service.group_map()
    assert len(group_map) == 2
    restored_ids = set(group_map.values())
    assert len(restored_ids) == 1
    assert canvas._group_labels.get(restored_ids.pop()) == "pair"
    page.close()
