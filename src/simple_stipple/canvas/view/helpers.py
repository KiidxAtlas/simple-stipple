# Helper methods extracted from view.py

from __future__ import annotations

import copy
import math
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation
from PySide6.QtWidgets import QApplication, QWidget

from simple_stipple.canvas.constants import MIN_SCALE as _MIN_SCALE
from simple_stipple.document.model import EntityRecord
from simple_stipple.engine.cad.snapping import polygon_centroid as _polygon_centroid

_MAX_SCALE = 20000.0  # px per mm — deep zoom for tiny features; mirrors view/main.py


def _rehydrate_meta(meta: dict) -> dict:
    """Convert JSON-round-tripped meta lists back to point tuples."""
    out = dict(meta)
    for key in ("start", "end", "center"):
        v = out.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            out[key] = (float(v[0]), float(v[1]))
    cps = out.get("control_points")
    if isinstance(cps, list):
        out["control_points"] = [
            (float(p[0]), float(p[1])) for p in cps if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
    return out


def _chamfer_vertex(self, entity_id: str, vi: int, dist: float) -> bool:
    if entity_id not in self._entities_by_id or dist <= 0:
        return False
    poly = self._entities_by_id[entity_id].points
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
    entity = copy.deepcopy(self._entities_by_id[entity_id])
    entity.points = new_poly
    # Corner surgery changes topology and can no longer be represented by
    # the source rectangle/polygon's old parametric metadata.
    entity.kind = "polyline"
    entity.meta = None
    self._canvas_service.update_entities([entity])
    self._redraw()
    self._notify()
    self._fire_poly_change()
    return True


def eventFilter(self, obj, event) -> bool:
    """Intercept Tab/Backtab on the draw-mode dim-input QLineEdits."""
    if event.type() == QEvent.Type.KeyPress:
        key = event.key()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            reverse = key == Qt.Key.Key_Backtab
            if (
                self._draw_shape_w_edit is not None
                and self._draw_shape_h_edit is not None
                and obj
                in {
                    self._draw_shape_w_edit,
                    self._draw_shape_h_edit,
                    self._draw_shape_sides_spin,
                }
            ):
                fields: list[Any] = [self._draw_shape_w_edit, self._draw_shape_h_edit]
                if self._draw_shape_sides_spin is not None:
                    fields.append(self._draw_shape_sides_spin)
                current = fields.index(obj)
                target = fields[(current + (-1 if reverse else 1)) % len(fields)]
                target.setFocus()
                target.selectAll()
                return True
            if (
                self._dim_distance_edit is not None
                and self._dim_angle_edit is not None
                and (obj is self._dim_distance_edit or obj is self._dim_angle_edit)
            ):
                # Focus + select only: dirty is driven by textEdited, so the
                # live value keeps tracking the cursor until the user types.
                if (obj is self._dim_distance_edit and not reverse) or (
                    obj is self._dim_angle_edit and reverse
                ):
                    self._dim_angle_edit.setFocus()
                    self._dim_angle_edit.selectAll()
                elif (obj is self._dim_angle_edit and not reverse) or (
                    obj is self._dim_distance_edit and reverse
                ):
                    self._dim_distance_edit.setFocus()
                    self._dim_distance_edit.selectAll()
                return True  # Consume the Tab — prevent Qt focus chain
    return QWidget.eventFilter(self, obj, event)


def set_entity_records(self, records: list[dict[str, Any]], *, fit: bool = False) -> None:
    """Restore entities from :meth:`get_entity_records` output."""
    ents: list[EntityRecord] = []
    max_gid = -1
    for r in records or []:
        pts = [(float(p[0]), float(p[1])) for p in r.get("points", [])]
        meta = r.get("meta")
        if isinstance(meta, dict):
            meta = _rehydrate_meta(meta)
        gid = r.get("group")
        gid = int(gid) if isinstance(gid, (int, float)) and gid is not None else None
        if gid is not None:
            max_gid = max(max_gid, gid)
        ents.append(
            EntityRecord(
                points=pts,
                id=str(r.get("id") or ""),
                kind=str(r.get("kind", "polyline")),
                meta=meta,
                construction=bool(r.get("construction", False)),
                hidden=bool(r.get("hidden", False)),
                locked=bool(r.get("locked", False)),
                group=gid,
                layer=(str(r["layer"]) if r.get("layer") is not None else None),
            )
        )
    self._entities = ents
    self._group_labels = {}
    for r in records or []:
        g = r.get("group")
        lbl = r.get("group_label")
        if g is not None and lbl:
            self._group_labels[int(g)] = str(lbl)
    self._next_group_id = max(self._next_group_id, max_gid + 1)
    self._sel = set()
    self._sync_shape_storage_from_entities()
    if fit:
        self._needs_fit = True
        self._fit()
    else:
        self._redraw()
    self._notify()


def _connected_entities(self, start_id: str) -> set[str]:
    """Return polylines connected to *start_id* via shared vertices."""
    if start_id not in self._entities_by_id:
        return set()

    def _key(pt: tuple[float, float]) -> tuple[int, int]:
        return (round(pt[0] * 1_000_000), round(pt[1] * 1_000_000))

    entities = self._entities
    graph: dict[str, set[str]] = {e.id: set() for e in entities}
    point_to_polys: dict[tuple[int, int], set[str]] = {}
    for entity in entities:
        seen: set[tuple[int, int]] = set()
        for pt in entity.points:
            k = _key(pt)
            if k in seen:
                continue
            seen.add(k)
            point_to_polys.setdefault(k, set()).add(entity.id)

    for linked in point_to_polys.values():
        if len(linked) < 2:
            continue
        ids = list(linked)
        for i in range(len(ids)):
            a = ids[i]
            for j in range(i + 1, len(ids)):
                b = ids[j]
                graph[a].add(b)
                graph[b].add(a)

    visited: set[str] = {start_id}
    stack: list[str] = [start_id]
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, set()):
            if nxt not in visited:
                visited.add(nxt)
                stack.append(nxt)
    return visited


