"""Behavior coverage for Pattern's pure outline-state seam."""

from __future__ import annotations

from simple_stipple.core.patterns.outline_identity import resolve_outline_ids, sync_outline_ids
from simple_stipple.features.draft.model import DraftModel
from simple_stipple.features.pattern.model import PatternModel
from simple_stipple.features.pattern.outline_state import (
    canvas_records,
    normalize_outline_items,
    outline_bounds,
    reconcile_outline_ids,
    smallest_containing_outline,
)
from simple_stipple.features.trace.model import TraceModel


def test_core_outline_identity_reuses_and_resolves_current_geometry() -> None:
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    ids = sync_outline_ids([square], [square], ["outline-a"])
    assert ids == ["outline-a"]
    assert resolve_outline_ids(["missing", "outline-a"], ids, [square]) == [square]


def test_trace_model_resets_result_without_losing_selected_source() -> None:
    model = TraceModel(image_path="source.png", last_output="out.dxf", image_width_px=120)
    model.reset_result()
    assert model.image_path == "source.png"
    assert model.last_output is None
    assert model.image_width_px == 0


def test_draft_model_keeps_import_and_export_history_without_qt() -> None:
    model = DraftModel()
    model.record_import("source.dxf", "Imported 2 layers")
    model.record_export("result.dxf")
    assert (model.last_input_path, model.import_note, model.last_output_path) == (
        "source.dxf",
        "Imported 2 layers",
        "result.dxf",
    )


def test_pattern_model_invalidates_cached_preview_without_qt() -> None:
    model = PatternModel(preview_polys_cache=[[(0.0, 0.0), (1.0, 1.0)]], export_is_current=True)
    model.invalidate_preview()
    assert not model.export_is_current
    assert model.preview_is_stale
    assert model.preview_polys_cache == []


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
