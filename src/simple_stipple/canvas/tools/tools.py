# pyright: reportAttributeAccessIssue=false
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

from simple_stipple.canvas import commands as canvas_commands  # noqa: F401 - legacy patch seam
from simple_stipple.canvas.constants import DRAG_THRESH
from simple_stipple.canvas.tools.base import CanvasTool
from simple_stipple.canvas.tools.radial_menu import RadialMenuService  # noqa: F401 - facade
from simple_stipple.core.cad.geometry import (
    arc_from_center_start_end,
    arc_from_three_points,
)

if TYPE_CHECKING:
    pass


class ScaleTool(CanvasTool):
    """Two-click reference distance used to scale selected geometry."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._measure_locked:
            # Start a fresh reference after the previous scale is locked.
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
        if snap_result is not None:
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
            if math.hypot(wx - v._measure_anchor[0], wy - v._measure_anchor[1]) < 1e-6:
                v._show_flash(
                    "Reference length must be greater than zero · pick another point", 1800
                )
                v._measure_hover = (wx, wy)
                v._redraw()
                return True
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
            v._measure_hover_pre = (snap_result[0], snap_result[1]) if snap_result else None
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

        if v._draw_primitive in {
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
        } or v._draw_primitive in getattr(v, "_PROCEDURAL_QUICK_SHAPES", set()):
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
            first_snap = v._draw_point_snap_types[0] if v._draw_point_snap_types else None
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
            if v._is_near_start() and len(v._draw_pts) >= 3:
                v._finish_draw(close=True)
                return True
            v._draw_pts.append((wx, wy))
            v._draw_point_snap_types.append(v._draw_snap_type or None)
            # A spline point controls a curve; it is not the end of a
            # dimensionable line segment.
            v._dismiss_dim_inputs()
            v._snap_engine.clear_relationship_reference()
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
            endpoint_snap = v._hit_test.nearest_endpoint(pos.x(), pos.y())
            if endpoint_snap is not None:
                wx, wy = endpoint_snap
                v._draw_snap_type = "vertex"
        v._draw_pts.append((wx, wy))
        v._draw_point_snap_types.append(v._draw_snap_type or None)
        # A click commits the relationship for this segment. The next segment
        # starts a fresh acquisition instead of inheriting a stale source/type
        # lock from the segment that just finished.
        v._snap_engine.clear_relationship_reference()
        # The live length already rides the rubber band as a painted badge; the
        # boxed Length/Angle fields only appear when the user asks (Tab), so
        # mouse drawing stays uncluttered.
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
                wx, wy = snap_result[0], snap_result[1]
                v._draw_snap = (wx, wy)
                v._draw_snap_type = snap_result[2]
            else:
                v._draw_snap = None
                v._draw_snap_type = None
            v._draw_shape_cursor_w = (wx, wy)
            v._cursor_wx = wx
            v._cursor_wy = wy
            v._update_shape_size_fields_from_preview()
            v._redraw()
            return True
        # 1. Resolve snap target
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        spline_mode = v._draw_primitive == "spline"
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            # Keep explicit point/grid snapping for spline controls, but do
            # not apply line-only inference such as parallel or extension.
            reference_point=v._draw_pts[-1] if v._draw_pts and not spline_mode else None,
            allow_inferred=not spline_mode,
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

        if spline_mode:
            v._snap_engine.clear_relationship_reference()
            v._draw_constraint = None
            v._angle_snap_active = False
            v._cursor_wx = eff_x
            v._cursor_wy = eff_y
            v._dismiss_dim_inputs()
            v._redraw()
            return True

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
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            allow_inferred=False,
        )
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        if len(v._pen_pts) >= 3:
            start_cx, start_cy = v._w2c(*v._pen_pts[0])
            if math.hypot(pos.x() - start_cx, pos.y() - start_cy) <= 10.0:
                v._finish_pen(close=True)
                return True
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
        from simple_stipple.core.cad.geometry import build_bezier_poly

        v = self.v
        pts = v._pen_pts
        if not pts:
            return
        pen_color = QColor("#2f81f7")
        painter.setPen(QPen(pen_color, 1.6))
        if len(pts) >= 2:
            preview = build_bezier_poly(pts, v._pen_tangents, segments=64)
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
        if v._mode == "trim":
            v.preview_trim_at(pos.x(), pos.y())
        else:
            v.preview_extend_at(pos.x(), pos.y())
        hover = v._hit_test.entity_at(pos.x(), pos.y())
        if hover != v._hover_poly:
            v._hover_poly = hover
            v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True


class KnifeTool(CanvasTool):
    """Split every crossed shape with a temporary two-point line."""

    def press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position()
        self.v._knife_start_w = self.v._c2w(pos.x(), pos.y())
        self.v._knife_end_w = self.v._knife_start_w
        self.v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        if self.v._knife_start_w is None:
            return False
        pos = event.position()
        self.v._knife_end_w = self.v._c2w(pos.x(), pos.y())
        self.v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        start = self.v._knife_start_w
        if start is None:
            return False
        pos = event.position()
        end = self.v._c2w(pos.x(), pos.y())
        self.v._knife_start_w = None
        self.v._knife_end_w = None
        self.v.knife_cut(start, end)
        self.v.set_mode("select")
        return True
