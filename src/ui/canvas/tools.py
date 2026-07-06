"""Canvas interaction tools.

Each mode's mouse behavior lives in a Tool object with press/move/release/
double_click hooks operating on the shared view state (``self.v``). The
view's Qt event handlers keep only the mode-independent plumbing (panning,
right-click, gizmo/overlay priority) and dispatch to the active tool —
replacing the former 350–500-line if/elif mode ladders.

Tools are stateless strategies: all interaction state stays on the view so
undo snapshots, session persistence, and cross-tool handoffs keep working
unchanged.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPen

from src.backend.geometry.arc import arc_from_center_start_end, arc_from_three_points
from src.constants import DRAG_THRESH

if TYPE_CHECKING:
    from src.ui.canvas.view import PolylineView


class CanvasTool:
    """Base tool: hooks return True when the event was fully handled."""

    def __init__(self, view: PolylineView) -> None:
        self.v = view

    def press(self, event: QMouseEvent) -> bool:
        return False

    def move(self, event: QMouseEvent) -> bool:
        return False

    def release(self, event: QMouseEvent) -> bool:
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        return False

    def key(self, event) -> bool:
        """Tool-specific key handling; runs before the command registry."""
        return False

    def paint_overlay(self, painter) -> None:
        """Draw tool-specific overlays on top of the rendered canvas."""


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


def apply_edit_drag(v: PolylineView, event: QMouseEvent) -> bool:
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
    drag_snap_result = v._resolve_drag_snap(
        pos.x(),
        pos.y(),
        wx,
        wy,
        allow_polyline=allow_snap,
        allow_grid=allow_snap,
        exclude_vertices=v._edit_drag_targets,
        exclude_segments=v._immediate_segments_for_vertices(v._edit_drag_targets),
        reference_point=v._entities[v._edit_poly].points[v._edit_vert],
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

    cur_pt = v._entities[v._edit_poly].points[v._edit_vert]
    if abs(cur_pt[0] - snap_wx) > 1e-9 or abs(cur_pt[1] - snap_wy) > 1e-9:
        if not v._edit_undo_pushed:
            v._push_undo()
            v._edit_undo_pushed = True
        v._edit_drag_moved = True

    v._apply_edit_vertex_position(snap_wx, snap_wy)
    v._cursor_wx, v._cursor_wy = snap_wx, snap_wy
    if snap_type:
        v._hover_snap = (snap_wx, snap_wy)
        v._hover_snap_type = snap_type
    v._redraw()
    return True


def release_edit_drag(v: PolylineView) -> None:
    """Finish a vertex drag (Edit mode or select-mode direct editing)."""
    v._edit_dragging = False
    v._edit_linked_verts = set()
    v._edit_drag_targets = set()
    v._edit_drag_anchor = None
    v._redraw()
    v._notify()
    if v._edit_drag_moved:
        v._fire_poly_change()
    v._edit_drag_moved = False
    v._edit_undo_pushed = False


class MeasureTool(CanvasTool):
    """Two-click distance/angle measurement overlay (any mode)."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._measure_locked:
            # Click again to reset measurement
            v._measure_locked = False
            v._measure_anchor = None
            v._measure_hover = None
            v._measure_end = None
            v._measure_snapped_a = False
            v._measure_snapped_b = False
            v._dismiss_measure_edit()
            v._redraw()
            return True
        wx, wy = v._c2w(pos.x(), pos.y())
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._measure_anchor,
        )
        snapped = snap_result is not None
        if snapped:
            wx, wy = snap_result[0], snap_result[1]
        # Angle snap with Shift
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift and v._measure_anchor is not None:
            wx, wy = v._angle_snap(*v._measure_anchor, wx, wy)
        if v._measure_anchor is None:
            v._measure_anchor = (wx, wy)
            v._measure_hover = (wx, wy)
            v._measure_snapped_a = snapped
        else:
            v._measure_end = (wx, wy)
            v._measure_hover = (wx, wy)
            v._measure_snapped_b = snapped
            v._measure_locked = True
            v._show_measure_edit()
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._measure_locked:
            return True
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
        )
        if v._measure_anchor is None:
            # Pre-first-click: just track snap indicator
            v._measure_hover_pre = (
                (snap_result[0], snap_result[1]) if snap_result else None
            )
            if snap_result is not None:
                v._cursor_wx, v._cursor_wy = snap_result[0], snap_result[1]
                v._hover_snap = (snap_result[0], snap_result[1])
                v._hover_snap_type = snap_result[2]
            v._redraw()
            return True
        # After anchor placed — compute hover with snap + optional angle snap
        if snap_result is not None:
            mx, my = snap_result[0], snap_result[1]
            v._hover_snap = (mx, my)
            v._hover_snap_type = snap_result[2]
        else:
            mx, my = wx, wy
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            mx, my = v._angle_snap(*v._measure_anchor, mx, my)
        v._measure_hover = (mx, my)
        v._cursor_wx, v._cursor_wy = mx, my
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True


