# pyright: reportAttributeAccessIssue=false

"""Tools that select and edit existing geometry."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, Polygon

from simple_stipple.canvas.constants import DRAG_THRESH
from simple_stipple.canvas.tools.base import CanvasTool
from simple_stipple.canvas.tools.dragging import (
    _seg_hits_rect,
    apply_bezier_handle_drag,
    apply_edit_drag,
    release_bezier_handle_drag,
    start_bezier_handle_drag,
)
from simple_stipple.canvas.view.helpers import connected_entity_ids


class EditTool(CanvasTool):
    """Vertex editing: drag vertices, band-select vertices, insert on edge."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        handle_hit = v._find_bezier_handle(pos.x(), pos.y())
        if handle_hit is not None:
            start_bezier_handle_drag(v, handle_hit)
            return True
        hit = v._hit_test.nearest_vertex_by_id(pos.x(), pos.y())

        if shift and hit is not None:
            if hit in v._edit_selected_verts:
                v._edit_selected_verts.discard(hit)
            else:
                v._edit_selected_verts.add(hit)
            v._redraw()
            return True

        if hit is None:
            # Empty space: default drag behavior is box selection (matches
            # Select mode). Shift adds to the current vertex selection;
            # a plain drag replaces it.
            v._shift_drag = True
            v._band_start = pos
            v._band_additive = shift
            v._lmb_prev = pos
            v._lmb_press = None
            return True

        eid, vi = hit
        entity = v._document.entity_for_id(eid)
        if entity is None:
            return True
        if entity.locked:
            v._show_flash("Shape is locked", 1200)
            return True
        if vi < 0 or vi >= len(entity.points):
            return True
        v._edit_poly = eid
        v._edit_vert = vi
        v._edit_dragging = True
        v._edit_drag_moved = False
        v._edit_undo_pushed = False
        v._edit_drag_anchor = entity.points[vi]
        if hit in v._edit_selected_verts and len(v._edit_selected_verts) > 1:
            v._edit_drag_targets = set(v._edit_selected_verts)
        else:
            v._edit_selected_verts = {hit}
            v._edit_drag_targets = v._linked_vertices_by_id(eid, vi)
        v._edit_linked_verts = set(v._edit_drag_targets)
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if apply_bezier_handle_drag(v, event):
            return True
        if apply_edit_drag(v, event):
            return True

        if v._shift_drag and v._band_start:
            v._lmb_prev = pos
            v._redraw()
            return True
        old_handle = v._hover_bezier_handle
        v._hover_bezier_handle = v._find_bezier_handle(pos.x(), pos.y())
        old_hover = v._hover_vert
        v._hover_vert = v._hit_test.nearest_vertex_by_id(pos.x(), pos.y())
        if v._hover_vert != old_hover or v._hover_bezier_handle != old_handle:
            v._update_cursor()
            v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._bezier_handle_drag is not None:
            release_bezier_handle_drag(v)
            return True
        if v._shift_drag and v._band_start:
            bx, by = v._band_start.x(), v._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            v._select_edit_vertices_in_rect(x1c, y1c, x2c, y2c, additive=v._band_additive)
            v._shift_drag = False
            v._band_start = None
            v._band_additive = False
            v._lmb_prev = None
            v._redraw()
            return True
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        # Edit the geometry the user can actually see. Procedural entities
        # (circle, rectangle, spline, etc.) may store sparse control points
        # while rendering a tessellation reconstructed from metadata.
        wx, wy = v._c2w(pos.x(), pos.y())
        hit = None
        best_dist = 8.0
        for eid in v._document.entity_ids():
            if not v._entity_selectable_by_id(eid):
                continue
            entity = v._document.entity_for_id(eid)
            if entity is None:
                continue
            visible_poly = v._flattened_points_by_id(eid)
            dist, result = cast(
                tuple[float | None, tuple[int, tuple[float, float]] | None],
                v._hit_test.closest_point(
                    visible_poly,
                    wx,
                    wy,
                    pos.x(),
                    pos.y(),
                    return_segment=True,
                ),
            )
            if dist is not None and dist < best_dist and result is not None:
                best_dist = dist
                seg_idx, closest_pt = result
                hit = (eid, seg_idx, closest_pt, visible_poly)
        if hit is not None:
            eid, seg_idx, pt, visible_poly = hit
            entity = v._document.entity_for_id(eid)
            if entity is None:
                return True
            if entity.locked:
                v._show_flash("Shape is locked", 1200)
                return True
            entity = deepcopy(entity)
            if entity.kind != "polyline" or entity.meta is not None:
                # Adding a vertex changes topology, which most procedural
                # schemas cannot represent. Demote once, using the rendered
                # geometry as the canonical editable path, so the new vertex
                # remains visible and draggable after redraw/save/export.
                entity.points = list(visible_poly)
                entity.kind = "polyline"
                entity.meta = None
            poly = entity.points
            if seg_idx + 1 > len(poly):
                return True
            poly.insert(seg_idx + 1, pt)
            v._canvas_service.update_entities([entity])
            v._edit_selected_verts = {(eid, seg_idx + 1)}
            v._redraw()
            v._notify()
            v._fire_poly_change()
        return True


