"""Canvas geometry editing, construction, selection, transform gizmo, and smoothing operations.

This is the single implementation home for document-changing CAD operations;
the view owns widget lifecycle and dispatches into this mixin.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, ClassVar, Protocol, cast

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from simple_stipple.canvas.hit_testing import HitTestService
from simple_stipple.core.cad.constraints import (
    ConstraintKind,
    GeometricConstraint,
    solve_constraints,
)
from simple_stipple.core.cad.geometry import fit_polyline_to_bezier, minimum_clearance
from simple_stipple.core.cad.shape_factory import transform_meta
from simple_stipple.core.cad.snapping import snap_to_polyline as _snap_to_polyline_candidates
from simple_stipple.core.document.commands import (
    BooleanOpCommand,
    ExplodeCommand,
    MergeCommand,
    MoveEntityCommand,
    SplitCommand,
    TransformCommand,
)
from simple_stipple.core.document.geometry import (
    move_entity_control_point,
    synchronize_entity_control_points,
    transform_entity_metadata,
    update_entity_parameter,
)
from simple_stipple.core.document.model import (
    EntityRecord,
    OperationResult,
    new_entity_id,
)
from simple_stipple.core.editing.boolean import offset_polyline
from simple_stipple.core.editing.smoothing import simplify, smooth
from simple_stipple.core.editing.topology import (
    extension_point,
    split_paths,
    trim_polyline,
    trim_preview,
)


class EditingService:
    """Adapt canvas editing actions to pure backend operations and commands."""

    def __init__(self, host) -> None:
        self._host = host

    @staticmethod
    def _is_poly_closed(poly: list[tuple[float, float]]) -> bool:
        """Check if a polyline is geometrically closed (first ≈ last)."""
        if len(poly) < 3:
            return False
        return math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01

    def _split_geometry_with_line(
        self, new_poly: list[tuple[float, float]]
    ) -> tuple[bool, int, int]:
        entity_ids = [entity.id for entity in self._host._entities_by_id.values()]
        result = split_paths(
            [list(entity.points) for entity in self._host._entities_by_id.values()],
            new_poly,
            entity_ids,
        )
        if not result.changed:
            self._host._last_split_result_ids = set()
            return False, 0, 0

        emitted: dict[str, int] = {}
        entities: list[EntityRecord] = []
        changed: list[EntityRecord] = []
        for item in result.paths:
            source = self._host._entity_for_id(item.source_id)
            if source is None:
                continue
            piece_number = emitted.get(item.source_id, 0)
            identifier = source.id if piece_number == 0 else new_entity_id()
            emitted[item.source_id] = piece_number + 1
            record = EntityRecord(
                points=item.points,
                id=identifier,
                kind="polyline" if item.changed else source.kind,
                meta=None if item.changed else deepcopy(source.meta),
                construction=source.construction,
                hidden=source.hidden,
                locked=source.locked,
                group=source.group if not item.changed else None,
                layer=source.layer,
            )
            entities.append(record)
            if item.changed:
                changed.append(record)
        self._host._document.entities = entities
        self._host._document.ensure_unique_ids()
        # Read ``.id`` only after ensure_unique_ids(), which rewrites duplicate
        # ids in place on these same records.
        self._host._last_split_result_ids = {record.id for record in changed}
        return True, result.closed_splits, result.open_splits

    def _carve_geometry_with_shape(self, cutter: list[tuple[float, float]]) -> tuple[bool, int]:
        """Subtract a closed drawn profile from every overlapping closed region."""
        return self._carve_geometry_with_shapes([cutter])

    def _carve_geometry_with_shapes(
        self, cutters: list[list[tuple[float, float]]]
    ) -> tuple[bool, int]:
        """Subtract one even-odd compound profile from overlapping regions.

        Nested contours are combined with symmetric difference, so a ring cuts
        an annulus rather than incorrectly treating its inner contour as more
        material.  This is also the common commit path for procedural tools.
        """
        usable = [c for c in cutters if len(c) >= 4 and self._is_poly_closed(c)]
        if not usable:
            return False, 0
        cutter_shape = None
        for cutter in usable:
            shape = Polygon(cutter).buffer(0)
            if shape.is_empty:
                continue
            cutter_shape = (
                shape if cutter_shape is None else cutter_shape.symmetric_difference(shape)
            )
        if cutter_shape is None or cutter_shape.is_empty:
            return False, 0

        def _rings(geometry) -> list[list[tuple[float, float]]]:
            polygons = (
                [geometry]
                if isinstance(geometry, Polygon)
                else list(geometry.geoms)
                if isinstance(geometry, (MultiPolygon, GeometryCollection))
                else []
            )
            result: list[list[tuple[float, float]]] = []
            for polygon in polygons:
                if not isinstance(polygon, Polygon) or polygon.is_empty:
                    continue
                result.append([(float(x), float(y)) for x, y in polygon.exterior.coords])
                result.extend(
                    [[(float(x), float(y)) for x, y in ring.coords] for ring in polygon.interiors]
                )
            return result

        entities: list[EntityRecord] = []
        changed: list[EntityRecord] = []
        carved = 0
        for source in self._host._entities_by_id.values():
            if not self._is_poly_closed(source.points) or source.locked or source.construction:
                entities.append(source)
                continue
            source_shape = Polygon(source.points).buffer(0)
            if source_shape.is_empty or source_shape.intersection(cutter_shape).area <= 1e-9:
                entities.append(source)
                continue
            # Drawing an enclosing profile is a normal creation gesture, not
            # an instruction to erase every region inside it.  A cutter can
            # make a hole from inside a source or trim through its boundary,
            # but it must never consume a source that it wholly contains.
            remaining = source_shape.difference(cutter_shape)
            if remaining.is_empty or remaining.area <= 1e-9:
                entities.append(source)
                continue
            rings = _rings(remaining)
            carved += 1
            for ring_index, ring in enumerate(rings):
                record = EntityRecord(
                    points=ring,
                    id=source.id if ring_index == 0 else new_entity_id(),
                    kind="polyline",
                    construction=source.construction,
                    hidden=source.hidden,
                    locked=source.locked,
                    layer=source.layer,
                )
                entities.append(record)
                changed.append(record)
        if not carved:
            return False, 0
        self._host._document.entities = entities
        self._host._document.ensure_unique_ids()
        # Read ``.id`` only after ensure_unique_ids(), which rewrites duplicate
        # ids in place on these same records.
        self._host._last_split_result_ids = {record.id for record in changed}
        return True, carved

    # ---- Snap helpers (inlined from _SnapMixin) ----

    def _snap_to_polyline(
        self,
        cx: float,
        cy: float,
        *,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return _snap_to_polyline_candidates(
            cx,
            cy,
            {eid: e.points for eid, e in self._host._entities_by_id.items()},
            self._host._noninteractive_ids(),
            self._host._scale,
            self._host._w2c,
            self._host._c2w,
            self._host._poly_bounds,
            self._host._is_poly_closed,
            HitTestService.segment_intersection,
            reference_point=reference_point,
            draw_points=self._host._draw_pts,
            mode=self._host._mode,
            allow_vertex=self._host._snap_vertex_enabled,
            allow_midpoint=self._host._snap_midpoint_enabled,
            allow_intersection=self._host._snap_intersection_enabled,
            allow_edge=self._host._snap_edge_enabled,
        )

    def _resolve_snap(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        reference_point: tuple[float, float] | None = None,
        allow_inferred: bool = True,
    ) -> tuple[float, float, str] | None:
        return self._host._snap_engine.query(
            cx,
            cy,
            wx,
            wy,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            reference_point=reference_point,
            allow_inferred=allow_inferred,
        )

    def _resolve_drag_snap(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        allow_vertex: bool = True,
        exclude_vertices: set[tuple[str, int]] | None = None,
        exclude_segments: set[tuple[str, int]] | None = None,
        exclude_polys: set[str] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return self._host._snap_engine.query(
            cx,
            cy,
            wx,
            wy,
            drag=True,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            allow_vertex=allow_vertex,
            exclude_vertices=exclude_vertices,
            exclude_segments=exclude_segments,
            exclude_polys=exclude_polys,
            reference_point=reference_point,
        )

    def _angle_snap(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        return self._host._snap_engine.angle(ax, ay, wx, wy)

    # ---- Shape preview helpers (inlined from _DrawModeMixin) ----

    def _offset_selected(self, distance: float) -> int:
        indices = self._host._mutable_selected_ids()
        if not indices or abs(distance) <= 1e-9:
            self._host._last_operation_result = OperationResult.unchanged(
                "Select editable geometry and use a non-zero offset"
            )
            return 0

        created: list[tuple[list[tuple[float, float]], bool]] = []
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            offset_poly = self._host._offset_polyline(poly, distance)
            if offset_poly is None or len(offset_poly) < 2:
                continue
            created.append((offset_poly, entity.construction))
        if not created:
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "Offset produced no geometry",
                    "Try the opposite direction or a smaller distance",
                )
            )
            return 0

        entities = [
            EntityRecord(
                points=poly,
                construction=is_construction,
                layer=self._host._active_layer,
            )
            for poly, is_construction in created
        ]
        result = self._host._canvas_service.create_entities(entities)
        self._host._apply_operation_result(
            OperationResult(
                changed=result.changed,
                message=f"Offset created {len(result.created_ids)} shape(s)",
                created_ids=result.created_ids,
                selected_ids=result.selected_ids,
                metadata={"distance": distance},
            )
        )
        self._host._set_repeat_action(
            f"Offset {distance:g}", lambda value=distance: self._host.offset_selected(value)
        )
        return len(result.created_ids)

    def _fire_poly_change(self) -> None:
        """Notify the on_poly_change callback when polylines are structurally modified."""
        self._host._construction_service._solve_geometric_constraints()
        self._host._sync_shape_storage_from_entities()
        self._host._model.notify_geometry_changed()
        if callable(self._host._on_poly_change):
            self._host._on_poly_change()

    def _ctx_delete_poly(self, entity_id: str) -> None:
        if entity_id not in self._host._entities_by_id:
            return
        result = self._host._canvas_service.delete_entities((entity_id,))
        if not result.changed:
            return
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()

    def _ctx_deselect(self, entity_id: str) -> None:
        self._host._sel = self._host._sel - {entity_id}
        self._host._redraw()
        self._host._notify()

    def _ctx_select(self, entity_id: str) -> None:
        self._host._sel = self._host._sel | {entity_id}
        self._host._redraw()
        self._host._notify()

    def _distribute_selected(self, axis: str, spacing: float, *, mode: str = "gap") -> bool:
        """Distribute selected shapes along ``axis`` at fixed ``spacing``.

        ``mode="gap"`` spaces adjacent bounding-box edges (edge-to-edge);
        ``mode="center"`` spaces bounding-box centers (center-to-center).
        The shape lowest along the axis stays anchored.
        """
        indices = self._host._mutable_selected_ids()
        if len(indices) < 2 or spacing < 0 or mode not in ("gap", "center", "even"):
            return False
        if axis == "horizontal":
            lo, hi = 0, 2
        elif axis == "vertical":
            lo, hi = 1, 3
        else:
            return False

        keyed = [
            (eid, self._host._poly_bounds(self._host._entity_for_id(eid).points)) for eid in indices
        ]
        keyed.sort(key=lambda x: x[1][lo])

        if mode == "even":
            # The prompted value is the TOTAL span: compute the equal gap
            # that places the shapes edge-to-edge across exactly that length.
            extents = sum(max(0.0, b[hi] - b[lo]) for _eid, b in keyed)
            spacing = (spacing - extents) / max(1, len(keyed) - 1)
            mode = "gap"

        candidates = {eid: deepcopy(self._host._entity_for_id(eid)) for eid in indices}
        first_b = keyed[0][1]
        cur_edge = first_b[hi]
        cur_center = (first_b[lo] + first_b[hi]) / 2.0
        for eid, b in keyed[1:]:
            if mode == "center":
                delta = (cur_center + spacing) - (b[lo] + b[hi]) / 2.0
            else:
                delta = (cur_edge + spacing) - b[lo]
            dx, dy = (delta, 0.0) if axis == "horizontal" else (0.0, delta)
            entity = candidates[eid]
            entity.points = [(x + dx, y + dy) for x, y in entity.points]
            transform_entity_metadata(
                entity,
                transform="translate",
                center=(0.0, 0.0),
                dx=dx,
                dy=dy,
            )
            nb = self._host._poly_bounds(entity.points)
            cur_edge = nb[hi]
            cur_center = (nb[lo] + nb[hi]) / 2.0

        self._host._canvas_service.update_entities(list(candidates.values()))

        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _scale_single_line_extent(self, eid: str, axis: str, target: float) -> bool:
        """Uniformly scale a 2-point line about its start point so its extent
        along ``axis`` ("w"/"h") equals ``target``, preserving its angle.

        Axis-only scaling would shear the segment and change its angle, so a
        lone line gets proportional scaling instead.
        """
        entity = self._host._entity_for_id(eid)
        if entity is None:
            return False
        (ax, ay), (bx, by) = entity.points
        extent = abs(bx - ax) if axis == "w" else abs(by - ay)
        if extent <= 1e-6:
            self._host._show_flash(
                "Line has no {} — change its angle first".format(
                    "width" if axis == "w" else "height"
                ),
                1100,
            )
            return False
        f = max(1e-4, min(1e4, target / extent))
        entity = deepcopy(entity)
        entity.points[1] = (ax + (bx - ax) * f, ay + (by - ay) * f)
        if entity.kind == "line" and isinstance(entity.meta, dict):
            entity.meta["start"], entity.meta["end"] = entity.points
        self._host._canvas_service.update_entities([entity])
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _set_selected_height(self, height: float) -> bool:
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None or height <= 0:
            return False
        if len(indices) == 1 and len(self._host._entities_by_id[indices[0]].points) == 2:
            return self._host._scale_single_line_extent(indices[0], "h", height)
        if len(indices) == 1:
            entity = self._host._entities_by_id[indices[0]]
            parameter = {
                "rectangle": ("height", height),
                "rounded_rectangle": ("height", height),
                "ellipse": ("ry", height / 2.0),
                "circle": ("radius", height / 2.0),
                "slot": ("width", height),
            }.get(entity.kind)
            # Parametric primitives update a single metadata field directly.
            # With the persistent aspect lock enabled that shortcut skipped
            # the companion dimension entirely, leaving a highlighted Lock
            # button that had no effect. Use the common uniform scaler while
            # locked so W and H always change together.
            if parameter is not None and not self._host._aspect_ratio_locked:
                return self._host.set_shape_param(indices[0], *parameter)
        cur_w = bounds[2] - bounds[0]
        cur_h = bounds[3] - bounds[1]
        if cur_h <= 1e-6:
            return False
        fy = max(1e-4, min(1e4, height / cur_h))
        fx = fy if (self._host._aspect_ratio_locked and cur_w > 1e-6) else 1.0
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        candidates = []
        for eid in indices:
            entity = deepcopy(self._host._entity_for_id(eid))
            if entity is None:
                continue
            entity.kind, entity.meta = "polyline", None
            entity.points = [(cx + (x - cx) * fx, cy + (y - cy) * fy) for x, y in entity.points]
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _preview_selected_extent(self, axis: str, target: float) -> bool:
        """Transiently resize the selection for live HUD typing feedback.

        Mutates live entity points in place and redraws — NO command-boundary
        call, so no per-keystroke undo records. Callers must wrap a typing
        session in ``begin_preview``/``cancel_preview`` and finish through the
        real commit path (``_set_selected_width`` etc.), mirroring how gizmo
        drags fold a gesture into one undo step. The math mirrors those commit
        paths so the preview and the committed result agree.
        """
        ids = self._host._mutable_selected_ids()
        if not ids or target <= 0:
            return False
        if len(ids) == 1:
            live = self._host._entities_by_id.get(ids[0])
            if live is not None and len(live.points) == 2:
                (ax, ay), (bx, by) = live.points
                if axis == "a":
                    # _set_selected_line_angle rotates about the start point.
                    cur_len = math.hypot(bx - ax, by - ay)
                    if cur_len <= 1e-9:
                        return False
                    ar = math.radians(target)
                    live.points[1] = (ax + cur_len * math.cos(ar), ay + cur_len * math.sin(ar))
                    self._host._redraw()
                    return True
                # _scale_single_line_extent / _set_selected_line_length:
                # uniform scale about the start point.
                if axis == "l":
                    extent = math.hypot(bx - ax, by - ay)
                else:
                    extent = abs(bx - ax) if axis == "w" else abs(by - ay)
                if extent <= 1e-6:
                    return False
                f = max(1e-4, min(1e4, target / extent))
                live.points[1] = (ax + (bx - ax) * f, ay + (by - ay) * f)
                self._host._redraw()
                return True
        bounds = self._host._selection_bounds(ids)
        if bounds is None:
            return False
        x0, y0, x1, y1 = bounds
        cur = (x1 - x0) if axis == "w" else (y1 - y0)
        if cur <= 1e-6 or axis not in ("w", "h"):
            return False
        f = max(1e-4, min(1e4, target / cur))
        fx = f if (axis == "w" or self._host._aspect_ratio_locked) else 1.0
        fy = f if (axis == "h" or self._host._aspect_ratio_locked) else 1.0
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        for eid in ids:
            live = self._host._entities_by_id.get(eid)
            if live is not None:
                live.points = [(cx + (px - cx) * fx, cy + (py - cy) * fy) for px, py in live.points]
        self._host._redraw()
        return True

    def _set_selected_width(self, width: float) -> bool:
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None or width <= 0:
            return False
        if len(indices) == 1 and len(self._host._entities_by_id[indices[0]].points) == 2:
            return self._host._scale_single_line_extent(indices[0], "w", width)
        if len(indices) == 1:
            entity = self._host._entities_by_id[indices[0]]
            parameter = {
                "rectangle": ("width", width),
                "rounded_rectangle": ("width", width),
                "ellipse": ("rx", width / 2.0),
                "circle": ("radius", width / 2.0),
                "slot": ("length", width),
            }.get(entity.kind)
            if parameter is not None and not self._host._aspect_ratio_locked:
                return self._host.set_shape_param(indices[0], *parameter)
        cur_w = bounds[2] - bounds[0]
        cur_h = bounds[3] - bounds[1]
        if cur_w <= 1e-6:
            return False
        fx = max(1e-4, min(1e4, width / cur_w))
        fy = fx if (self._host._aspect_ratio_locked and cur_h > 1e-6) else 1.0
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        candidates = []
        for eid in indices:
            entity = deepcopy(self._host._entity_for_id(eid))
            if entity is None:
                continue
            entity.kind, entity.meta = "polyline", None
            entity.points = [(cx + (x - cx) * fx, cy + (y - cy) * fy) for x, y in entity.points]
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _other_linework(self, exclude_entity_id: str):
        """Point lists for every other visible entity (for trim/extend)."""
        return [
            list(entity.points)
            for entity_id, entity in self._host._entities_by_id.items()
            if entity_id != exclude_entity_id
            and self._host._entity_selectable(entity_id)
            and len(entity.points) >= 2
        ]

    def trim_at(self, cx: float, cy: float) -> bool:
        """Remove the clicked portion of a polyline up to its nearest
        intersections with other shapes."""
        eid = self._host._hit_test.entity_at(cx, cy)
        if eid is None:
            self._host._apply_operation_result(OperationResult.unchanged("Click a segment to trim"))
            return False
        if self._host._is_locked(eid):
            self._host._apply_operation_result(OperationResult.unchanged("Shape is locked"))
            return False
        wx, wy = self._host._c2w(cx, cy)
        pts = self._host._entities_by_id[eid].points
        if len(pts) < 2:
            return False
        cutters = self._host._other_linework(eid)
        if not cutters:
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "Nothing to trim against",
                    "Add or reveal intersecting geometry",
                )
            )
            return False
        try:
            out = trim_polyline(pts, cutters, (wx, wy))
        except (TypeError, ValueError):
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "Trim failed", "The target or cutter geometry may be invalid"
                )
            )
            return False
        if not out:
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "No intersection to trim to", "Extend a cutter across the target"
                )
            )
            return False
        first, *rest = out
        source = self._host._entities_by_id[eid]
        replacements = [deepcopy(source)]
        replacements[0].points = first
        replacements[0].kind = "polyline"
        replacements[0].meta = None
        replacements.extend(
            EntityRecord(
                points=piece,
                construction=source.construction,
                layer=source.layer,
            )
            for piece in rest
        )
        result = self._host._canvas_service.update_entities(replacements, source_ids=(source.id,))
        self._host._apply_operation_result(result)
        return True

    def preview_trim_at(self, cx: float, cy: float) -> None:
        """Preview the exact segment that a trim click would remove."""
        eid = self._host._hit_test.entity_at(cx, cy)
        if eid is None:
            self._host._clear_operation_preview()
            return
        cutters = self._host._other_linework(eid)
        if not cutters:
            self._host._clear_operation_preview()
            return
        try:
            wx, wy = self._host._c2w(cx, cy)
            removed = trim_preview(self._host._entities_by_id[eid].points, cutters, (wx, wy))
        except (TypeError, ValueError):
            removed = None
        if removed is None:
            self._host._clear_operation_preview()
            return
        self._host._set_operation_preview([removed])

    def preview_extend_at(self, cx: float, cy: float) -> None:
        """Preview extension from the nearest open endpoint to its first target."""
        best: tuple[str, int, float] | None = None
        for entity_id, entity in self._host._entities_by_id.items():
            if len(entity.points) < 2 or self._host._is_poly_closed(entity.points):
                continue
            for endsel in (0, -1):
                ex, ey = self._host._w2c(*entity.points[endsel])
                distance = math.hypot(cx - ex, cy - ey)
                if distance < 18 and (best is None or distance < best[2]):
                    best = (entity_id, endsel, distance)
        if best is None:
            self._host._clear_operation_preview()
            return
        entity_id, endsel, _distance = best
        entity = self._host._entities_by_id[entity_id]
        points = entity.points
        tip = points[endsel]
        others = self._host._other_linework(entity_id)
        if not others:
            self._host._clear_operation_preview()
            return
        reach = (
            max(
                self._host._bbox()[2] - self._host._bbox()[0],
                self._host._bbox()[3] - self._host._bbox()[1],
                1.0,
            )
            * 3
        )
        try:
            hit = extension_point(points, others, start=endsel == 0, reach=reach)
        except (TypeError, ValueError):
            self._host._clear_operation_preview()
            return
        if hit is not None:
            self._host._set_operation_preview([[tip, hit]])
        else:
            self._host._clear_operation_preview()

    def extend_at(self, cx: float, cy: float) -> bool:
        """Lengthen the nearest open polyline end to its first intersection
        with another shape."""
        best: tuple[str, int] | None = None  # (entity_id, 0=start / -1=end)
        best_d = 12.0
        for entity_id, entity in self._host._entities_by_id.items():
            if not self._host._entity_selectable(entity_id) or self._host._is_locked(entity_id):
                continue
            pts = entity.points
            if len(pts) < 2 or self._host._is_poly_closed(pts):
                continue
            for endsel in (0, -1):
                ex, ey = self._host._w2c(*pts[endsel])
                d = math.hypot(cx - ex, cy - ey)
                if d < best_d:
                    best_d = d
                    best = (entity_id, endsel)
        if best is None:
            # Fall back to the polyline under the cursor: extend whichever
            # open end is closer to the click.
            poly_hit = self._host._hit_test.entity_at(cx, cy)
            if (
                poly_hit is not None
                and not self._host._is_locked(poly_hit)
                and len(self._host._entities_by_id[poly_hit].points) >= 2
                and not self._host._is_poly_closed(self._host._entities_by_id[poly_hit].points)
            ):
                wx, wy = self._host._c2w(cx, cy)
                pts_hit = self._host._entities_by_id[poly_hit].points
                d_start = math.hypot(pts_hit[0][0] - wx, pts_hit[0][1] - wy)
                d_end = math.hypot(pts_hit[-1][0] - wx, pts_hit[-1][1] - wy)
                best = (poly_hit, 0 if d_start <= d_end else -1)
        if best is None:
            self._host._show_flash("Click an open polyline to extend", 1100)
            return False
        eid, endsel = best
        pts = self._host._entities_by_id[eid].points
        bx0, by0, bx1, by1 = self._host._bbox()
        reach = max(bx1 - bx0, by1 - by0, 1.0) * 3.0
        others = self._host._other_linework(eid)
        if not others:
            self._host._show_flash("Nothing to extend to", 1100)
            return False
        try:
            hit = extension_point(pts, others, start=endsel == 0, reach=reach)
        except (TypeError, ValueError):
            return False
        if hit is None:
            self._host._show_flash("No shape in that direction", 1100)
            return False
        e = deepcopy(self._host._entities_by_id[eid])
        if endsel == 0:
            e.points = [hit] + list(pts)
        else:
            e.points = list(pts) + [hit]
        e.kind = "polyline"
        e.meta = None
        result = self._host._canvas_service.update_entities([e])
        if not result.changed:
            return False
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash("Extended", 800)
        return True

    def boolean_selected(self, op: str) -> int:
        """Boolean operation on the closed shapes in the selection.

        ``union`` welds overlapping shapes, ``subtract`` cuts the later
        shapes out of the first (lowest-index) shape, ``intersect`` keeps
        the common area, ``divide`` splits the union into its faces.
        Results are plain closed polylines (holes become separate loops so
        laser paths stay cuttable). Returns the number of result shapes.
        """
        indices = [
            i
            for i in self._host._mutable_selected_ids()
            if (ent := self._host._entity_for_id(i)) is not None
            and len(ent.points) >= 4
            and self._host._is_poly_closed(ent.points)
        ]
        if len(indices) < 2:
            self._host._apply_operation_result(OperationResult.unchanged("Select 2+ closed shapes"))
            return 0
        entity_ids = tuple(indices)
        try:
            result = self._host._canvas_service.execute(
                BooleanOpCommand(entity_ids=entity_ids, operation=op)
            )
        except ValueError:
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "Boolean operation failed",
                    "Check for overlapping edges or invalid outlines",
                )
            )
            return 0

        if not result.changed:
            self._host._apply_operation_result(
                OperationResult.unchanged(
                    "No area left after operation",
                    "The selected outlines may not overlap for this operation",
                )
            )
            return 0

        self._host._apply_operation_result(result)
        return len(result.selected_ids)

    def selection_geometry(self) -> dict[str, Any] | None:
        """Bbox + single-entity parameters for the properties panel."""
        indices = self._host._selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None:
            return None
        info: dict[str, Any] = {
            "x": bounds[0],
            "y": bounds[1],
            "w": bounds[2] - bounds[0],
            "h": bounds[3] - bounds[1],
            "count": len(indices),
        }
        info.update(self._selection_metrics(indices))
        if len(indices) == 1:
            eid = next(iter(indices))
            if not self._add_single_entity_geometry(info, eid):
                return None
        return info

    def _selection_metrics(self, indices: set[str]) -> dict[str, float]:
        """Return aggregate dimensions that apply to any selection size."""
        total_length = 0.0
        total_area = 0.0
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            points = entity.points
            total_length += sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            if self._host._is_poly_closed(points) and len(points) >= 4:
                total_area += (
                    abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))) / 2.0
                )
        metrics = {"length": total_length, "area": total_area}
        if 2 <= len(indices) <= 100:
            clearance = minimum_clearance(
                [
                    self._host._entity_for_id(eid).points
                    for eid in indices
                    if self._host._entity_for_id(eid) is not None
                ]
            )
            if clearance is not None:
                metrics["clearance"] = clearance
        return metrics

    def _add_single_entity_geometry(self, info: dict[str, Any], eid: str) -> bool:
        """Add parametric display fields for one selected entity."""
        entity = self._host._entity_for_id(eid)
        if entity is None:
            return False
        info["kind"] = entity.kind
        info["meta"] = deepcopy(entity.meta) if entity.meta else {}
        info["entity_id"] = eid
        display_kind, display_meta = self._display_shape_info(entity)
        info["display_kind"] = display_kind
        rotation = display_meta.get("rotation")
        if rotation is None:
            rotation = self._first_segment_angle(entity.points)
        info["rotation"] = float(rotation or 0.0) % 360.0
        self._apply_parametric_dimensions(info, entity)
        return True

    @staticmethod
    def _first_segment_angle(points: list[tuple[float, float]]) -> float | None:
        for first, second in zip(points, points[1:]):
            dx, dy = second[0] - first[0], second[1] - first[1]
            if math.hypot(dx, dy) > 1e-9:
                return math.degrees(math.atan2(dy, dx))
        return None

    @staticmethod
    def _apply_parametric_dimensions(info: dict[str, Any], entity: EntityRecord) -> None:
        meta = entity.meta or {}
        if entity.kind in {"rectangle", "rounded_rectangle"}:
            info["w"] = float(meta.get("width", info["w"]))
            info["h"] = float(meta.get("height", info["h"]))
        elif entity.kind == "ellipse":
            info["w"] = 2.0 * float(meta.get("rx", info["w"] / 2.0))
            info["h"] = 2.0 * float(meta.get("ry", info["h"] / 2.0))
        elif entity.kind == "circle":
            diameter = 2.0 * float(meta.get("radius", info["w"] / 2.0))
            info["w"] = info["h"] = diameter
            if meta.get("radius") is not None:
                info["diameter"] = 2.0 * float(meta["radius"])
        elif entity.kind == "slot":
            info["w"] = float(meta.get("length", info["w"]))
            info["h"] = float(meta.get("width", info["h"]))

    @staticmethod
    def _display_shape_info(entity: EntityRecord) -> tuple[str, dict[str, Any]]:
        display_kind = entity.kind
        display_meta = deepcopy(entity.meta) if entity.meta else {}
        if entity.kind != "polyline":
            return display_kind, display_meta
        from simple_stipple.core.cad.detection import detect_primitive

        detected = detect_primitive(entity.points)
        if detected is None:
            return display_kind, display_meta
        display_kind = detected.kind
        display_meta = dict(detected.metadata)
        sides = display_meta.get("sides", 0)
        if (
            display_kind == "polygon"
            and isinstance(sides, (str, int, float))
            and int(sides or 0) == 3
        ):
            display_kind = "triangle"
        return display_kind, display_meta

    def move_selection_to(self, x: float | None, y: float | None) -> bool:
        """Place the selection bbox's bottom-left corner at (x, y)."""
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        dx = (x - bounds[0]) if x is not None else 0.0
        dy = (y - bounds[1]) if y is not None else 0.0
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return False
        entity_ids = tuple(eid for eid in indices if self._host._entity_for_id(eid) is not None)
        result = self._host._canvas_service.execute(
            MoveEntityCommand(entity_ids=entity_ids, dx=dx, dy=dy)
        )
        if not result.changed:
            return False
        self._host._refresh_driving_dimensions()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def set_shape_param(self, entity_id: str, key: str, value: float) -> bool:
        """Edit a parametric entity's defining parameter and rebuild its
        geometry (circle radius, polygon radius/sides, ellipse rx/ry,
        arc radius). Returns False for non-parametric entities."""
        e = self._host._entity_for_id(entity_id)
        if e is None:
            return False
        candidate = deepcopy(e)
        if not update_entity_parameter(candidate, key, value):
            return False
        result = self._host._canvas_service.update_entities([candidate])
        if not result.changed:
            return False
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _warn_if_locked_selection(self, mutable_indices: list[str]) -> None:
        """Flash when an operation no-ops purely because the selection is locked."""
        if not mutable_indices and self._host._selected_ids():
            self._host._show_flash("Shape is locked", 1200)

    def align_selected(self, mode: str) -> bool:
        """Align each selected alignment "unit" to the selection's overall
        bounds. Grouped shapes are treated as a single rigid unit (aligned
        together by their combined bbox) so aligning a single selected group
        is a no-op and aligning a group alongside other shapes keeps the
        group's internal layout intact."""
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if len(indices) < 2 or bounds is None:
            self._warn_if_locked_selection(indices)
            return False
        center_x = (bounds[0] + bounds[2]) / 2.0
        center_y = (bounds[1] + bounds[3]) / 2.0
        units = self._alignment_units(indices)
        if len(units) < 2:
            return False  # a single shape (or single group) has nothing to align to
        if mode not in ("left", "center-x", "right", "top", "center-y", "bottom", "center"):
            return False

        candidates = {
            eid: deepcopy(self._host._entity_for_id(eid))
            for eid in indices
            if self._host._entity_for_id(eid) is not None
        }
        for member_indices in units.values():
            unit_bounds = self._host._selection_bounds(member_indices)
            if unit_bounds is None:
                continue
            dx, dy = self._alignment_delta(mode, bounds, unit_bounds)
            if dx == 0.0 and dy == 0.0:
                continue
            for eid in member_indices:
                entity = candidates[eid]
                entity.points = [(x + dx, y + dy) for x, y in entity.points]
                transform_entity_metadata(
                    entity,
                    transform="translate",
                    center=(center_x, center_y),
                    dx=dx,
                    dy=dy,
                )
        result = self._host._canvas_service.update_entities(list(candidates.values()))
        if not result.changed:
            return False
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _alignment_units(self, indices: list[str]) -> dict[object, list[str]]:
        """Treat selected groups as rigid alignment units."""
        units: dict[object, list[str]] = {}
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            key: object = ("group", entity.group) if entity.group is not None else ("shape", eid)
            units.setdefault(key, []).append(eid)
        return units

    @staticmethod
    def _alignment_delta(
        mode: str,
        selection_bounds: tuple[float, float, float, float],
        unit_bounds: tuple[float, float, float, float],
    ) -> tuple[float, float]:
        """Calculate one unit's translation against the full selection."""
        bx0, by0, bx1, by1 = selection_bounds
        px0, py0, px1, py1 = unit_bounds
        if mode == "center":
            return (bx0 + bx1 - px0 - px1) / 2.0, (by0 + by1 - py0 - py1) / 2.0
        if mode == "left":
            return bx0 - px0, 0.0
        if mode == "center-x":
            return (bx0 + bx1 - px0 - px1) / 2.0, 0.0
        if mode == "right":
            return bx1 - px1, 0.0
        if mode == "top":
            return 0.0, by1 - py1
        if mode == "center-y":
            return 0.0, (by0 + by1 - py0 - py1) / 2.0
        return 0.0, by0 - py0

    def mirror_selected(self, axis: str) -> bool:
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None:
            self._warn_if_locked_selection(indices)
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        if axis not in {"horizontal", "vertical"}:
            return False
        entity_ids = tuple(eid for eid in indices if self._host._entity_for_id(eid) is not None)
        result = self._host._canvas_service.execute(
            TransformCommand(
                entity_ids=entity_ids,
                operation="mirror",
                origin=(cx, cy),
                x=1.0 if axis == "horizontal" else 0.0,
            )
        )
        if not result.changed:
            return False
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def align_selected_to_axis(self, axis: str) -> bool:
        """Rotate the selection so its dominant straight edge lies flat on the
        X axis ("horizontal") or parallel to the Y axis ("vertical").

        The dominant edge is the longest segment across the selection — for a
        rotated ellipse that is (approximately) its major-axis chord, so
        "lie flat" does the intuitive thing there too.
        """
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None:
            self._warn_if_locked_selection(indices)
            return False
        theta: float | None = None
        best_len = 0.0
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            pts = entity.points
            for i in range(len(pts) - 1):
                dx = pts[i + 1][0] - pts[i][0]
                dy = pts[i + 1][1] - pts[i][1]
                seg = math.hypot(dx, dy)
                if seg > best_len:
                    best_len = seg
                    theta = math.degrees(math.atan2(dy, dx))
        if theta is None or best_len <= 1e-9:
            self._host._show_flash("No straight edge to align to", 1200)
            return False
        # A segment has no direction: fold into [-90, 90) and rotate the
        # short way onto the target axis.
        folded = ((theta + 90.0) % 180.0) - 90.0
        target = 0.0 if axis == "horizontal" else 90.0
        delta = target - folded
        if delta > 90.0:
            delta -= 180.0
        elif delta < -90.0:
            delta += 180.0
        if abs(delta) <= 1e-9:
            return True  # already flat — don't push an empty undo entry
        if not self.rotate_selected(delta):
            return False
        self._host._show_flash(
            "Rotated to lie flat" if axis == "horizontal" else "Rotated to stand upright",
            900,
        )
        return True

    def rotate_selected(self, angle_deg: float) -> bool:
        indices = self._host._mutable_selected_ids()
        bounds = self._host._selection_bounds(indices)
        if not indices or bounds is None:
            self._warn_if_locked_selection(indices)
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        entity_ids = tuple(eid for eid in indices if self._host._entity_for_id(eid) is not None)
        result = self._host._canvas_service.execute(
            TransformCommand(
                entity_ids=entity_ids,
                operation="rotate",
                origin=(cx, cy),
                x=angle_deg,
            )
        )
        if not result.changed:
            return False
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def scale_by_reference(self, factor: float, origin: tuple[float, float]) -> bool:
        """Uniformly scale the current selection (or all visible geometry).

        The first reference point is the fixed base point, matching standard
        CAD scale-by-reference behavior. Locked and hidden entities are never
        changed implicitly.
        """
        selected = self._host._selected_ids()
        indices = self._host._mutable_selected_ids()
        if not selected:
            indices = [
                entity.id
                for entity in self._host._entities
                if not entity.hidden and not entity.locked
            ]
        if not indices or not math.isfinite(factor) or factor <= 0:
            return False
        result = self._host._canvas_service.execute(
            TransformCommand(
                entity_ids=tuple(indices),
                operation="scale",
                origin=origin,
                x=factor,
                y=factor,
            )
        )
        if not result.changed:
            return False
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _apply_shape_size_inputs(self) -> None:
        if (
            self._host._draw_shape_w_edit is None
            or self._host._draw_shape_h_edit is None
            or self._host._draw_shape_anchor_w is None
            or not self._host._shape_primitive_active()
        ):
            return
        try:
            w = max(0.001, float(self._host._draw_shape_w_edit.text().strip()))
            h = max(0.001, float(self._host._draw_shape_h_edit.text().strip()))
        except ValueError:
            return

        sx, sy = self._host._draw_shape_anchor_w
        if self._host._draw_shape_cursor_w is None:
            self._host._draw_shape_cursor_w = (sx + w, sy + h)
        ex0, ey0 = self._host._draw_shape_cursor_w
        sign_x = 1.0 if ex0 >= sx else -1.0
        sign_y = 1.0 if ey0 >= sy else -1.0
        self._host._draw_shape_cursor_w = (sx + sign_x * w, sy + sign_y * h)
        self._host._redraw()

    def _immediate_segments_for_vertices(
        self,
        vertices: set[tuple[str, int]],
    ) -> set[tuple[str, int]]:
        """Return segment keys ``(entity_id, seg_idx)`` touching the given vertices."""
        excluded: set[tuple[str, int]] = set()
        for eid, vi in vertices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            n = len(poly)
            if n < 2:
                continue
            closed = self._host._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            if seg_count <= 0:
                continue
            if closed:
                excluded.add((eid, vi % seg_count))
                excluded.add((eid, (vi - 1) % seg_count))
            else:
                if 0 <= vi < seg_count:
                    excluded.add((eid, vi))
                if 0 <= (vi - 1) < seg_count:
                    excluded.add((eid, vi - 1))
        return excluded

    def _offset_polyline(
        self,
        poly: list[tuple[float, float]],
        distance: float,
    ) -> list[tuple[float, float]] | None:
        return offset_polyline(poly, distance)

    def _update_shape_size_fields_from_preview(self) -> None:
        if self._host._draw_shape_w_edit is None or self._host._draw_shape_h_edit is None:
            return
        enabled = (
            self._host._shape_primitive_active() and self._host._draw_shape_anchor_w is not None
        )
        self._host._draw_shape_w_edit.setEnabled(enabled)
        self._host._draw_shape_h_edit.setEnabled(enabled)
        if self._host._draw_shape_sides_spin is not None:
            self._host._draw_shape_sides_spin.setEnabled(enabled)
        if not enabled:
            return
        if self._host._draw_shape_anchor_w is None or self._host._draw_shape_cursor_w is None:
            return
        sx, sy = self._host._draw_shape_anchor_w
        ex, ey = self._host._draw_shape_cursor_w
        self._host._draw_shape_w_edit.setText(f"{abs(ex - sx):.2f}")
        self._host._draw_shape_h_edit.setText(f"{abs(ey - sy):.2f}")
        # The fields sit on the badge anchors (bbox edges), which move as the
        # preview grows — track them.
        self._host._reposition_shape_dim_inputs()

    def offset_selected(self, distance: float) -> int:
        """Public command/API wrapper for the canonical offset operation."""
        return self._offset_selected(distance)

    def _selected_single_line(self) -> str | None:
        """Entity ID of the sole selected 2-point line, or ``None``."""
        if len(self._host._sel) != 1:
            return None
        entity_id = next(iter(self._host._sel))
        entity = self._host._entity_for_id(entity_id)
        if entity is None or len(entity.points) != 2:
            return None
        return entity_id

    def _sel_badge_axes(self) -> list[tuple[str, QRectF]]:
        """Available selection badges as ordered (axis, hit-rect) pairs."""
        pairs = [
            ("w", self._host._sel_badge_w_rect),
            ("h", self._host._sel_badge_h_rect),
            ("l", self._host._sel_badge_l_rect),
            ("a", self._host._sel_badge_a_rect),
        ]
        return [(a, r) for a, r in pairs if r is not None]

    def _set_selected_line_length(self, length: float) -> bool:
        indices = self._host._mutable_selected_ids()
        if len(indices) != 1 or length <= 0:
            return False
        poly = self._host._entities_by_id[indices[0]].points
        if len(poly) != 2:
            return False
        ax, ay = poly[0]
        bx, by = poly[1]
        dx, dy = bx - ax, by - ay
        cur_len = math.hypot(dx, dy)
        if cur_len <= 1e-9:
            return False
        ux, uy = dx / cur_len, dy / cur_len
        entity = deepcopy(self._host._entities_by_id[indices[0]])
        entity.points[1] = (ax + ux * length, ay + uy * length)
        if entity.kind == "line" and isinstance(entity.meta, dict):
            entity.meta["start"], entity.meta["end"] = entity.points
        self._host._canvas_service.update_entities([entity])
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def _set_selected_line_angle(self, angle_deg: float) -> bool:
        """Rotate the selected 2-point line about its start point to an
        absolute angle in degrees (CCW from +X), preserving its length."""
        indices = self._host._mutable_selected_ids()
        if len(indices) != 1:
            return False
        poly = self._host._entities_by_id[indices[0]].points
        if len(poly) != 2:
            return False
        ax, ay = poly[0]
        bx, by = poly[1]
        cur_len = math.hypot(bx - ax, by - ay)
        if cur_len <= 1e-9:
            return False
        ar = math.radians(angle_deg)
        entity = deepcopy(self._host._entities_by_id[indices[0]])
        entity.points[1] = (
            ax + cur_len * math.cos(ar),
            ay + cur_len * math.sin(ar),
        )
        if entity.kind == "line" and isinstance(entity.meta, dict):
            entity.meta["start"], entity.meta["end"] = entity.points
        self._host._canvas_service.update_entities([entity])
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    # ── Second restoration pass: methods referenced as callbacks
    #    (menu actions) that the call-only audit missed. ──

    def _send_selected_to_draft(self) -> None:
        cb = getattr(self._host, "_send_selected_to_draft_cb", None)
        if not callable(cb):
            return
        selected = self._host.get_selected()
        if not selected:
            self._host._show_flash("Select shape(s) first", 1000)
            return
        selected_ids = set(self._host.get_selected_ids())
        payload = [
            {
                "points": [[float(x), float(y)] for x, y in entity.points],
                "layer": entity.layer,
            }
            for entity in self._host._entities
            if entity.id in selected_ids
        ]
        cb(payload)
        self._host._show_flash("Sent to Draft", 900)

    def _send_selected_to_pattern(self) -> None:
        cb = getattr(self._host, "_send_selected_to_pattern_cb", None)
        if not callable(cb):
            return
        selected = self._host.get_selected()
        if not selected:
            self._host._show_flash("Select shape(s) first", 1000)
            return
        selected_ids = set(self._host.get_selected_ids())
        # Text is stored as several contours in one group.  A direct hit can
        # select only one contour (especially after loading an older workspace
        # whose selection state predates group expansion); sending that lone
        # contour makes letters such as ``e`` self-touching and invalid in the
        # Pattern page.  Treat every selected group as one object at this
        # transfer boundary so text and other grouped artwork always arrive
        # complete.
        selected_groups = {
            entity.group
            for entity in self._host._entities
            if entity.id in selected_ids and entity.group is not None
        }
        if selected_groups:
            selected_ids.update(
                entity.id for entity in self._host._entities if entity.group in selected_groups
            )
        payload = [
            {
                "points": [[float(x), float(y)] for x, y in entity.points],
                "layer": entity.layer,
            }
            for entity in self._host._entities
            if entity.id in selected_ids
        ]
        cb(payload)
        self._host._show_flash("Sent to Pattern", 900)

    def _use_selected_as_custom_tile(self) -> None:
        cb = getattr(self._host, "_use_selected_as_custom_tile_cb", None)
        if not callable(cb):
            return
        selected = self._host.get_selected()
        if not selected:
            self._host._show_flash("Select shape(s) first", 1000)
            return
        cb([[(x, y) for x, y in poly] for poly in selected])
        self._host._show_flash("Custom tile set", 900)

    def explode_selected_to_segments(self) -> int:
        indices = self._host._mutable_selected_ids()
        entity_ids = tuple(
            eid
            for eid in indices
            if (ent := self._host._entity_for_id(eid)) is not None and len(ent.points) > 2
        )
        if not entity_ids:
            return 0
        result = self._host._canvas_service.execute(ExplodeCommand(entity_ids=entity_ids))
        if not result.changed:
            return 0
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return len(result.selected_ids)

    def merge_selected_segments_to_objects(self, *, record_undo: bool = True) -> int:
        indices = self._host._mutable_selected_ids()
        if len(indices) < 2:
            return 0
        entity_ids = tuple(eid for eid in indices if self._host._entity_for_id(eid) is not None)
        result = self._host._canvas_service.execute(
            MergeCommand(entity_ids=entity_ids), record=record_undo
        )
        if not result.changed:
            return 0
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return len(result.selected_ids)

    # ── Base right-click handling + vertex ops (restored from _select/_edit mixins) ──

    def knife_cut(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        """Split intersected geometry with a two-point knife stroke."""
        if math.dist(start, end) < 1e-9:
            self._host._last_operation_result = OperationResult.unchanged(
                "Knife stroke is too short"
            )
            return False
        entity_ids = tuple(entity.id for entity in self._host._entities_by_id.values())
        result = self._host._canvas_service.execute(
            SplitCommand(entity_ids=entity_ids, cutter=(start, end))
        )
        if not result.changed:
            self._host._apply_operation_result(
                OperationResult.unchanged("Knife did not cross any geometry")
            )
            return False
        self._host._apply_operation_result(result)
        return True

    def _apply_operation_result(self, result: OperationResult) -> OperationResult:
        """Publish one operation outcome and select its outputs by stable ID."""
        self._host._last_operation_result = result
        if result.selected_ids:
            self._host._sel = set(result.selected_ids)
        if result.changed:
            self._host._sync_shape_storage_from_entities()
            self._host._redraw()
            self._host._notify()
            self._host._fire_poly_change()
        elif result.message:
            self._host._redraw()
        text = result.message
        if result.warnings:
            warning_text = "; ".join(result.warnings)
            text = f"{text} — {warning_text}" if text else warning_text
        if text:
            self._host._show_flash(text, 1200 if result.warnings or not result.changed else 900)
        return result

    def _set_repeat_action(self, label: str, callback) -> None:
        self._host._last_repeat_action = (str(label), callback)

    def _set_operation_preview(self, polys: list[list[tuple[float, float]]]) -> None:
        self._host._operation_preview_polys = [list(poly) for poly in polys]
        self._host._redraw()

    def _clear_operation_preview(self) -> None:
        if self._host._operation_preview_polys:
            self._host._operation_preview_polys = []
            self._host._redraw()


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
                # Intersection-merged paths revisit junction points; drag
                # every coincident copy together so the junction stays welded.
                for j, pt in enumerate(other.points):
                    if j != vert_idx and _eq(target_pt, pt):
                        linked.add((eid, j))
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
        whole_entities: list[str] = []
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
            # Selecting every vertex means the shape itself: deleting down to a
            # degenerate triangle stump is never the intent.
            if len({vi for vi in vis if not (closed and vi == len(poly) - 1)}) >= available:
                whole_entities.append(eid)
                continue
            max_removable = max(0, available - 3)
            if max_removable <= 0:
                continue
            candidates = sorted(vi for vi in vis if not (closed and vi == len(poly) - 1))
            keep = set(candidates[:max_removable])
            if keep:
                grouped[eid] = keep

        if not grouped and not whole_entities:
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
        if whole_entities:
            self._host._canvas_service.delete_entities(tuple(whole_entities))
            self._host._sel -= set(whole_entities)
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


class ConstructionService:
    """Adapt backend construction and constraint operations to canvas state."""

    def __init__(self, host) -> None:
        self._host = host

    def _solve_geometric_constraints(self) -> int:
        """Re-solve persistent constraints and prune references to deleted entities."""
        entities_by_id = {entity.id: entity for entity in self._host._entities}
        self._host._constraints = [
            constraint
            for constraint in self._host._constraints
            if all(entity_id in entities_by_id for entity_id in constraint.entity_ids)
        ]
        if not self._host._constraints:
            return 0
        solved = solve_constraints(
            {entity_id: list(entity.points) for entity_id, entity in entities_by_id.items()},
            self._host._constraints,
        )
        changed = 0
        for entity_id, points in solved.items():
            entity = entities_by_id[entity_id]
            if entity.points == points:
                continue
            entity.points = points
            if entity.kind == "line" and len(points) == 2:
                entity.meta = {"start": points[0], "end": points[1]}
            elif entity.kind in {
                "arc",
                "circle",
                "ellipse",
                "polygon",
                "rectangle",
                "rounded_rectangle",
                "slot",
                "star",
            }:
                # Parametric shapes retain their defining metadata. Fixed and
                # projection constraints may replace their tessellation, but
                # must not silently degrade the shape into a plain polyline.
                pass
            else:
                entity.kind = "polyline"
                entity.meta = None
            changed += 1
        changed += self._apply_shape_and_projection_constraints(entities_by_id)
        return changed

    def _apply_shape_and_projection_constraints(
        self, entities_by_id: dict[str, EntityRecord]
    ) -> int:
        """Apply persistent constraints whose defining data lives in shape metadata."""
        changed = 0
        for constraint in self._host._constraints:
            if not constraint.enabled or len(constraint.entity_ids) < 2:
                continue
            source = entities_by_id.get(constraint.entity_ids[0])
            target = entities_by_id.get(constraint.entity_ids[1])
            if source is None or target is None:
                continue
            if constraint.kind == "projection":
                snapshot = (list(target.points), target.kind, deepcopy(target.meta))
                target.points = list(source.points)
                target.kind = source.kind
                target.meta = deepcopy(source.meta)
                target.construction = True
                if snapshot != (target.points, target.kind, target.meta):
                    changed += 1
                continue
            source_meta = source.meta or {}
            target_meta = target.meta or {}
            source_center = source_meta.get("center")
            target_center = target_meta.get("center")
            if constraint.kind == "concentric" and source_center and target_center:
                sx, sy = map(float, source_center)
                tx, ty = map(float, target_center)
                dx, dy = sx - tx, sy - ty
                target.meta = {**target_meta, "center": (sx, sy)}
                target.points = [(x + dx, y + dy) for x, y in target.points]
                changed += int(bool(dx or dy))
            elif constraint.kind == "equal" and "radius" in source_meta and "radius" in target_meta:
                source_radius = float(source_meta["radius"])
                target_radius = float(target_meta["radius"])
                if target_radius <= 1e-12:
                    continue
                center = tuple(map(float, target_meta.get("center", (0.0, 0.0))))
                scale = source_radius / target_radius
                target.meta = {**target_meta, "radius": source_radius}
                target.points = [
                    (center[0] + (x - center[0]) * scale, center[1] + (y - center[1]) * scale)
                    for x, y in target.points
                ]
                changed += int(abs(source_radius - target_radius) > 1e-12)
            elif constraint.kind == "tangent" and source_center and target_center:
                source_radius = float(source_meta.get("radius", 0.0))
                target_radius = float(target_meta.get("radius", 0.0))
                if source_radius <= 0 or target_radius <= 0:
                    continue
                sx, sy = map(float, source_center)
                tx, ty = map(float, target_center)
                dx, dy = tx - sx, ty - sy
                distance = math.hypot(dx, dy)
                ux, uy = (dx / distance, dy / distance) if distance > 1e-12 else (1.0, 0.0)
                desired = source_radius + target_radius
                new_center = (sx + ux * desired, sy + uy * desired)
                shift = (new_center[0] - tx, new_center[1] - ty)
                target.meta = {**target_meta, "center": new_center}
                target.points = [(x + shift[0], y + shift[1]) for x, y in target.points]
                changed += int(math.hypot(*shift) > 1e-12)
        return changed

    def add_geometric_constraint(self, kind: str) -> int:  # noqa: C901
        """Attach a persistent constraint to selected edges or vertices."""
        segment_refs = [
            ref
            for ref in getattr(self._host, "_constraint_segment_refs", [])
            if isinstance(ref, dict) and str(ref.get("entity_id", "")) in self._host._entities_by_id
        ]
        vertex_refs = [
            (str(entity_id), int(vertex_index))
            for entity_id, vertex_index in getattr(self._host, "_edit_selected_verts", set())
            if str(entity_id) in self._host._entities_by_id
        ]
        line_indices = [
            index
            for index in self._host._selected_ids()
            if len(self._host._entities_by_id[index].points) == 2
        ]
        selected_ids = list(self._host._selected_ids())
        selected_entities = [
            self._host._entities_by_id[index]
            for index in selected_ids
            if index in self._host._entities_by_id
        ]
        if kind == "unfix":
            fixed = [
                constraint
                for constraint in self._host._constraints
                if constraint.kind == "fixed"
                and set(constraint.entity_ids).intersection(selected_ids)
            ]
            if not fixed:
                self._host._show_flash("Select fixed geometry to unfix", 1200)
                return 0
            before = self._host._canvas_service.begin_preview()
            self._host._constraints = [
                constraint for constraint in self._host._constraints if constraint not in fixed
            ]
            self._host._canvas_service.commit_preview(before)
            self._host._redraw()
            self._host._notify()
            self._host._fire_poly_change()
            self._host._show_flash(f"Unfixed {len(fixed)} item(s)", 1000)
            return len(fixed)
        if kind == "projection":
            if not selected_entities:
                self._host._show_flash("Select geometry to project as construction", 1200)
                return 0
            before = self._host._canvas_service.begin_preview()
            additions: list[GeometricConstraint] = []
            projected_ids: list[str] = []
            for source in selected_entities:
                projection = EntityRecord(
                    points=list(source.points),
                    kind=source.kind,
                    meta=deepcopy(source.meta),
                    construction=True,
                    layer=source.layer,
                )
                self._host._document.append(projection)
                projected_ids.append(projection.id)
                additions.append(
                    GeometricConstraint(kind="projection", entity_ids=(source.id, projection.id))
                )
            self._host._constraints.extend(additions)
            self._host._canvas_service.commit_preview(before)
            self._host.set_selection(projected_ids)
            self._host._sync_shape_storage_from_entities()
            self._host._redraw()
            self._host._notify()
            self._host._fire_poly_change()
            self._host._show_flash(f"Projected {len(additions)} construction reference(s)", 1000)
            return len(additions)
        if kind == "midpoint" and len(vertex_refs) == 1 and len(segment_refs) == 1:
            point_id, point_vertex = vertex_refs[0]
            segment = segment_refs[0]
            additions = [
                GeometricConstraint(
                    kind="midpoint",
                    entity_ids=(str(segment["entity_id"]), point_id),
                    parameters={
                        "first_segment": int(segment["segment_index"]),
                        "point_vertex": point_vertex,
                    },
                )
            ]
        elif kind == "symmetric" and len(vertex_refs) == 2 and len(segment_refs) >= 1:
            first, second = vertex_refs
            axis = segment_refs[0]
            additions = [
                GeometricConstraint(
                    kind="symmetric",
                    entity_ids=(str(axis["entity_id"]), first[0], second[0]),
                    parameters={
                        "first_segment": int(axis["segment_index"]),
                        "first_vertex": first[1],
                        "second_vertex": second[1],
                    },
                )
            ]
        elif kind == "intersection" and len(vertex_refs) == 1 and len(segment_refs) == 2:
            point_id, point_vertex = vertex_refs[0]
            intersection_first, intersection_second = segment_refs
            additions = [
                GeometricConstraint(
                    kind="intersection",
                    entity_ids=(
                        str(intersection_first["entity_id"]),
                        str(intersection_second["entity_id"]),
                        point_id,
                    ),
                    parameters={
                        "first_segment": int(intersection_first["segment_index"]),
                        "second_segment": int(intersection_second["segment_index"]),
                        "point_vertex": point_vertex,
                    },
                )
            ]
        elif kind == "coincident" and len(vertex_refs) == 1 and len(segment_refs) == 1:
            point_id, point_vertex = vertex_refs[0]
            segment = segment_refs[0]
            additions = [
                GeometricConstraint(
                    kind="coincident",
                    entity_ids=(str(segment["entity_id"]), point_id),
                    parameters={
                        "first_segment": int(segment["segment_index"]),
                        "point_vertex": point_vertex,
                    },
                )
            ]
        else:
            additions = []
        binary = {
            "parallel",
            "perpendicular",
            "equal",
            "equal_length",
            "coincident",
            "collinear",
            "smooth",
        }
        if kind in {"concentric", "tangent", "equal"} and len(selected_entities) == 2:
            round_kinds = {"arc", "circle"}
            first, second = selected_entities
            if first.kind in round_kinds and second.kind in round_kinds:
                additions = [
                    GeometricConstraint(
                        kind=cast(ConstraintKind, kind),
                        entity_ids=(first.id, second.id),
                        parameters={"shape": True},
                    )
                ]
        if (
            kind == "smooth"
            and len(selected_entities) == 2
            and all(entity.kind in {"bezier", "spline"} for entity in selected_entities)
        ):
            first, second = selected_entities
            additions = [
                GeometricConstraint(
                    kind="smooth",
                    entity_ids=(first.id, second.id),
                    parameters={"curve": True},
                )
            ]
        if kind in {"horizontal", "vertical"} and not (segment_refs or line_indices):
            self._host._show_flash("Select one or more edges", 1200)
            return 0
        if kind == "fixed" and not selected_entities:
            self._host._show_flash("Select geometry to fix", 1200)
            return 0
        if not additions:
            if kind == "coincident" and len(vertex_refs) == 2:
                first_ref, second_ref = vertex_refs
                additions = [
                    GeometricConstraint(
                        kind="coincident",
                        entity_ids=(first_ref[0], second_ref[0]),
                        parameters={"first_vertex": first_ref[1], "second_vertex": second_ref[1]},
                    )
                ]
            elif kind in binary and len(segment_refs) == 2:
                segment_first, segment_second = segment_refs
                additions = [
                    GeometricConstraint(
                        kind=cast(ConstraintKind, kind),
                        entity_ids=(
                            str(segment_first["entity_id"]),
                            str(segment_second["entity_id"]),
                        ),
                        parameters={
                            "first_segment": int(segment_first["segment_index"]),
                            "second_segment": int(segment_second["segment_index"]),
                        },
                    )
                ]
                if kind == "coincident":
                    first_entity = self._host._entities_by_id[str(segment_first["entity_id"])]
                    second_entity = self._host._entities_by_id[str(segment_second["entity_id"])]
                    a = int(segment_first["segment_index"])
                    b = int(segment_second["segment_index"])
                    choice = min(
                        (
                            (
                                math.dist(
                                    first_entity.points[a + da], second_entity.points[b + db]
                                ),
                                da,
                                db,
                            )
                            for da in (0, 1)
                            for db in (0, 1)
                        ),
                        key=lambda item: item[0],
                    )
                    additions[0].parameters.update(
                        {"first_endpoint": choice[1], "second_endpoint": choice[2]}
                    )
            elif kind in binary and len(line_indices) != 2:
                first_edge_ref: dict[str, Any] | None = None
                if segment_refs:
                    first_edge_ref = segment_refs[0]
                elif len(line_indices) == 1:
                    first_edge_ref = {
                        "entity_id": self._host._entities_by_id[line_indices[0]].id,
                        "segment_index": 0,
                    }
                if (
                    kind in {"parallel", "perpendicular", "equal_length"}
                    and first_edge_ref is not None
                ):
                    # Two-step workflow: keep the first edge and let the next
                    # click choose what it constrains to. Selecting both edges
                    # up front still works (the len==2 branch above).
                    self._host._constraint_segment_refs = [first_edge_ref]
                    self._host._constraint_pick_armed = kind
                    self._host._update_cursor()
                    self._host._show_flash(
                        "Click the edge to constrain it to · Esc cancels", 2000
                    )
                    return 0
                self._host._show_flash("Select two edges (Shift-click to add the second)", 1600)
                return 0
        if kind in {"midpoint", "symmetric", "intersection"} and not additions:
            self._host._show_flash("Select the required point and reference edge(s)", 1600)
            return 0
        before = self._host._canvas_service.begin_preview()
        constraint_kind = cast(ConstraintKind, kind)
        if not additions and kind in {"horizontal", "vertical"}:
            edge_sources = segment_refs or [
                {"entity_id": self._host._entities_by_id[index].id, "segment_index": 0}
                for index in line_indices
            ]
            additions = [
                GeometricConstraint(
                    kind=constraint_kind,
                    entity_ids=(str(source["entity_id"]),),
                    parameters={"first_segment": int(source["segment_index"])},
                )
                for source in edge_sources
            ]
        elif not additions and kind == "fixed":
            additions = [
                GeometricConstraint(
                    kind="fixed",
                    entity_ids=(entity.id,),
                    parameters={"points": [list(point) for point in entity.points]},
                )
                for entity in selected_entities
            ]
        elif not additions and kind in binary:
            first, second = (self._host._entities_by_id[index] for index in line_indices)
            parameters: dict[str, Any] = {}
            if kind == "coincident":
                choice = min(
                    (
                        (math.dist(first.points[a], second.points[b]), a, b)
                        for a in (0, 1)
                        for b in (0, 1)
                    ),
                    key=lambda item: item[0],
                )
                parameters = {"first_endpoint": choice[1], "second_endpoint": choice[2]}
            additions = [
                GeometricConstraint(
                    kind=constraint_kind,
                    entity_ids=(first.id, second.id),
                    parameters=parameters,
                )
            ]
        self._host._constraints.extend(additions)
        self._solve_geometric_constraints()
        self._host._canvas_service.commit_preview(before)
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"Added {kind.replace('_', ' ')} constraint", 1000)
        return len(additions)

    def remove_constraints_for_selection(self) -> int:
        selected_ids = {
            eid for eid in self._host._selected_ids() if self._host._entity_for_id(eid) is not None
        }
        removed = [
            constraint
            for constraint in self._host._constraints
            if selected_ids.intersection(constraint.entity_ids)
        ]
        if not removed:
            self._host._show_flash("Selection has no constraints", 900)
            return 0
        before = self._host._canvas_service.begin_preview()
        self._host._constraints = [
            constraint for constraint in self._host._constraints if constraint not in removed
        ]
        self._host._canvas_service.commit_preview(before)
        self._host._fire_poly_change()
        self._host._redraw()
        self._host._notify()
        self._host._show_flash(f"Removed {len(removed)} constraint(s)", 1000)
        return len(removed)

    def _commit_construction_entities(
        self, records: list[tuple[list[tuple[float, float]], str, dict[str, Any] | None]]
    ) -> int:
        if not records:
            return 0
        entities = [
            EntityRecord(
                points=points,
                kind=kind,
                meta=metadata,
                construction=True,
                layer=self._host._active_layer,
            )
            for points, kind, metadata in records
        ]
        self._host._canvas_service.create_entities(entities)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return len(records)

    @staticmethod
    def _infinite_line_points(
        origin: tuple[float, float], direction: tuple[float, float], *, ray: bool = False
    ) -> list[tuple[float, float]]:
        length = math.hypot(*direction)
        if length <= 1e-12:
            return []
        ux, uy = direction[0] / length, direction[1] / length
        reach = 1_000_000.0
        if ray:
            return [origin, (origin[0] + ux * reach, origin[1] + uy * reach)]
        return [
            (origin[0] - ux * reach, origin[1] - uy * reach),
            (origin[0] + ux * reach, origin[1] + uy * reach),
        ]

    # ── Methods restored from pre-refactor mixins (were dropped in the
    #    mixin-inlining refactor; callers in widget.py/render.py remained). ──

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
        entity = EntityRecord(
            points=list(poly),
            kind=kind,
            meta=meta,
            construction=self._host._draw_construction_mode,
            layer=self._host._active_layer,
        )
        self._host._canvas_service.create_entities([entity])
        self._host._notify()
        self._host._fire_poly_change()
        self._host._refresh_draw_sidebar_state()
        self._host._redraw()
        if enter_edit:
            self._host.set_mode("edit")


