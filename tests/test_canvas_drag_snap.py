"""Drag/resize edge snapping on documents with thousands of edges."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simple_stipple.canvas.constants import SNAP_DIST
from simple_stipple.canvas.snap import SnapEngine
from simple_stipple.core.document.model import EntityRecord


def _large_document_host(edge_half_length: float) -> SimpleNamespace:
    # 5000 short segments far from the pointer: well above the old 4000-edge
    # cutoff that silently disabled edge snapping document-wide.
    clutter = [
        EntityRecord(points=[(1000.0 + i * 3.0, 1000.0), (1001.0 + i * 3.0, 1000.0)])
        for i in range(5000)
    ]
    near_edge = EntityRecord(
        id="edge", points=[(-edge_half_length, 10.0), (edge_half_length, 10.0)]
    )
    dragged = EntityRecord(id="dragged", points=[(0.0, 0.0), (4.0, 0.0)])
    return SimpleNamespace(
        _entities=[*clutter, near_edge, dragged],
        _is_poly_closed=lambda _points: False,
        _scale=1.0,
        _sel={"dragged"},
        _grid_snap=False,
        _grid_spacing=5.0,
        _guides=[],
        _move_start_pts=list(dragged.points),
    )


# 80 world units spans a few index cells; 20000 exceeds the per-segment cell
# budget and exercises the always-checked long-segment list. The pointer stays
# well clear of the edge's midpoint and endpoints so only the edge qualifies.
@pytest.mark.parametrize("edge_half_length", [40.0, 10000.0])
def test_resize_handle_snaps_to_nearby_edge_on_large_document(edge_half_length: float) -> None:
    engine = SnapEngine(_large_document_host(edge_half_length))

    near = 10.0 - SNAP_DIST / 2.0
    assert engine._resize_handle_snap_adjust(12.0, near) == (12.0, 10.0, "edge")
    assert engine._resize_handle_snap_adjust(12.0, 10.0 - SNAP_DIST * 3.0) is None


@pytest.mark.parametrize("edge_half_length", [40.0, 10000.0])
def test_selection_drag_snaps_to_nearby_edge_on_large_document(edge_half_length: float) -> None:
    engine = SnapEngine(_large_document_host(edge_half_length))

    # Drag the horizontal selection up to just short of the edge at y=10.
    adjust = engine._object_snap_adjust(10.0, 10.0 - SNAP_DIST / 2.0)

    assert adjust is not None
    adj_dx, adj_dy, indicators = adjust
    assert adj_dx == 0.0
    assert adj_dy == pytest.approx(SNAP_DIST / 2.0)
    assert {kind for _target, kind, _dragged in indicators} == {"edge"}
