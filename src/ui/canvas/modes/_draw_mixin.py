"""Draw-mode helper methods for PolylineView."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from shapely.errors import GEOSException
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
)
from shapely.ops import split as shapely_split

from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.ui.canvas._constants import CLOSE_SNAP_DIST as _CLOSE_SNAP_DIST
from src.ui.sidebars.canvas_sidebar import DrawSidebar
from src.ui.widgets.tool_picker_dialog import ToolPickerDialog


class _DrawModeMixin:
    """Draw-mode helper methods. No __init__; all state lives on the primary class."""

    def _build_draw_sidebar(self) -> None:
        panel = DrawSidebar(
            parent=self.viewport(),
            on_draw_clicked=self._on_draw_button_clicked,
            on_finish_open=lambda: self._finish_draw(close=False),
            on_close_edit=lambda: self._finish_draw(close=True),
            on_undo_point=self._key_backspace,
            on_toggle_snap=self._toggle_sidebar_snap,
            on_toggle_split=self._toggle_sidebar_split,
            on_cycle_arc_mode=self._cycle_arc_mode,
            on_cycle_constraint_mode=self._cycle_constraint_mode,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self.set_mode("select"),
        )
        panel.hide()

        # Create tool picker dialog
        self._tool_picker_dialog = ToolPickerDialog(parent=self.viewport())

        anim = QPropertyAnimation(panel, b"pos", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._draw_sidebar = panel
        self._draw_sidebar_anim = anim
        self._refresh_draw_sidebar_state()

    def _toggle_sidebar_snap(self) -> None:
        self._grid_snap = not self._grid_snap
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _toggle_sidebar_split(self) -> None:
        self._draw_split_enabled = not self._draw_split_enabled
        self._refresh_draw_sidebar_state()
        self._show_flash("Split: on" if self._draw_split_enabled else "Split: off", 800)

    def _on_draw_button_clicked(self) -> None:
        """Show the tool picker modal and handle tool selection."""
        if (
            hasattr(self, "_tool_picker_dialog")
            and self._tool_picker_dialog is not None
        ):
            if self._tool_picker_dialog.exec() == 1:  # QDialog.Accepted
                tool = self._tool_picker_dialog.get_selected_tool()
                if tool is not None:
                    self._set_draw_primitive(tool)

    def _cancel_draw_points(self) -> None:
        if self._mode != "draw":
            return
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._draw_snap = None
        self._draw_snap_type = None
        self._draw_constraint = None
        self._angle_snap_active = False
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        if hasattr(self, "_dismiss_shape_dim_inputs"):
            self._dismiss_shape_dim_inputs()
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._draw_sidebar is None:
            return
        if not self._draw_sidebar_visible:
            self._draw_sidebar.hide()

    def _layout_draw_sidebar(self) -> None:
        if self._draw_sidebar is None:
            return
        y = 8
        target_h = max(260, self.viewport().height() - 16)
        self._draw_sidebar.setFixedHeight(min(430, target_h))
        x = 8 if self._draw_sidebar_visible else -self._draw_sidebar.width() + 20
        self._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._draw_sidebar is None or self._draw_sidebar_anim is None:
            return
        if self._draw_sidebar_visible == visible and self._draw_sidebar.isVisible():
            self._refresh_draw_sidebar_state()
            return

        self._draw_sidebar_visible = visible
        self._refresh_draw_sidebar_state()
        y = 8
        hidden_x = -self._draw_sidebar.width() + 20
        shown_x = 8
        self._draw_sidebar.setFixedHeight(
            min(430, max(260, self.viewport().height() - 16))
        )

        if not animate:
            if visible:
                self._draw_sidebar.show()
                self._draw_sidebar.move(shown_x, y)
            else:
                self._draw_sidebar.move(hidden_x, y)
                self._draw_sidebar.hide()
            return

        if visible:
            self._draw_sidebar.show()
            self._draw_sidebar.move(hidden_x, y)
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.setEndValue(QPoint(shown_x, y))
            self._draw_sidebar_anim.start()
        else:
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(self._draw_sidebar.pos())
            self._draw_sidebar_anim.setEndValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.start()

    def _refresh_draw_sidebar_state(self) -> None:
        if not isinstance(self._draw_sidebar, DrawSidebar):
            return
        has_pts = len(self._draw_pts)
        self._draw_sidebar.set_polyline_actions_enabled(
            can_finish=has_pts >= 2,
            can_close=has_pts >= 3,
            can_undo=has_pts >= 1,
        )
        self._draw_sidebar.set_snap_label(self._grid_snap)
        self._draw_sidebar.set_split_label(self._draw_split_enabled)
        self._draw_sidebar.set_active_tool(self._draw_primitive)
        self._draw_sidebar.set_arc_mode(self._draw_arc_mode)
        self._draw_sidebar.set_arc_mode_enabled(self._draw_primitive == "arc")
        self._draw_sidebar.set_constraint_mode(self._draw_constraint_lock)
        self._draw_sidebar.set_constraint_mode_enabled(
            self._draw_primitive in {"line", "polyline"}
        )
        self._update_shape_size_fields_from_preview()

    def _cycle_arc_mode(self) -> None:
        if self._draw_primitive != "arc":
            self._set_draw_primitive("arc")
            return
        self._draw_arc_mode = (
            "center-start-end" if self._draw_arc_mode == "3point" else "3point"
        )
        self._draw_arc_pts.clear()
        self._refresh_draw_sidebar_state()
        self._show_flash(
            "Arc: center-start-end"
            if self._draw_arc_mode == "center-start-end"
            else "Arc: three-point",
            900,
        )
        self._redraw()

    def _cycle_constraint_mode(self) -> None:
        modes = [None, "H", "V", "45"]
        try:
            idx = modes.index(self._draw_constraint_lock)
        except ValueError:
            idx = 0
        self._draw_constraint_lock = modes[(idx + 1) % len(modes)]
        self._refresh_draw_sidebar_state()
        self._show_flash(
            f"Constraint: {self._draw_constraint_lock}"
            if self._draw_constraint_lock
            else "Constraint: Free",
            900,
        )
        self._redraw()

    def _set_draw_primitive(self, tool: str) -> None:
        valid = {
            "polyline",
            "line",
            "arc",
            "spline",
            "rectangle",
            "circle",
            "ellipse",
            "polygon",
        }
        if tool not in valid:
            return
        self._draw_primitive = tool
        self._draw_pts.clear()
        self._draw_arc_pts.clear()
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._dismiss_dim_inputs()
        self._update_shape_size_fields_from_preview()
        self._refresh_draw_sidebar_state()
        self._show_flash(f"Tool: {tool}", 650)
        self._redraw()

    def _shape_primitive_active(self) -> bool:
        return self._draw_primitive in {"rectangle", "circle", "ellipse", "polygon"}

    def _update_shape_size_fields_from_preview(self) -> None:
        if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
            return
        enabled = (
            self._shape_primitive_active() and self._draw_shape_anchor_w is not None
        )
        self._draw_shape_w_edit.setEnabled(enabled)
        self._draw_shape_h_edit.setEnabled(enabled)
        if not enabled:
            return
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        self._draw_shape_w_edit.setText(f"{abs(ex - sx):.2f}")
        self._draw_shape_h_edit.setText(f"{abs(ey - sy):.2f}")

    def _apply_shape_size_inputs(self) -> None:
        if (
            self._draw_shape_w_edit is None
            or self._draw_shape_h_edit is None
            or self._draw_shape_anchor_w is None
            or not self._shape_primitive_active()
        ):
            return
        try:
            w = max(0.001, float(self._draw_shape_w_edit.text().strip()))
            h = max(0.001, float(self._draw_shape_h_edit.text().strip()))
        except ValueError:
            return

        sx, sy = self._draw_shape_anchor_w
        if self._draw_shape_cursor_w is None:
            self._draw_shape_cursor_w = (sx + w, sy + h)
        ex0, ey0 = self._draw_shape_cursor_w
        sign_x = 1.0 if ex0 >= sx else -1.0
        sign_y = 1.0 if ey0 >= sy else -1.0
        self._draw_shape_cursor_w = (sx + sign_x * w, sy + sign_y * h)
        self._redraw()

    def _append_draw_polyline(
        self,
        poly: list[tuple[float, float]],
        *,
        enter_edit: bool = False,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> None:
        if len(poly) < 2:
            return
        self._push_undo()
        new_idx = self._append_entity(list(poly), kind=kind, meta=meta)
        if self._draw_construction_mode:
            self._construction_polys.add(new_idx)
        self._sel = {new_idx}
        self._notify()
        self._fire_poly_change()
        self._refresh_draw_sidebar_state()
        self._redraw()
        if enter_edit:
            self.set_mode("edit")

    def _commit_drawn_polyline(
        self,
        poly: list[tuple[float, float]],
        *,
        primitive: str,
        close: bool = False,
        created_flash: str = "Polyline created",
    ) -> bool:
        if len(poly) < 2:
            return False

        self._push_undo()
        split_happened = False
        split_closed = 0
        split_open = 0
        can_cut_split = self._draw_split_enabled and (
            primitive in {"line", "polyline", "arc", "spline"}
            or self._draw_construction_mode
        )
        if can_cut_split and not close and len(poly) >= 2:
            split_happened, split_closed, split_open = self._split_geometry_with_line(
                poly
            )

        kind = "polyline"
        meta: dict[str, Any] | None = None
        if primitive == "line" and len(poly) >= 2:
            kind = "line"
            meta = {"start": tuple(poly[0]), "end": tuple(poly[-1])}
        elif primitive == "arc" and len(poly) >= 3:
            kind = "arc"
            from src.backend.geometry.arc import (
                arc_spec_from_center_start_end,
                arc_spec_from_three_points,
            )

            if self._draw_arc_mode == "center-start-end":
                spec = arc_spec_from_center_start_end(poly[0], poly[1], poly[2])
            else:
                spec = arc_spec_from_three_points(poly[0], poly[1], poly[2])
            if spec is not None:
                meta = {
                    "center": spec.center,
                    "radius": spec.radius,
                    "start_angle": spec.start_angle,
                    "end_angle": spec.end_angle,
                }
        elif primitive == "spline" and len(poly) >= 2:
            kind = "spline"
            meta = {
                "segments": 24,
                "closed": close,
                "control_points": [tuple(pt) for pt in poly],
                "degree": 3,
            }
        self._entity_kinds.append(kind)
        self._entity_meta.append(meta)
        self._polys.append(list(poly))
        new_idx = len(self._polys) - 1
        if self._draw_construction_mode:
            self._construction_polys.add(new_idx)

        merged_idx: int | None = None
        if (
            primitive in {"line", "polyline"}
            and not self._draw_construction_mode
            and not split_happened
            and any(snap_type == "vertex" for snap_type in self._draw_point_snap_types)
        ):
            merged_idx = self._try_merge_endpoints()
            if merged_idx is not None:
                new_idx = merged_idx

        self._sel.clear()
        self._sel.add(new_idx)
        self._notify()
        self._fire_poly_change()
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._draw_constraint = None
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        if split_happened:
            if split_closed and split_open:
                self._show_flash("Regions cut + segments split", 900)
            elif split_closed:
                self._show_flash("Regions cut", 900)
            else:
                self._show_flash("Segments split", 900)
        elif merged_idx is not None and self._is_poly_closed(self._polys[new_idx]):
            self._show_flash("Polyline closed", 800)
        elif merged_idx is not None:
            self._show_flash("Segments merged", 800)
        else:
            self._show_flash(created_flash, 800)
        self._redraw()
        if close or (
            merged_idx is not None and self._is_poly_closed(self._polys[new_idx])
        ):
            self.set_mode("edit")
        return True

    def _commit_shape_preview(self) -> bool:
        if not self._draw_shape_preview_active:
            return False
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return False
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)

        poly: list[tuple[float, float]] = []
        kind = "polyline"
        meta: dict[str, Any] | None = None
        if self._draw_primitive == "rectangle":
            poly = build_rect_poly(cx, cy, w, h)
            kind = "rectangle"
            meta = {
                "center": (cx, cy),
                "width": w,
                "height": h,
                "rotation": 0.0,
            }
        elif self._draw_primitive == "circle":
            # Match preview behavior: first click is center, drag to radius.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
            kind = "circle"
            meta = {"center": (sx, sy), "radius": radius}
        elif self._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
            kind = "ellipse"
            meta = {"center": (cx, cy), "rx": w / 2.0, "ry": h / 2.0, "rotation": 0.0}
        elif self._draw_primitive == "polygon":
            poly = build_polygon_poly(cx, cy, min(w, h) / 2.0, 6)

        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None

        if len(poly) >= 2:
            self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._show_flash(f"{self._draw_primitive.title()} created", 800)
            self._refresh_draw_sidebar_state()
            self._redraw()
            return True

        self._refresh_draw_sidebar_state()
        self._redraw()
        return False

    def _finish_draw(self, *, close: bool = False) -> None:
        if self._mode != "draw" or len(self._draw_pts) < 2:
            return
        if self._draw_primitive == "spline" and len(self._draw_pts) < 3:
            self._show_flash("Spline needs at least 3 points", 900)
            return
        if close and self._draw_pts[0] != self._draw_pts[-1]:
            self._draw_pts.append(self._draw_pts[0])
        drawn = list(self._draw_pts)
        self._commit_drawn_polyline(
            drawn,
            primitive=self._draw_primitive,
            close=close,
            created_flash="Polyline created",
        )

    def _close_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if len(poly) < 3:
                continue
            if self._is_poly_closed(poly):
                continue
            self._polys[idx] = [*poly, poly[0]]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _open_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if not self._is_poly_closed(poly):
                continue
            if len(poly) < 2:
                continue
            self._polys[idx] = poly[:-1]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _toggle_selected_construction(self) -> None:
        if not self._sel:
            return
        self._push_undo()
        for idx in list(self._sel):
            if idx in self._construction_polys:
                self._construction_polys.discard(idx)
            else:
                self._construction_polys.add(idx)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _try_merge_endpoints(self) -> int | None:
        """Merge endpoint-touching polylines. Returns survivor index or None."""
        if len(self._polys) < 2:
            return None
        survivor_idx = len(self._polys) - 1
        if len(self._polys[survivor_idx]) < 2:
            return None

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        merged_any = False
        changed = True
        while changed:
            changed = False
            survivor = self._polys[survivor_idx]
            if len(survivor) < 2:
                break
            survivor_start, survivor_end = survivor[0], survivor[-1]
            for i, poly in enumerate(self._polys):
                if i == survivor_idx or len(poly) < 2:
                    continue
                p_start, p_end = poly[0], poly[-1]
                if _eq(p_start, p_end):
                    continue
                merged: list[tuple[float, float]] | None = None
                if _eq(survivor_end, p_start):
                    merged = survivor[:-1] + poly
                elif _eq(survivor_end, p_end):
                    merged = survivor[:-1] + list(reversed(poly))
                elif _eq(survivor_start, p_end):
                    merged = poly[:-1] + survivor
                elif _eq(survivor_start, p_start):
                    merged = list(reversed(poly))[:-1] + survivor
                if merged is None:
                    continue
                popped_was_construction = i in self._construction_polys
                survivor_was_construction = survivor_idx in self._construction_polys
                self._polys[survivor_idx] = merged
                if survivor_idx < len(self._entity_kinds):
                    self._entity_kinds[survivor_idx] = "polyline"
                if survivor_idx < len(self._entity_meta):
                    self._entity_meta[survivor_idx] = None
                self._polys.pop(i)
                if i < len(self._entity_kinds):
                    self._entity_kinds.pop(i)
                if i < len(self._entity_meta):
                    self._entity_meta.pop(i)
                remapped: set[int] = set()
                for ci in self._construction_polys:
                    if ci == i:
                        continue
                    remapped.add(ci - 1 if ci > i else ci)
                self._construction_polys = remapped
                if i < survivor_idx:
                    survivor_idx -= 1
                if popped_was_construction or survivor_was_construction:
                    self._construction_polys.add(survivor_idx)
                merged_any = True
                changed = True
                break
        return survivor_idx if merged_any else None

    def _is_near_start(self) -> bool:
        """Check if cursor is near the first draw point (close-polygon zone)."""
        if (
            len(self._draw_pts) < 3
            or self._cursor_wx is None
            or self._cursor_wy is None
        ):
            return False
        start_cx, start_cy = self._w2c(*self._draw_pts[0])
        cur_cx, cur_cy = self._w2c(self._cursor_wx, self._cursor_wy)
        return math.hypot(cur_cx - start_cx, cur_cy - start_cy) < _CLOSE_SNAP_DIST

    def _draw_preview_outcomes(self) -> list[str]:
        if self._mode != "draw" or self._cursor_wx is None or self._cursor_wy is None:
            return []
        outcomes: list[str] = []

        if self._is_near_start():
            outcomes.append("Close")
            return outcomes

        if self._draw_primitive in {"line", "polyline"} and self._draw_pts:
            start = self._draw_pts[0]
            end = (self._cursor_wx, self._cursor_wy)
            if self._would_close_existing_polyline(start, end):
                outcomes.append("Close")
            elif self._would_merge_existing_polyline(start, end):
                outcomes.append("Merge")
            preview = list(self._draw_pts) + [end]
            if len(preview) >= 2 and self._would_split_existing_geometry(preview):
                outcomes.append("Split")
        elif self._draw_construction_mode and self._draw_pts:
            preview = list(self._draw_pts) + [(self._cursor_wx, self._cursor_wy)]
            if len(preview) >= 2 and self._would_split_existing_geometry(preview):
                outcomes.append("Split")

        return outcomes

    @staticmethod
    def _points_equal(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6

    def _would_close_existing_polyline(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        for poly in self._polys:
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            if (
                self._points_equal(start, poly[0]) and self._points_equal(end, poly[-1])
            ) or (
                self._points_equal(start, poly[-1]) and self._points_equal(end, poly[0])
            ):
                return True
        return False

    def _would_merge_existing_polyline(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        touched = 0
        for poly in self._polys:
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            endpoints = (poly[0], poly[-1])
            if any(self._points_equal(start, pt) for pt in endpoints):
                touched += 1
            if any(self._points_equal(end, pt) for pt in endpoints):
                touched += 1
        return touched > 0

    def _would_split_existing_geometry(
        self,
        preview_poly: list[tuple[float, float]],
    ) -> bool:
        if len(preview_poly) < 2:
            return False
        try:
            cutter = LineString(preview_poly)
            if cutter.is_empty or cutter.length < 1e-9:
                return False
        except (TypeError, ValueError, GEOSException):
            return False

        for poly in self._polys:
            if len(poly) < 2:
                continue
            if self._is_poly_closed(poly):
                try:
                    coords = list(poly)
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    shp = Polygon(coords)
                    if not shp.is_valid:
                        shp = shp.buffer(0)
                    if shp.is_empty or not cutter.intersects(shp):
                        continue
                    if self._would_split_closed_polygon(shp, cutter):
                        return True
                    pts = list(
                        poly[:-1] if self._points_equal(poly[0], poly[-1]) else poly
                    )
                    edge_count = len(pts)
                    for j in range(edge_count):
                        a = pts[j]
                        b = pts[(j + 1) % edge_count]
                        if len(self._split_segment_by_cutter_points(a, b, cutter)) >= 2:
                            return True
                except (TypeError, ValueError, GEOSException):
                    continue
            else:
                try:
                    for j in range(len(poly) - 1):
                        a = poly[j]
                        b = poly[j + 1]
                        if len(self._split_segment_by_cutter_points(a, b, cutter)) >= 2:
                            return True
                except (TypeError, ValueError, GEOSException):
                    continue
        return False

    def _would_split_closed_polygon(self, polygon: Polygon, cutter: LineString) -> bool:
        if polygon.is_empty or cutter.is_empty or not cutter.intersects(polygon):
            return False
        boundary = polygon.boundary
        try:
            overlap = cutter.intersection(boundary)
        except (TypeError, ValueError, GEOSException):
            return False
        if isinstance(overlap, (LineString, MultiLineString)) and overlap.length > 1e-6:
            return False

        inner = polygon.buffer(-1e-6)
        if inner.is_empty:
            inner = polygon
        try:
            inside = cutter.intersection(inner)
        except (TypeError, ValueError, GEOSException):
            return False
        if getattr(inside, "is_empty", True):
            return False

        bounds = polygon.bounds
        diag = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])
        ext_cutter = self._extend_line(cutter, max(diag * 2.0, 1.0))
        split_candidates: list[tuple[int, list]] = []
        for order, candidate in enumerate((cutter, ext_cutter)):
            pieces = shapely_split(polygon, candidate)
            trial = list(pieces.geoms) if hasattr(pieces, "geoms") else []
            if len(trial) >= 2:
                split_candidates.append((order, trial))
        return bool(split_candidates)

    def _split_segment_by_cutter_points(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        cutter: LineString,
    ) -> list[list[tuple[float, float]]]:
        if self._points_equal(a, b):
            return []
        seg_line = LineString([a, b])
        if seg_line.is_empty or not cutter.intersects(seg_line):
            return [[a, b]]

        try:
            inter = seg_line.intersection(cutter)
        except (TypeError, ValueError, GEOSException):
            return [[a, b]]

        dx = b[0] - a[0]
        dy = b[1] - a[1]
        denom = dx * dx + dy * dy
        if denom < 1e-12:
            return [[a, b]]

        points: list[tuple[float, float]] = [a, b]

        def _add_point(pt: tuple[float, float]) -> None:
            if any(self._points_equal(pt, existing) for existing in points):
                return
            points.append(pt)

        if isinstance(inter, Point):
            _add_point((float(inter.x), float(inter.y)))
        elif isinstance(inter, MultiPoint):
            for g in inter.geoms:
                _add_point((float(g.x), float(g.y)))
        elif isinstance(inter, LineString):
            coords = list(inter.coords)
            if len(coords) >= 2:
                _add_point((float(coords[0][0]), float(coords[0][1])))
                _add_point((float(coords[-1][0]), float(coords[-1][1])))
        elif isinstance(inter, MultiLineString):
            for g in inter.geoms:
                coords = list(g.coords)
                if len(coords) >= 2:
                    _add_point((float(coords[0][0]), float(coords[0][1])))
                    _add_point((float(coords[-1][0]), float(coords[-1][1])))
        elif isinstance(inter, GeometryCollection):
            for pt in self._iter_intersection_points(inter):
                _add_point((float(pt[0]), float(pt[1])))

        def _param(pt: tuple[float, float]) -> float:
            return ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / denom

        ordered = sorted(points, key=_param)
        parts: list[list[tuple[float, float]]] = []
        for i in range(len(ordered) - 1):
            p0 = ordered[i]
            p1 = ordered[i + 1]
            if self._points_equal(p0, p1):
                continue
            parts.append([p0, p1])
        return parts or [[a, b]]
