# Helper methods extracted from view.py

from __future__ import annotations

import copy
import math
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation
from PySide6.QtWidgets import QApplication, QWidget

from simple_stipple.canvas.constants import MIN_SCALE as _MIN_SCALE
from simple_stipple.core.document.model import EntityRecord

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


def eventFilter(self, obj, event) -> bool:
    """Tab/Backtab cycling on HUD inputs; Escape cancels the badge editor."""
    if event.type() == QEvent.Type.KeyPress:
        key = event.key()
        # Escape on the selection badge editor cancels the live-typing preview
        # instead of committing whatever partial value is visible.
        if key == Qt.Key.Key_Escape and obj is self._sel_dim_edit:
            snapshot = self._sel_dim_snapshot
            self._hud_service._dismiss_sel_dim_editor()
            if snapshot is not None:
                self._canvas_service.cancel_preview(snapshot)
            self._redraw()
            return True
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


def connected_entity_ids(
    entities: list[EntityRecord], entities_by_id: dict[str, EntityRecord], start_id: str
) -> set[str]:
    """Return entity IDs connected to *start_id* through shared vertices."""
    if start_id not in entities_by_id:
        return set()

    def _key(pt: tuple[float, float]) -> tuple[int, int]:
        return (round(pt[0] * 1_000_000), round(pt[1] * 1_000_000))

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
        drag_shapes = {
            "rectangle",
            "rounded_rectangle",
            "slot",
            "circle",
            "ellipse",
            "polygon",
            "star",
        }
        if self._draw_primitive in drag_shapes:
            if not self._draw_shape_preview_active:
                return (
                    f"{self._draw_primitive.replace('_', ' ').title()}: drag to size · Esc exits",
                    "accent",
                )
            return (
                f"{self._draw_primitive.replace('_', ' ').title()}: release to place · Esc cancels",
                "accent",
            )
        if self._draw_primitive == "text":
            return "Text: click to place text · Esc exits", "accent"
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
    if self._mode == "knife":
        return "Knife: drag across shapes to cut them · Esc exits", "accent"
    if self._sel:
        return (
            f"{len(self._sel)} selected · use contextual actions or drag the gizmo",
            "success",
        )
    return "Select geometry · drag empty space for a selection window", "neutral"


def get_context_actions(self) -> tuple[tuple[str, str, str], ...]:
    """Return safe, canvas-local actions for the current drawing state."""
    # Selection actions deliberately live in the persistent status strip as
    # well as the right-click menu.  They are the three highest-frequency
    # organization operations and should not require leaving the geometry to
    # hunt through the inspector.
    if self._mode != "draw" and self._sel:
        actions: list[tuple[str, str, str]] = [
            ("delete-selection", "Delete", "Delete the selected geometry"),
        ]
        if len(self._sel) > 1:
            actions.append(("group-selection", "Group", "Group the selected geometry"))
        active_layer = self.active_layer
        selected_layers = {
            entity.layer
            for entity_id in self._sel
            if (entity := self._entities_by_id.get(entity_id)) is not None
        }
        if active_layer and selected_layers != {active_layer}:
            actions.append(
                (
                    "move-to-active-layer",
                    f"Move to {active_layer}",
                    f"Move the selection to the active layer, {active_layer}",
                )
            )
        return tuple(actions[:3])
    if self._mode != "draw":
        return ()
    point_count = len(self._draw_pts)
    if self._draw_shape_preview_active:
        # Shape preview commits with Enter or a second click and cancels with
        # Esc — a two-button "Commit shape / Cancel" strip just flickered in as
        # a distracting popup, so leave the strip clean here.
        return ()
    if point_count == 0:
        return ()
    draw_actions: list[tuple[str, str, str]] = [
        ("undo-point", "Undo point", "Remove the last drawn point"),
    ]
    if point_count >= 2:
        draw_actions.append(("finish-path", "Finish path", "Finish the open path"))
    if point_count >= 3:
        draw_actions.append(("close-path", "Close path", "Finish and close the path"))
    draw_actions.append(("cancel-draw", "Cancel", "Discard the in-progress drawing"))
    return tuple(draw_actions)


def trigger_context_action(self, action: str) -> bool:
    """Execute a validated contextual action in the canvas command layer."""
    available = {action_id for action_id, _label, _tooltip in get_context_actions(self)}
    if action not in available:
        return False
    if action == "undo-point":
        self._key_backspace()
    elif action == "delete-selection":
        self.delete_selected()
    elif action == "group-selection":
        self._group_selected()
    elif action == "move-to-active-layer":
        active_layer = self.active_layer
        if not active_layer:
            return False
        self.move_indices_to_layer(self.get_selected_ids(), active_layer)
    elif action == "finish-path":
        self._finish_draw(close=False)
    elif action == "close-path":
        self._finish_draw(close=True)
    elif action == "commit-shape":
        self._commit_shape_preview()
    elif action == "cancel-draw":
        self._cancel_draw_points()
    else:
        return False
    self._refresh_draw_sidebar_state()
    return True


# ── Drawing ───────────────────────────────────────────────────────────────


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
    from simple_stipple.core.cad.geometry import parse_coordinate

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
        from simple_stipple.ui.components.units import format_length

        precision.append(f"Grid {format_length(self._grid_spacing, self._unit_system)}")
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
