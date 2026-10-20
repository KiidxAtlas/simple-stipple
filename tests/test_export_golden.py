"""Golden-file tests for the exporters, plus round-trip properties.

The golden files pin the exact geometry each exporter writes for one fixed
drawing, so a refactor that silently changes output (rounding, closure,
layer naming, native-shape conversion) fails here. When a change to the
output is intended, regenerate them with::

    UPDATE_GOLDEN=1 pytest tests/test_export_golden.py

and review the diff of ``tests/data/golden``.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.entities import LWPolyline
from hypothesis import given, settings
from hypothesis import strategies as st

from simple_stipple.core.cad.shape_factory import ShapeFactory
from simple_stipple.core.formats.dxf import (
    DXF_CLOSURE_EPS,
    DXF_DEDUP_EPS,
    load_dxf_polylines,
    write_polylines_dxf,
)
from simple_stipple.core.formats.svg import write_polylines_svg

GOLDEN = Path(__file__).parent / "data" / "golden"
_UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

_SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
_ZIGZAG = [(12.0, 0.0), (14.5, 3.25), (17.0, 0.0), (19.5, 3.25)]
_CIRCLE = ShapeFactory.circle(center=(30.0, 5.0), radius=4.0)


def _check_golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing golden file {path}; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8")


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (tuple, list)) or type(value).__name__ == "Vec3":
        return [_canonical(v) for v in value]
    return value


def _dxf_summary(path: Path) -> str:
    """Entity geometry and attributes, without handles and timestamps."""
    doc = ezdxf.readfile(str(path))
    entities = []
    for entity in doc.modelspace():
        attribs = {
            key: _canonical(value)
            for key, value in sorted(entity.dxf.all_existing_dxf_attribs().items())
            if key not in {"handle", "owner"}
        }
        record: dict[str, Any] = {"type": entity.dxftype(), "attribs": attribs}
        if isinstance(entity, LWPolyline):
            record["points"] = _canonical([list(p) for p in entity.get_points("xyb")])
        if entity.xdata is not None:
            record["xdata"] = {
                appid: [[tag.code, _canonical(tag.value)] for tag in tags]
                for appid, tags in sorted(entity.xdata.data.items())
            }
        entities.append(record)
    layers = sorted(layer.dxf.name for layer in doc.layers)
    return json.dumps({"layers": layers, "entities": entities}, indent=2) + "\n"


def test_svg_export_matches_golden(tmp_path: Path) -> None:
    out = tmp_path / "drawing.svg"
    stats = write_polylines_svg(
        [_SQUARE, _ZIGZAG, list(_CIRCLE.points)],
        out,
        entity_groups=[1, 1, None],
    )
    assert stats["polylines"] == 3
    _check_golden("drawing.svg", out.read_text(encoding="utf-8"))


def test_dxf_export_matches_golden(tmp_path: Path) -> None:
    out = tmp_path / "drawing.dxf"
    kind, meta = _CIRCLE.to_meta_dict()
    write_polylines_dxf(
        [_SQUARE, _ZIGZAG, list(_CIRCLE.points)],
        str(out),
        pattern_layer="PATTERN",
        border_polys=[[(-1.0, -1.0), (36.0, -1.0), (36.0, 11.0), (-1.0, 11.0), (-1.0, -1.0)]],
        entity_kinds=["polyline", "polyline", kind],
        entity_meta=[None, None, meta],
        entity_groups=[7, 7, None],
        group_labels={7: "pair"},
        object_names=["square", None, "hole"],
    )
    _check_golden("drawing.dxf.json", _dxf_summary(out))


_coordinate = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False)
_SEPARATION = 10 * max(DXF_DEDUP_EPS, DXF_CLOSURE_EPS)


def _well_formed(pts: list[tuple[float, float]]) -> bool:
    """Distinct consecutive vertices and distinct endpoints — the writer
    deliberately merges anything closer than its epsilons."""
    steps = all(math.dist(a, b) > _SEPARATION for a, b in zip(pts, pts[1:]))
    return steps and math.dist(pts[0], pts[-1]) > _SEPARATION


_open_path = st.lists(st.tuples(_coordinate, _coordinate), min_size=2, max_size=12).filter(
    _well_formed
)


@settings(max_examples=60, deadline=None)
@given(paths=st.lists(_open_path, min_size=1, max_size=5))
def test_dxf_round_trip_preserves_open_polylines(tmp_path_factory, paths) -> None:
    out = tmp_path_factory.mktemp("dxf") / "round_trip.dxf"
    write_polylines_dxf(paths, str(out), open_paths=True)
    loaded = load_dxf_polylines(str(out))
    assert len(loaded) == len(paths)
    for written, read in zip(paths, loaded):
        assert len(read) == len(written)
        for (wx, wy), (rx, ry) in zip(written, read):
            assert math.isclose(wx, rx, abs_tol=1e-6)
            assert math.isclose(wy, ry, abs_tol=1e-6)
