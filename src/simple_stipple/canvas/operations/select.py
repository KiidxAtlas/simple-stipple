"""Selection, vertex, Bezier, and draw-commit interaction service."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from simple_stipple.core.document.model import EntityRecord
from simple_stipple.core.cad.editor_geometry import (
    move_entity_control_point,
    synchronize_entity_control_points,
    transform_entity_metadata,
)
from simple_stipple.core.cad.shape_factory import transform_meta


class SelectionService:
    """Own selection-mode entity and vertex state transitions."""

    def __init__(self, host) -> None:
        self._host = host

    def _transform_entity_meta(
        self,
        entity_id: str,
        *,
        center: tuple[float, float],
        kind: str,
        meta: dict[str, Any] | None,
        transform: str,
        factor: float | None = None,
        angle_deg: float = 0.0,
        axis: str | None = None,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> None:
        """Transform an entity's parametric metadata via its Shape class.

        All per-kind transform math lives on the Shape subclasses in
        src/simple_stipple/core/cad/shapes.py — this is a thin delegation shim kept for
        the legacy kind+meta storage until the canvas migrates to shapes.
        """
        entity = self._host._entity_for_id(entity_id)
        if entity is None:
            return
        transform_entity_metadata(
            entity,
            transform=transform,
            center=center,
            factor=factor,
            angle_degrees=angle_deg,
            axis=axis,
            dx=dx,
            dy=dy,
        )

    @staticmethod
    def _translated_entity_meta(
        kind: str,
        meta: dict[str, Any] | None,
        dx: float,
        dy: float,
    ) -> dict[str, Any] | None:
        # Translate never changes kind (only a non-uniform scale can, via
        # Shape.scale_xy), so the caller's own `kind` stays valid — only
        # the metadata needs the transformed result.
        result = transform_meta(kind, meta, transform="translate", dx=dx, dy=dy)
        return result[1] if result is not None else None

    def _key_delete(self) -> None:
        if self._host._selected_guide is not None:
            self._delete_selected_guide()
            return
        if self._host._selected_dimension is not None:
            self._host._delete_selected_dimension()
            return
        if self._host._mode == "edit":
            if self._host._edit_selected_verts:
                self._delete_edit_vertices(set(self._host._edit_selected_verts))
                return
            if self._host._hover_vert is not None:
                self._delete_edit_vertices({self._host._hover_vert})
                return
        if self._host._mode == "select":
            self._host.delete_selected()

    def _delete_selected_guide(self) -> None:
        """Remove the currently selected ruler guide (Delete/Backspace)."""
        gi = self._host._selected_guide
        if gi is None or not (0 <= gi < len(self._host._guides)):
            self._host._selected_guide = None
            return
        self._host._remove_guide(gi)
        self._host._selected_guide = None
        self._host._guide_drag = None
        self._host._notify()

    def _key_backspace(self) -> None:
        if self._host._selected_guide is not None:
            self._delete_selected_guide()
            return
        if self._host._selected_dimension is not None:
            self._host._delete_selected_dimension()
            return
        if getattr(self._host, "_mode", None) == "draw" and getattr(self._host, "_draw_pts", []):
            self._host._draw_pts.pop()
            if getattr(self._host, "_draw_point_snap_types", []):
                self._host._draw_point_snap_types.pop()
            if not getattr(self._host, "_draw_pts", []):
                self._host._dismiss_dim_inputs()
                self._host._draw_constraint = None
            self._host._refresh_draw_sidebar_state()
            self._host._redraw()
        elif getattr(self._host, "_mode", None) == "edit":
            self._key_delete()
        elif getattr(self._host, "_mode", None) == "select":
            self._host.delete_selected()

    def _linked_vertices_by_id(self, entity_id: str, vert_idx: int) -> set[tuple[str, int]]:
        entity = self._host._document.entity_for_id(entity_id)
        if entity is None or vert_idx >= len(entity.points):
            return set()
        target_pt = entity.points[vert_idx]
        linked = {(entity_id, vert_idx)}

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        for eid in self._host._document.entity_ids():
            other = self._host._document.entity_for_id(eid)
            if other is None:
                continue
            if eid == entity_id:
                is_closed = len(other.points) >= 4 and _eq(other.points[0], other.points[-1])
                if is_closed and (vert_idx == 0 or vert_idx == len(other.points) - 1):
                    linked.add((eid, 0))
                    linked.add((eid, len(other.points) - 1))
            else:
                for j, pt in enumerate(other.points):
                    if _eq(target_pt, pt):
                        linked.add((eid, j))
        return linked

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        if self._host._edit_poly is None or self._host._edit_vert is None:
            return
        entity = self._host._entities_by_id[self._host._edit_poly]
        if move_entity_control_point(
            entity,
            self._host._edit_vert,
            (wx, wy),
            displayed_point_count=len(entity.points),
        ):
            return

        targets = (
            getattr(self._host, "_edit_drag_targets", None)
            or getattr(self._host, "_edit_linked_verts", None)
            or {(self._host._edit_poly, self._host._edit_vert)}
        )
        for pi, vi in targets:
            if self._host._is_locked(pi):
                continue
            entity = self._host._entity_for_id(pi)
            if entity is None or not (0 <= vi < len(entity.points)):
                continue
            entity.points[vi] = (wx, wy)

        if self._host._edit_poly is not None:
            entity = self._host._entity_for_id(self._host._edit_poly)
            if entity is not None:
                synchronize_entity_control_points(entity)

    def _bezier_handles(self, entity_id: str) -> list[tuple[int, str, tuple[float, float]]]:
        """Return editable incoming/outgoing handle tips for one Bézier."""
        entity = self._host._entity_for_id(entity_id)
        if entity is None or entity.kind != "bezier" or not entity.meta:
            return []
        count = len(entity.points)
        legacy = [tuple(value) for value in entity.meta.get("tangents", [])]
        outgoing = [tuple(value) for value in entity.meta.get("handles_out", legacy)]
        incoming = [
            tuple(value) for value in entity.meta.get("handles_in", [(-x, -y) for x, y in legacy])
        ]
        outgoing.extend([(0.0, 0.0)] * (count - len(outgoing)))
        incoming.extend([(0.0, 0.0)] * (count - len(incoming)))
        handles: list[tuple[int, str, tuple[float, float]]] = []
        for index, anchor in enumerate(entity.points):
            for side, vector in (("in", incoming[index]), ("out", outgoing[index])):
                handles.append(
                    (index, side, (anchor[0] + float(vector[0]), anchor[1] + float(vector[1])))
                )
        return handles

    def _find_bezier_handle(self, cx: float, cy: float) -> tuple[str, int, str] | None:
        """Find the closest editable Bézier handle among selected entities.

        Returns (entity_id, anchor_index, side) or None.
        """
        best: tuple[float, str, int, str] | None = None
        if self._host._mode == "select":
            entity_ids = list(self._host._sel)
        else:
            entity_ids = [e.id for e in self._host._entities]
        for entity_id in entity_ids:
            entity = self._host._document.entity_for_id(entity_id)
            if entity is None:
                continue
            for anchor_index, side, point in self._bezier_handles(entity_id):
                hx, hy = self._host._w2c(*point)
                distance = math.hypot(cx - hx, cy - hy)
                if distance <= 9.0 and (best is None or distance < best[0]):
                    best = (distance, entity_id, anchor_index, side)
        return None if best is None else (best[1], best[2], best[3])

    def _set_bezier_handle(
        self,
        entity_id: str,
        anchor_index: int,
        side: str,
        point: tuple[float, float],
        *,
        break_pair: bool = False,
    ) -> bool:
        entity = self._host._entity_for_id(entity_id)
        if (
            entity is None
            or entity.kind != "bezier"
            or not entity.meta
            or not 0 <= anchor_index < len(entity.points)
        ):
            return False
        anchor = entity.points[anchor_index]
        vector = (point[0] - anchor[0], point[1] - anchor[1])
        count = len(entity.points)
        legacy = [tuple(value) for value in entity.meta.get("tangents", [])]
        outgoing = [tuple(value) for value in entity.meta.get("handles_out", legacy)]
        incoming = [
            tuple(value) for value in entity.meta.get("handles_in", [(-x, -y) for x, y in legacy])
        ]
        outgoing.extend([(0.0, 0.0)] * (count - len(outgoing)))
        incoming.extend([(0.0, 0.0)] * (count - len(incoming)))
        node_types = [str(value) for value in entity.meta.get("node_types", [])]
        node_types.extend(["symmetric"] * (count - len(node_types)))
        if break_pair:
            node_types[anchor_index] = "corner"
        mode = node_types[anchor_index]
        target = incoming if side == "in" else outgoing
        other = outgoing if side == "in" else incoming
        target[anchor_index] = vector
        if mode == "symmetric":
            other[anchor_index] = (-vector[0], -vector[1])
        elif mode == "smooth":
            old_length = math.hypot(*other[anchor_index])
            length = math.hypot(*vector)
            if length > 1e-12:
                paired_length = old_length if old_length > 1e-12 else length
                other[anchor_index] = (
                    -vector[0] / length * paired_length,
                    -vector[1] / length * paired_length,
                )
        entity.meta["handles_in"] = incoming
        entity.meta["handles_out"] = outgoing
        entity.meta["node_types"] = node_types
        entity.meta["tangents"] = outgoing
        self._host._sync_shape_storage_from_entities()
        return True

    def set_bezier_node_type(self, entity_id: str, anchor_index: int, mode: str) -> bool:
        """Convert an anchor to corner, smooth, or symmetric behavior."""
        if mode not in {"corner", "smooth", "symmetric"}:
            return False
        entity = self._host._entity_for_id(entity_id)
        if (
            entity is None
            or entity.kind != "bezier"
            or not entity.meta
            or not 0 <= anchor_index < len(entity.points)
        ):
            return False
        before = self._host._canvas_service.begin_preview()
        node_types = [str(value) for value in entity.meta.get("node_types", [])]
        node_types.extend(["symmetric"] * (len(entity.points) - len(node_types)))
        node_types[anchor_index] = mode
        entity.meta["node_types"] = node_types
        if mode != "corner":
            handles = {
                side: tip for vi, side, tip in self._bezier_handles(entity_id) if vi == anchor_index
            }
            anchor = entity.points[anchor_index]
            out = handles.get("out") or anchor
            vector = (out[0] - anchor[0], out[1] - anchor[1])
            self._set_bezier_handle(entity_id, anchor_index, "out", out)
            if math.hypot(*vector) <= 1e-12:
                self._set_bezier_handle(
                    entity_id, anchor_index, "out", (anchor[0] + 1.0, anchor[1])
                )
        self._host._canvas_service.commit_preview(before)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _select_edit_vertices_in_rect(
        self, x1c: float, y1c: float, x2c: float, y2c: float, *, additive: bool = True
    ) -> int:
        if not additive:
            self._host._edit_selected_verts.clear()
        added = 0
        for entity in self._host._entities:
            if not self._host._entity_selectable_by_id(entity.id):
                continue
            for vi, (vx, vy) in enumerate(entity.points):
                cx, cy = self._host._w2c(vx, vy)
                if x1c <= cx <= x2c and y1c <= cy <= y2c:
                    self._host._edit_selected_verts.add((entity.id, vi))
                    added += 1
        return added

    def _shape_primitive_active(self) -> bool:
        primitive = getattr(self._host, "_draw_primitive", "polyline")
        return primitive in {
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
        } or primitive in getattr(self._host, "_PROCEDURAL_QUICK_SHAPES", set())

    def _is_near_start(self) -> bool:
        if getattr(self._host, "_mode", None) != "draw":
            return False
        draw_pts = getattr(self._host, "_draw_pts", [])
        if len(draw_pts) < 3:
            return False
        cursor_wx = getattr(self._host, "_cursor_wx", None)
        cursor_wy = getattr(self._host, "_cursor_wy", None)
        if cursor_wx is None or cursor_wy is None:
            return False
        start_cx, start_cy = self._host._w2c(*draw_pts[0])
        cur_cx, cur_cy = self._host._w2c(cursor_wx, cursor_wy)
        return math.hypot(cur_cx - start_cx, cur_cy - start_cy) < 10.0

    def _finish_draw(self, *, close: bool = False) -> None:
        if (
            getattr(self._host, "_mode", None) != "draw"
            or len(getattr(self._host, "_draw_pts", [])) < 2
        ):
            return
        draw_pts = self._host._draw_pts
        primitive = getattr(self._host, "_draw_primitive", "polyline")
        if primitive == "spline" and len(draw_pts) < 3:
            self._host._show_flash("Spline needs at least 3 points", 900)
            return
        if close and draw_pts[0] != draw_pts[-1]:
            draw_pts.append(draw_pts[0])
        drawn = list(draw_pts)
        self._commit_drawn_polyline(
            drawn, primitive=primitive, close=close, created_flash="Polyline created"
        )

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
        before = self._host._canvas_service.begin_preview()
        split_happened = False
        split_closed = 0
        split_open = 0
        carved_regions = 0
        kind = "polyline"
        meta: dict[str, Any] | None = None
        cutter_poly = list(poly)
        if primitive == "line" and len(poly) >= 2:
            kind = "line"
            meta = {"start": tuple(poly[0]), "end": tuple(poly[-1])}
        elif primitive == "arc" and len(poly) >= 3:
            from simple_stipple.core.cad.geometry import (
                arc_spec_from_center_start_end,
                arc_spec_from_three_points,
            )
            from simple_stipple.core.cad.shape_factory import ShapeFactory

            if getattr(self._host, "_draw_arc_mode", "center-start-end") == "center-start-end":
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
                # Cutting with the three construction points produced two
                # straight chords.  Use the actual displayed arc instead.
                cutter_poly = list(
                    ShapeFactory.arc(
                        spec.center,
                        spec.radius,
                        spec.start_angle,
                        spec.end_angle,
                        segments=128,
                    ).points
                )
        elif primitive == "spline" and len(poly) >= 2:
            from simple_stipple.core.cad.geometry import build_spline_poly

            kind = "spline"
            meta = {
                "segments": 64,
                "closed": close,
                "control_points": [tuple(pt) for pt in poly],
                "degree": 3,
            }
            cutter_poly = build_spline_poly(poly, segments=64, closed=close)

        can_cut_split = getattr(self._host, "_draw_split_enabled", True) and primitive in {
            "line",
            "polyline",
            "arc",
            "spline",
        }
        if can_cut_split and not close and len(cutter_poly) >= 2:
            split_happened, split_closed, split_open = self._host._split_geometry_with_line(
                cutter_poly
            )
        elif getattr(self._host, "_draw_split_enabled", True) and close and len(cutter_poly) >= 4:
            split_happened, carved_regions = self._host._carve_geometry_with_shape(cutter_poly)

        # Closed profiles are persistent CAD geometry: carving subtracts their
        # area but does not consume the profile.  Only an open line/path that
        # fully splits a closed region acts as a disposable cutting stroke.
        consume_cutter = bool(split_closed and not close)

        new_idx: int | None = None
        if not consume_cutter:
            rec = EntityRecord(
                points=list(poly), kind=kind, meta=meta, layer=self._host._active_layer
            )
            self._host._entities.append(rec)
            new_idx = len(self._host._entities) - 1
            if getattr(self._host, "_draw_construction_mode", False):
                self._host._entities[new_idx].construction = True

        merged_idx: int | None = None
        if (
            primitive in {"line", "polyline"}
            and not getattr(self._host, "_draw_construction_mode", False)
            and not split_happened
            and new_idx is not None
            and any(
                snap_type == "vertex"
                for snap_type in getattr(self._host, "_draw_point_snap_types", [])
            )
        ):
            merged_idx = self._try_merge_endpoints()
            if merged_idx is not None:
                new_idx = merged_idx

        if consume_cutter:
            live_ids = set(self._host._entities_by_id)
            self._host._document.selection = {
                eid for eid in self._host._last_split_result_ids if eid in live_ids
            }
        elif new_idx is not None:
            self._host._document.selection = {self._host._entities[new_idx].id}
        self._host._canvas_service.commit_preview(before)
        self._host._notify()
        self._host._fire_poly_change()
        self._host._draw_pts = []
        self._host._draw_point_snap_types = []
        self._host._draw_constraint = None
        self._host._dismiss_dim_inputs()
        self._host._refresh_draw_sidebar_state()
        if split_happened:
            if carved_regions:
                self._host._show_flash(f"Carved {carved_regions} region(s)", 1000)
            elif split_closed and split_open:
                self._host._show_flash("Regions cut + segments split", 900)
            elif split_closed:
                self._host._show_flash("Regions cut", 900)
            else:
                self._host._show_flash("Segments split · cutter kept", 1000)
        elif (
            merged_idx is not None
            and new_idx is not None
            and self._host._is_poly_closed(self._host._entities[new_idx].points)
        ):
            self._host._show_flash("Polyline closed", 800)
        elif merged_idx is not None:
            self._host._show_flash("Segments merged", 800)
        else:
            self._host._show_flash(created_flash, 800)
        self._host._redraw()
        return True

    def _finish_pen(self, *, close: bool = False) -> bool:
        """Commit the in-progress pen-tool curve as a ``kind="bezier"``
        entity (anchors on ``.points``, tangent offsets in ``meta``)."""
        if len(self._host._pen_pts) < 2:
            self._cancel_pen()
            return False
        from simple_stipple.core.cad.geometry import build_bezier_poly

        entity = EntityRecord(
            # Keep one anchor per tangent.  ``build_bezier_poly`` closes the
            # final-to-first segment from this flag; duplicating anchor zero
            # here created an extra zero-handle node and made later editing
            # disagree with what was drawn.
            points=list(self._host._pen_pts),
            kind="bezier",
            meta={
                "tangents": list(self._host._pen_tangents),
                "handles_out": list(self._host._pen_tangents),
                "handles_in": [(-x, -y) for x, y in self._host._pen_tangents],
                "node_types": [
                    "smooth" if math.hypot(x, y) > 1e-9 else "corner"
                    for x, y in self._host._pen_tangents
                ],
                "segments": 64,
                "closed": close,
            },
            layer=self._host._active_layer,
            construction=getattr(self._host, "_draw_construction_mode", False),
        )
        preview = build_bezier_poly(
            entity.points,
            list(self._host._pen_tangents),
            segments=64,
            closed=close,
        )
        changed = False
        closed_splits = open_splits = carved_count = 0
        if self._host._draw_split_enabled and not entity.construction and len(preview) >= 2:
            before = self._host._canvas_service.begin_preview()
            if close:
                changed, carved_count = self._host._carve_geometry_with_shape(preview)
            else:
                changed, closed_splits, open_splits = self._host._split_geometry_with_line(preview)
            if changed:
                # Closed profiles remain editable after carving.  Only an
                # open curve that fully crosses a region is consumed.
                consume = not close and closed_splits > 0
                if not consume:
                    self._host._entities.append(entity)
                    self._host._document.selection = {self._host._entities[-1].id}
                else:
                    live_ids = set(self._host._entities_by_id)
                    self._host._document.selection = {
                        eid for eid in self._host._last_split_result_ids if eid in live_ids
                    }
                self._host._canvas_service.commit_preview(before)
        if not changed:
            self._host._canvas_service.create_entities([entity])
        self._host._pen_pts.clear()
        self._host._pen_tangents.clear()
        self._host._pen_dragging = False
        self._host._pen_press_screen = None
        if carved_count:
            self._host._show_flash(f"Carved {carved_count} region(s)", 1000)
        elif closed_splits:
            self._host._show_flash("Regions cut", 900)
        elif open_splits:
            self._host._show_flash("Segments split · curve kept", 1000)
        else:
            self._host._show_flash("Curve created", 800)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _cancel_pen(self) -> None:
        self._host._pen_pts.clear()
        self._host._pen_tangents.clear()
        self._host._pen_dragging = False
        self._host._pen_press_screen = None
        self._host._redraw()

    def _close_selected_polylines(self, *, record_undo: bool = True) -> int:
        indices = self._host._selected_ids()
        if not indices:
            return 0
        candidates = []
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            if len(poly) < 3 or self._host._is_poly_closed(poly):
                continue
            entity = deepcopy(entity)
            entity.points = [*poly, poly[0]]
            candidates.append(entity)
        changed = len(candidates)
        if changed:
            self._host._canvas_service.update_entities(candidates, record=record_undo)
            self._host._redraw()
            self._host._notify()
            self._host._fire_poly_change()
        return changed

    def close_selection_as_path(self) -> None:
        """Join the selected segments into one path (when several are
        selected) and close it — the context-menu "Close path" action."""
        if not self._host._sel:
            return
        # One push covers both steps below — otherwise merge-then-close
        # (a single user-visible action) costs two separate Ctrl+Z presses.
        before = self._host._canvas_service.begin_preview()
        if len(self._host._sel) > 1:
            self._host.merge_selected_segments_to_objects(record_undo=False)
        closed = self._close_selected_polylines(record_undo=False)
        self._host._canvas_service.commit_preview(before)
        if closed:
            self._host._show_flash("Path closed", 900)
        else:
            self._host._show_flash("Already closed", 900)

    def _open_selected_polylines(self) -> int:
        indices = self._host._selected_ids()
        if not indices:
            return 0
        candidates = []
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            if not self._host._is_poly_closed(poly) or len(poly) < 2:
                continue
            entity = deepcopy(entity)
            entity.points = poly[:-1]
            candidates.append(entity)
        changed = len(candidates)
        if changed:
            self._host._canvas_service.update_entities(candidates)
            self._host._redraw()
            self._host._notify()
            self._host._fire_poly_change()
        return changed

    def _toggle_selected_construction(self) -> None:
        if not self._host._sel:
            return
        candidates = []
        for entity_id in self._host._sel:
            entity = self._host._document.entity_for_id(entity_id)
            if entity is not None:
                e = deepcopy(entity)
                e.construction = not e.construction
                candidates.append(e)
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()

    def _try_merge_endpoints(self) -> int | None:
        if len(self._host._entities) < 2:
            return None
        survivor_idx = len(self._host._entities) - 1
        if len(self._host._entities[survivor_idx].points) < 2:
            return None

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        merged_any = False
        changed = True
        while changed:
            changed = False
            survivor = self._host._entities[survivor_idx].points
            if len(survivor) < 2:
                break
            survivor_start, survivor_end = survivor[0], survivor[-1]
            for i, poly in enumerate(e.points for e in self._host._entities):
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
                popped_was_construction = self._host._entities[i].construction
                survivor_was_construction = self._host._entities[survivor_idx].construction
                self._host._entities[survivor_idx].points = merged
                self._host._entities[survivor_idx].kind = "polyline"
                self._host._entities[survivor_idx].meta = None
                del self._host._entities[i]
                if i < survivor_idx:
                    survivor_idx -= 1
                if popped_was_construction or survivor_was_construction:
                    self._host._entities[survivor_idx].construction = True
                merged_any = True
                changed = True
                break
        return survivor_idx if merged_any else None

    def _delete_edit_vertices(self, verts: set[tuple[str, int]]) -> int:
        if not verts:
            return 0
        requested: dict[str, set[int]] = {}
        for eid, vi in verts:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            if entity.locked:
                continue
            poly = entity.points
            if not (0 <= vi < len(poly)):
                continue
            requested.setdefault(eid, set()).add(vi)

        grouped: dict[str, set[int]] = {}
        for eid, vis in requested.items():
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            closed = self._host._is_poly_closed(poly)
            available = (len(poly) - 1) if closed else len(poly)
            max_removable = max(0, available - 3)
            if max_removable <= 0:
                continue
            candidates = sorted(vi for vi in vis if not (closed and vi == len(poly) - 1))
            keep = set(candidates[:max_removable])
            if keep:
                grouped[eid] = keep

        if not grouped:
            return 0
        deleted = 0
        updated = []
        for eid in sorted(grouped.keys(), reverse=True):
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            entity = deepcopy(entity)
            poly = entity.points
            closed = self._host._is_poly_closed(poly)
            for vi in sorted(grouped[eid], reverse=True):
                if 0 <= vi < len(poly):
                    poly.pop(vi)
                    deleted += 1
            if closed and len(poly) >= 4:
                poly[-1] = poly[0]
            updated.append(entity)
        self._host._canvas_service.update_entities(updated)
        self._host._edit_selected_verts.clear()
        self._host._edit_drag_targets = set()
        self._host._edit_linked_verts = set()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return deleted

    def _prompt_offset_selected(self) -> None:
        if not self._host._sel:
            self._host._show_flash("Select shape(s) first", 1000)
            return
        self._host._show_hud_prompt(
            "Offset distance (mm)",
            1.0,
            self._host.offset_selected,
            preview=self._preview_offset_selected,
        )

    def _preview_offset_selected(self, distance: float) -> None:
        preview = []
        for entity_id in self._host._selected_ids():
            entity = self._host._entity_for_id(entity_id)
            if entity is None:
                continue
            poly = self._host._offset_polyline(entity.points, distance)
            if poly is not None and len(poly) >= 2:
                preview.append(poly)
        self._host._set_operation_preview(preview)
