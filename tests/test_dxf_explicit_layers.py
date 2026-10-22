"""Explicit document layers take precedence over shape-name-derived layers."""

import ezdxf
import pytest

from simple_stipple.core.formats.service import DxfService


def test_explicit_layers_keep_dimensions_text_and_groups(tmp_path):
    output = tmp_path / "layers.dxf"
    paths = [[(0, 0), (10, 0)], [(20, 0), (30, 0)], [(0, 0), (30, 0)]]
    text = {"text": "FIRE", "family": "Tahoma", "height_mm": 4, "bold": False, "italic": False}
    dimension = {
        "type": "linear",
        "p1": (0, 0),
        "p2": (30, 0),
        "offset": 8,
        "precision": 3,
    }
    DxfService.write_polylines_dxf(
        paths,
        str(output),
        entity_layers=["Cut outline", "Engraving", "Measurements"],
        entity_kinds=["polyline", "polyline", "dimension"],
        entity_meta=[{"name": "Not a layer"}, {"text_params": text}, dimension],
        entity_groups=[7, 7, None],
        group_labels={7: "FIRE\nSAFE"},
        object_names=["Outline", "FIRE", None],
    )
    document = ezdxf.readfile(output)
    assert not document.audit().has_errors
    entities = list(document.modelspace())
    assert [(entity.dxftype(), entity.dxf.layer) for entity in entities] == [
        ("LWPOLYLINE", "Cut outline"),
        ("LWPOLYLINE", "Engraving"),
        ("DIMENSION", "Measurements"),
    ]
    assert "Not_a_layer" not in document.layers
    assert "Artwork" not in document.layers
    assert len({document.layers.get(entity.dxf.layer).dxf.color for entity in entities}) == 3
    layers, report = DxfService.load_dxf_polylines_by_layer_with_report(str(output))
    assert {"Cut outline", "Engraving"} <= layers.keys()
    assert report.group_labels == {7: "FIRE\nSAFE"}
    assert {entry["layer"] for entry in report.groups} == {"Cut outline", "Engraving"}
    assert report.texts == [{"layer": "Engraving", "index": 0, "params": text}]


@pytest.mark.parametrize("layers", [[], ["Cut", "Engraving"]])
def test_mismatched_layer_list_does_not_overwrite_output(tmp_path, layers):
    output = tmp_path / "existing.dxf"
    output.write_bytes(b"original drawing")
    with pytest.raises(ValueError, match="Each exported path must have a layer name"):
        DxfService.write_polylines_dxf([[(0, 0), (10, 0)]], str(output), entity_layers=layers)
    assert output.read_bytes() == b"original drawing"
