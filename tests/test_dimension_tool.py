"""Persistent dimension/annotation tool — reference-only overlay like guides:
view-state (saved/loaded), not undo-tracked, never DXF-exported.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.test_canvas_behavior import (  # noqa: E402
    click_world,
    key,
    make_view,
    move,
    square,
)


def _place_dimension(v, p1, p2, offset_click):
    v.toggle_dimension_mode()
    click_world(v, *p1)
    click_world(v, *p2)
    click_world(v, *offset_click)


def test_dimension_tool_places_a_persistent_annotation(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    assert len(v._dimensions) == 1
    dim = v._dimensions[0]
    assert dim["p1"] == pytest.approx((0.0, 0.0))
    assert dim["p2"] == pytest.approx((30.0, 0.0))
    assert dim["offset"] == pytest.approx(8.0, abs=0.5)
    # Placing doesn't leave the tool armed for another dimension.
    assert v._dim_pending_p1 is None
    assert v._dim_pending_p2 is None


def test_dimension_is_not_undo_tracked(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    v._duplicate_selected()  # a real, undo-tracked mutation
    assert v.poly_count == 2

    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    assert len(v._dimensions) == 1

    assert v.undo()  # undoes the duplicate, not the dimension
    assert v.poly_count == 1
    assert len(v._dimensions) == 1  # untouched


def test_escape_cancels_in_progress_dimension(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    assert v._dim_pending_p1 is not None
    key(v, Qt.Key.Key_Escape)
    assert v._dim_pending_p1 is None
    assert len(v._dimensions) == 0


def test_delete_removes_selected_dimension(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    v.toggle_dimension_mode()  # back to select mode
    v.set_mode("select")

    # Click on the offset dimension line (midpoint, 8mm above the segment).
    click_world(v, 15.0, 8.0)
    assert v._selected_dimension == 0

    key(v, Qt.Key.Key_Delete)
    assert v._dimensions == []
    assert v._selected_dimension is None


def test_backspace_also_removes_selected_dimension(qapp):
    """Key_Delete is routed through the command registry (edit.delete), but
    Backspace is intercepted earlier by its own dedicated handler — both
    must delete a selected dimension."""
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    v.toggle_dimension_mode()
    v.set_mode("select")

    click_world(v, 15.0, 8.0)
    assert v._selected_dimension == 0

    key(v, Qt.Key.Key_Backspace)
    assert v._dimensions == []
    assert v._selected_dimension is None


def test_dimensions_round_trip_through_view_state(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    state = v.get_view_state()
    assert "dimensions" in state

    v2 = make_view(qapp, [])
    v2.set_view_state(state)
    assert len(v2._dimensions) == 1
    assert v2._dimensions[0]["p1"] == pytest.approx((0.0, 0.0))
    assert v2._dimensions[0]["p2"] == pytest.approx((30.0, 0.0))


def test_dxf_writer_never_references_dimensions():
    import inspect

    from src.backend.dxf import io as dxf_io

    src = inspect.getsource(dxf_io)
    assert "_dimensions" not in src


def test_hovering_before_first_click_shows_a_snap_indicator(qapp):
    """Before p1 is placed, moving near a snappable vertex must set the
    generic hover-snap indicator (same one Draw/Measure use) — otherwise
    there is no visual feedback that a click here will snap at all."""
    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    cx, cy = v._w2c(10.2, 9.9)  # near the square's (10, 10) corner
    move(v, cx, cy)
    assert v._hover_snap == pytest.approx((10.0, 10.0))


def test_clicking_near_a_vertex_snaps_p1_and_p2_to_it(qapp):
    """The click itself must land exactly on the snap target, not the raw
    (slightly-off) cursor position — this is the actual placement, not just
    the preview."""
    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    click_world(v, 10.2, 9.9)  # near (10, 10) corner -> should snap exactly
    assert v._dim_pending_p1 == pytest.approx((10.0, 10.0))

    click_world(v, -0.2, 0.15)  # near (0, 0) corner -> should snap exactly
    assert v._dim_pending_p2 == pytest.approx((0.0, 0.0))


def test_alt_disables_snapping_while_placing(qapp):
    from PySide6.QtCore import Qt as _Qt

    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    cx, cy = v._w2c(10.2, 9.9)
    click_world_pos = (cx, cy)
    from tests.test_canvas_behavior import click

    click(v, *click_world_pos, mods=_Qt.KeyboardModifier.AltModifier)
    wx, wy = v._c2w(cx, cy)
    assert v._dim_pending_p1 == pytest.approx((wx, wy))
    assert v._dim_pending_p1 != pytest.approx((10.0, 10.0))
