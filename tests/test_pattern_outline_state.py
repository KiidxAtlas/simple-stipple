"""Behavior coverage for Pattern's pure outline-state seam."""

from __future__ import annotations

from simple_stipple.features.pattern.outline_state import (
    canvas_records,
    normalize_outline_items,
    outline_bounds,
    reconcile_outline_ids,
    smallest_containing_outline,
)


def test_normalized_transferred_outlines_keep_valid_paths_and_layers() -> None:
    normalized = normalize_outline_items(
        [
            {"points": [(0, 0), (2, 0)], "layer": "Cut"},
            [(1, 1), (1, 2)],
            {"points": [("bad", 0)]},
        ]
    )
    assert normalized.polylines == [[(0.0, 0.0), (2.0, 0.0)], [(1.0, 1.0), (1.0, 2.0)]]
    assert normalized.layers == ["Cut", None]


def test_outline_records_reconcile_identity_and_layer_order() -> None:
    paths = [[(0.0, 0.0), (2.0, 0.0)], [(1.0, 1.0), (1.0, 2.0)]]
    ids = reconcile_outline_ids(
        ["kept"], paths, lambda count: [f"new-{index}" for index in range(count)]
    )
    records, layers = canvas_records(paths, ids, {"kept": "Cut"})
    assert ids == ["kept", "new-0"]
    assert [record["layer"] for record in records] == ["Cut", "Outline"]
    assert layers == ["Cut", "Outline"]
    assert outline_bounds(paths) == (2.0, 2.0)


def test_smallest_containing_outline_prefers_nested_region() -> None:
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    inner = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0), (3.0, 3.0)]
    assert smallest_containing_outline(["outer", "inner"], [outer, inner], (5.0, 5.0)) == "inner"
    assert smallest_containing_outline(["outer", "inner"], [outer, inner], (20.0, 20.0)) is None