def set_mode(self, mode: str) -> None:
    if mode == self._mode:
        return
    if self._mode == "draw":
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._draw_snap = None
        self._draw_snap_type = None
        self._hover_snap = None
        self._hover_snap_type = None
        self._angle_snap_active = False
        self._draw_constraint = None
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
        self._dismiss_dim_inputs()
    elif self._mode == "edit":
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None
    self._mode = mode
    if mode in ("draw", "edit"):
        self._measure_mode = False
    self._set_draw_sidebar_visible(mode == "draw")
    self._refresh_draw_sidebar_state()
    self._update_cursor()
    self._redraw()
    self.modeChanged.emit(mode)


def select_geometry_category(self, category: str) -> int:
    """Select an interactive semantic category without exposing kind switches to UI code."""
    blocked = self._noninteractive_ids()
    parametric = {
        "arc",
        "circle",
        "ellipse",
        "rectangle",
        "rounded_rectangle",
        "polygon",
        "star",
        "slot",
        "spline",
        "bezier",
    }

    def matches(entity) -> bool:
        if category == "construction":
            return entity.construction
        if category == "text":
            return entity.kind == "text"
        if category == "parametric":
            return entity.kind in parametric
        if category == "generic_paths":
            return entity.kind in {"polyline", "line"}
        return entity.kind == category

    self._sel = {
        index
        for index, entity in enumerate(self._entities)
        if index not in blocked and matches(entity)
    }
    self._redraw()
    self._notify()
    self._show_flash(f"Selected {len(self._sel)} {category.replace('_', ' ')}", 900)
    return len(self._sel)