class DimensionTool(CanvasTool):
    """Persistent drafting-dimension placement (any mode): click p1, click
    p2 (same snap + Shift-angle-snap as MeasureTool), then click again to
    set how far the dimension line sits from the measured segment and
    finalize it into ``v._dimensions`` — a reference overlay like ruler
    guides (view-state only, never undo-tracked or DXF-exported)."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())

        if v._dim_pending_p1 is not None and v._dim_pending_p2 is not None:
            pending = {
                "p1": v._dim_pending_p1,
                "p2": v._dim_pending_p2,
                "offset": v._dim_pending_offset,
            }
            v._dimensions.append(
                {
                    "p1": v._dim_pending_p1,
                    "p2": v._dim_pending_p2,
                    "offset": v._dimension_offset_at(pending, wx, wy),
                }
            )
            v._dim_pending_p1 = None
            v._dim_pending_p2 = None
            v._notify()
            v._redraw()
            return True

        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._dim_pending_p1,
        )
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift and v._dim_pending_p1 is not None:
            wx, wy = v._angle_snap(*v._dim_pending_p1, wx, wy)

        if v._dim_pending_p1 is None:
            v._dim_pending_p1 = (wx, wy)
        else:
            v._dim_pending_p2 = (wx, wy)
            v._dim_pending_offset = 5.0
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._dim_pending_p1 is not None and v._dim_pending_p2 is not None:
            # Third stage: live-preview the offset as the cursor moves.
            pending = {
                "p1": v._dim_pending_p1,
                "p2": v._dim_pending_p2,
                "offset": v._dim_pending_offset,
            }
            v._dim_pending_offset = v._dimension_offset_at(pending, wx, wy)
            v._redraw()
            return True

        # Placing p1 or p2: resolve + preview the same snap press() will
        # commit, so the rubber-band line (and the snap-ring indicator)
        # show exactly where the click will land — matching MeasureTool.
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._dim_pending_p1,
        )
        if snap_result is not None:
            mx, my = snap_result[0], snap_result[1]
            v._hover_snap = (mx, my)
            v._hover_snap_type = snap_result[2]
        else:
            mx, my = wx, wy
            v._hover_snap = None
            v._hover_snap_type = None
        if (
            bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and v._dim_pending_p1 is not None
        ):
            mx, my = v._angle_snap(*v._dim_pending_p1, mx, my)
        v._cursor_wx, v._cursor_wy = mx, my
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True


class EditTool(CanvasTool):
    """Vertex editing: drag vertices, band-select vertices, insert on edge."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        hit = v._find_nearest_vertex(pos.x(), pos.y())

        if shift:
            if hit is not None:
                if hit in v._edit_selected_verts:
                    v._edit_selected_verts.discard(hit)
                else:
                    v._edit_selected_verts.add(hit)
                v._redraw()
                return True
            v._shift_drag = True
            v._band_start = pos
            v._lmb_prev = pos
            v._lmb_press = None
            return True

        if hit is not None:
            pi, vi = hit
            if v._is_locked(pi):
                return True
            if pi < 0 or pi >= len(v._entities):
                return True
            if vi < 0 or vi >= len(v._entities[pi].points):
                return True
            v._edit_poly = pi
            v._edit_vert = vi
            v._edit_dragging = True
            v._edit_drag_moved = False
            v._edit_undo_pushed = False
            v._edit_drag_anchor = v._entities[pi].points[vi]
            if hit in v._edit_selected_verts and len(v._edit_selected_verts) > 1:
                v._edit_drag_targets = set(v._edit_selected_verts)
            else:
                v._edit_selected_verts = {hit}
                v._edit_drag_targets = v._linked_vertices(pi, vi)
            v._edit_linked_verts = set(v._edit_drag_targets)
            v._redraw()
            return True

        if v._edit_selected_verts:
            v._edit_selected_verts.clear()
            v._redraw()
        v._lmb_press = pos
        v._lmb_prev = pos
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if apply_edit_drag(v, event):
            return True
        if v._shift_drag and v._band_start:
            v._lmb_prev = pos
            v._redraw()
            return True
        old_hover = v._hover_vert
        v._hover_vert = v._find_nearest_vertex(pos.x(), pos.y())
        if v._hover_vert != old_hover:
            v._update_cursor()
            v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._shift_drag and v._band_start:
            bx, by = v._band_start.x(), v._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            v._select_edit_vertices_in_rect(x1c, y1c, x2c, y2c, additive=True)
            v._shift_drag = False
            v._band_start = None
            v._lmb_prev = None
            v._redraw()
            return True
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        hit = v._find_nearest_edge(pos.x(), pos.y())
        if hit is not None:
            pi, seg_idx, pt = hit
            if pi < 0 or pi >= len(v._entities):
                return True
            poly = v._entities[pi].points
            if seg_idx + 1 > len(poly):
                return True
            v._push_undo()
            poly.insert(seg_idx + 1, pt)
            v._redraw()
            v._notify()
            v._fire_poly_change()
        return True


