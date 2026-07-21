"""Fill-region construction and hatching."""

import pytest

from src.backend.pattern.fill import FillSpec, apply_fill, build_fill_region
from src.backend.pattern.processing import PatternProcessor

OUTER = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
INNER = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]


def test_build_fill_region_subtracts_hole():
    region = build_fill_region([OUTER, INNER])
    assert region.area == pytest.approx(100 * 100 - 40 * 40)


def test_apply_fill_lines_skip_hole():
    region = build_fill_region([OUTER, INNER])
    spec = FillSpec(mode="lines", spacing=10, angle_deg=0.0, keep_pattern=True)
    lines = apply_fill(region, spec)
    assert len(lines) == 14
    # Every segment midpoint must lie outside the hole.
    for seg in lines:
        (x0, y0), (x1, y1) = seg[0], seg[-1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        inside_hole = 30 < mx < 70 and 30 < my < 70
        assert not inside_hole, f"fill line crosses hole: {seg}"


def test_zigzag_fill_reduces_paths_without_crossing_hole():
    from shapely.geometry import LineString

    region = build_fill_region([OUTER, INNER])
    lines = apply_fill(region, FillSpec(mode="lines", spacing=10))
    zigzag = apply_fill(region, FillSpec(mode="zigzag", spacing=10))
    assert zigzag
    assert len(zigzag) <= len(lines)
    assert all(region.buffer(1e-8).covers(LineString(path)) for path in zigzag)


def test_concentric_fill_produces_successive_closed_insets():
    region = build_fill_region([OUTER])
    paths = apply_fill(region, FillSpec(mode="concentric", spacing=10))
    assert len(paths) >= 4
    assert all(path[0] == pytest.approx(path[-1]) for path in paths)


def test_fillspec_rejects_unknown_mode():
    with pytest.raises(ValueError):
        FillSpec(mode="nonsense")  # type: ignore[arg-type]  # deliberately invalid


def test_fill_inset_can_collapse_a_narrow_region_without_crashing():
    region = build_fill_region([[(0.0, 0.0), (2.0, 0.0), (2.0, 20.0), (0.0, 20.0), (0.0, 0.0)]])
    assert apply_fill(region, FillSpec(mode="lines", spacing=1.0, inset=1.1)) == []


def test_self_intersecting_outline_is_repaired_to_polygonal_region():
    bow_tie = [(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    region = build_fill_region([bow_tie])
    assert region is not None
    assert region.is_valid
    assert region.area > 0


def test_self_intersection_that_repairs_to_multipolygon_is_supported():
    split_bow_tie = [
        (0.0, 0.0),
        (4.0, 4.0),
        (0.0, 8.0),
        (8.0, 8.0),
        (4.0, 4.0),
        (8.0, 0.0),
        (0.0, 0.0),
    ]
    region = build_fill_region([split_bow_tie])
    assert region is not None
    assert region.is_valid
    assert region.area == pytest.approx(32.0)


def test_outline_and_pattern_fill_targets_can_be_enabled_together():
    spec = FillSpec.from_dict({"mode": "lines", "target_outline": True, "target_pattern": True})
    assert spec.target_outline
    assert spec.target_pattern


def test_outline_and_pattern_fills_partition_cells_without_overlap():
    from shapely.geometry import LineString, Polygon

    service = PatternProcessor()
    params = {"brick_w": 20.0, "brick_h": 20.0, "gap": 2.0}
    pattern = service.build_pattern_polys(
        [OUTER],
        pattern="Brick",
        params=params,
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
    )
    cell = next(
        poly
        for poly in pattern
        if len(poly) >= 4
        and Polygon(poly).area > 1.0
        and Polygon(poly).bounds[0] > 0
        and Polygon(poly).bounds[2] < 100
    )

    outline_fill: list[list[tuple[float, float]]] = []
    service.build_pattern_polys(
        [OUTER],
        pattern="Brick",
        params=params,
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        fill_options={
            "mode": "lines",
            "spacing": 5.0,
            "target_outline": True,
            "target_pattern": False,
        },
        fill_polys_out=outline_fill,
    )
    cell_interior = Polygon(cell).buffer(-0.01)
    assert all(
        LineString(line).intersection(cell_interior).length == pytest.approx(0.0)
        for line in outline_fill
    )

    pattern_fill: list[list[tuple[float, float]]] = []
    service.build_pattern_polys(
        [OUTER],
        pattern="Brick",
        params=params,
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        fill_options={"mode": "lines", "spacing": 5.0, "target_pattern": True},
        fill_polys_out=pattern_fill,
    )
    assert any(LineString(line).intersection(cell_interior).length > 0 for line in pattern_fill)

    combined: list[list[tuple[float, float]]] = []
    service.build_pattern_polys(
        [OUTER],
        pattern="Brick",
        params=params,
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        fill_options={
            "mode": "lines",
            "spacing": 5.0,
            "target_outline": True,
            "target_pattern": True,
        },
        fill_polys_out=combined,
    )
    assert combined == outline_fill + pattern_fill

    cell_fill: list[list[tuple[float, float]]] = []
    service.build_pattern_polys(
        [OUTER],
        pattern="Brick",
        params=params,
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        fill_options={
            "mode": "lines",
            "spacing": 5.0,
            "target_pattern": True,
            "cell_cutouts": [cell],
        },
        fill_polys_out=cell_fill,
    )
    assert all(
        LineString(line).intersection(cell_interior).length == pytest.approx(0.0)
        for line in cell_fill
    )


def test_pattern_cell_fill_ignores_open_strokes_instead_of_failing():
    service = PatternProcessor()
    fill: list[list[tuple[float, float]]] = []

    result = service.build_pattern_polys(
        [OUTER],
        pattern="Flow Lines",
        params={"spacing": 20.0, "amplitude": 3.0, "wavelength": 30.0, "angle": 0.0},
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        fill_options={"mode": "lines", "spacing": 2.0, "target_pattern": True},
        fill_polys_out=fill,
    )

    assert result
    assert fill == []


def test_pattern_cutout_signature_follows_translated_rotated_and_mirrored_tiles():
    service = PatternProcessor()
    source = [(0.0, 0.0), (3.0, 0.0), (2.0, 2.0), (0.0, 1.0), (0.0, 0.0)]
    translated = [(x + 20.0, y - 7.0) for x, y in source]
    rotated = [(-y + 5.0, x + 8.0) for x, y in source]
    mirrored = [(-x + 12.0, y + 4.0) for x, y in source]

    signature = service._poly_repeat_signature(source)
    assert service._poly_repeat_signature(translated) == signature
    assert service._poly_repeat_signature(rotated) == signature
    assert service._poly_repeat_signature(mirrored) == signature


def test_topographic_generator_rejects_non_finite_spacing_without_crashing():
    service = PatternProcessor()
    result = service.build_pattern_polys(
        [OUTER],
        pattern="Topographic",
        params={"spacing": float("nan"), "quality": "high"},
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
    )
    assert result == []


def test_voronoi_generator_rejects_non_finite_gap_without_crashing():
    service = PatternProcessor()
    result = service.build_pattern_polys(
        [OUTER],
        pattern="Voronoi",
        params={"n_cells": 20, "gap": float("nan"), "seed": 42},
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
    )
    assert result == []


def test_zone_explicit_no_fill_does_not_inherit_document_fill(monkeypatch):
    service = PatternProcessor()
    calls = []

    def fake_build(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(service, "build_pattern_polys", fake_build)
    service.build_zone_pattern_polys(
        [
            {
                "polys": [OUTER],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (100.0, 100.0),
                "fill": None,
                "output_mode": "pattern",
            }
        ],
        include_border=True,
        orig_w=100.0,
        orig_h=100.0,
        fill_options={"mode": "lines", "spacing": 2.0},
    )

    assert calls[0]["pattern"] == "Honeycomb"
    assert calls[0]["fill_options"] is None


def test_fill_only_zone_suppresses_pattern_but_keeps_its_fill(monkeypatch):
    service = PatternProcessor()
    calls = []
    zone_fill = {"mode": "crosshatch", "spacing": 1.25}

    def fake_build(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(service, "build_pattern_polys", fake_build)
    service.build_zone_pattern_polys(
        [
            {
                "polys": [OUTER],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (100.0, 100.0),
                "fill": zone_fill,
                "output_mode": "fill",
            }
        ],
        include_border=True,
        orig_w=100.0,
        orig_h=100.0,
    )

    assert calls[0]["pattern"] == "— None —"
    assert calls[0]["fill_options"] == zone_fill
