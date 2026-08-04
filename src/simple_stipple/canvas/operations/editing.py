"""Canvas geometry editing, construction, and selection operations.

This is the single implementation home for document-changing CAD operations;
the view owns widget lifecycle and dispatches into this mixin.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import (
    QRectF,
)
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from simple_stipple.document.commands import (
    BooleanOpCommand,
    ExplodeCommand,
    MergeCommand,
    MoveEntityCommand,
    ResampleCommand,
    SplitCommand,
    TransformCommand,
)
from simple_stipple.document.model import (
    EntityRecord,
    OperationResult,
    new_entity_id,
)
from simple_stipple.engine.cad.editor_geometry import (
    transform_entity_metadata,
    update_entity_parameter,
)
from simple_stipple.engine.cad.geometry import minimum_clearance
from simple_stipple.engine.cad.shapes import ShapeFactory
from simple_stipple.engine.cad.snapping import snap_to_polyline as _snap_to_polyline_candidates
from simple_stipple.engine.editing.offset import offset_polyline
from simple_stipple.engine.editing.split import split_paths
from simple_stipple.engine.editing.transform import scale
from simple_stipple.engine.editing.trim_extend import (
    extension_point,
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
            self._host._segment_intersection_point,
            reference_point=reference_point,
            draw_points=self._host._draw_pts,
            mode=self._host._mode,
            allow_vertex=self._host._snap_vertex_enabled,
            allow_midpoint=self._host._snap_midpoint_enabled,
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
    ) -> tuple[float, float, str] | None:
        return self._host._snap_engine.query(
            cx,
            cy,
            wx,
            wy,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            reference_point=reference_point,
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
        self._host._solve_geometric_constraints()
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
        if len(indices) < 2 or spacing < 0 or mode not in ("gap", "center"):
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
        eid = self._host._find_poly_at(cx, cy)
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
        eid = self._host._find_poly_at(cx, cy)
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
            poly_hit = self._host._find_poly_at(cx, cy)
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
        info["length"] = total_length
        info["area"] = total_area
        if 2 <= len(indices) <= 100:
            clearance = minimum_clearance(
                [
                    self._host._entity_for_id(eid).points
                    for eid in indices
                    if self._host._entity_for_id(eid) is not None
                ]
            )
            if clearance is not None:
                info["clearance"] = clearance
        if len(indices) == 1:
            eid = next(iter(indices))
            e = self._host._entity_for_id(eid)
            if e is None:
                return None
            info["kind"] = e.kind
            info["meta"] = deepcopy(e.meta) if e.meta else {}
            info["entity_id"] = eid
            display_kind = e.kind
            display_meta: dict[str, Any] = info["meta"]  # type: ignore[assignment]
            if e.kind == "polyline":
                from simple_stipple.engine.cad.recognition import recognize_polyline

                recognized = recognize_polyline(e.points)
                if recognized is not None:
                    display_kind = recognized.kind
                    display_meta = dict(recognized.metadata)
                    sides = int(display_meta.get("sides", 0) or 0)
                    if display_kind == "polygon" and sides == 3:
                        display_kind = "triangle"
            info["display_kind"] = display_kind
            rotation = display_meta.get("rotation")
            if rotation is None and len(e.points) >= 2:
                for first, second in zip(e.points, e.points[1:]):
                    dx, dy = second[0] - first[0], second[1] - first[1]
                    if math.hypot(dx, dy) > 1e-9:
                        rotation = math.degrees(math.atan2(dy, dx))
                        break
            info["rotation"] = float(rotation or 0.0) % 360.0
            if e.meta:
                if e.kind in {"rectangle", "rounded_rectangle"}:
                    info["w"] = float(e.meta.get("width", info["w"]))
                    info["h"] = float(e.meta.get("height", info["h"]))
                elif e.kind == "ellipse":
                    info["w"] = 2.0 * float(e.meta.get("rx", info["w"] / 2.0))
                    info["h"] = 2.0 * float(e.meta.get("ry", info["h"] / 2.0))
                elif e.kind == "circle":
                    diameter = 2.0 * float(e.meta.get("radius", info["w"] / 2.0))
                    info["w"] = info["h"] = diameter
                elif e.kind == "slot":
                    info["w"] = float(e.meta.get("length", info["w"]))
                    info["h"] = float(e.meta.get("width", info["h"]))
            if e.kind == "circle" and e.meta and e.meta.get("radius") is not None:
                info["diameter"] = 2.0 * float(e.meta["radius"])
        return info

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
        bx0, by0, bx1, by1 = bounds
        center_x = (bx0 + bx1) / 2.0
        center_y = (by0 + by1) / 2.0

        units: dict[object, list[str]] = {}
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            gid = entity.group
            key: object = ("group", gid) if gid is not None else ("shape", eid)
            units.setdefault(key, []).append(eid)
        if len(units) < 2:
            return False  # a single shape (or single group) has nothing to align to
        if mode not in ("left", "center-x", "right", "top", "center-y", "bottom"):
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
            px0, py0, px1, py1 = unit_bounds
            dx = dy = 0.0
            if mode == "left":
                dx = bx0 - px0
            elif mode == "center-x":
                dx = center_x - (px0 + px1) / 2.0
            elif mode == "right":
                dx = bx1 - px1
            elif mode == "top":
                dy = by1 - py1
            elif mode == "center-y":
                dy = center_y - (py0 + py1) / 2.0
            elif mode == "bottom":
                dy = by0 - py0
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

    def _scale_all(self, factor: float) -> None:
        """Scale all polylines uniformly around their bounding box center."""
        if not self._host._entities:
            return
        all_pts = [pt for p in (e.points for e in self._host._entities_by_id.values()) for pt in p]
        xs, ys = zip(*all_pts)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        candidates = [deepcopy(entity) for entity in self._host._entities_by_id.values()]
        for entity in candidates:
            entity.points = scale(entity.points, (cx, cy), factor)
            transform_entity_metadata(
                entity,
                transform="scale",
                center=(cx, cy),
                factor=factor,
            )
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()

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

    @staticmethod
    def _points_equal(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6

    def _segments_for_polylines(self, poly_ids: set[str]) -> set[tuple[str, int]]:
        segments: set[tuple[str, int]] = set()
        for eid in poly_ids:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            poly = entity.points
            n = len(poly)
            if n < 2:
                continue
            closed = self._host._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            segments.update((eid, si) for si in range(max(0, seg_count)))
        return segments

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

    def _vertices_for_polylines(self, poly_ids: set[str]) -> set[tuple[str, int]]:
        vertices: set[tuple[str, int]] = set()
        for eid in poly_ids:
            entity = self._host._entity_for_id(eid)
            if entity is not None:
                vertices.update((eid, vi) for vi in range(len(entity.points)))
        return vertices

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

    def _show_geometry_preflight(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from simple_stipple.engine.cad.preflight import analyze_geometry

        polys = self._host.get_selected() or self._host.get_polylines_state()
        report = analyze_geometry(polys)
        minimum = "—" if report.minimum_segment is None else f"{report.minimum_segment:.4g} mm"
        QMessageBox.information(
            self._host,
            "Geometry Preflight",
            f"{report.summary()}\n\n"
            f"Analysis tolerance: {report.tolerance:.4g} mm\n"
            f"Minimum segment: {minimum}\n\n"
            "Open paths may be intentional engraving strokes. Invalid, duplicate, "
            "zero-length, and tiny geometry should be repaired before fabrication.",
        )

    def recognize_selected_shapes(self) -> int:
        """Convert conservative imported-polyline matches to parametric shapes."""
        from simple_stipple.engine.cad.recognition import recognize_polyline

        matches: list[EntityRecord] = []
        for eid in self._host._mutable_selected_ids():
            entity = self._host._entity_for_id(eid)
            if entity is None or entity.kind != "polyline":
                continue
            recognized = recognize_polyline(entity.points)
            if recognized is not None:
                candidate = deepcopy(entity)
                candidate.kind = recognized.kind
                candidate.meta = dict(recognized.metadata)
                matches.append(candidate)
        if not matches:
            self._host._show_flash("No unambiguous circles, rectangles, or regular polygons", 1600)
            return 0
        self._host._canvas_service.update_entities(matches)
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"Recognized {len(matches)} shape(s)", 1000)
        return len(matches)

    def reverse_selected_paths(self) -> int:
        from simple_stipple.engine.cad.path_ops import reverse_path

        indices = self._host._mutable_selected_ids()
        if not indices:
            return 0
        candidates: list[EntityRecord] = []
        for eid in indices:
            entity = deepcopy(self._host._entity_for_id(eid))
            if entity is None:
                continue
            entity.points = reverse_path(entity.points)
            if entity.kind == "line" and len(entity.points) == 2:
                entity.meta = {"start": entity.points[0], "end": entity.points[1]}
            elif entity.kind == "bezier" and entity.meta:
                old_in = list(entity.meta.get("handles_in", []))
                old_out = list(entity.meta.get("handles_out", []))
                if old_in or old_out:
                    entity.meta["handles_in"] = list(reversed(old_out))
                    entity.meta["handles_out"] = list(reversed(old_in))
                    entity.meta["node_types"] = list(reversed(entity.meta.get("node_types", [])))
                tangents = list(reversed(entity.meta.get("tangents", [])))
                entity.meta["tangents"] = [(-float(x), -float(y)) for x, y in tangents]
            elif entity.kind == "spline" and entity.meta:
                entity.meta["control_points"] = list(entity.points)
            elif entity.kind != "polyline":
                entity.kind = "polyline"
                entity.meta = None
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"Reversed {len(indices)} path(s)", 900)
        return len(indices)

    def set_selected_path_start(self) -> bool:
        from simple_stipple.engine.cad.path_ops import set_closed_start

        indices = self._host._mutable_selected_ids()
        if len(indices) != 1:
            self._host._show_flash("Select exactly one closed path", 1000)
            return False
        eid = indices[0]
        entity = self._host._entity_for_id(eid)
        if entity is None:
            return False
        points = entity.points
        if not self._host._is_poly_closed(points):
            self._host._show_flash("Path is open", 800)
            return False
        if self._host._hover_vert is not None and self._host._hover_vert[0] == eid:
            vertex = self._host._hover_vert[1]
        elif self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            vertex = min(
                range(len(points) - 1),
                key=lambda item: math.dist(
                    points[item], (self._host._cursor_wx, self._host._cursor_wy)
                ),
            )
        else:
            self._host._show_flash("Hover the desired start vertex", 1000)
            return False
        entity = deepcopy(entity)
        entity.points = set_closed_start(points, vertex)
        entity.kind = "polyline"
        entity.meta = None
        self._host._canvas_service.update_entities([entity])
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash("Path start updated", 800)
        return True

    def resample_selected_paths(self, value: float, *, by_count: bool = False) -> int:
        indices = self._host._mutable_selected_ids()
        if not indices:
            return 0
        entity_ids = tuple(eid for eid in indices if self._host._entity_for_id(eid) is not None)
        try:
            result = self._host._canvas_service.execute(
                ResampleCommand(
                    entity_ids=entity_ids,
                    value=float(round(value) if by_count else value),
                    by_count=by_count,
                )
            )
        except ValueError:
            self._host._show_flash("No selected path could be resampled", 1100)
            return 0
        if not result.changed:
            return 0
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"Resampled {len(entity_ids)} path(s)", 900)
        return len(entity_ids)

    def prompt_resample_spacing(self) -> None:
        self._host._show_hud_prompt(
            "Point spacing (mm)", 1.0, self._host.resample_selected_paths, minimum=0.001
        )

    def prompt_resample_count(self) -> None:
        self._host._show_hud_prompt(
            "Point count",
            32.0,
            lambda value: self._host.resample_selected_paths(value, by_count=True),
            minimum=2.0,
            is_length=False,
        )

    def fit_selected_to_primitive(self, primitive: str) -> int:
        from simple_stipple.engine.cad.path_ops import fit_circle, fit_line

        indices = self._host._mutable_selected_ids()
        replacements: dict[str, tuple[list[tuple[float, float]], str, dict[str, Any]]] = {}
        for eid in indices:
            entity = self._host._entity_for_id(eid)
            if entity is None:
                continue
            points = entity.points
            if primitive == "line":
                line_result = fit_line(points)
                if line_result is not None:
                    replacements[eid] = (
                        list(line_result),
                        "line",
                        {"start": line_result[0], "end": line_result[1]},
                    )
            elif primitive in {"circle", "arc"}:
                circle_result = fit_circle(points)
                if circle_result is None:
                    continue
                center, radius = circle_result
                if primitive == "circle":
                    circle_shape = ShapeFactory.circle(center, radius)
                    replacements[eid] = (
                        list(circle_shape.points),
                        "circle",
                        {"center": center, "radius": radius},
                    )
                elif len(points) >= 2:
                    start = (
                        math.degrees(math.atan2(points[0][1] - center[1], points[0][0] - center[0]))
                        % 360
                    )
                    end = (
                        math.degrees(
                            math.atan2(points[-1][1] - center[1], points[-1][0] - center[0])
                        )
                        % 360
                    )
                    middle = (
                        math.degrees(
                            math.atan2(
                                points[len(points) // 2][1] - center[1],
                                points[len(points) // 2][0] - center[0],
                            )
                        )
                        % 360
                    )
                    if (middle - start) % 360 > (end - start) % 360:
                        start, end = end, start
                    arc_shape = ShapeFactory.arc(center, radius, start, end, segments=48)
                    replacements[eid] = (
                        list(arc_shape.points),
                        "arc",
                        {
                            "center": center,
                            "radius": radius,
                            "start_angle": start,
                            "end_angle": end,
                        },
                    )
        if not replacements:
            self._host._show_flash(f"Could not fit selection to {primitive}", 1100)
            return 0
        candidates: list[EntityRecord] = []
        for eid, (points, kind, metadata) in replacements.items():
            entity = deepcopy(self._host._entity_for_id(eid))
            if entity is None:
                continue
            entity.points, entity.kind, entity.meta = points, kind, metadata
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"Fitted {len(replacements)} path(s) to {primitive}", 1000)
        return len(replacements)

    def create_procedural_primitive(self, primitive: str) -> int:
        """Create an advanced primitive at the cursor using conservative defaults."""
        from simple_stipple.engine.cad.primitives import (
            chamfered_star,
            dovetail_box,
            finger_joint_box,
            gear,
            keyhole,
            ring,
            rounded_star,
            spiral,
            superellipse,
            tabbed_panel,
            teardrop,
        )

        center = (
            (self._host._cursor_wx, self._host._cursor_wy)
            if self._host._cursor_wx is not None and self._host._cursor_wy is not None
            else (0.0, 0.0)
        )
        generators = {
            "gear": lambda: [gear()],
            "spiral": lambda: [spiral()],
            "superellipse": lambda: [superellipse()],
            "teardrop": lambda: [teardrop()],
            "keyhole": lambda: [keyhole()],
            "ring": lambda: list(ring()),
            "rounded_star": lambda: [rounded_star()],
            "chamfered_star": lambda: [chamfered_star()],
            "finger_joint_box": lambda: [finger_joint_box()],
            "dovetail_box": lambda: [dovetail_box()],
            "tabbed_panel": lambda: [tabbed_panel()],
        }
        generator = generators.get(primitive)
        if generator is None:
            return 0
        try:
            paths = generator()
        except ValueError as exc:
            self._host._show_flash(str(exc), 1200)
            return 0
        records = [
            (
                [(point[0] + center[0], point[1] + center[1]) for point in path],
                primitive,
                {"generator": primitive, "center": center},
            )
            for path in paths
            if len(path) >= 2
        ]
        if not records:
            return 0
        group = self._host._next_group_id if len(records) > 1 else None
        entities = [
            EntityRecord(
                points=points,
                kind=kind,
                meta=metadata,
                group=group,
                layer=self._host._active_layer,
                construction=self._host._draw_construction_mode,
            )
            for points, kind, metadata in records
        ]
        closed_paths = [entity.points for entity in entities if self._is_poly_closed(entity.points)]
        if (
            self._host._draw_split_enabled
            and not self._host._draw_construction_mode
            and closed_paths
        ):
            before = self._host._canvas_service.begin_preview()
            carved, carved_count = self._carve_geometry_with_shapes(closed_paths)
            if carved:
                for entity in entities:
                    self._host._entities_by_id[entity.id] = entity
                self._host._document.entities.extend(entities)
                self._host._document.selection = {entity.id for entity in entities}
                self._host._canvas_service.commit_preview(before)
                self._host._redraw()
                self._host._notify()
                self._host._fire_poly_change()
                self._host._show_flash(f"Carved {carved_count} region(s)", 1000)
                if group is not None:
                    self._host._group_labels[group] = "Ring"
                return len(entities)
        result = self._host._canvas_service.create_entities(entities)
        if group is not None:
            self._host._group_labels[group] = "Ring"
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash(f"{primitive.replace('_', ' ').title()} created", 900)
        return len(result.created_ids)

    def create_polygon_from_selected_edge(self, sides: float = 6.0) -> int:
        from simple_stipple.engine.cad.primitives import regular_polygon_from_edge

        indices = [
            eid
            for eid in self._host._mutable_selected_ids()
            if (ent := self._host._entity_for_id(eid)) is not None and len(ent.points) == 2
        ]
        if len(indices) != 1:
            self._host._show_flash("Select exactly one edge", 900)
            return 0
        start, end = self._host._entities_by_id[indices[0]].points
        points = regular_polygon_from_edge(start, end, int(round(sides)))
        vertices = points[:-1]
        center = (
            sum(point[0] for point in vertices) / len(vertices),
            sum(point[1] for point in vertices) / len(vertices),
        )
        radius = math.dist(center, vertices[0])
        rotation = (
            math.degrees(math.atan2(vertices[0][1] - center[1], vertices[0][0] - center[0])) + 90.0
        )
        entity = EntityRecord(
            points=points,
            kind="polygon",
            meta={
                "source": "edge",
                "center": center,
                "radius": radius,
                "rotation": rotation,
                "sides": int(round(sides)),
            },
            layer=self._host._active_layer,
        )
        self._host._canvas_service.create_entities([entity])
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return 1

    def prompt_polygon_from_edge(self) -> None:
        self._host._show_hud_prompt(
            "Polygon sides",
            6.0,
            self._host.create_polygon_from_selected_edge,
            minimum=3.0,
            is_length=False,
        )

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

    def create_symbol_from_selection(self) -> None:
        if not self._host._sel:
            self._host._show_flash("Select geometry for the symbol", 1100)
            return

        def _save(name: str) -> None:
            clean = name.strip()
            if not clean:
                raise ValueError("Symbol name cannot be empty")
            points = [
                point
                for entity_id in self._host._sel
                for entity in (self._host._entity_for_id(entity_id),)
                if entity is not None
                for point in entity.points
            ]
            if not points:
                raise ValueError("Selection has no geometry")
            origin_x = min(x for x, _y in points)
            origin_y = min(y for _x, y in points)
            self._host._copy_selected()
            records = deepcopy(self._host._clipboard)
            for record in records:
                record["polyline"] = [
                    (x - origin_x, y - origin_y) for x, y in record.get("polyline", [])
                ]
                record["meta"] = self._host._translated_entity_meta(
                    str(record.get("kind", "polyline")),
                    record.get("meta"),
                    -origin_x,
                    -origin_y,
                )
            self._host._symbol_library[clean] = records
            self._host._show_flash(f"Symbol saved: {clean}", 1000)
            self._host._notify()

        self._host._show_text_hud_prompt("Symbol name", _save)

    def insert_symbol(self) -> None:
        if not self._host._symbol_library:
            self._host._show_flash("No symbols in this workspace", 1100)
            return

        def _insert(name: str) -> None:
            if not self._host.insert_symbol_named(name):
                choices = ", ".join(sorted(self._host._symbol_library))
                raise ValueError(f"Choose: {choices}")

        self._host._show_text_hud_prompt(
            f"Symbol: {', '.join(sorted(self._host._symbol_library))}",
            _insert,
        )

    def insert_symbol_named(self, name: str) -> bool:
        match = next(
            (
                key
                for key in self._host._symbol_library
                if key.casefold() == name.strip().casefold()
            ),
            None,
        )
        if match is None:
            return False
        old_clipboard = deepcopy(self._host._clipboard)
        self._host._clipboard = deepcopy(self._host._symbol_library[match])
        x = self._host._cursor_wx if self._host._cursor_wx is not None else 0.0
        y = self._host._cursor_wy if self._host._cursor_wy is not None else 0.0
        created = self._host._paste_records(x, y)
        self._host._clipboard = old_clipboard
        created_ids = tuple(created)
        self._host._apply_operation_result(
            OperationResult(
                changed=bool(created),
                message=f"Inserted symbol: {match}" if created else "Symbol contains no geometry",
                created_ids=created_ids,
                selected_ids=created_ids,
                metadata={"symbol": match},
            )
        )
        return bool(created)

    def rename_symbol(self, old_name: str, new_name: str) -> bool:
        clean = new_name.strip()
        if old_name not in self._host._symbol_library or not clean:
            return False
        conflict = next(
            (name for name in self._host._symbol_library if name.casefold() == clean.casefold()),
            None,
        )
        if conflict is not None and conflict != old_name:
            self._host._show_flash(f"A symbol named {clean} already exists", 1200)
            return False
        records = self._host._symbol_library.pop(old_name)
        self._host._symbol_library[clean] = records
        self._host._notify()
        self._host._show_flash(f"Renamed symbol to {clean}", 900)
        return True

    def prompt_rename_symbol(self, old_name: str) -> None:
        self._host._show_text_hud_prompt(
            f"Rename {old_name}", lambda new_name: self._host.rename_symbol(old_name, new_name)
        )

    def delete_symbol(self, name: str) -> bool:
        if name not in self._host._symbol_library:
            return False
        del self._host._symbol_library[name]
        self._host._notify()
        self._host._show_flash(f"Deleted symbol: {name}", 900)
        return True

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

    def prompt_morph_selected_paths(self) -> None:
        if len(self._host._mutable_selected_ids()) != 2:
            self._host._show_flash("Select exactly two paths to morph", 1200)
            return

        def _apply(percent: float) -> None:
            self._host._morph_selected_paths(percent)

        self._host._show_hud_prompt(
            "Morph amount (%)",
            50.0,
            _apply,
            minimum=0.0,
            is_length=False,
            preview=self._host._preview_morph_selected,
        )

    def _preview_morph_selected(self, percent: float) -> None:
        from simple_stipple.engine.cad.path_ops import morph_paths

        indices = self._host._mutable_selected_ids()
        if len(indices) != 2:
            self._host._clear_operation_preview()
            return
        try:
            points = morph_paths(
                self._host._entities_by_id[indices[0]].points,
                self._host._entities_by_id[indices[1]].points,
                percent / 100.0,
            )
        except ValueError:
            self._host._clear_operation_preview()
            return
        self._host._set_operation_preview([points])

    def _morph_selected_paths(self, percent: float) -> bool:
        from simple_stipple.engine.cad.path_ops import morph_paths

        indices = self._host._mutable_selected_ids()
        if len(indices) != 2:
            self._host._apply_operation_result(
                OperationResult.unchanged("Select exactly two paths to morph")
            )
            return False
        try:
            points = morph_paths(
                self._host._entities_by_id[indices[0]].points,
                self._host._entities_by_id[indices[1]].points,
                percent / 100.0,
            )
        except ValueError as exc:
            self._host._apply_operation_result(OperationResult.unchanged(str(exc)))
            return False
        entity = EntityRecord(points=points, layer=self._host._active_layer)
        result = self._host._canvas_service.create_entities([entity])
        entity_id = result.created_ids[0]
        self._host._apply_operation_result(
            OperationResult(
                changed=True,
                message=f"Created {percent:g}% path morph",
                created_ids=(entity_id,),
                selected_ids=(entity_id,),
                metadata={"amount": percent / 100.0},
            )
        )
        self._host._set_repeat_action(
            f"Morph {percent:g}%", lambda value=percent: self._host._morph_selected_paths(value)
        )
        return True

    def _set_repeat_action(self, label: str, callback) -> None:
        self._host._last_repeat_action = (str(label), callback)

    def _set_operation_preview(self, polys: list[list[tuple[float, float]]]) -> None:
        self._host._operation_preview_polys = [list(poly) for poly in polys]
        self._host._redraw()

    def _clear_operation_preview(self) -> None:
        if self._host._operation_preview_polys:
            self._host._operation_preview_polys = []
            self._host._redraw()