class DrawTool(CanvasTool):
    """Point placement for polylines, primitives, arcs, splines, text, and
    the bezier pen — bezier is a ``_draw_primitive`` like any other, not a
    separate mode, so switching to/from it doesn't hide the draw sidebar or
    require its own mode-transition bookkeeping."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            return self._bezier_press(event)
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())

        if v._draw_snap is not None:
            wx, wy = v._draw_snap

        if v._draw_primitive == "text":
            # Click chooses the anchor; the dialog does the rest.
            v.prompt_add_text(wx, wy)
            v.set_mode("select")
            return True

        if v._draw_primitive in {"rectangle", "circle", "ellipse", "polygon", "slot"}:
            if not v._draw_shape_preview_active:
                v._draw_shape_preview_active = True
                v._draw_shape_anchor_w = (wx, wy)
                v._draw_shape_cursor_w = (wx, wy)
                if v._draw_shape_w_edit is not None:
                    v._draw_shape_w_edit.setFocus()
                    v._draw_shape_w_edit.selectAll()
            else:
                v._draw_shape_cursor_w = (wx, wy)
                v._commit_shape_preview()
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        if v._draw_primitive == "line":
            if not v._draw_pts:
                v._draw_pts = [(wx, wy)]
                v._draw_point_snap_types = [v._draw_snap_type or None]
                v._refresh_draw_sidebar_state()
                v._redraw()
                return True
            p0 = v._draw_pts[0]
            v._draw_pts = [p0, (wx, wy)]
            first_snap = (
                v._draw_point_snap_types[0] if v._draw_point_snap_types else None
            )
            v._draw_point_snap_types = [first_snap, v._draw_snap_type or None]
            v._finish_draw(close=False)
            v._draw_pts.clear()
            v._draw_point_snap_types.clear()
            v._refresh_draw_sidebar_state()
            return True

        if v._draw_primitive == "arc":
            v._draw_arc_pts.append((wx, wy))
            if len(v._draw_arc_pts) >= 3:
                p0, p1, p2 = v._draw_arc_pts[:3]
                if v._draw_arc_mode == "center-start-end":
                    arc_poly = arc_from_center_start_end(p0, p1, p2, 24)
                else:
                    arc_poly = arc_from_three_points(p0, p1, p2, 24)
                v._commit_drawn_polyline(
                    arc_poly,
                    primitive="arc",
                    created_flash="Arc created",
                )
                v._draw_arc_pts.clear()
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        if v._draw_primitive == "spline":
            v._draw_pts.append((wx, wy))
            v._draw_point_snap_types.append(v._draw_snap_type or None)
            if len(v._draw_pts) == 1:
                v._show_dim_inputs()
            v._dim_distance_dirty = False
            v._dim_angle_dirty = False
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        # B. If dim inputs have user-typed values, compute point from those
        if v._draw_pts and (v._dim_distance_dirty or v._dim_angle_dirty):
            v._apply_dim_input()
            # Show dim inputs again for the next segment
            v._show_dim_inputs()
            return True
        # Apply H/V constraint to the placed point
        if v._draw_constraint == "H" and v._draw_pts:
            wy = v._draw_pts[-1][1]
        elif v._draw_constraint == "V" and v._draw_pts:
            wx = v._draw_pts[-1][0]
        # Close polygon when clicking near start point
        if v._is_near_start():
            v._finish_draw(close=True)
            return True
        # Connect to existing polyline endpoint when starting a new draw
        if not v._draw_pts:
            endpoint_snap = v._find_nearest_endpoint(pos.x(), pos.y())
            if endpoint_snap is not None:
                wx, wy = endpoint_snap
                v._draw_snap_type = "vertex"
        v._draw_pts.append((wx, wy))
        v._draw_point_snap_types.append(v._draw_snap_type or None)
        # B. Show dim inputs after first point is placed
        if len(v._draw_pts) == 1:
            v._show_dim_inputs()
        # Reset dirty flags for the new segment
        v._dim_distance_dirty = False
        v._dim_angle_dirty = False
        v._refresh_draw_sidebar_state()
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            return self._bezier_move(event)
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._draw_shape_preview_active:
            v._draw_shape_cursor_w = (wx, wy)
            v._cursor_wx = wx
            v._cursor_wy = wy
            v._update_shape_size_fields_from_preview()
            v._redraw()
            return True
        # 1. Resolve snap target
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
        )
        if snap_result is not None:
            v._draw_snap = (snap_result[0], snap_result[1])
            v._draw_snap_type = snap_result[2]
        else:
            v._draw_snap = None
            v._draw_snap_type = None

        # 2. Determine effective position (snap or raw cursor)
        eff_x = v._draw_snap[0] if v._draw_snap else wx
        eff_y = v._draw_snap[1] if v._draw_snap else wy

        # 3. Angle snap with Shift
        shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift_held and v._draw_pts:
            anchor = v._draw_pts[-1]
            eff_x, eff_y = v._angle_snap(anchor[0], anchor[1], eff_x, eff_y)
            v._draw_snap = (eff_x, eff_y)
            v._angle_snap_active = True
        else:
            v._angle_snap_active = False

        # 4. Explicit draw constraint locks, then fallback auto-detection.
        #    A user-typed angle takes highest precedence: the segment locks
        #    to that ray with live feedback, and the pointer sets the length.
        v._draw_constraint = None
        typed_angle = v._typed_draw_angle()
        if v._draw_pts and typed_angle is not None:
            last_wx, last_wy = v._draw_pts[-1]
            ar = math.radians(typed_angle)
            dirx, diry = math.cos(ar), math.sin(ar)
            typed_dist = v._typed_draw_distance()
            if typed_dist is not None:
                length = typed_dist
            else:
                proj = (eff_x - last_wx) * dirx + (eff_y - last_wy) * diry
                length = max(0.0, proj)
            eff_x = last_wx + dirx * length
            eff_y = last_wy + diry * length
            v._draw_snap = (eff_x, eff_y)
            v._angle_snap_active = True
            v._draw_constraint = f"∠{typed_angle:g}°"
        elif v._draw_pts:
            last_wx, last_wy = v._draw_pts[-1]
            if v._draw_constraint_lock == "H":
                v._draw_constraint = "H"
                eff_y = last_wy
                if v._draw_snap is not None:
                    v._draw_snap = (v._draw_snap[0], last_wy)
            elif v._draw_constraint_lock == "V":
                v._draw_constraint = "V"
                eff_x = last_wx
                if v._draw_snap is not None:
                    v._draw_snap = (last_wx, v._draw_snap[1])
            elif v._draw_constraint_lock == "45":
                v._draw_constraint = "45"
                eff_x, eff_y = v._angle_snap(last_wx, last_wy, eff_x, eff_y)
                v._draw_snap = (eff_x, eff_y)
                v._angle_snap_active = True
            else:
                seg_dx = eff_x - last_wx
                seg_dy = eff_y - last_wy
                seg_dist = math.hypot(seg_dx, seg_dy)
                if seg_dist > 1e-9:
                    seg_angle = math.degrees(math.atan2(seg_dy, seg_dx)) % 360
                    if seg_angle < 3 or seg_angle > 357 or (177 < seg_angle < 183):
                        v._draw_constraint = "H"
                        eff_y = last_wy
                        if v._draw_snap is not None:
                            v._draw_snap = (v._draw_snap[0], last_wy)
                    elif 87 < seg_angle < 93 or 267 < seg_angle < 273:
                        v._draw_constraint = "V"
                        eff_x = last_wx
                        if v._draw_snap is not None:
                            v._draw_snap = (last_wx, v._draw_snap[1])

        # 5. Update cursor to final effective position (all modifications applied)
        v._cursor_wx = eff_x
        v._cursor_wy = eff_y

        # 6. Update dimension HUD position and values
        if v._draw_pts:
            last_wx, last_wy = v._draw_pts[-1]
            eff_wx = v._cursor_wx if v._cursor_wx is not None else wx
            eff_wy = v._cursor_wy if v._cursor_wy is not None else wy
            seg_dist = math.hypot(eff_wx - last_wx, eff_wy - last_wy)
            seg_angle = math.degrees(math.atan2(eff_wy - last_wy, eff_wx - last_wx))
            cur_cx, cur_cy = v._w2c(eff_wx, eff_wy)
            v._update_dim_positions(cur_cx, cur_cy)
            v._update_dim_values(seg_dist, seg_angle)

        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        if self.v._draw_primitive == "bezier":
            return self._bezier_release(event)
        return True

    def double_click(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            v._finish_pen()
            return True
        # Double-click finishes and closes the polygon (Fusion 360 behavior)
        if len(v._draw_pts) >= 3:
            v._finish_draw(close=True)
        else:
            v._finish_draw()
        return True

    def key(self, event) -> bool:
        if self.v._draw_primitive != "bezier":
            return False
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.v._finish_pen()
            return True
        return False

    def paint_overlay(self, painter) -> None:
        if self.v._draw_primitive == "bezier":
            self._paint_bezier_overlay(painter)

    # ── Bezier pen: click places a corner anchor (straight segments in/out);
    # click-and-drag places a smooth anchor with a symmetric tangent handle
    # sized by the drag vector. Enter/double-click finalizes the curve as a
    # ``kind="bezier"`` entity; Escape (via the shared draw-mode Escape
    # handler) discards the in-progress curve. ──────────────────────────────

    def _bezier_press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(), pos.y(), wx, wy, allow_polyline=allow_snap, allow_grid=allow_snap
        )
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        v._pen_pts.append((wx, wy))
        v._pen_tangents.append((0.0, 0.0))
        v._pen_dragging = True
        v._pen_press_screen = (pos.x(), pos.y())
        v._redraw()
        return True

    def _bezier_move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._pen_dragging and v._pen_pts:
            anchor = v._pen_pts[-1]
            v._pen_tangents[-1] = (wx - anchor[0], wy - anchor[1])
        v._cursor_wx, v._cursor_wy = wx, wy
        v._redraw()
        return True

    def _bezier_release(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._pen_dragging and v._pen_press_screen is not None and v._pen_tangents:
            pos = event.position()
            dx = pos.x() - v._pen_press_screen[0]
            dy = pos.y() - v._pen_press_screen[1]
            if math.hypot(dx, dy) < DRAG_THRESH:
                # No real drag happened: a plain click makes a corner anchor.
                v._pen_tangents[-1] = (0.0, 0.0)
        v._pen_dragging = False
        v._pen_press_screen = None
        v._redraw()
        return True

    def _paint_bezier_overlay(self, painter) -> None:
        from src.backend.geometry.spline import build_bezier_poly

        v = self.v
        pts = v._pen_pts
        if not pts:
            return
        pen_color = QColor("#2f81f7")
        painter.setPen(QPen(pen_color, 1.6))
        if len(pts) >= 2:
            preview = build_bezier_poly(pts, v._pen_tangents, segments=16)
            path_pts = [v._w2c(*p) for p in preview]
            for a, b in zip(path_pts, path_pts[1:]):
                painter.drawLine(QPointF(*a), QPointF(*b))
        # Rubber-band segment from the last anchor to the live cursor.
        if v._cursor_wx is not None and v._cursor_wy is not None:
            last = v._w2c(*pts[-1])
            cur = v._w2c(v._cursor_wx, v._cursor_wy)
            painter.setPen(QPen(pen_color, 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(*last), QPointF(*cur))
        # Anchors + tangent handles.
        for i, (ax, ay) in enumerate(pts):
            sx, sy = v._w2c(ax, ay)
            painter.setPen(QPen(pen_color, 1.4))
            painter.setBrush(QColor("#0d1117"))
            painter.drawEllipse(QPointF(sx, sy), 3.5, 3.5)
            tx, ty = v._pen_tangents[i]
            if abs(tx) > 1e-9 or abs(ty) > 1e-9:
                hx, hy = v._w2c(ax + tx, ay + ty)
                hx2, hy2 = v._w2c(ax - tx, ay - ty)
                painter.setPen(QPen(QColor("#56d4dd"), 1.0))
                painter.drawLine(QPointF(hx2, hy2), QPointF(hx, hy))
                painter.setBrush(QColor("#56d4dd"))
                painter.drawEllipse(QPointF(hx, hy), 2.5, 2.5)
                painter.drawEllipse(QPointF(hx2, hy2), 2.5, 2.5)


class TrimExtendTool(CanvasTool):
    """Trim (click the portion to remove) / Extend (click near an open
    end). Both cut/extend to the nearest intersection with other shapes."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._mode == "trim":
            v.trim_at(pos.x(), pos.y())
        else:
            v.extend_at(pos.x(), pos.y())
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        hover = v._find_poly_at(pos.x(), pos.y())
        if hover != v._hover_poly:
            v._hover_poly = hover
            v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True