def get_command_guidance(self) -> tuple[str, str]:
    """Persistent next-step guidance for the active canvas command."""
    if self._dimension_mode:
        return self._dimension_tool.guidance(), "accent"
    if self._measure_mode:
        if self._measure_anchor is None:
            return "Scale: pick first reference point · Esc exits", "accent"
        if not self._measure_locked:
            return "Scale: pick second reference point · Shift snaps angle", "accent"
        return "Scale reference locked · Enter applies distance · Esc exits", "success"
    if self._mode == "draw":
        if not self._draw_pts and not self._draw_shape_preview_active:
            return f"{self._draw_primitive.title()}: pick first point · Esc exits", "accent"
        return (
            f"{self._draw_primitive.title()}: pick next point · Enter finishes · Esc cancels",
            "accent",
        )
    if self._mode == "edit":
        return (
            "Edit vertices: drag points · double-click an edge to insert · Esc exits",
            "accent",
        )
    if self._mode == "trim":
        return "Trim: hover a segment to preview removal · click to apply · Esc exits", "accent"
    if self._mode == "extend":
        return "Extend: hover an open end to preview · click to apply · Esc exits", "accent"
    if self._sel:
        return (
            f"{len(self._sel)} selected · use contextual actions or drag the gizmo",
            "success",
        )
    return "Select geometry · drag empty space for a selection window", "neutral"


def _moving_sample_points(self) -> list[tuple[float, float]]:
    """Sampled vertices of the selection at drag start (for object snap).

    Includes each selected shape's own CENTER (circle/arc/ellipse exact
    center, or polygon centroid) as an extra sample point — otherwise
    only the shape's rim/vertex points are tested against other shapes'
    snap targets, so dragging one circle's center onto another circle's
    center (object-centroid snapping) would never actually trigger.

    Centers are collected SEPARATELY from rim points and appended AFTER
    the rim-point subsampling below, rather than being appended to the
    same list before subsampling — a rim tessellated with >=64 points
    (e.g. the default 64-segment circle) plus one appended center
    already exceeds `_MOVE_SNAP_SAMPLE`, and the naive stride subsample
    `pts[int(i * step)]` for i in range(64) never actually lands on the
    last (center) index, silently dropping it every time. That's why
    circle-to-circle center snapping could appear to simply not work.
    """
    rim_pts: list[tuple[float, float]] = []
    center_pts: list[tuple[float, float]] = []
    for entity_id in self._sel:
        if entity_id in self._entities_by_id and not self._is_locked(entity_id):
            rim_pts.extend(self._entities_by_id[entity_id].points)
            center = self._entity_center(entity_id)
            if center is not None:
                center_pts.append(center)
    if len(rim_pts) > self._MOVE_SNAP_SAMPLE:
        step = len(rim_pts) / self._MOVE_SNAP_SAMPLE
        rim_pts = [rim_pts[int(i * step)] for i in range(self._MOVE_SNAP_SAMPLE)]
    return rim_pts + center_pts


# ── Drawing ───────────────────────────────────────────────────────────────


def _update_cursor(self) -> None:
    if self._space_pan_active:
        self.setCursor(
            Qt.CursorShape.ClosedHandCursor
            if self._space_pan_dragging
            else Qt.CursorShape.OpenHandCursor
        )
        return
    if self._mode == "pan":
        self.setCursor(
            Qt.CursorShape.ClosedHandCursor
            if self._lmb_prev is not None
            else Qt.CursorShape.OpenHandCursor
        )
        return
    if (
        self._measure_mode
        or self._dimension_mode
        or self._mode
        in (
            "draw",
            "edit",
            "trim",
            "extend",
        )
    ):
        self.setCursor(Qt.CursorShape.CrossCursor)
    elif self._mode == "select" and self._hover_vert is not None and self._sel:
        self.setCursor(Qt.CursorShape.OpenHandCursor)
    else:
        self.unsetCursor()