class GizmoService:
    """Own resize, rotate, and scale drag state transitions."""

    def __init__(self, host) -> None:
        self._host = host

    _HANDLE_ANCHORS: ClassVar[dict[str, tuple[tuple[float, float], tuple[float, float]]]] = {
        # handle → (anchor position, handle position) as bbox fractions
        "nw": ((1.0, 0.0), (0.0, 1.0)),
        "n": ((0.5, 0.0), (0.5, 1.0)),
        "ne": ((0.0, 0.0), (1.0, 1.0)),
        "e": ((0.0, 0.5), (1.0, 0.5)),
        "se": ((0.0, 1.0), (1.0, 0.0)),
        "s": ((0.5, 1.0), (0.5, 0.0)),
        "sw": ((1.0, 1.0), (0.0, 0.0)),
        "w": ((1.0, 0.5), (0.0, 0.5)),
    }

    def _start_gizmo_drag(
        self, mode: str, wx: float, wy: float, *, from_center: bool = False
    ) -> bool:
        bounds = self._host._selection_bounds()
        if bounds is None or not self._host._sel:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._host._gizmo_local_shape = None
        if mode.startswith("scale-"):
            frac_a, frac_h = self._HANDLE_ANCHORS[mode[6:]]
            if from_center:
                frac_a = (0.5, 0.5)
            indices = self._host._mutable_selected_ids()
            entity = self._host._entities_by_id[indices[0]] if len(indices) == 1 else None
            meta = entity.meta if entity is not None and isinstance(entity.meta, dict) else None
            dims = None
            if entity is not None and meta is not None:
                if entity.kind in {"rectangle", "rounded_rectangle"}:
                    dims = (
                        float(meta.get("width", 0)),
                        float(meta.get("height", 0)),
                        "width",
                        "height",
                    )
                elif entity.kind == "ellipse":
                    dims = (2 * float(meta.get("rx", 0)), 2 * float(meta.get("ry", 0)), "rx", "ry")
                elif entity.kind == "circle":
                    diameter = 2 * float(meta.get("radius", 0))
                    dims = (diameter, diameter, "radius", "radius")
                elif entity.kind == "slot":
                    dims = (
                        float(meta.get("length", 0)),
                        float(meta.get("width", 0)),
                        "length",
                        "width",
                    )
            if dims is not None and min(dims[0], dims[1]) > 1e-9:
                assert meta is not None
                cx, cy = (float(v) for v in meta.get("center", (cx, cy)))
                rotation = float(meta.get("rotation", 0.0))
                angle = math.radians(rotation)

                def _world(frac: tuple[float, float]) -> tuple[float, float]:
                    lx = (frac[0] - 0.5) * dims[0]
                    ly = (frac[1] - 0.5) * dims[1]
                    return (
                        cx + lx * math.cos(angle) - ly * math.sin(angle),
                        cy + lx * math.sin(angle) + ly * math.cos(angle),
                    )

                self._host._gizmo_anchor_w = _world(frac_a)
                self._host._gizmo_handle_w = _world(frac_h)
                self._host._gizmo_local_shape = {
                    "entity_id": indices[0],
                    "center": (cx, cy),
                    "rotation": rotation,
                    "width": dims[0],
                    "height": dims[1],
                    "x_key": dims[2],
                    "y_key": dims[3],
                    "from_center": from_center,
                }
            else:
                x0, y0, x1, y1 = bounds
                self._host._gizmo_anchor_w = (
                    x0 + (x1 - x0) * frac_a[0],
                    y0 + (y1 - y0) * frac_a[1],
                )
                self._host._gizmo_handle_w = (
                    x0 + (x1 - x0) * frac_h[0],
                    y0 + (y1 - y0) * frac_h[1],
                )
        else:
            vec = (wx - cx, wy - cy)
            if math.hypot(vec[0], vec[1]) < 1e-9:
                return False
            self._host._gizmo_start_vec = vec
        self._host._gizmo_drag_mode = mode
        self._host._gizmo_center_w = (cx, cy)
        # Locked entities stay in the selection but must not be transformed —
        # match drag-move/nudge, which already skip them.
        mutable_ids = self._host._mutable_selected_ids()
        if len(mutable_ids) < len(self._host._selected_ids()):
            self._host._show_flash("Locked shapes were not transformed", 1200)
        self._host._gizmo_snapshot = {
            eid: list(self._host._entities_by_id[eid].points) for eid in mutable_ids
        }

        def _meta_copy(eid: str) -> dict[str, Any] | None:
            meta = self._host._entities_by_id[eid].meta
            return dict(meta) if isinstance(meta, dict) else None

        self._host._gizmo_meta_snapshot = {eid: _meta_copy(eid) for eid in mutable_ids}
        # A non-uniform scale can change kind mid-drag (arc -> elliptical_arc),
        # so later mouse-move events must keep deriving from the drag-start
        # kind + meta pair, not the live (possibly already-converted) entity —
        # otherwise the next move reconstructs the wrong shape from a kind
        # that no longer matches its own snapshot meta.
        self._host._gizmo_kind_snapshot = {
            eid: self._host._entities_by_id[eid].kind for eid in mutable_ids
        }
        self._host._gizmo_drag_moved = False
        self._host._gizmo_undo_pushed = False
        return bool(self._host._gizmo_snapshot)

    def _apply_handle_scale(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        """Resize the selection by dragging a frame handle. Corners resize
        X and Y independently (Shift = keep aspect), edges scale one axis;
        holding Alt at press scales from the center."""
        if self._host._gizmo_anchor_w is None or self._host._gizmo_handle_w is None:
            return
        handle = (self._host._gizmo_drag_mode or "")[6:]
        if self._host._gizmo_local_shape is not None:
            self._apply_local_parametric_scale(handle, wx, wy, mods)
            return
        ax, ay = self._host._gizmo_anchor_w
        hx, hy = self._host._gizmo_handle_w

        if mods is None:
            mods = QApplication.keyboardModifiers()

        # Snap the dragged handle itself to nearby vertex/midpoint/edge/
        # center of other shapes (any layer) plus grid/guides — mirrors
        # move-drag snapping so resize feels consistent. Alt disables it.
        allow_snap = not bool(mods & Qt.KeyboardModifier.AltModifier)
        snap_result = (
            self._host._snap_engine._resize_handle_snap_adjust(wx, wy) if allow_snap else None
        )
        if snap_result is not None:
            wx, wy, snap_type = snap_result
            self._host._hover_snap = (wx, wy)
            self._host._hover_snap_type = snap_type
        else:
            self._host._hover_snap = None
            self._host._hover_snap_type = None

        def _factor(cur: float, a: float, h: float) -> float:
            span = h - a
            if abs(span) < 1e-9:
                return 1.0
            f = (cur - a) / span
            # Clamp magnitude only — preserve sign so dragging a handle past
            # the opposite edge flips the shape (mirrors it) instead of
            # getting stuck at a minimum positive scale.
            if abs(f) < 0.05:
                f = 0.05 if f >= 0.0 else -0.05
            return max(-20.0, min(20.0, f))

        sx = _factor(wx, ax, hx)
        sy = _factor(wy, ay, hy)
        if handle in ("n", "s"):
            sx = 1.0
        elif handle in ("e", "w"):
            sy = 1.0
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            # Shift = keep aspect (uniform, dominant axis wins)
            s = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
            sx = sy = s
        if self._host._aspect_ratio_locked:
            # Persistent lock (properties panel toggle) overrides the
            # handle-type/Shift logic above — every handle, edge or
            # corner, scales both axes together from whichever one the
            # drag actually moved.
            if handle in ("n", "s"):
                sx = sy
            elif handle in ("e", "w"):
                sy = sx
            else:
                s = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
                sx = sy = s
        if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
            self._host._gizmo_drag_moved = True
        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True
        for eid, src_poly in self._host._gizmo_snapshot.items():
            self._host._entities_by_id[eid].points = [
                (ax + (x - ax) * sx, ay + (y - ay) * sy) for x, y in src_poly
            ]
            # Keep parametric meta (circle/ellipse/rectangle "center") in
            # sync with the resized points — otherwise centroid-based snap
            # targets stay stale at the shape's PRE-resize position, since
            # `_entity_center()` reads meta["center"] directly rather than
            # recomputing it from `.points`. Always derive from the drag-
            # start snapshot (never the live/already-updated meta) so
            # repeated mouse-move events don't compound the transform.
            snap_meta = self._host._gizmo_meta_snapshot.get(eid)
            if isinstance(snap_meta, dict):
                entity_kind = self._host._gizmo_kind_snapshot.get(
                    eid, self._host._entities_by_id[eid].kind
                )
                if abs(sx - sy) <= 1e-9:
                    result = transform_meta(
                        entity_kind,
                        snap_meta,
                        transform="scale",
                        center=(ax, ay),
                        factor=sx,
                        points=src_poly,
                    )
                    if result is not None:
                        new_kind, new_meta = result
                        self._host._entities_by_id[eid].kind = new_kind
                        self._host._entities_by_id[eid].meta = new_meta
                    else:
                        self._host._entities_by_id[eid].meta = snap_meta
                else:
                    # Curves (bezier/spline) and lines are affine-invariant —
                    # they stay parametric under a non-uniform scale via
                    # Shape.scale_xy, and a circular arc becomes an elliptical
                    # one the same way. A world-axis non-uniform scale of a
                    # circle/ellipse/rectangle, though, can turn it into a
                    # shape (ellipse, parallelogram) its own schema can't
                    # represent; transform_meta returns None for those, and
                    # the transformed points become the canonical geometry
                    # instead of leaving stale metadata that redraw would
                    # use to restore the old shape.
                    result = transform_meta(
                        entity_kind,
                        snap_meta,
                        transform="scale",
                        center=(ax, ay),
                        factor=sx,
                        factor_y=sy,
                        points=src_poly,
                    )
                    if result is not None:
                        new_kind, new_meta = result
                        self._host._entities_by_id[eid].kind = new_kind
                        self._host._entities_by_id[eid].meta = new_meta
                    else:
                        self._host._entities_by_id[eid].kind = "polyline"
                        self._host._entities_by_id[eid].meta = None
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _apply_local_parametric_scale(
        self, handle: str, wx: float, wy: float, mods: Qt.KeyboardModifier | None
    ) -> None:
        """Resize a rotated parametric shape in its own coordinate system."""
        state = self._host._gizmo_local_shape
        if state is None or self._host._gizmo_anchor_w is None:
            return
        eid = state["entity_id"]
        cx, cy = state["center"]
        angle = math.radians(-float(state["rotation"]))

        def _local(point: tuple[float, float]) -> tuple[float, float]:
            dx, dy = point[0] - cx, point[1] - cy
            return (
                dx * math.cos(angle) - dy * math.sin(angle),
                dx * math.sin(angle) + dy * math.cos(angle),
            )

        ax, ay = _local(self._host._gizmo_anchor_w)
        px, py = _local((wx, wy))
        width, height = float(state["width"]), float(state["height"])
        new_w = width if handle in {"n", "s"} else max(1e-3, abs(px - ax))
        new_h = height if handle in {"e", "w"} else max(1e-3, abs(py - ay))
        if state["from_center"]:
            new_w = width if handle in {"n", "s"} else max(1e-3, 2 * abs(px))
            new_h = height if handle in {"e", "w"} else max(1e-3, 2 * abs(py))
        if mods is not None and mods & Qt.KeyboardModifier.ShiftModifier:
            factor = max(new_w / width, new_h / height)
            new_w, new_h = width * factor, height * factor
        if state["x_key"] == state["y_key"]:
            diameter = new_w if handle in {"e", "w"} else new_h
            if len(handle) == 2:
                diameter = max(new_w, new_h)
            new_w = new_h = diameter
        candidate = deepcopy(self._host._entities_by_id[eid])
        x_value = new_w / 2.0 if state["x_key"] in {"rx", "radius"} else new_w
        y_value = new_h / 2.0 if state["y_key"] in {"ry", "radius"} else new_h
        update_entity_parameter(candidate, str(state["x_key"]), x_value)
        update_entity_parameter(candidate, str(state["y_key"]), y_value)
        if not state["from_center"]:
            local_center = (
                0.0 if handle in {"n", "s"} else (ax + px) / 2.0,
                0.0 if handle in {"e", "w"} else (ay + py) / 2.0,
            )
            forward = math.radians(float(state["rotation"]))
            target_center = (
                cx + local_center[0] * math.cos(forward) - local_center[1] * math.sin(forward),
                cy + local_center[0] * math.sin(forward) + local_center[1] * math.cos(forward),
            )
            old_center = candidate.meta.get("center", (cx, cy)) if candidate.meta else (cx, cy)
            dx, dy = target_center[0] - old_center[0], target_center[1] - old_center[1]
            candidate.points = [(x + dx, y + dy) for x, y in candidate.points]
            transform_entity_metadata(candidate, transform="translate", dx=dx, dy=dy)
        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True
        # Mirror the uniform-scale path: mutate the live entity in place for
        # the drag preview and let ``commit_preview`` record it on release.
        # (``_update_entity_in_storage`` never existed, so every parametric
        # handle drag raised AttributeError mid-gesture.)
        live = self._host._entities_by_id[eid]
        live.points = list(candidate.points)
        if candidate.meta is not None:
            live.meta = deepcopy(candidate.meta)
        self._host._gizmo_drag_moved = True
        self._host._sync_shape_storage_from_entities()
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _apply_gizmo_drag(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        if self._host._gizmo_drag_mode is None or not self._host._gizmo_snapshot:
            return
        if self._host._gizmo_drag_mode.startswith("scale-"):
            self._apply_handle_scale(wx, wy, mods)
            return
        if self._host._gizmo_center_w is None or self._host._gizmo_start_vec is None:
            return

        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True

        cx, cy = self._host._gizmo_center_w
        start_vx, start_vy = self._host._gizmo_start_vec
        cur_vx, cur_vy = wx - cx, wy - cy

        scale = 1.0
        angle = 0.0
        if self._host._gizmo_drag_mode == "scale":
            start_d = math.hypot(start_vx, start_vy)
            cur_d = math.hypot(cur_vx, cur_vy)
            if start_d > 1e-9:
                scale = max(0.05, min(20.0, cur_d / start_d))
            if abs(scale - 1.0) > 1e-4:
                self._host._gizmo_drag_moved = True
        elif self._host._gizmo_drag_mode == "rotate":
            start_a = math.atan2(start_vy, start_vx)
            cur_a = math.atan2(cur_vy, cur_vx)
            angle = cur_a - start_a
            if mods is not None and mods & Qt.KeyboardModifier.ShiftModifier:
                increment = math.radians(self._host._rotation_snap_increment)
                angle = round(angle / increment) * increment
            if abs(angle) > math.radians(0.2):
                self._host._gizmo_drag_moved = True

        ca, sa = math.cos(angle), math.sin(angle)
        for eid, src_poly in self._host._gizmo_snapshot.items():
            out_poly: list[tuple[float, float]] = []
            for x, y in src_poly:
                sx = cx + (x - cx) * scale
                sy = cy + (y - cy) * scale
                rx = cx + (sx - cx) * ca - (sy - cy) * sa
                ry = cy + (sx - cx) * sa + (sy - cy) * ca
                out_poly.append((rx, ry))
            self._host._entities_by_id[eid].points = out_poly
            # Same staleness fix as _apply_handle_scale: recompute meta["center"]
            # under the identical scale+rotate transform, from the drag-start
            # snapshot, so circle/ellipse centroid snapping stays accurate
            # after a uniform corner-scale or rotate gizmo drag too.
            snap_meta = self._host._gizmo_meta_snapshot.get(eid)
            if isinstance(snap_meta, dict):
                entity_kind = self._host._gizmo_kind_snapshot.get(
                    eid, self._host._entities_by_id[eid].kind
                )
                result = transform_meta(
                    entity_kind,
                    snap_meta,
                    transform=("rotate" if self._host._gizmo_drag_mode == "rotate" else "scale"),
                    center=(cx, cy),
                    angle_deg=math.degrees(angle),
                    factor=scale,
                    points=src_poly,
                )
                if result is not None:
                    new_kind, new_meta = result
                    self._host._entities_by_id[eid].kind = new_kind
                    self._host._entities_by_id[eid].meta = new_meta
                else:
                    self._host._entities_by_id[eid].meta = snap_meta
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _end_gizmo_drag(self) -> bool:
        moved = self._host._gizmo_drag_moved
        if moved:
            self._host._canvas_service.commit_preview(self._host._gizmo_command_snapshot)
        elif self._host._gizmo_undo_pushed:
            # Sub-threshold drags still mutated live geometry; roll that back
            # rather than leaving an unundoable micro-transform behind.
            self._host._canvas_service.cancel_preview(self._host._gizmo_command_snapshot)
        self._host._gizmo_drag_mode = None
        self._host._gizmo_center_w = None
        self._host._gizmo_start_vec = None
        self._host._gizmo_anchor_w = None
        self._host._gizmo_handle_w = None
        self._host._gizmo_snapshot = {}
        self._host._gizmo_meta_snapshot = {}
        self._host._gizmo_kind_snapshot = {}
        self._host._gizmo_local_shape = None
        self._host._gizmo_drag_moved = False
        self._host._gizmo_undo_pushed = False
        self._host._gizmo_command_snapshot = None
        self._host._hover_snap = None
        self._host._hover_snap_type = None
        return moved


# ════════════════════════════════════════════════════════════════════════════
# Grouping / ungrouping
# ════════════════════════════════════════════════════════════════════════════


class SignalEmitter(Protocol):
    def emit(self, *args: object) -> None: ...


class SmoothingHost(Protocol):
    _canvas_service: Any
    _entities: list
    _entities_by_id: dict[str, Any]
    _smoothing_method: str
    _smooth_iterations: int
    _simplify_tolerance: float
    _sel: set[str]
    selectionChanged: SignalEmitter
    smoothingMethodChanged: SignalEmitter
    smoothIterationsChanged: SignalEmitter
    simplifyToleranceChanged: SignalEmitter

    def _mutable_selected_ids(self) -> list[str]: ...
    def _is_poly_closed(self, points: list[tuple[float, float]]) -> bool: ...
    def _redraw(self) -> None: ...
    def _notify(self) -> None: ...
    def _fire_poly_change(self) -> None: ...
    def _refresh_draw_sidebar_state(self) -> None: ...


class SmoothingService:
    """Mutates canvas state only around pure backend path operations."""

    def __init__(self, host: SmoothingHost) -> None:
        self._host = host

    def set_method(self, method: str) -> None:
        if method not in ("chaikin", "gaussian", "catmull_rom"):
            return
        host = self._host
        host._smoothing_method = method
        host.selectionChanged.emit(len(host._sel))
        host._refresh_draw_sidebar_state()

    def method_changed(self, method: str) -> None:
        self.set_method(method)
        self._host.smoothingMethodChanged.emit(method)

    def set_iterations(self, iterations: int) -> None:
        self._host._smooth_iterations = int(iterations)

    def iterations_changed(self, iterations: int) -> None:
        self.set_iterations(iterations)
        self._host.smoothIterationsChanged.emit(self._host._smooth_iterations)

    def set_tolerance(self, tolerance: float) -> None:
        self._host._simplify_tolerance = float(tolerance)

    def tolerance_changed(self, tolerance: float) -> None:
        self.set_tolerance(tolerance)
        self._host.simplifyToleranceChanged.emit(self._host._simplify_tolerance)

    def smooth_selected(self, iterations: int = 1) -> int:
        host = self._host
        indices = [
            index
            for index in host._mutable_selected_ids()
            if len(host._entities_by_id[index].points) >= 3
        ]
        if not indices:
            return 0
        candidates = []
        for index in indices:
            entity = deepcopy(host._entities_by_id[index])
            entity.points = smooth(
                entity.points,
                method=host._smoothing_method,
                iterations=iterations,
                closed=host._is_poly_closed(entity.points),
            )
            entity.kind, entity.meta = "polyline", None
            candidates.append(entity)
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(indices)

    def simplify_selected(self, tolerance: float = 0.2) -> int:
        host = self._host
        indices = [
            index
            for index in host._mutable_selected_ids()
            if len(host._entities_by_id[index].points) >= 3
        ]
        candidates = []
        for index in indices:
            entity = host._entities_by_id[index]
            points = simplify(entity.points, tolerance, closed=host._is_poly_closed(entity.points))
            if 2 <= len(points) < len(entity.points):
                candidate = deepcopy(entity)
                candidate.points, candidate.kind, candidate.meta = points, "polyline", None
                candidates.append(candidate)
        if not candidates:
            return 0
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(candidates)

    def fit_selected_to_curve(self, tolerance: float = 0.3, corner_angle_deg: float = 55.0) -> int:
        host = self._host
        fitted = []
        for index in host._mutable_selected_ids():
            entity = host._entities_by_id[index]
            if len(entity.points) < 3:
                continue
            result = fit_polyline_to_bezier(
                entity.points,
                tolerance=tolerance,
                corner_angle_deg=corner_angle_deg,
                closed=host._is_poly_closed(entity.points),
            )
            if result is not None:
                fitted.append((index, result))
        if not fitted:
            return 0
        candidates = []
        for index, (anchors, tangents) in fitted:
            entity = deepcopy(host._entities_by_id[index])
            entity.points = anchors
            entity.kind = "bezier"
            entity.meta = {"control_points": tangents}
            candidates.append(entity)
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(fitted)

    def _changed(self) -> None:
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
