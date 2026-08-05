"""Phase 5 — pattern grids belong to the document, not to each region.

Two adjacent regions with the same settings must produce one lattice, not two
that meet at a seam. That means cells sit on ``origin + k * step``, never on a
grid anchored to each region's own bounding box.
"""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from simple_stipple.engine.patterns._shared import lattice_cells
from simple_stipple.engine.patterns.processing import PatternProcessor

# Deliberately awkward: LEFT's width is not a whole number of 5 mm columns, so
# a bbox-anchored lattice cannot help but disagree with RIGHT's.
LEFT = Polygon([(0, 0), (13.7, 0), (13.7, 20), (0, 20)])
RIGHT = Polygon([(13.7, 0), (40, 0), (40, 20), (13.7, 20)])

STEP = 5.0


def _centres(region: Polygon, **kwargs) -> list[tuple[float, float]]:
    return [
        (round(cx, 6), round(cy, 6))
        for cx, cy, _row, _col in lattice_cells(region, STEP, STEP, pad=0.0, **kwargs)
    ]


def _on_grid(centres, origin=(0.0, 0.0)) -> bool:
    return all(
        abs((cx - origin[0]) % STEP) < 1e-6 and abs((cy - origin[1]) % STEP) < 1e-6
        for cx, cy in centres
    )


def test_adjacent_regions_share_one_lattice() -> None:
    left, right = _centres(LEFT), _centres(RIGHT)
    assert left and right
    assert _on_grid(left) and _on_grid(right)
    # The shared column exists in both, at the same coordinate — no seam.
    assert set(left) & set(right)


def test_a_moved_origin_moves_every_region_together() -> None:
    origin = (1.25, 0.75)
    left = _centres(LEFT, origin_x=origin[0], origin_y=origin[1])
    right = _centres(RIGHT, origin_x=origin[0], origin_y=origin[1])
    assert _on_grid(left, origin) and _on_grid(right, origin)


def test_row_stagger_is_global_so_half_drop_does_not_break_at_the_edge() -> None:
    left = dict.fromkeys(cy for _cx, cy in _centres(LEFT, repeat_mode="Half drop"))
    right = dict.fromkeys(cy for _cx, cy in _centres(RIGHT, repeat_mode="Half drop"))
    assert set(left) == set(right), "rows must line up across the shared edge"

    # A staggered row is offset by half a column in both regions, or neither.
    def xs_at(region, y):
        return {cx for cx, cy in _centres(region, repeat_mode="Half drop") if cy == y}

    for y in left:
        assert {x % STEP for x in xs_at(LEFT, y)} == {x % STEP for x in xs_at(RIGHT, y)}


def test_align_to_region_is_the_deliberate_opt_out() -> None:
    """Anchoring to the shape is available, but only when asked for."""
    params = {"r": 2.0, "gap": 0.3}
    processor = PatternProcessor()
    document = processor._resolve_origin(RIGHT, params)
    assert document is params  # untouched: no flag, no region anchoring

    anchored = processor._resolve_origin(RIGHT, {**params, "align_to_region": True})
    assert anchored["origin_x"] == pytest.approx(RIGHT.bounds[0])
    assert anchored["origin_y"] == pytest.approx(RIGHT.bounds[1])


def test_the_document_lattice_reaches_the_engine_through_one_door() -> None:
    processor = PatternProcessor()
    processor.lattice_origin = (2.5, 1.5)
    processor.lattice_seed = 7
    params = processor._apply_document_lattice({"r": 2.0})
    assert (params["origin_x"], params["origin_y"]) == (2.5, 1.5)
    assert params["seed"] == 7


def test_a_fixed_seed_makes_voronoi_reproducible() -> None:
    processor = PatternProcessor()
    processor.lattice_seed = 11
    panel = [[(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0), (0.0, 0.0)]]
    runs = [
        processor.build_pattern_polys(
            panel,
            pattern="Voronoi",
            params={"n_cells": 25, "gap": 0.3},
            scale=(40.0, 40.0),
            orig_w=40.0,
            orig_h=40.0,
        )
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
