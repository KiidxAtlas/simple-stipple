"""Pointer-drag state transitions shared by the select and edit tools."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent

if TYPE_CHECKING:
    from simple_stipple.canvas.view.main import CanvasView


def _seg_hits_rect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    rx1: float,
    ry1: float,
    rx2: float,
    ry2: float,
) -> bool:
    """Liang-Barsky segment/rect intersection (canvas coordinates)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, p1[0] - rx1),
        (dx, rx2 - p1[0]),
        (-dy, p1[1] - ry1),
        (dy, ry2 - p1[1]),
    ):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
                if r < t0:
                    return False
    return True


def apply_edit_drag(v: CanvasView, event: QMouseEvent) -> bool:
    """Shared vertex-drag update used by both Edit mode and select-mode
    direct vertex editing. Returns True when a drag consumed the event."""
    if not (v._edit_dragging and v._edit_poly is not None and v._edit_vert is not None):
        return False
    pos = event.position()
    wx, wy = v._c2w(pos.x(), pos.y())
    if v._shift_drag and v._band_start:
        v._lmb_prev = pos
        v._redraw()
        return True
    allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
    edit_entity = v._document.entity_for_id(v._edit_poly) if v._edit_poly is not None else None
    if edit_entity is None:
        return True
    drag_snap_result = v._resolve_drag_snap(
        pos.x(),
        pos.y(),
        wx,
        wy,
        allow_polyline=allow_snap,
        allow_grid=allow_snap,
        exclude_vertices=v._edit_drag_targets,
        exclude_segments=v._immediate_segments_for_vertices(v._edit_drag_targets),
        exclude_polys={v._edit_poly} if v._edit_poly is not None else set(),
        reference_point=edit_entity.points[v._edit_vert],
    )
    snap_wx, snap_wy = wx, wy
    snap_type = ""
    if drag_snap_result is not None:
        snap_wx, snap_wy, snap_type = drag_snap_result

    anchor_for_constraint = v._edit_drag_anchor

    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        anchor_pt = anchor_for_constraint
        if anchor_pt is not None:
            dx = snap_wx - anchor_pt[0]
            dy = snap_wy - anchor_pt[1]
            if abs(dx) >= abs(dy):
                snap_wy = anchor_pt[1]
                snap_type = "horizontal"
            else:
                snap_wx = anchor_pt[0]
                snap_type = "vertical"

    cur_pt = edit_entity.points[v._edit_vert]
    if abs(cur_pt[0] - snap_wx) > 1e-9 or abs(cur_pt[1] - snap_wy) > 1e-9:
        if not v._edit_undo_pushed:
            v._edit_command_snapshot = v._canvas_service.begin_preview()
            v._edit_undo_pushed = True
        v._edit_drag_moved = True

    v._apply_edit_vertex_position(snap_wx, snap_wy)
    v._cursor_wx, v._cursor_wy = snap_wx, snap_wy
    if snap_type:
        v._hover_snap = (snap_wx, snap_wy)
        v._hover_snap_type = snap_type
    v._redraw()
    return True


def release_edit_drag(v: CanvasView) -> None:
    """Finish a vertex drag (Edit mode or select-mode direct editing)."""
    v._edit_dragging = False
    v._edit_linked_verts = set()
    v._edit_drag_targets = set()
    v._edit_drag_anchor = None
    v._redraw()
    v._notify()
    if v._edit_drag_moved:
        v._canvas_service.commit_preview(v._edit_command_snapshot)
        v._fire_poly_change()
    v._edit_drag_moved = False
    v._edit_undo_pushed = False
    v._edit_command_snapshot = None


def start_bezier_handle_drag(v: CanvasView, hit: tuple[str, int, str]) -> bool:
    entity_id, _anchor_index, _side = hit
    entity = v._document.entity_for_id(entity_id)
    if entity is None or entity.locked:
        return False
    v._bezier_handle_drag = (entity_id, _anchor_index, _side)
    v._bezier_handle_drag_moved = False
    v._bezier_handle_undo_pushed = False
    v._redraw()
    return True


def apply_bezier_handle_drag(v: CanvasView, event: QMouseEvent) -> bool:
    hit = v._bezier_handle_drag
    if hit is None:
        return False
    entity_id, anchor_index, side = hit
    pos = event.position()
    wx, wy = v._c2w(pos.x(), pos.y())
    entity = v._document.entity_for_id(entity_id)
    if entity is None:
        return False
    anchor = entity.points[anchor_index]
    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        wx, wy = v._angle_snap(*anchor, wx, wy)
    current = next(
        tip
        for vi, handle_side, tip in v._bezier_handles(entity_id)
        if vi == anchor_index and handle_side == side
    )
    if math.dist(current, (wx, wy)) > 1e-9:
        if not v._bezier_handle_undo_pushed:
            v._bezier_command_snapshot = v._canvas_service.begin_preview()
            v._bezier_handle_undo_pushed = True
        v._bezier_handle_drag_moved = True
        v._set_bezier_handle(
            entity_id,
            anchor_index,
            side,
            (wx, wy),
            break_pair=bool(event.modifiers() & Qt.KeyboardModifier.AltModifier),
        )
    v._redraw()
    return True


def release_bezier_handle_drag(v: CanvasView) -> None:
    moved = v._bezier_handle_drag_moved
    v._bezier_handle_drag = None
    v._bezier_handle_drag_moved = False
    v._bezier_handle_undo_pushed = False
    v._redraw()
    v._notify()
    if moved:
        v._canvas_service.commit_preview(v._bezier_command_snapshot)
        v._fire_poly_change()
    v._bezier_command_snapshot = None