class SelectTool(CanvasTool):
    """Selection, box select, drag-move, direct vertex editing, gizmos."""

    def press_overlays(self, event: QMouseEvent) -> bool:
        """Selection badges and the transform gizmo take priority over
        everything else (including measure mode)."""
        from PySide6.QtCore import QPointF

        v = self.v
        pos = event.position()
        pt = QPointF(pos.x(), pos.y())
        for axis, rect in v._sel_badge_axes():
            if rect.contains(pt):
                v._show_sel_dim_editor(axis, rect)
                return True
        wx0, wy0 = v._c2w(pos.x(), pos.y())
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        for name, rect in v._gizmo_handle_rects:
            if rect.contains(pt) and v._start_gizmo_drag(
                f"scale-{name}", wx0, wy0, from_center=alt
            ):
                v._redraw()
                return True
        if (
            v._gizmo_rotate_rect is not None
            and v._gizmo_rotate_rect.contains(pt)
            and v._start_gizmo_drag("rotate", wx0, wy0)
        ):
            v._redraw()
            return True
        if (
            v._gizmo_scale_rect is not None
            and v._gizmo_scale_rect.contains(pt)
            and v._start_gizmo_drag("scale", wx0, wy0)
        ):
            v._redraw()
            return True
        if v._gizmo_move_rect is not None and v._gizmo_move_rect.contains(pt):
            # Dedicated move handle — always drags the whole selection as a
            # unit, bypassing per-shape hit-testing (handy for thin/tiny or
            # overlapping shapes that are awkward to grab directly).
            v._lmb_press = pos
            v._lmb_prev = pos
            v._lmb_target = None
            v._move_origin = (wx0, wy0)
            v._move_dragging = False
            v._move_undo_pushed = False
            v._move_snap_exclude_vertices = set()
            v._move_snap_exclude_segments = set()
            v._redraw()
            return True
        return False

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        v._shift_drag = False
        v._band_start = None
        v._band_additive = False
        v._lmb_press = pos
        v._lmb_prev = pos
        target = v._find_poly_at(pos.x(), pos.y())
        was_selected_before = target in v._sel if target is not None else False
        v._lmb_target = target

        if v._selectable and target is None:
            # Default drag behavior in select mode is box selection.
            v._shift_drag = True
            v._band_start = pos
            v._band_additive = shift
            v._lmb_press = None
            v._lmb_prev = pos
            v._lmb_target = None
            return True

        # Select-mode direct vertex editing: single-click selects the segment,
        # shows its points, and allows immediate vertex drag.
        if target is not None:
            ctrl = bool(
                event.modifiers()
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )
            shift_toggle = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            gid = v._group_of(target)
            if gid is not None:
                members = {
                    i
                    for i, e in enumerate(v._entities)
                    if e.group == gid and not e.hidden
                }
                if ctrl or shift_toggle:
                    # Toggle the whole group as one unit.
                    if members <= v._sel:
                        v._sel -= members
                    else:
                        v._sel |= members
                elif target not in v._sel:
                    v._sel = members
                # else: already selected — preserve current selection for group move
            elif ctrl or shift_toggle:
                v._sel.add(target)
            elif target not in v._sel:
                v._sel = {target}
            v._notify()
            hit = v._find_nearest_vertex(pos.x(), pos.y())
            target_kind = v._entities[target].kind
            # Parametric shapes never vertex-drag in select mode: every rim
            # point of a circle/ellipse is a "vertex", which made plain
            # drag-to-move nearly impossible. Resize via the frame handles
            # or the properties panel; vertex editing lives in Edit mode.
            if (
                hit is not None
                and hit[0] == target
                and was_selected_before
                and target_kind
                not in {"arc", "circle", "ellipse", "rectangle", "polygon", "slot"}
                and not v._is_locked(target)
            ):
                pi, vi = hit
                if pi < 0 or pi >= len(v._entities):
                    return True
                if vi < 0 or vi >= len(v._entities[pi].points):
                    return True
                v._edit_poly = pi
                v._edit_vert = vi
                v._edit_dragging = True
                v._edit_drag_moved = False
                v._edit_undo_pushed = False
                v._edit_drag_anchor = v._entities[pi].points[vi]
                v._edit_selected_verts = {hit}
                v._edit_drag_targets = v._linked_vertices(pi, vi)
                v._edit_linked_verts = set(v._edit_drag_targets)
                v._redraw()
                return True
        # Prepare for move if clicking on an already-selected poly
        if target is not None and target in v._sel:
            wx, wy = v._c2w(pos.x(), pos.y())
            v._move_origin = (wx, wy)
            v._move_dragging = False
            v._move_undo_pushed = False
            v._move_snap_exclude_vertices = set()
            v._move_snap_exclude_segments = set()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())

        if apply_edit_drag(v, event):
            return True

        if v._sel:
            old_hover = v._hover_vert
            hit = v._find_nearest_vertex(pos.x(), pos.y())
            if hit is not None and hit[0] in v._sel:
                v._hover_vert = hit
            else:
                v._hover_vert = None
            if v._hover_vert != old_hover:
                v._update_cursor()
                v._redraw()
                return True
        elif v._hover_vert is not None:
            v._hover_vert = None
            v._update_cursor()

        if event.buttons() & Qt.MouseButton.LeftButton:
            if v._shift_drag and v._band_start:
                v._lmb_prev = pos
                v._redraw()
                return True
            # Move selected shapes. Snapping works on the selection's own
            # geometry: the shape's vertices snap to static vertices/edges/
            # grid/guides regardless of where the user grabbed it.
            if v._move_origin is not None and v._lmb_press is not None:
                dx_px = pos.x() - v._lmb_press.x()
                dy_px = pos.y() - v._lmb_press.y()
                if not v._move_dragging and (
                    abs(dx_px) > DRAG_THRESH or abs(dy_px) > DRAG_THRESH
                ):
                    v._move_dragging = True
                    v._move_anchor_w = v._move_origin
                    v._move_applied_w = (0.0, 0.0)
                    v._move_start_pts = v._moving_sample_points()
                if v._move_dragging:
                    # Invariant: _move_dragging only ever becomes True right
                    # above, together with _move_anchor_w — so it's always
                    # set by the time we get here.
                    assert v._move_anchor_w is not None
                    if not v._move_undo_pushed:
                        v._push_undo()
                        v._move_undo_pushed = True
                    new_wx, new_wy = v._c2w(pos.x(), pos.y())
                    raw_dx = new_wx - v._move_anchor_w[0]
                    raw_dy = new_wy - v._move_anchor_w[1]
                    snap_indicators: list[
                        tuple[tuple[float, float], str, tuple[float, float]]
                    ] = []
                    allow_snap = not bool(
                        event.modifiers() & Qt.KeyboardModifier.AltModifier
                    )
                    if allow_snap:
                        adj = v._object_snap_adjust(raw_dx, raw_dy)
                        if adj is not None:
                            raw_dx += adj[0]
                            raw_dy += adj[1]
                            snap_indicators = adj[2]
                    step_dx = raw_dx - v._move_applied_w[0]
                    step_dy = raw_dy - v._move_applied_w[1]
                    if abs(step_dx) > 1e-12 or abs(step_dy) > 1e-12:
                        for idx in v._sel:
                            if v._is_locked(idx):
                                continue
                            if idx < 0 or idx >= len(v._entities):
                                continue
                            v._entities[idx].points = [
                                (x + step_dx, y + step_dy)
                                for x, y in v._entities[idx].points
                            ]
                            v._transform_entity_meta(
                                idx,
                                center=(0.0, 0.0),
                                kind=v._entities[idx].kind,
                                meta=v._entities[idx].meta,
                                transform="translate",
                                dx=step_dx,
                                dy=step_dy,
                            )
                        v._move_applied_w = (raw_dx, raw_dy)
                    v._cursor_wx, v._cursor_wy = new_wx, new_wy
                    v._hover_snap_multi = snap_indicators
                    if snap_indicators:
                        v._hover_snap = snap_indicators[0][0]
                        v._hover_snap_type = snap_indicators[0][1]
                    v._redraw()
                    return True
            if v._lmb_prev:
                v._ox += pos.x() - v._lmb_prev.x()
                v._oy += pos.y() - v._lmb_prev.y()
                v._lmb_prev = pos
                v._redraw()
            return True
        # Passive hover: pre-highlight the polyline a click would select.
        hover = v._find_poly_at(pos.x(), pos.y()) if v._selectable else None
        if hover != v._hover_poly:
            v._hover_poly = hover
            v._redraw()
            return True
        # Only repaint if the displayed cursor-position text
        # (2 decimal places) actually changed.
        _prev_cx = v._prev_cursor_display
        _cur_cx = (round(wx, 2), round(wy, 2))
        if _prev_cx == _cur_cx:
            return True
        v._prev_cursor_display = _cur_cx
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._shift_drag and v._band_start and v._selectable:
            bx, by = v._band_start.x(), v._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            # CAD marquee semantics: dragging left→right selects only fully
            # enclosed shapes (window); right→left selects anything the box
            # touches (crossing).
            window = pos.x() >= bx
            if not v._band_additive:
                v._sel.clear()
            picked: set[int] = set()
            for idx, e in enumerate(v._entities):
                if not v._entity_selectable(idx):
                    continue
                poly = e.points
                if not poly:
                    continue
                pts_c = [v._w2c(x, y) for x, y in poly]
                inside = [x1c <= cx <= x2c and y1c <= cy <= y2c for cx, cy in pts_c]
                if window:
                    if all(inside):
                        picked.add(idx)
                    continue
                if any(inside):
                    picked.add(idx)
                    continue
                n = len(pts_c)
                seg_count = n if v._is_poly_closed(poly) else n - 1
                for i in range(seg_count):
                    if _seg_hits_rect(pts_c[i], pts_c[(i + 1) % n], x1c, y1c, x2c, y2c):
                        picked.add(idx)
                        break
            # A marquee that catches part of a group selects the whole group.
            gids = {v._entities[i].group for i in picked} - {None}
            if gids:
                for i, e in enumerate(v._entities):
                    if e.group in gids and v._entity_selectable(i):
                        picked.add(i)
            v._sel |= picked
            v._redraw()
            v._notify()
            v._shift_drag = False
            v._band_start = None
            v._band_additive = False
            return True

        if v._move_dragging:
            # Move completed — already applied incrementally
            v._move_dragging = False
            v._move_origin = None
            v._move_undo_pushed = False
            v._move_snap_exclude_vertices = set()
            v._move_snap_exclude_segments = set()
            v._lmb_press = None
            v._lmb_prev = None
            v._lmb_target = None
            v._redraw()
            v._notify()
            v._fire_poly_change()
            return True
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if not v._selectable:
            return True
        hit = v._find_poly_at(pos.x(), pos.y())
        if hit is not None and v.text_params_at(hit) is not None:
            v.prompt_edit_text(hit)
            return True
        if hit is not None:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift:
                v._sel = v._connected_poly_indices(hit)
                v._show_flash(f"Object selected ({len(v._sel)})", 800)
            else:
                v._sel = {hit}
            v._redraw()
            v._notify()
        elif v._entities:
            # Double-click on empty canvas = fit view.
            v.fit()
        return True
