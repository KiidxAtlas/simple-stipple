"""Outline-ID resolution when polylines are reordered."""

from src.ui.pages.pattern.services import PatternProcessingService


def test_sync_outline_ids_preserves_ids_across_reorder():
    pps = PatternProcessingService()
    old_polys = [
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)],
        [(10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 10.0)],
    ]
    old_ids = ["id_a", "id_b"]
    new_polys = [old_polys[1], old_polys[0]]
    assert pps.sync_outline_ids(new_polys, old_polys, old_ids) == ["id_b", "id_a"]