def toggle_dimension_mode(self, kind: str = "linear") -> None:
    if self._dimension_mode and self._dimension_kind == kind:
        self._dimension_mode = False
    else:
        self._dimension_mode = True
        if self._mode != "select":
            self.set_mode("select")
        self._dimension_kind = "angle" if kind == "angle" else "linear"
        self._measure_mode = False
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_locked = False
        self._measure_end = None
        self._dismiss_measure_edit()
    self._dim_pending_p1 = None
    self._dim_pending_p2 = None
    self._dim_selected_segments.clear()
    self._dim_hover_segment = None
    self._dimension_tool.reset()
    self._update_cursor()
    self._redraw()
    self.modeChanged.emit(self._mode)
    if self._dimension_mode:
        label = (
            "Angular dimension"
            if self._dimension_kind == "angle"
            else "Smart dimension · segments, vertices, or circle"
        )
        self._show_flash(f"{label} · snap points with clicks · Alt bypasses snap", 1800)


def toggle_measure(self) -> None:
    self._measure_mode = not self._measure_mode
    if self._measure_mode:
        if self._mode != "select":
            self.set_mode("select")
        self._dimension_mode = False
        self._dim_pending_p1 = None
        self._dim_pending_p2 = None
        self._dim_selected_segments.clear()
        self._dim_hover_segment = None
    self._measure_anchor = None
    self._measure_hover = None
    self._measure_locked = False
    self._measure_end = None
    self._measure_snapped_a = False
    self._measure_snapped_b = False
    self._dismiss_measure_edit()
    self._update_cursor()
    self._redraw()
    self.modeChanged.emit(self._mode)
    if self._measure_mode:
        count = len(self._mutable_selected_ids())
        scope = (
            f"{count} selected object{'s' if count != 1 else ''}"
            if count
            else "all visible objects"
        )
        self._show_flash(f"Scale by reference · affects {scope}", 1800)


def _background_edit_hit(self, cx: float, cy: float) -> str | None:
    if not self._bg_selected or not self._bg_editable or self._bg_pil is None:
        return None
    corners = self._background_canvas_corners()
    top_x = (corners["nw"][0] + corners["ne"][0]) / 2.0
    top_y = (corners["nw"][1] + corners["ne"][1]) / 2.0
    center = self._w2c(
        self._bg_x_mm + self._bg_w_mm / 2.0,
        self._bg_y_mm + self._bg_h_mm / 2.0,
    )
    length = max(1.0, math.hypot(top_x - center[0], top_y - center[1]))
    rotate_handle = (
        top_x + (top_x - center[0]) / length * 24.0,
        top_y + (top_y - center[1]) / length * 24.0,
    )
    if math.hypot(cx - rotate_handle[0], cy - rotate_handle[1]) <= 10:
        return "rotate"
    for name, (hx, hy) in corners.items():
        if math.hypot(cx - hx, cy - hy) <= 10:
            return name
    wx, wy = self._background_unrotate(*self._c2w(cx, cy))
    if (
        self._bg_x_mm <= wx <= self._bg_x_mm + self._bg_w_mm
        and self._bg_y_mm <= wy <= self._bg_y_mm + self._bg_h_mm
    ):
        return "move"
    return None


def show_coordinate_entry(self, initial: str = "") -> None:
    """Place a draw point or selection using CAD coordinate notation."""
    from simple_stipple.engine.cad.coordinates import parse_coordinate

    origin = self._draw_pts[-1] if self._mode == "draw" and self._draw_pts else (0.0, 0.0)
    unit_scale = 25.4 if self._unit_system == "in" else 1.0

    def _apply(text: str) -> None:
        point = parse_coordinate(text, origin=origin, scale=unit_scale)
        if self._mode == "draw" and self._draw_primitive in {"line", "polyline"}:
            self._draw_pts.append(point)
            self._draw_point_snap_types.append("coordinate")
            self._cursor_wx, self._cursor_wy = point
            self._show_dim_inputs()
            self._redraw()
            return
        if self._sel:
            if not self.move_selection_to(*point):
                self._show_flash("Selection is already at that coordinate", 900)
            return
        raise ValueError("Start a line/polyline or select geometry first")

    self._show_text_hud_prompt(
        "Coordinate: x,y · @dx,dy · @distance<angle",
        _apply,
        initial=initial,
    )


