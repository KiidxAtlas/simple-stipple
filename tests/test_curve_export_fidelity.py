"""Curves (spline/bezier) and arcs must stay smooth wherever their geometry
leaves the canvas as a plain polygon — sent to another page, used as a fill
outline, or exported/imported via DXF — not just when painted on-screen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import make_view  # noqa: E402


def _spline_view(qapp):
    from src.backend.model.document import EntityRecord

    v = make_view(qapp, [])
    v._entities = [
        EntityRecord(
            points=[(0.0, 0.0), (10.0, 20.0), (30.0, 20.0), (40.0, 0.0)],
            kind="spline",
            meta={"segments": 24, "closed": False},
        )
    ]
    return v


def _bezier_view(qapp):
    from src.backend.model.document import EntityRecord

    v = make_view(qapp, [])
    v._entities = [
        EntityRecord(
            points=[(0.0, 0.0), (40.0, 0.0)],
            kind="bezier",
            meta={"tangents": [(0.0, 15.0), (0.0, 15.0)], "segments": 16, "closed": False},
        )
    ]
    return v


def _arc_view(qapp):
    from src.backend.model.document import EntityRecord

    # A big arc with only a couple of raw points stored — if consumers used
    # .points directly this would look like a two-segment zig-zag.
    v = make_view(qapp, [])
    v._entities = [
        EntityRecord(
            points=[(100.0, 0.0), (0.0, 100.0)],
            kind="arc",
            meta={"center": (0.0, 0.0), "radius": 100.0, "start_angle": 0.0, "end_angle": 90.0},
        )
    ]
    return v


@pytest.mark.parametrize("build", [_spline_view, _bezier_view, _arc_view])
def test_get_selected_returns_tessellated_curve_not_sparse_anchors(qapp, build):
    v = build(qapp)
    raw_count = len(v._entities[0].points)
    v.set_selection([v._entities[0].id])
    tessellated = v.get_selected()[0]
    assert len(tessellated) > raw_count


@pytest.mark.parametrize("build", [_spline_view, _bezier_view, _arc_view])
def test_get_polylines_state_returns_tessellated_curve(qapp, build):
    v = build(qapp)
    raw_count = len(v._entities[0].points)
    tessellated = v.get_polylines_state()[0]
    assert len(tessellated) > raw_count


def test_curve_points_themselves_are_left_untouched(qapp):
    """Flattening must be read-only — .points stays the sparse, editable
    control-point representation Edit mode/undo/session-save rely on."""
    v = _spline_view(qapp)
    before = list(v._entities[0].points)
    v.set_selection([v._entities[0].id])
    v.get_selected()
    v.get_polylines_state()
    assert v._entities[0].points == before


def test_dxf_export_writes_native_spline_entity(qapp, tmp_path):
    from src.backend.dxf.io import write_polylines_dxf

    v = _spline_view(qapp)
    export = v.get_export_dxf_state()
    assert export[0]["kind"] == "spline"

    out = tmp_path / "spline.dxf"
    write_polylines_dxf(
        [r["polyline"] for r in export],
        str(out),
        entity_kinds=[r["kind"] for r in export],
        entity_meta=[r["meta"] for r in export],
    )
    import ezdxf

    doc = ezdxf.readfile(str(out))  # type: ignore[attr-defined]
    types = [e.dxftype() for e in doc.modelspace()]
    assert "SPLINE" in types


def test_secondary_dxf_layer_preserves_native_curve_kind(qapp, tmp_path):
    import ezdxf

    from src.backend.dxf.io import write_polylines_dxf

    export = _spline_view(qapp).get_export_dxf_state()
    out = tmp_path / "secondary-curve.dxf"
    write_polylines_dxf(
        [[(0.0, 0.0), (1.0, 0.0)]],
        str(out),
        pattern_layer="Primary",
        extra_layer_records={"Curves": export},
    )

    entities = list(ezdxf.readfile(out).modelspace().query('*[layer=="Curves"]'))
    assert [entity.dxftype() for entity in entities] == ["SPLINE"]


def test_dxf_export_flattens_bezier_to_a_smooth_polyline(qapp, tmp_path):
    from src.backend.dxf.io import write_polylines_dxf

    v = _bezier_view(qapp)
    export = v.get_export_dxf_state()
    # No native DXF bezier entity — must fall back to a tessellated polyline,
    # not the 2 raw anchor points.
    assert export[0]["kind"] == "polyline"
    assert len(export[0]["polyline"]) > 2

    out = tmp_path / "bezier.dxf"
    write_polylines_dxf(
        [r["polyline"] for r in export],
        str(out),
        entity_kinds=[r["kind"] for r in export],
        entity_meta=[r["meta"] for r in export],
    )
    import ezdxf

    doc = ezdxf.readfile(str(out))  # type: ignore[attr-defined]
    poly = next(iter(doc.modelspace()))
    assert len(list(poly.get_points())) > 2  # type: ignore[attr-defined]


def test_dxf_import_no_longer_drops_native_spline_entities(tmp_path):
    """A DXF authored elsewhere (AutoCAD, Inkscape, ...) with a real SPLINE
    entity used to come back as "unsupported" (silently dropped)."""
    import ezdxf

    doc = ezdxf.new("R2010")  # type: ignore[attr-defined]
    doc.header["$INSUNITS"] = 4  # millimeters
    msp = doc.modelspace()
    msp.add_spline(
        fit_points=[(0, 0), (10, 20), (30, 20), (40, 0)],
    )
    path = tmp_path / "external_spline.dxf"
    doc.saveas(str(path))

    from src.backend.dxf.io import load_dxf_polylines_by_layer_with_report

    by_layer, report = load_dxf_polylines_by_layer_with_report(str(path))
    assert report.unsupported_entities == {}
    assert report.flattened_entities.get("SPLINE") == 1
    assert report.units == "Millimeters"
    all_pts = [p for polys in by_layer.values() for poly in polys for p in poly]
    assert len(all_pts) > 4  # smooth curve, not just the 4 fit points


def test_dxf_import_scales_inch_coordinates_to_millimeters(tmp_path):
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    doc.modelspace().add_line((0, 0), (1, 0))
    source = tmp_path / "inch.dxf"
    doc.saveas(source)

    from src.backend.dxf.io import load_dxf_polylines

    points = load_dxf_polylines(str(source))[0]
    assert points[0] == pytest.approx((0, 0))
    assert points[1] == pytest.approx((25.4, 0))


def test_dxf_import_flattens_lwpolyline_bulge_arcs(tmp_path):
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_lwpolyline([(0, 0, 1.0), (10, 0, 0.0)], format="xyb")
    source = tmp_path / "bulge.dxf"
    doc.saveas(source)

    from src.backend.dxf.io import load_dxf_polylines_by_layer_with_report

    by_layer, report = load_dxf_polylines_by_layer_with_report(str(source))
    points = next(iter(by_layer.values()))[0]
    assert len(points) > 2
    assert max(abs(y) for _x, y in points) > 4.9
    assert report.flattened_entities["LWPOLYLINE (bulge arcs)"] == 1


def test_dxf_import_expands_transformed_block_references(tmp_path):
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    block = doc.blocks.new("PART")
    block.add_line((0, 0), (10, 0))
    doc.modelspace().add_blockref("PART", (20, 5), dxfattribs={"rotation": 90})
    source = tmp_path / "block.dxf"
    doc.saveas(source)

    from src.backend.dxf.io import load_dxf_polylines_by_layer_with_report

    by_layer, report = load_dxf_polylines_by_layer_with_report(str(source))
    points = next(iter(by_layer.values()))[0]
    assert points[0] == pytest.approx((20, 5))
    assert points[1] == pytest.approx((20, 15))
    assert report.flattened_entities["INSERT (block contents)"] == 1
