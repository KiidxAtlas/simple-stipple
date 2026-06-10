"""Select-mode helper methods for PolylineView."""

from __future__ import annotations

import math
from copy import deepcopy

from PySide6.QtCore import QPointF, QTimer
from PySide6.QtWidgets import QInputDialog, QMenu


class _SelectModeMixin:
    """Select-mode, clipboard, and context-menu helper methods.

    No __init__; all state lives on the primary class.
    """

    def _copy_selected(self) -> None:
        if not self._sel:
            return
        self._clipboard = []
        for i in sorted(self._sel):
            if i >= len(self._polys):
                continue
            self._clipboard.append({
                "polyline": list(self._polys[i]),
                "kind": self._entity_kinds[i]
                if i < len(self._entity_kinds)
                else "polyline",
                "meta": deepcopy(self._entity_meta[i])
                if i < len(self._entity_meta) and self._entity_meta[i] is not None
                else None,
                "construction": i in self._construction_polys,
            })

    def _paste_clipboard(self) -> None:
        if not self._clipboard:
            return
        self._push_undo()
        offset = 1.0  # mm
        new_indices = []
        for record in self._clipboard:
            poly = list(record.get("polyline", []))
            new_poly = [(x + offset, y + offset) for x, y in poly]
            kind = str(record.get("kind", "polyline"))
            meta = self._translated_entity_meta(
                kind,
                record.get("meta"),
                offset,
                offset,
            )
            new_idx = self._append_entity(new_poly, kind=kind, meta=meta)
            if record.get("construction"):
                self._construction_polys.add(new_idx)
            new_indices.append(new_idx)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _duplicate_selected(self) -> None:
        if not self._sel:
            return
        self._copy_selected()
        self._paste_clipboard()

    def _duplicate_selected_with_offset(self) -> None:
        """Duplicate selected with larger offset for better visibility."""
        if not self._sel:
            return
        # Calculate bounding box to determine smart offset
        if not self._sel or not self._polys:
            return

        # Get bounding box of selection
        min_x, max_x, min_y, max_y = (
            float("inf"),
            float("-inf"),
            float("inf"),
            float("-inf"),
        )
        for idx in self._sel:
            if idx < len(self._polys):
                for x, y in self._polys[idx]:
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)

        width = max_x - min_x if max_x > min_x else 10.0
        height = max_y - min_y if max_y > min_y else 10.0

        # Offset is 10% of bounding box, minimum 2mm
        offset = max(2.0, min(width, height) * 0.1)

        self._copy_selected()
        self._paste_clipboard_with_offset(offset)

    def _paste_clipboard_with_offset(self, offset: float) -> None:
        """Paste clipboard items with specified offset."""
        if not self._clipboard:
            return
        self._push_undo()
        new_indices = []
        for record in self._clipboard:
            poly = list(record.get("polyline", []))
            new_poly = [(x + offset, y + offset) for x, y in poly]
            kind = str(record.get("kind", "polyline"))
            meta = self._translated_entity_meta(
                kind,
                record.get("meta"),
                offset,
                offset,
            )
            new_idx = self._append_entity(new_poly, kind=kind, meta=meta)
            if record.get("construction"):
                self._construction_polys.add(new_idx)
            new_indices.append(new_idx)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _cut_selected(self) -> None:
        if not self._sel:
            return
        cut_set = {idx for idx in self._sel if idx not in self._locked_polys}
        if not cut_set:
            return
        self._copy_selected()
        self._push_undo()
        self._compact_entities(cut_set)
        self._sel.clear()
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._sel:
            return
        mutable = [idx for idx in self._sel if idx not in self._locked_polys]
        if not mutable:
            return
        if not self._nudge_undo_pushed:
            self._push_undo()
            self._nudge_undo_pushed = True
            QTimer.singleShot(500, self._reset_nudge_undo)
        for idx in mutable:
            if idx < len(self._polys):
                self._polys[idx] = [(x + dx, y + dy) for x, y in self._polys[idx]]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entity_kinds[idx]
                    if idx < len(self._entity_kinds)
                    else "polyline",
                    meta=self._entity_meta[idx]
                    if idx < len(self._entity_meta)
                    else None,
                    transform="translate",
                    dx=dx,
                    dy=dy,
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _reset_nudge_undo(self) -> None:
        self._nudge_undo_pushed = False

    def _scale_all(self, factor: float) -> None:
        """Scale all polylines uniformly around their bounding box center."""
        if not self._polys:
            return
        self._push_undo()
        all_pts = [pt for p in self._polys for pt in p]
        xs, ys = zip(*all_pts)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        self._polys = [
            [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in poly]
            for poly in self._polys
        ]
        for idx in range(len(self._polys)):
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="scale",
                factor=factor,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _group_selected(self) -> None:
        if len(self._sel) < 2:
            self._show_flash("Select 2+ shapes to group", 1000)
            return
        gid = self._next_group_id
        self._next_group_id += 1
        for idx in self._sel:
            self._groups[idx] = gid
        self._show_flash(f"Grouped {len(self._sel)} shapes", 900)
        self._notify()

    def _ungroup_selected(self) -> None:
        ungrouped = {self._groups.pop(idx) for idx in self._sel if idx in self._groups}
        if not ungrouped:
            return
        # Also remove other group members if their whole group is being dissolved
        stale = {idx for idx, gid in list(self._groups.items()) if gid in ungrouped}
        for idx in stale:
            self._groups.pop(idx, None)
        self._show_flash("Ungrouped", 700)
        self._notify()

    def _ctx_select(self, idx: int) -> None:
        self._sel.add(idx)
        self._redraw()
        self._notify()

    def _ctx_deselect(self, idx: int) -> None:
        self._sel.discard(idx)
        self._redraw()
        self._notify()

    def _ctx_delete_poly(self, idx: int) -> None:
        self._push_undo()
        self._polys.pop(idx)
        if idx < len(self._entity_kinds):
            self._entity_kinds.pop(idx)
        if idx < len(self._entity_meta):
            self._entity_meta.pop(idx)
        remapped: set[int] = set()
        for pi in self._construction_polys:
            if pi == idx:
                continue
            remapped.add(pi - 1 if pi > idx else pi)
        self._construction_polys = remapped
        self._sel.discard(idx)
        self._sel = {i if i < idx else i - 1 for i in self._sel if i != idx}
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _offset_selected_with_feedback(self, distance: float) -> None:
        created = self.offset_selected(distance)
        if created:
            self._show_flash(f"Offset {created} polyline(s)", 900)
        else:
            self._show_flash("Offset failed", 900)

    def _prompt_offset_selected(self) -> None:
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return
        value, ok = QInputDialog.getDouble(
            self,
            "Offset Geometry",
            "Offset distance (mm):",
            1.0,
            -1_000_000.0,
            1_000_000.0,
            3,
        )
        if ok:
            self._offset_selected_with_feedback(value)

    def _active_vertex_for_shortcuts(self) -> tuple[int, int] | None:
        """Return the best vertex target for round/chamfer keyboard shortcuts."""
        if self._edit_poly is not None and self._edit_vert is not None:
            return (self._edit_poly, self._edit_vert)
        if self._hover_vert is not None:
            return self._hover_vert
        if self._cursor_wx is not None and self._cursor_wy is not None:
            cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
            return self._find_nearest_vertex(cx, cy)
        return None

    def _normalized_corner_vertex(self, pi: int, vi: int) -> tuple[int, int] | None:
        """Return a valid interior-corner vertex for round/chamfer, if possible."""
        if not (0 <= pi < len(self._polys)):
            return None
        if pi in self._locked_polys:
            return None
        poly = self._polys[pi]
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return None
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return None
        if not closed and (vi == 0 or vi == n - 1):
            return None
        return (pi, vi)

    def _corner_vertex_for_shortcuts(self) -> tuple[int, int] | None:
        """Resolve keyboard round/chamfer target to the nearest valid corner."""
        active = self._active_vertex_for_shortcuts()
        if active is not None:
            normalized = self._normalized_corner_vertex(*active)
            if normalized is not None:
                return normalized

        if self._cursor_wx is None or self._cursor_wy is None:
            return None
        ccx, ccy = self._w2c(self._cursor_wx, self._cursor_wy)

        # Prefer currently selected polylines when available.
        if self._sel:
            poly_indices = [
                pi for pi in sorted(self._sel) if 0 <= pi < len(self._polys)
            ]
        else:
            poly_indices = list(range(len(self._polys)))

        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for pi in poly_indices:
            poly = self._polys[pi]
            for vi, pt in enumerate(poly):
                normalized = self._normalized_corner_vertex(pi, vi)
                if normalized is None:
                    continue
                cx, cy = self._w2c(*pt)
                d = math.hypot(ccx - cx, ccy - cy)
                if d < best_dist:
                    best_dist = d
                    best = normalized
        return best

    def _prompt_round_shortcut(self) -> None:
        target = self._corner_vertex_for_shortcuts()
        if target is None:
            self._show_flash("Pick a valid corner vertex first", 1000)
            return
        pi, vi = target
        radius, ok = QInputDialog.getDouble(
            self,
            "Round Corner",
            "Radius (mm):",
            1.0,
            0.01,
            1_000_000.0,
            3,
        )
        if not ok:
            return
        if self._round_vertex(pi, vi, radius):
            self._show_flash("Rounded corner", 900)
        else:
            self._show_flash("Round failed", 900)

    def _prompt_chamfer_shortcut(self) -> None:
        target = self._corner_vertex_for_shortcuts()
        if target is None:
            self._show_flash("Pick a valid corner vertex first", 1000)
            return
        pi, vi = target
        distance, ok = QInputDialog.getDouble(
            self,
            "Chamfer Corner",
            "Distance (mm):",
            1.0,
            0.01,
            1_000_000.0,
            3,
        )
        if not ok:
            return
        if self._chamfer_vertex(pi, vi, distance):
            self._show_flash("Chamfered corner", 900)
        else:
            self._show_flash("Chamfer failed", 900)

    def _send_selected_to_pattern(self) -> None:
        cb = getattr(self, "_send_selected_to_pattern_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent to Pattern Fill", 900)

    def _send_selected_to_draft(self) -> None:
        cb = getattr(self, "_send_selected_to_draft_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent to Draft", 900)

    def _use_selected_as_fill_pattern(self) -> None:
        cb = getattr(self, "_use_selected_as_fill_pattern_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent as fill pattern", 900)

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if self._mode == "draw":
            if self._draw_shape_preview_active and self._shape_primitive_active():
                self._cancel_draw_points()
                self._show_flash("Shape preview canceled", 700)
                return
            # Right-click = finish open polyline (no close), stay in draw mode
            self._finish_draw(close=False)
            return

        if self._mode == "edit":
            hit = self._find_nearest_vertex(cx, cy)
            if hit is not None:
                pi, vi = hit
                menu = QMenu()

                def _prompt_round_corner() -> None:
                    radius, ok = QInputDialog.getDouble(
                        self,
                        "Round Corner",
                        "Radius (mm):",
                        1.0,
                        0.01,
                        1000000.0,
                        3,
                    )
                    if ok:
                        self._round_vertex(pi, vi, radius)

                def _prompt_chamfer_corner() -> None:
                    distance, ok = QInputDialog.getDouble(
                        self,
                        "Chamfer Corner",
                        "Distance (mm):",
                        1.0,
                        0.01,
                        1000000.0,
                        3,
                    )
                    if ok:
                        self._chamfer_vertex(pi, vi, distance)

                poly = self._polys[pi]
                is_closed = (
                    len(poly) >= 4
                    and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                    < 0.01
                )
                unique_count = len(poly) - 1 if is_closed else len(poly)
                if unique_count > 3:
                    menu.addAction("Delete vertex", lambda: self._delete_vertex(pi, vi))
                if (is_closed and unique_count >= 3) or (
                    not is_closed and 0 < vi < len(poly) - 1
                ):
                    menu.addAction("Round corner…", _prompt_round_corner)
                    menu.addAction("Chamfer corner…", _prompt_chamfer_corner)
                menu.addAction("Delete polyline", lambda: self._delete_poly(pi))
                menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))
            return

        # Select-mode menus are owned by DxfCanvas._rightclick_cb (the only
        # instantiable canvas). Display-only canvases get no context menu.