def _animate_view_to(self, target_scale: float, cx: float, cy: float) -> None:
    """Animate to a new scale anchored at a canvas point (instant when
    the widget is not visible, e.g. headless tests)."""
    target_scale = max(_MIN_SCALE, min(_MAX_SCALE, target_scale))
    wx, wy = self._c2w(cx, cy)
    end_ox = cx - wx * target_scale
    end_oy = cy + wy * target_scale
    app = QApplication.instance()
    reduced_motion = app is not None and bool(app.property("reducedMotion"))
    if not self.isVisible() or reduced_motion:
        self._scale, self._ox, self._oy = target_scale, end_ox, end_oy
        self._redraw()
        self.viewChanged.emit()
        return
    anim = QVariantAnimation(self)
    anim.setDuration(140)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    s0, ox0, oy0 = self._scale, self._ox, self._oy

    def _step(t: float) -> None:
        self._scale = s0 + (target_scale - s0) * t
        self._ox = ox0 + (end_ox - ox0) * t
        self._oy = oy0 + (end_oy - oy0) * t
        self._redraw()
        self.viewChanged.emit()

    anim.valueChanged.connect(_step)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


def add_polylines_state(self, polys: list[list[tuple[float, float]]], fit: bool = False) -> None:
    """Append polylines as new entities alongside whatever is already on
    the canvas — unlike ``set_polylines_state``, which replaces
    everything. Used for cross-tab "send selection to Draft", where the
    existing draft content must be preserved. Newly added entities end
    up selected."""
    if not polys:
        return
    entities = [
        EntityRecord(points=list(points), layer=self._active_layer)
        for points in polys
        if len(points) >= 2
    ]
    self._canvas_service.create_entities(entities)
    self._sync_shape_storage_from_entities()

    if fit:
        self._needs_fit = True
        self._fit()
    else:
        self._redraw()
    self._notify()
    self._fire_poly_change()


def get_view_state(self) -> dict[str, Any]:
    return {
        "scale": self._scale,
        "ox": self._ox,
        "oy": self._oy,
        "fit_scale": self._fit_scale,
        "mode": self._mode,
        "grid_visible": self._grid_visible,
        "grid_snap": self._grid_snap,
        "grid_spacing": self._grid_spacing,
        "guides": [[o, c] for o, c in self._guides],
        "dimensions": [dict(d) for d in self._dimensions],
        "rulers_visible": self._rulers_visible,
        "geometry_health_visible": self._geometry_health_visible,
        "curvature_visible": self._curvature_visible,
        "constraints": [constraint.to_dict() for constraint in self._constraints],
        "layer_colors": dict(self._layer_colors),
        "hidden_indices": sorted(self._flagged("hidden")),
        "locked_indices": sorted(self._flagged("locked")),
        "groups": {str(i): g for i, g in self._group_map().items()},
        "group_labels": {str(k): v for k, v in self._group_labels.items()},
        "symbols": copy.deepcopy(self._symbol_library),
    }


def set_ghost_polylines(
    self,
    polys: list[list[tuple[float, float]]] | None,
    *,
    visible: bool | None = None,
) -> None:
    """Install (or clear) the ghost-overlay polylines.

    Pass ``None`` or an empty list to clear the overlay. ``visible`` may be
    used to control overlay visibility independently of the data; if
    omitted the current visibility flag is preserved.
    """
    new_ghosts: list[list[tuple[float, float]]]
    if not polys:
        new_ghosts = []
    else:
        new_ghosts = [list(p) for p in polys]
    changed = new_ghosts != self._ghost_polys
    self._ghost_polys = new_ghosts
    if visible is not None and bool(visible) != self._ghost_visible:
        self._ghost_visible = bool(visible)
        changed = True
    if changed:
        self._redraw()


