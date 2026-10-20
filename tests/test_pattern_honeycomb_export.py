from __future__ import annotations

import xml.etree.ElementTree as ET

import ezdxf
import pytest
from ezdxf.entities import LWPolyline

from simple_stipple.core.formats.laserstar import export_laserstar_package
from simple_stipple.core.patterns.processing import PatternProcessor
from simple_stipple.features.pattern.export import (
    export_document_file,
    export_layers_from_preview,
)
from simple_stipple.features.pattern.workers import PreviewRequest, solve_preview

OUTLINE = [(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
CELL_A = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0), (5.0, 10.0), (5.0, 5.0)]
CELL_B = [(15.0, 5.0), (20.0, 5.0), (20.0, 10.0), (15.0, 10.0), (15.0, 5.0)]
HATCH = [(6.0, 7.0), (9.0, 7.0)]
LAYERS = export_layers_from_preview(
    {"outline": [OUTLINE], "pattern": [CELL_A, CELL_B], "fill": [HATCH]}, []
)


def _dxf_layer_counts(path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in ezdxf.readfile(path).modelspace():
        counts[entity.dxf.layer] = counts.get(entity.dxf.layer, 0) + 1
    return counts


def test_preview_fill_rides_on_the_pattern_layer() -> None:
    assert LAYERS == {"outline": [OUTLINE], "pattern": [CELL_A, CELL_B, HATCH]}


def test_dxf_export_writes_outline_and_pattern_layers(tmp_path) -> None:
    target = tmp_path / "part.dxf"
    export_document_file(str(target), "dxf", [], vector_layers=LAYERS)
    assert _dxf_layer_counts(target) == {"outline": 1, "pattern": 3}


def test_svg_export_writes_named_inkscape_layers(tmp_path) -> None:
    target = tmp_path / "part.svg"
    export_document_file(str(target), "svg", [], vector_layers=LAYERS)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    label = "{http://www.inkscape.org/namespaces/inkscape}label"
    groups = ET.parse(target).getroot().findall("svg:g", ns)
    assert {g.get(label): len(g.findall("svg:path", ns)) for g in groups} == {
        "Outline": 1,
        "Pattern": 3,
    }


def test_fvi_export_labels_each_path_with_its_layer(tmp_path) -> None:
    target = tmp_path / "part.fvi"
    export_document_file(str(target), "fvi", [], vector_layers=LAYERS)
    labels = [
        line.rsplit(": ", 1)[1] for line in target.read_text().splitlines() if "; Path " in line
    ]
    assert sorted(labels) == ["outline", "pattern", "pattern", "pattern"]


@pytest.mark.parametrize("vector_format", ["dxf", "fvi"])
def test_laserstar_package_keeps_the_layers(tmp_path, vector_format: str) -> None:
    folder = export_laserstar_package(
        tmp_path, "Job", [OUTLINE, CELL_A], vector_layers=LAYERS, vector_format=vector_format
    )
    vector = folder / f"01_pattern-and-outline.{vector_format}"
    if vector_format == "dxf":
        assert _dxf_layer_counts(vector) == {"outline": 1, "pattern": 3}
    else:
        text = vector.read_text()
        assert text.count(": outline") == 1 and text.count(": pattern") == 3


def test_honeycomb_export_keeps_closed_cells_and_outline_on_two_layers(tmp_path) -> None:
    outline = [[(0.0, 0.0), (24.0, 0.0), (24.0, 20.0), (0.0, 20.0), (0.0, 0.0)]]
    path = tmp_path / "honeycomb.dxf"
    preview = solve_preview(
        PreviewRequest(
            all_polys=outline,
            pattern="Honeycomb",
            params={"r": 2.0, "gap": 0.3, "quality": "high"},
            scale=(24.0, 20.0),
            border_fade=0.0,
            orig_w=24.0,
            orig_h=20.0,
            fill_options={"mode": "lines", "spacing": 2.0, "target_pattern": True},
        ),
        PatternProcessor(),
    )

    export_document_file(
        str(path), "dxf", [], vector_layers=export_layers_from_preview(preview, [])
    )

    doc = ezdxf.readfile(path)
    entities = list(doc.modelspace())
    assert {entity.dxf.layer for entity in entities} == {"pattern", "outline"}
    pattern_geometry = [entity for entity in entities if entity.dxf.layer == "pattern"]
    assert pattern_geometry
    closed_cells = [
        entity for entity in pattern_geometry if isinstance(entity, LWPolyline) and entity.closed
    ]
    assert len(closed_cells) > 1
    assert all(isinstance(entity, LWPolyline) for entity in pattern_geometry)
