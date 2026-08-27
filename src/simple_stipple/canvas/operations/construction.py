"""Construction geometry: guides, references, and derived helpers."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, cast

from simple_stipple.core.cad.constraints import (
    ConstraintKind,
    GeometricConstraint,
    solve_constraints,
)
from simple_stipple.core.cad.shape_factory import ShapeFactory
from simple_stipple.core.document.model import EntityRecord


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

    def construction_line_from_selection(self, *, ray: bool = False) -> int:
        indices = [
            index
            for index in self._host._selected_ids()
            if len(self._host._entities_by_id[index].points) == 2
        ]
        if len(indices) != 1:
            self._host._show_flash("Select exactly one line segment", 1100)
            return 0
        start, end = self._host._entities_by_id[indices[0]].points
        origin = start if ray else ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        kind = "ray" if ray else "xline"
        points = self._infinite_line_points(origin, (end[0] - start[0], end[1] - start[1]), ray=ray)
        count = self._commit_construction_entities(
            [
                (
                    points,
                    kind,
                    {"origin": origin, "direction": (end[0] - start[0], end[1] - start[1])},
                )
            ]
        )
        if count:
            self._host._show_flash(
                "Construction ray created" if ray else "Construction line created", 900
            )
        return count

    def create_angle_bisector(self) -> int:
        from simple_stipple.core.cad.primitives import angle_bisector

        lines = [
            self._host._entities_by_id[index].points
            for index in self._host._selected_ids()
            if len(self._host._entities_by_id[index].points) == 2
        ]
        if len(lines) != 2:
            self._host._show_flash("Select exactly two intersecting lines", 1200)
            return 0
        result = angle_bisector((lines[0][0], lines[0][1]), (lines[1][0], lines[1][1]))
        if result is None:
            self._host._show_flash("Parallel lines have no unique angle bisector", 1300)
            return 0
        origin, direction = result
        points = self._infinite_line_points(origin, direction)
        return self._commit_construction_entities(
            [(points, "xline", {"origin": origin, "direction": direction})]
        )

    def create_centerline(self) -> int:
        from simple_stipple.core.cad.primitives import centerline

        lines = [
            self._host._entities_by_id[index].points
            for index in self._host._selected_ids()
            if len(self._host._entities_by_id[index].points) == 2
        ]
        if len(lines) != 2:
            self._host._show_flash("Select exactly two edges", 1100)
            return 0
        result = centerline((lines[0][0], lines[0][1]), (lines[1][0], lines[1][1]))
        return self._commit_construction_entities([(list(result), "line", None)])

    def create_circle_through_three_points(self) -> int:
        from simple_stipple.core.cad.primitives import circumcircle

        selected = self._host._selected_ids()
        candidates: list[tuple[float, float]] = []
        if len(selected) == 1:
            candidates = list(self._host._entities_by_id[selected[0]].points[:3])
        elif len(selected) == 3:
            candidates = [
                self._host._entities_by_id[index].points[0]
                for index in selected
                if self._host._entities_by_id[index].points
            ]
        if len(candidates) != 3:
            self._host._show_flash("Select one 3+ point path or three point-bearing objects", 1500)
            return 0
        result = circumcircle(*candidates)
        if result is None:
            self._host._show_flash("Those points are collinear", 1000)
            return 0
        center, radius = result
        shape = ShapeFactory.circle(center, radius)
        return self._commit_construction_entities(
            [(list(shape.points), "circle", {"center": center, "radius": radius})]
        )

    def create_tangents_from_point(self) -> int:
        from simple_stipple.core.cad.primitives import tangents_from_point

        selected = [self._host._entities_by_id[index] for index in self._host._selected_ids()]
        circles = [entity for entity in selected if entity.kind == "circle" and entity.meta]
        others = [entity for entity in selected if entity not in circles and entity.points]
        if len(circles) != 1 or len(others) != 1:
            self._host._show_flash("Select one circle and one point-bearing object", 1400)
            return 0
        center = tuple(circles[0].meta["center"])
        point = max(others[0].points, key=lambda value: math.dist(value, center))
        lines = tangents_from_point(point, center, float(circles[0].meta["radius"]))
        if not lines:
            self._host._show_flash("Point must be outside the circle", 1100)
            return 0
        return self._commit_construction_entities([(list(line), "line", None) for line in lines])

    def create_common_circle_tangents(self) -> int:
        from simple_stipple.core.cad.primitives import common_circle_tangents

        circles = [
            self._host._entities_by_id[index]
            for index in self._host._selected_ids()
            if self._host._entities_by_id[index].kind == "circle"
            and self._host._entities_by_id[index].meta
        ]
        if len(circles) != 2:
            self._host._show_flash("Select exactly two circles", 1100)
            return 0
        first, second = circles
        lines = common_circle_tangents(
            tuple(first.meta["center"]),
            float(first.meta["radius"]),
            tuple(second.meta["center"]),
            float(second.meta["radius"]),
        )
        if not lines:
            self._host._show_flash("No real common tangents", 1000)
            return 0
        return self._commit_construction_entities([(list(line), "line", None) for line in lines])

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