class SelectTool(CanvasTool):
    """Selection, box select, drag-move, direct vertex editing, gizmos."""

    def press_overlays(self, event: QMouseEvent) -> bool:
        """Selection badges and the transform gizmo take priority over
        everything else (including measure mode)."""
        from PySide6.QtCore import QPointF

        v = self.v
        pos = event.position()
        if (
            event.modifiers() & Qt.KeyboardModifier.AltModifier
            and len(v._hit_test.entities_at(pos.x(), pos.y())) > 1
        ):
            # Alt-click is reserved for cycling overlapping geometry. Let the
            # selection tool see it instead of an existing selection gizmo.
            return False
        # Vertex and bezier-handle editing live in Edit mode only; Select mode
        # transforms whole shapes (gizmo, move, badge editors).
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
                v._show_flash("Resize · Shift keeps proportions · Alt scales from center", 1800)
                v._redraw()
                return True
        if (
            v._gizmo_rotate_rect is not None
            and v._gizmo_rotate_rect.contains(pt)
            and v._start_gizmo_drag("rotate", wx0, wy0)
        ):
            v._show_flash("Rotate · Shift snaps angle", 1400)
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
            v._show_flash("Move · Alt temporarily disables snapping", 1400)
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
        # A preflight marker is the fastest route from "something is wrong" to
        # the path that is wrong, so it gets first refusal on the click.
        marker_handler = getattr(v, "_on_issue_marker_clicked", None)
        if callable(marker_handler):
            marker = v.issue_marker_at(pos.x(), pos.y())
            if marker is not None and marker_handler(marker):
                v._lmb_press = None
                return True
        candidates = v._hit_test.entities_at(pos.x(), pos.y())
        target = candidates[0] if candidates else None
        if (
            target is not None
            and len(candidates) > 1
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            previous = next((eid for eid in candidates if eid in v._sel), None)
            target = (
                candidates[0]
                if previous is None
                else candidates[(candidates.index(previous) + 1) % len(candidates)]
            )
            v._show_flash(
                f"Selected overlapping object {candidates.index(target) + 1}/{len(candidates)}",
                900,
            )
        v._lmb_target = target

        if v._selectable and target is None and v._region_picking:
            region_id = v._find_region_at(pos.x(), pos.y())
            if region_id is not None:
                v._sel = v._sel ^ {region_id} if shift else {region_id}
                v._lmb_press = None
                v._lmb_prev = pos
                v._lmb_target = None
                v._redraw()
                v._notify()
                return True
        if v._selectable and target is None:
            if v._lasso_select_enabled:
                v._lasso_active = True
                v._lasso_points = [QPointF(pos.x(), pos.y())]
                v._lasso_additive = shift
                v._lmb_press = None
                v._lmb_prev = pos
                v._lmb_target = None
                return True
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
            target_entity = v._document.entity_for_id(target)
            if target_entity is None:
                return True
            ctrl = bool(
                event.modifiers()
                & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            )
            shift_toggle = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            gid = v._grouping_service.group_of(target)
            if gid is not None:
                members = {e.id for e in v._entities if e.group == gid and not e.hidden}
                if ctrl or shift_toggle:
                    # Toggle the whole group as one unit.
                    if members <= v._sel:
                        v._sel -= members
                    else:
                        v._sel |= members
                elif target_entity.id not in v._sel:
                    v._sel = members
                # else: already selected — preserve current selection for group move
            elif ctrl or shift_toggle:
                # Toggle, matching the grouped branch above: a modifier-click
                # on an already-selected shape removes it from the selection.
                if target_entity.id in v._sel:
                    v._sel = v._sel - {target_entity.id}
                else:
                    v._sel = v._sel | {target_entity.id}
            elif target_entity.id not in v._sel:
                v._sel = {target_entity.id}
            edge_hit = v._hit_test.nearest_edge(pos.x(), pos.y())
            if edge_hit is not None and edge_hit[0] == target:
                ref = {"entity_id": target, "segment_index": int(edge_hit[1])}
                refs = list(getattr(v, "_constraint_segment_refs", []))
                if not (ctrl or shift_toggle):
                    refs = [ref]
                elif ref not in refs:
                    refs.append(ref)
                if len(refs) > 2:
                    refs = refs[-2:]
                v._constraint_segment_refs = refs
            v._notify()
            # Vertex dragging used to live here; Select mode now transforms whole
            # shapes only (gizmo/move), point editing belongs to Edit mode.
        # Prepare for move if clicking on an already-selected poly
        if target is not None and v._selection_drag_edits:
            target_entity = v._document.entity_for_id(target)
            if target_entity is not None and target_entity.id in v._sel:
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

        if apply_bezier_handle_drag(v, event):
            return True
        if apply_edit_drag(v, event):
            return True

        # A generated fill can contain tens of thousands of independently
        # selectable strokes. Passive hover used to run a full geometry hit
        # test for every mouse-move event, making a faithful preview unusable.
        # Keep click selection and all pattern-cell actions available, but do
        # not scan the full result merely to tint a stroke beneath the cursor.
        if (
            v._dense_preview_render
            and len(v._entities) >= 2_000
            and event.buttons() == Qt.MouseButton.NoButton
        ):
            if v._hover_poly is not None or v._hover_vert is not None:
                v._hover_poly = None
                v._hover_vert = None
                v._update_cursor()
                v._redraw()
            return True

        # No per-vertex hover tracking in Select mode — point editing is Edit-only.
        if event.buttons() & Qt.MouseButton.LeftButton:
            if v._lasso_active:
                last = v._lasso_points[-1]
                if math.hypot(pos.x() - last.x(), pos.y() - last.y()) >= 3.0:
                    v._lasso_points.append(QPointF(pos.x(), pos.y()))
                v._lmb_prev = pos
                v._redraw()
                return True
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
                if not v._move_dragging and (abs(dx_px) > DRAG_THRESH or abs(dy_px) > DRAG_THRESH):
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
                        v._move_command_snapshot = v._canvas_service.begin_preview()
                        v._move_undo_pushed = True
                    new_wx, new_wy = v._c2w(pos.x(), pos.y())
                    raw_dx = new_wx - v._move_anchor_w[0]
                    raw_dy = new_wy - v._move_anchor_w[1]
                    snap_indicators: list[tuple[tuple[float, float], str, tuple[float, float]]] = []
                    allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                    if allow_snap:
                        adj = v._snap_engine._object_snap_adjust(raw_dx, raw_dy)
                        if adj is not None:
                            raw_dx += adj[0]
                            raw_dy += adj[1]
                            snap_indicators = adj[2]
                    step_dx = raw_dx - v._move_applied_w[0]
                    step_dy = raw_dy - v._move_applied_w[1]
                    if abs(step_dx) > 1e-12 or abs(step_dy) > 1e-12:
                        for entity_id in v._sel:
                            entity = v._document.entity_for_id(entity_id)
                            if entity is None or entity.locked:
                                continue
                            try:
                                entity = v._entity_for_id(entity_id)
                                if entity is None:
                                    continue
                            except (ValueError, KeyError):
                                continue
                            entity.points = [(x + step_dx, y + step_dy) for x, y in entity.points]
                            v._transform_entity_meta(
                                entity_id,
                                center=(0.0, 0.0),
                                kind=entity.kind,
                                meta=entity.meta,
                                transform="translate",
                                dx=step_dx,
                                dy=step_dy,
                            )
                        v._refresh_driving_dimensions()
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
        hover = v._hit_test.entity_at(pos.x(), pos.y()) if v._selectable else None
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
        if v._bezier_handle_drag is not None:
            release_bezier_handle_drag(v)
            return True
        if v._lasso_active:
            points = [(p.x(), p.y()) for p in v._lasso_points]
            if not points or math.dist(points[-1], (pos.x(), pos.y())) >= 1.0:
                points.append((pos.x(), pos.y()))
            picked_ids: set[str] = set()
            if len(points) >= 3:
                try:
                    region = Polygon(points)
                    if not region.is_valid:
                        region = region.buffer(0)
                    boundary = LineString(points + [points[0]])
                    for entity_id in v._document.entity_ids():
                        entity = v._document.entity_for_id(entity_id)
                        if (
                            entity is None
                            or not v._entity_selectable_by_id(entity_id)
                            or not entity.points
                        ):
                            continue
                        screen = [v._w2c(x, y) for x, y in entity.points]
                        geometry = Point(screen[0]) if len(screen) == 1 else LineString(screen)
                        if (
                            region.covers(geometry)
                            or region.intersects(geometry)
                            or boundary.intersects(geometry)
                        ):
                            picked_ids.add(entity_id)
                except (TypeError, ValueError, GEOSException):
                    picked_ids.clear()
            gids = {
                v._document.entity_for_id(eid).group  # type: ignore[union-attr]
                for eid in picked_ids
                if v._document.entity_for_id(eid) is not None
            } - {None}
            picked: set[str] = set(picked_ids)
            if gids:
                picked |= {
                    entity_id
                    for entity_id in v._document.entity_ids()
                    if v._document.entity_for_id(entity_id) is not None
                    and v._document.entity_for_id(entity_id).group in gids  # type: ignore[union-attr]
                    and v._entity_selectable_by_id(entity_id)
                }
            if not v._lasso_additive:
                v._sel = set()
            v._sel |= picked
            v._lasso_active = False
            v._lasso_select_enabled = False
            v._lasso_points.clear()
            v._lasso_additive = False
            v._lmb_prev = None
            # Lasso is a one-shot arm (like a modal tool), not a persistent
            # mode switch — say so, since the next drag silently reverts to
            # the ordinary box marquee.
            v._show_flash(f"Selected {len(picked)} · back to box selection", 1200)
            v._redraw()
            v._notify()
            return True
        if v._shift_drag and v._band_start and v._selectable:
            bx, by = v._band_start.x(), v._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            # CAD marquee semantics: dragging left→right selects only fully
            # enclosed shapes (window); right→left selects anything the box
            # touches (crossing).
            window = pos.x() >= bx
            if not v._band_additive:
                v._sel = set()
            band_picked_ids: set[str] = set()
            for entity_id in v._document.entity_ids():
                entity = v._document.entity_for_id(entity_id)
                if entity is None or not v._entity_selectable_by_id(entity_id):
                    continue
                poly = entity.points
                if not poly:
                    continue
                pts_c = [v._w2c(x, y) for x, y in poly]
                inside = [x1c <= cx <= x2c and y1c <= cy <= y2c for cx, cy in pts_c]
                if window:
                    if all(inside):
                        band_picked_ids.add(entity_id)
                    continue
                if any(inside):
                    band_picked_ids.add(entity_id)
                    continue
                n = len(pts_c)
                seg_count = n if v._is_poly_closed(poly) else n - 1
                for i in range(seg_count):
                    if _seg_hits_rect(pts_c[i], pts_c[(i + 1) % n], x1c, y1c, x2c, y2c):
                        band_picked_ids.add(entity_id)
                        break
            # A marquee that catches part of a group selects the whole group.
            gids = {
                v._document.entity_for_id(eid).group  # type: ignore[union-attr]
                for eid in band_picked_ids
                if v._document.entity_for_id(eid) is not None
            } - {None}
            band_picked: set[str] = set(band_picked_ids)
            if gids:
                for entity_id in v._document.entity_ids():
                    entity = v._document.entity_for_id(entity_id)
                    if (
                        entity is not None
                        and entity.group in gids
                        and v._entity_selectable_by_id(entity_id)
                    ):
                        band_picked.add(entity_id)
            v._sel |= band_picked
            v._redraw()
            v._notify()
            v._shift_drag = False
            v._band_start = None
            v._band_additive = False
            return True

        if v._move_dragging:
            # Move completed — already applied incrementally
            v._move_dragging = False
            v._canvas_service.commit_preview(v._move_command_snapshot)
            v._move_command_snapshot = None
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
        hit_id = v._hit_test.entity_at(pos.x(), pos.y())
        if hit_id is not None:
            entity = v._document.entity_for_id(hit_id)
            if entity is None:
                return True
            if v.text_params_at(entity.id) is not None:
                v.prompt_edit_text(entity.id)
                return True
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift:
                connected_ids = connected_entity_ids(v._entities, v._entities_by_id, hit_id)
                v._sel = connected_ids
                v._show_flash(f"Object selected ({len(v._sel)})", 800)
            else:
                v._sel = {entity.id}
            v._redraw()
            v._notify()
        elif v._entities:
            profile = v._hit_test.profile_at(pos.x(), pos.y())
            if profile:
                v._sel = profile
                v._show_flash(f"Selected enclosed profile · {len(v._sel)} edge(s)", 1200)
                v._redraw()
                v._notify()
            else:
                # Double-click outside a profile keeps the familiar fit shortcut.
                v.fit()
        return True