def get_status_summary(self) -> dict[str, object]:
    precision = []
    if self._grid_visible:
        precision.append(f"Grid {self._grid_spacing:g}mm")
    if self._grid_snap:
        precision.append("Snap")
    if self._measure_mode:
        precision.append("Scale")
    if self._dimension_mode:
        precision.append("Dimension")
    if self._draw_construction_mode:
        precision.append("Construction")
    topo = self.get_topology_summary()
    return {
        "mode": self._mode,
        "selected_count": len(self._sel),
        "object_count": len(self._entities),
        "precision": " · ".join(precision) if precision else "Free move",
        "topology": (f"{topo['closed']} closed · {topo['open']} open · {topo['points']} pts"),
    }


def _remove_dimensions_for_entities(self, entity_ids: set[str]) -> int:
    """Remove annotations whose driving references include deleted geometry."""

    def references_deleted(dimension: dict) -> bool:
        driving = dimension.get("driving")
        if not isinstance(driving, dict):
            return False
        sources = driving.get("sources", [])
        return any(
            isinstance(source, dict) and str(source.get("entity_id", "")) in entity_ids
            for source in sources
        )

    before = len(self._dimensions)
    self._dimensions = [
        dimension for dimension in self._dimensions if not references_deleted(dimension)
    ]
    removed = before - len(self._dimensions)
    if removed:
        self._selected_dimension = None
        self._all_dimensions_selected = False
        self._dimension_drag = None
    return removed


def get_entity_records(self) -> list[dict[str, Any]]:
    """Serialize entities (geometry + kind/meta/flags/group) for layer
    storage and sessions. JSON-safe: points become [x, y] lists."""
    out: list[dict[str, Any]] = []
    for e in self._entities:
        out.append(
            {
                "id": e.id,
                "points": [[float(x), float(y)] for x, y in e.points],
                "kind": e.kind,
                "meta": copy.deepcopy(e.meta) if e.meta is not None else None,
                "construction": e.construction,
                "hidden": e.hidden,
                "locked": e.locked,
                "group": e.group,
                "layer": e.layer,
                "group_label": (self._group_labels.get(e.group) if e.group is not None else None),
            }
        )
    return out


def _entity_center(self, entity_id: str) -> tuple[float, float] | None:
    """An entity's true center point, if it has one: the exact
    meta-defined center for parametric shapes (circle/arc/ellipse —
    precise even for coarse tessellation or open arcs), else the
    area-weighted centroid for any other closed polygon. Returns None
    for open polylines/lines, which have no meaningful "center".
    """
    if entity_id not in self._entities_by_id:
        return None
    e = self._entities_by_id[entity_id]
    meta = e.meta
    if isinstance(meta, dict):
        center = meta.get("center")
        if isinstance(center, (tuple, list)) and len(center) == 2:
            return (float(center[0]), float(center[1]))
    poly = self._geometry_for_entity_by_id(entity_id).tessellate()
    if len(poly) >= 3 and self._is_poly_closed(poly):
        return _polygon_centroid(poly)
    return None


def _dismiss_shape_dim_inputs(self) -> None:
    for edit in (
        self._draw_shape_w_edit,
        self._draw_shape_h_edit,
        self._draw_shape_sides_spin,
    ):
        if edit is None:
            continue
        if bool(edit.property("shape_hud_temp")):
            edit.hide()
            edit.deleteLater()
    if self._draw_shape_w_edit is not None and bool(
        self._draw_shape_w_edit.property("shape_hud_temp")
    ):
        self._draw_shape_w_edit = None
    if self._draw_shape_h_edit is not None and bool(
        self._draw_shape_h_edit.property("shape_hud_temp")
    ):
        self._draw_shape_h_edit = None
    if self._draw_shape_sides_spin is not None and bool(
        self._draw_shape_sides_spin.property("shape_hud_temp")
    ):
        self._draw_shape_sides_spin = None
