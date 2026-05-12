"""Edit-mode vertex-operation helper methods for PolylineView."""

from __future__ import annotations

import math

from src.backend.geometry.primitives import build_circle_poly, build_ellipse_poly


class _EditModeMixin:
    """Edit-mode vertex operations. No __init__; all state lives on the primary class."""

    def _key_delete(self) -> None:
        if self._mode == "edit":
            if self._edit_selected_verts:
                self._delete_edit_vertices(set(self._edit_selected_verts))
                return
            if self._hover_vert is not None:
                self._delete_edit_vertices({self._hover_vert})
                return
        if self._mode == "select":
            self.delete_selected()

    def _key_backspace(self) -> None:
        if self._mode == "draw" and self._draw_pts:
            self._draw_pts.pop()
            if self._draw_point_snap_types:
                self._draw_point_snap_types.pop()
            if not self._draw_pts:
                self._dismiss_dim_inputs()
                self._draw_constraint = None
            self._refresh_draw_sidebar_state()
            self._redraw()
        elif self._mode == "edit":
            self._key_delete()
        elif self._mode == "select":
            self.delete_selected()

    def _can_delete_vertex(self, pi: int, vi: int) -> bool:
        if not (0 <= pi < len(self._polys)):
            return False
        poly = self._polys[pi]
        if not (0 <= vi < len(poly)):
            return False
        is_closed = (
            len(poly) >= 4
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 1e-6
        )
        unique_count = len(poly) - 1 if is_closed else len(poly)
        return unique_count > 3

    def _delete_edit_vertices(self, verts: set[tuple[int, int]]) -> int:
        if not verts:
            return 0

        grouped: dict[int, set[int]] = {}
        for pi, vi in verts:
            if pi in self._locked_polys:
                continue
            if self._can_delete_vertex(pi, vi):
                grouped.setdefault(pi, set()).add(vi)
        if not grouped:
            return 0

        self._push_undo()
        deleted = 0

        for pi in sorted(grouped.keys(), reverse=True):
            if not (0 <= pi < len(self._polys)):
                continue
            poly = self._polys[pi]
            is_closed = (
                len(poly) >= 4
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                < 1e-6
            )

            for vi in sorted(grouped[pi], reverse=True):
                if 0 <= vi < len(poly):
                    poly.pop(vi)
                    deleted += 1

            if is_closed and len(poly) >= 4:
                poly[-1] = poly[0]

        self._edit_selected_verts.clear()
        self._edit_drag_targets = set()
        self._edit_linked_verts = set()
        self._edit_poly = None
        self._edit_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return deleted

    def _linked_vertices(self, poly_idx: int, vert_idx: int) -> set[tuple[int, int]]:
        """Find all vertices linked to the given vertex (same point, across polylines)."""
        if poly_idx >= len(self._polys) or vert_idx >= len(self._polys[poly_idx]):
            return set()

        target_pt = self._polys[poly_idx][vert_idx]
        linked = {(poly_idx, vert_idx)}

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        for i, poly in enumerate(self._polys):
            if i == poly_idx:
                # For same polyline, check if it's a closed shape
                is_closed = len(poly) >= 4 and _eq(poly[0], poly[-1])
                if is_closed and (vert_idx == 0 or vert_idx == len(poly) - 1):
                    # First and last vertices of closed shape are linked
                    linked.add((i, 0))
                    linked.add((i, len(poly) - 1))
            else:
                # Check other polylines for matching endpoints
                for j, pt in enumerate(poly):
                    if _eq(target_pt, pt):
                        linked.add((i, j))

        return linked

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        """Move active edit vertex and all linked coincident vertices together."""
        if self._edit_poly is None or self._edit_vert is None:
            return

        # Parametric edit path: circles/ellipses should stay smooth while being resized.
        kind = (
            self._entity_kinds[self._edit_poly]
            if self._edit_poly < len(self._entity_kinds)
            else "polyline"
        )
        meta = (
            self._entity_meta[self._edit_poly]
            if self._edit_poly < len(self._entity_meta)
            else None
        )
        if kind in {"circle", "ellipse"} and isinstance(meta, dict):
            center = meta.get("center")
            if isinstance(center, tuple):
                cx = float(center[0])
                cy = float(center[1])
                if kind == "circle":
                    radius = max(1e-3, math.hypot(wx - cx, wy - cy))
                    meta["radius"] = radius
                    self._polys[self._edit_poly] = build_circle_poly(
                        cx,
                        cy,
                        radius,
                        segments=max(24, len(self._polys[self._edit_poly]) or 64),
                    )
                    return

                rot = float(meta.get("rotation", 0.0))
                if hasattr(self, "_rotate_point"):
                    lx, ly = self._rotate_point((wx, wy), (cx, cy), -rot)
                else:
                    lx, ly = wx, wy
                rx = max(1e-3, abs(lx - cx))
                ry = max(1e-3, abs(ly - cy))
                meta["rx"] = rx
                meta["ry"] = ry
                poly = build_ellipse_poly(
                    cx,
                    cy,
                    rx,
                    ry,
                    segments=max(24, len(self._polys[self._edit_poly]) or 64),
                )
                if abs(rot) > 1e-9 and hasattr(self, "_rotate_point"):
                    poly = [self._rotate_point(pt, (cx, cy), rot) for pt in poly]
                self._polys[self._edit_poly] = poly
                return

        targets = (
            self._edit_drag_targets
            or self._edit_linked_verts
            or {(self._edit_poly, self._edit_vert)}
        )
        for pi, vi in targets:
            if pi in self._locked_polys:
                continue
            if 0 <= pi < len(self._polys) and 0 <= vi < len(self._polys[pi]):
                self._polys[pi][vi] = (wx, wy)

    def _select_edit_vertices_in_rect(
        self,
        x1c: float,
        y1c: float,
        x2c: float,
        y2c: float,
        *,
        additive: bool = True,
    ) -> int:
        """Select edit vertices whose canvas coordinates are inside a rectangle."""
        if not additive:
            self._edit_selected_verts.clear()
        added = 0
        for pi, poly in enumerate(self._polys):
            for vi, (vx, vy) in enumerate(poly):
                cx, cy = self._w2c(vx, vy)
                if x1c <= cx <= x2c and y1c <= cy <= y2c:
                    key = (pi, vi)
                    if key not in self._edit_selected_verts:
                        added += 1
                    self._edit_selected_verts.add(key)
        return added

    def _delete_vertex(self, pi: int, vi: int) -> None:
        # Check if shape is currently closed BEFORE deletion
        poly = self._polys[pi]
        is_closed = (
            len(poly) >= 4
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        )

        self._push_undo()
        self._polys[pi].pop(vi)
        self._redraw()

        # Re-close shape if it was closed before deletion
        if is_closed and len(self._polys[pi]) >= 4:
            self._polys[pi][-1] = self._polys[pi][0]
        self._notify()
        self._fire_poly_change()

    def _chamfer_vertex(self, pi: int, vi: int, dist: float) -> bool:
        if not (0 <= pi < len(self._polys)) or dist <= 0:
            return False
        poly = self._polys[pi]
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        v1 = (ax - bx, ay - by)
        v2 = (cx - bx, cy - by)
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        d = min(dist, l1 * 0.45, l2 * 0.45)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        p1 = (bx + u1[0] * d, by + u1[1] * d)
        p2 = (bx + u2[0] * d, by + u2[1] * d)

        new_pts = pts[:vi] + [p1, p2] + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        self._push_undo()
        self._polys[pi] = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _round_vertex(self, pi: int, vi: int, radius: float) -> bool:
        if not (0 <= pi < len(self._polys)) or radius <= 0:
            return False
        poly = self._polys[pi]
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        u1 = (ax - bx, ay - by)
        u2 = (cx - bx, cy - by)
        l1 = math.hypot(*u1)
        l2 = math.hypot(*u2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        u1 = (u1[0] / l1, u1[1] / l1)
        u2 = (u2[0] / l2, u2[1] / l2)
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        phi = math.acos(dot)
        if phi < 1e-3 or abs(math.pi - phi) < 1e-3:
            return False

        offset = radius / math.tan(phi / 2.0)
        offset = min(offset, l1 * 0.45, l2 * 0.45)
        if offset <= 1e-6:
            return False
        r = offset * math.tan(phi / 2.0)

        t1 = (bx + u1[0] * offset, by + u1[1] * offset)
        t2 = (bx + u2[0] * offset, by + u2[1] * offset)

        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        if bl < 1e-9:
            return False
        bis = (bis[0] / bl, bis[1] / bl)
        center_dist = r / math.sin(phi / 2.0)
        center = (bx + bis[0] * center_dist, by + bis[1] * center_dist)

        a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
        a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
        # Use the minor arc between tangent points; choosing the major arc
        # produces loop-like rounding artifacts.
        span = a2 - a1
        while span <= -math.pi:
            span += 2 * math.pi
        while span > math.pi:
            span -= 2 * math.pi
        steps = max(4, min(24, int(abs(span) / (math.pi / 18.0))))
        arc_pts = [
            (
                center[0] + r * math.cos(a1 + span * (i / steps)),
                center[1] + r * math.sin(a1 + span * (i / steps)),
            )
            for i in range(steps + 1)
        ]

        new_pts = pts[:vi] + arc_pts + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        self._push_undo()
        self._polys[pi] = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _delete_poly(self, pi: int) -> None:
        self._push_undo()
        self._polys.pop(pi)
        remapped: set[int] = set()
        for idx in self._construction_polys:
            if idx == pi:
                continue
            remapped.add(idx - 1 if idx > pi else idx)
        self._construction_polys = remapped
        self._sel.discard(pi)
        self._sel = {i if i < pi else i - 1 for i in self._sel if i != pi}
        self._redraw()
        self._notify()
        self._fire_poly_change()
