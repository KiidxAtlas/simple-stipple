"""New pattern generators and the lattice settings every pattern now shares."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from simple_stipple.core.patterns.processing import (
    PATTERNS,
    PatternProcessor,
    migrate_pattern_name,
)
from simple_stipple.features.pattern.form import PARAM_SPECS

PANEL = Polygon([(0, 0), (60, 0), (60, 40), (0, 40)])

DEFAULTS = {
    "Honeycomb": {"r": 4, "gap": 0.5},
    "Brick": {"brick_w": 6, "brick_h": 3, "gap": 0.4},
    "Basketweave": {"strip_w": 2, "strip_l": 6, "gap": 0.3},
    "Mesh": {"r": 1, "spacing": 3},
    "Stipple Dots": {"r": 0.6, "spacing": 3},
    "Grip Stipple": {"element_size": 0.5, "spacing": 1.2, "seed": 1},
    "Voronoi": {"n_cells": 40, "gap": 0.3, "seed": 1},
    "Truchet": {"tile": 6, "gap": 0.3, "seed": 1},
    "Seigaiha": {"r": 6, "rings": 3, "ring_gap": 1.2},
    "Knurling": {"pitch": 1.5, "angle": 30, "cross": True, "groove": 0.3},
}

LATTICE_PATTERNS = ("Honeycomb", "Brick", "Basketweave", "Mesh", "Truchet", "Seigaiha")
# Every built-in pattern offers Repeat mode; these repeat one random tile.
SCATTER_PATTERNS = ("Stipple Dots", "Grip Stipple", "Voronoi")
REPEATING_PATTERNS = (*LATTICE_PATTERNS, "Knurling", *SCATTER_PATTERNS)


@pytest.mark.parametrize("name", ["Truchet", "Seigaiha", "Knurling"])
def test_new_patterns_generate_geometry(name: str) -> None:
    polys = PatternProcessor()._generate_base_pattern(PANEL, name, dict(DEFAULTS[name]))
    assert polys, f"{name} produced nothing"
    assert all(len(poly) >= 2 for poly in polys)


def test_knurling_cross_doubles_the_groove_families() -> None:
    service = PatternProcessor()
    single = service._generate_base_pattern(
        PANEL, "Knurling", {**DEFAULTS["Knurling"], "cross": False}
    )
    crossed = service._generate_base_pattern(PANEL, "Knurling", dict(DEFAULTS["Knurling"]))
    assert len(crossed) > len(single)


def test_truchet_is_reproducible_for_a_fixed_seed() -> None:
    service = PatternProcessor()
    first = service._generate_base_pattern(PANEL, "Truchet", dict(DEFAULTS["Truchet"]))
    second = service._generate_base_pattern(PANEL, "Truchet", dict(DEFAULTS["Truchet"]))
    assert first == second


@pytest.mark.parametrize("name", REPEATING_PATTERNS)
@pytest.mark.parametrize("mode", ["Straight", "Half drop", "Brick offset"])
def test_every_pattern_accepts_a_repeat_mode(name: str, mode: str) -> None:
    """Repeat mode used to exist only on Custom Tile; it is shared now."""
    params = {**DEFAULTS[name], "repeat_mode": mode}
    assert PatternProcessor()._generate_base_pattern(PANEL, name, params)


@pytest.mark.parametrize("name", LATTICE_PATTERNS)
def test_lattice_origin_shifts_the_pattern(name: str) -> None:
    """The document origin moves the whole grid, so every region moves with it."""
    service = PatternProcessor()
    base = service._generate_base_pattern(PANEL, name, dict(DEFAULTS[name]))
    shifted = service._generate_base_pattern(
        PANEL, name, {**DEFAULTS[name], "origin_x": 1.7, "origin_y": 1.1}
    )
    assert base != shifted


def test_every_pattern_offers_half_drop_in_the_form() -> None:
    named = {name for name in PATTERNS if name != "— None —"}
    for name in sorted(named):
        repeat = [field for field in PARAM_SPECS[name] if field.param_key == "repeat_mode"]
        assert repeat and "Half drop" in repeat[0].items, (name, PARAM_SPECS[name])


@pytest.mark.parametrize("name", REPEATING_PATTERNS)
def test_lattice_patterns_expose_their_controls_in_the_form(name: str) -> None:
    """The grid origin is a document control, so a region only chooses its
    repeat mode and whether to leave the document grid at all."""
    keys = {field.param_key or field.attr[1:] for field in PARAM_SPECS[name]}
    assert {"repeat_mode", "align_to_region"} <= keys
    assert not {"origin_x", "origin_y"} & keys


def test_every_pattern_has_a_form_spec_and_no_retired_ones_remain() -> None:
    named = {name for name in PATTERNS if name != "— None —"}
    assert named <= set(PARAM_SPECS)
    assert not {"Flow Lines", "Gradient Honeycomb", "Topographic"} & set(PARAM_SPECS)
    assert not {"Flow Lines", "Gradient Honeycomb", "Topographic"} & named


@pytest.mark.parametrize(
    "old,new",
    [
        ("Flow Lines", "Truchet"),
        ("Gradient Honeycomb", "Honeycomb"),
        ("Topographic", "Seigaiha"),
        ("Honeycomb", "Honeycomb"),
    ],
)
def test_retired_patterns_migrate_instead_of_failing(old: str, new: str) -> None:
    """An older workspace must open and render, not die on a missing generator."""
    assert migrate_pattern_name(old) == new


# ── Fill compatibility ────────────────────────────────────────────────────
#
# A pattern only fills well if it emits CLOSED cells: the fill system hatches
# inside each cell (``target_pattern``) or the region around them
# (``target_outline``). Open linework yields no cells at all, so fill floods
# the whole region and the strokes become a cosmetic overlay.

SQUARE = [[(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0), (0.0, 0.0)]]


def _fill_spec(**overrides) -> dict:
    return {
        "mode": "lines",
        "spacing": 1.0,
        "angle_deg": 0.0,
        "inset": 0.0,
        "keep_pattern": True,
        "target_outline": False,
        "target_pattern": False,
        **overrides,
    }


@pytest.mark.parametrize("name", ["Truchet", "Seigaiha", "Knurling", "Honeycomb", "Brick"])
def test_patterns_emit_closed_cells_with_area(name: str) -> None:
    polys = PatternProcessor()._generate_base_pattern(PANEL, name, dict(DEFAULTS[name]))
    assert polys
    for poly in polys:
        assert len(poly) >= 4
        assert poly[0] == pytest.approx(poly[-1]), f"{name} emitted an open path"
    assert sum(Polygon(poly).area for poly in polys) > 0.0


def test_no_pattern_is_registered_as_open_linework() -> None:
    assert PatternProcessor._OPEN_PATTERNS == set()
    for name in PATTERNS:
        assert PatternProcessor.should_close_pattern(name)


@pytest.mark.parametrize("name", ["Truchet", "Seigaiha", "Knurling", "Honeycomb"])
@pytest.mark.parametrize("target", ["target_pattern", "target_outline"])
def test_fill_reaches_both_targets(name: str, target: str) -> None:
    """Fill inside the cells and fill around them must both produce strokes."""
    strokes: list = []
    PatternProcessor().build_pattern_polys(
        SQUARE,
        pattern=name,
        params=dict(DEFAULTS[name]),
        scale=(50.0, 50.0),
        orig_w=50.0,
        orig_h=50.0,
        fill_options=_fill_spec(**{target: True}),
        fill_polys_out=strokes,
    )
    assert strokes, f"{name} produced no fill for {target}"


def test_outline_fill_stays_out_of_overlapping_cells() -> None:
    """Grains overlap, and boundary-clipped ones wind the other way: every
    covered spot must still count as a cell, or the hatch runs through them."""
    square = [[(0.0, 0.0), (11.0, 0.0), (11.0, 11.7), (0.0, 11.7), (0.0, 0.0)]]
    params = {"element_size": 0.5, "spacing": 0.4, "seed": 1}
    service = PatternProcessor()
    strokes: list = []
    grains = service.build_pattern_polys(
        square,
        pattern="Grip Stipple",
        params=params,
        scale=(11.0, 11.7),
        orig_w=11.0,
        orig_h=11.7,
        fill_options=_fill_spec(mode="crosshatch", spacing=0.1, target_outline=True),
        fill_polys_out=strokes,
    )
    covered = unary_union([Polygon(g).buffer(0) for g in grains if len(g) >= 4]).buffer(-0.02)
    assert strokes
    inside = sum(LineString(s).intersection(covered).length for s in strokes if len(s) >= 2)
    assert inside < 1e-6


def test_knurling_groove_leaves_room_to_fill_around_the_pads() -> None:
    """Diamonds tile the plane exactly, so without a groove there is no
    space around them for the outline fill to hatch."""
    service = PatternProcessor()
    flush = service._generate_base_pattern(
        PANEL, "Knurling", {**DEFAULTS["Knurling"], "groove": 0.0}
    )
    grooved = service._generate_base_pattern(
        PANEL, "Knurling", {**DEFAULTS["Knurling"], "groove": 0.6}
    )
    flush_area = sum(Polygon(poly).area for poly in flush)
    grooved_area = sum(Polygon(poly).area for poly in grooved)
    assert grooved_area < flush_area


@pytest.mark.parametrize("gap", [0.0, 0.3, 1.0])
@pytest.mark.parametrize("size", [12.0, 30.0, 60.0])
def test_truchet_generates_at_every_gap_and_size(gap: float, size: float) -> None:
    """The gap insets finished cells; it must not widen the lattice.

    Widening it pulled the arcs apart so they met nothing, polygonize found no
    enclosed cells, and the pattern collapsed to a single region-sized cell.
    """
    region = Polygon([(0, 0), (size, 0), (size, size), (0, size)])
    cells = PatternProcessor()._generate_base_pattern(
        region, "Truchet", {"tile": 6.0, "gap": gap, "seed": 1}
    )
    assert len(cells) > 1, f"Truchet collapsed at gap={gap}, size={size}"


# ── Scatter patterns repeat one random tile ───────────────────────────────

BIG = Polygon([(0, 0), (60, 0), (60, 60), (0, 60)])


def _dot_centres(mode: str) -> list[tuple[float, float]]:
    dots = PatternProcessor()._generate_base_pattern(
        BIG,
        "Stipple Dots",
        {"r": 0.3, "spacing": 2.0, "seed": 7, "repeat_mode": mode, "repeat_size": 10.0},
    )
    whole = [Polygon(dot) for dot in dots if Polygon(dot).area > 0.27]  # uncut circles
    return [(d.centroid.x, d.centroid.y) for d in whole]


def _has_point(points, target, tol: float = 1e-3) -> bool:
    return any(math.dist(point, target) <= tol for point in points)


@pytest.mark.parametrize(
    ("mode", "next_row"),
    [("Straight", (0.0, 10.0)), ("Half drop", (5.0, 10.0)), ("Brick offset", (10 / 3, 10.0))],
)
def test_stipple_repeat_copies_its_tile_with_the_row_offset(mode: str, next_row) -> None:
    centres = _dot_centres(mode)
    inner = [(x, y) for x, y in centres if 5 < x < 40 and 0 < y < 10]  # one even row
    assert inner
    for x, y in inner:
        assert _has_point(centres, (x + 10.0, y))  # next tile along the row
        assert _has_point(centres, (x + next_row[0], y + next_row[1]))


@pytest.mark.parametrize("mode", ["Straight", "Half drop", "Brick offset"])
def test_stipple_repeat_keeps_dot_spacing_across_tile_seams(mode: str) -> None:
    centres = _dot_centres(mode)
    closest = min(math.dist(a, b) for i, a in enumerate(centres) for b in centres[i + 1 :])
    assert closest >= 2.0 - 1e-3


def test_voronoi_half_drop_repeats_whole_cells() -> None:
    cells = PatternProcessor()._generate_base_pattern(
        BIG,
        "Voronoi",
        {"n_cells": 6, "gap": 0.2, "seed": 3, "repeat_mode": "Half drop", "repeat_size": 12.0},
    )
    centroids = {(round(Polygon(c).centroid.x, 2), round(Polygon(c).centroid.y, 2)) for c in cells}
    inner = [(x, y) for x, y in centroids if 12 < x < 36 and 12 < y < 24]
    assert inner
    for x, y in inner:
        assert (round(x + 6.0, 2), round(y + 12.0, 2)) in centroids


def test_knurling_half_drop_staggers_the_diamond_rows() -> None:
    service = PatternProcessor()
    straight = service._generate_base_pattern(PANEL, "Knurling", dict(DEFAULTS["Knurling"]))
    dropped = service._generate_base_pattern(
        PANEL, "Knurling", {**DEFAULTS["Knurling"], "repeat_mode": "Half drop"}
    )
    assert dropped and straight != dropped
