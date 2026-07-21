"""Draw sidebar widgets and draw-tool state composed by the canvas view."""

from __future__ import annotations

import math
from typing import Any, cast

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation

from src.backend.cad.constraints import ConstraintKind, GeometricConstraint, solve_constraints
from src.backend.cad.geometry import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
    build_rounded_rect_poly,
    build_star_poly,
    shape_slot,
)
from src.backend.cad.shapes import ShapeFactory
from src.backend.model.document import EntityRecord
from src.core.settings import normalize_draw_sidebar_shape_tools
from src.ui.widgets.canvas.draw_sidebar import DrawSidebar


class DrawOpsService:
    """Own draw sidebar widgets, tool state, and shape-preview commits."""

    def __init__(self, host) -> None:
        self._host = host

    def _build_draw_sidebar(self) -> None:
        was_visible = self._host._draw_sidebar_visible
        if self._host._draw_sidebar is not None:
            # Rebuild (e.g. the customize-sections dialog changed the
            # section list) — drop the old panel/animation cleanly first.
            self._host._draw_sidebar.hide()
            self._host._draw_sidebar.deleteLater()
            self._host._draw_sidebar = None
            self._host._draw_sidebar_anim = None

        panel = DrawSidebar(
            parent=self._host,
            on_polyline_family=self._on_polyline_family_change,
            on_shapes_family=self._on_shapes_family_change,
            on_text=lambda: self._set_draw_primitive("text"),
            on_arc_mode=self._on_arc_mode_change,
            on_constraint=self._on_constraint_change,
            on_split=self._on_split_change,
            on_dimension=self._host.toggle_dimension_mode,
            on_smoothing_method=self._host._on_smoothing_method_changed,
            on_finish_open=lambda: self._host._finish_draw(close=False),
            on_close_edit=lambda: self._host._finish_draw(close=True),
            on_undo_point=self._host._key_backspace,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self._host.set_mode("select"),
            width=self._host._draw_sidebar_width,
            sections=self._host._draw_sidebar_sections,
            path_tools=self._host._draw_sidebar_path_tools,
            shape_tools=self._host._draw_sidebar_shape_tools,
            on_width_changed=self._on_draw_sidebar_width_changed,
            on_height_changed=self._on_draw_sidebar_height_changed,
        )
        panel.hide()

        anim = QPropertyAnimation(panel, b"pos", self._host)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._host._draw_sidebar = panel
        self._host._draw_sidebar_anim = anim
        self._host._refresh_draw_sidebar_state()
        if was_visible:
            self._set_draw_sidebar_visible(True, animate=False)

    def _on_draw_sidebar_width_changed(self, width: int) -> None:
        self._host._draw_sidebar_width = width
        self._layout_draw_sidebar()
        self._host.drawSidebarWidthChanged.emit(width)

    def set_draw_sidebar_width(self, width: int) -> None:
        """Apply a width from settings (app startup / another window
        resized it) without re-emitting drawSidebarWidthChanged."""
        self._host._draw_sidebar_width = width
        if self._host._draw_sidebar is not None:
            self._host._draw_sidebar._apply_width(width)
            self._layout_draw_sidebar()

    def _on_draw_sidebar_height_changed(self, height: int) -> None:
        self._host._draw_sidebar_height = height
        self._layout_draw_sidebar()
        self._host.drawSidebarHeightChanged.emit(height)

    def set_draw_sidebar_height(self, height: int | None) -> None:
        """Apply a height from settings (app startup / another window
        resized it) without re-emitting drawSidebarHeightChanged. ``None``
        reverts to auto-fitting the available space."""
        self._host._draw_sidebar_height = height
        if self._host._draw_sidebar is not None and height is not None:
            self._host._draw_sidebar._apply_height(height)
        self._layout_draw_sidebar()

    def set_draw_sidebar_sections(self, sections: list[str]) -> None:
        self._host._draw_sidebar_sections = list(sections)
        self._build_draw_sidebar()

    def set_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        self._host._draw_sidebar_path_tools = list(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        self._host._draw_sidebar_shape_tools = normalize_draw_sidebar_shape_tools(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_always_visible(self, enabled: bool) -> None:
        self._host._draw_sidebar_always_visible = enabled
        self._set_draw_sidebar_visible(self._host._mode == "draw" or enabled)

    def _draw_sidebar_target_height(self, y: int) -> int:
        """Auto-fit height (available canvas space) unless the user has
        manually dragged the sidebar's own bottom-edge handle, in which
        case that override sticks until they resize it again."""
        if self._host._draw_sidebar_height is not None:
            return self._host._draw_sidebar_height
        return min(430, max(260, self._host.height() - y - 8))

    def _layout_draw_sidebar(self) -> None:
        if self._host._draw_sidebar is None:
            return
        left = self._host._chrome_left()
        top = self._host._chrome_top()
        y = top + 8
        self._host._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))
        x = (
            left + 8
            if self._host._draw_sidebar_visible
            else left - self._host._draw_sidebar.width() + 20
        )
        self._host._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._host._draw_sidebar is None or self._host._draw_sidebar_anim is None:
            return
        if self._host._draw_sidebar_always_visible:
            visible = True
        if self._host._draw_sidebar_visible == visible and self._host._draw_sidebar.isVisible():
            self._host._refresh_draw_sidebar_state()
            return

        self._host._draw_sidebar_visible = visible
        self._host._refresh_draw_sidebar_state()
        left = self._host._chrome_left()
        y = self._host._chrome_top() + 8
        hidden_x = left - self._host._draw_sidebar.width() + 20
        shown_x = left + 8
        self._host._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))

        if not animate:
            if visible:
                self._host._draw_sidebar.show()
                self._host._draw_sidebar.move(shown_x, y)
            else:
                self._host._draw_sidebar.move(hidden_x, y)
                self._host._draw_sidebar.hide()
            return

        if visible:
            self._host._draw_sidebar.show()
            self._host._draw_sidebar.move(hidden_x, y)
            self._host._draw_sidebar_anim.stop()
            self._host._draw_sidebar_anim.setStartValue(QPoint(hidden_x, y))
            self._host._draw_sidebar_anim.setEndValue(QPoint(shown_x, y))
            self._host._draw_sidebar_anim.start()
        else:
            self._host._draw_sidebar_anim.stop()
            self._host._draw_sidebar_anim.setStartValue(self._host._draw_sidebar.pos())
            self._host._draw_sidebar_anim.setEndValue(QPoint(hidden_x, y))
            self._host._draw_sidebar_anim.start()

    def _refresh_draw_sidebar_state(self) -> None:
        if not isinstance(self._host._draw_sidebar, DrawSidebar):
            return
        has_pts = len(self._host._draw_pts)
        self._host._draw_sidebar.set_polyline_actions_enabled(
            can_finish=has_pts >= 2,
            can_close=has_pts >= 3,
            can_undo=has_pts >= 1,
        )
        self._host._draw_sidebar.set_split_enabled(self._host._draw_split_enabled)
        self._host._draw_sidebar.set_smoothing_method(self._host._smoothing_method)
        self._host._draw_sidebar.set_active_tool(self._host._draw_primitive)
        self._host._draw_sidebar.set_arc_mode(self._host._draw_arc_mode)
        self._host._draw_sidebar.set_arc_mode_enabled(self._host._draw_primitive == "arc")
        self._host._draw_sidebar.set_constraint_mode(self._host._draw_constraint_lock)
        self._host._draw_sidebar.set_constraint_mode_enabled(
            self._host._draw_primitive in {"line", "polyline"}
        )
        self._host._update_shape_size_fields_from_preview()

    def _commit_shape_preview(self) -> bool:
        if not self._host._draw_shape_preview_active:
            return False
        if self._host._draw_shape_anchor_w is None or self._host._draw_shape_cursor_w is None:
            return False
        sx, sy = self._host._draw_shape_anchor_w
        ex, ey = self._host._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)

        poly: list[tuple[float, float]] = []
        kind = "polyline"
        meta: dict[str, Any] | None = None
        if self._host._draw_primitive in {"rectangle", "rounded_rectangle"}:
            rounded = self._host._draw_primitive == "rounded_rectangle"
            radius = min(w, h) * 0.1
            poly = (
                build_rounded_rect_poly(cx, cy, w, h, radius)
                if rounded
                else build_rect_poly(cx, cy, w, h)
            )
            kind = self._host._draw_primitive
            meta = {
                "center": (cx, cy),
                "width": w,
                "height": h,
                "rotation": 0.0,
            }
            if rounded:
                meta["radius"] = radius
        elif self._host._draw_primitive == "circle":
            # Match preview behavior: first click is center, drag to radius.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
            kind = "circle"
            meta = {"center": (sx, sy), "radius": radius}
        elif self._host._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
            kind = "ellipse"
            meta = {"center": (cx, cy), "rx": w / 2.0, "ry": h / 2.0, "rotation": 0.0}
        elif self._host._draw_primitive == "slot":
            poly = [(px + cx, py + cy) for px, py in shape_slot(w, h)]
            kind = "slot"
            meta = {"center": (cx, cy), "length": w, "width": h, "rotation": 0.0}
        elif self._host._draw_primitive == "polygon":
            # Center-first, matching circle: first click is center, drag
            # sets the radius directly (was previously bounding-box corner
            # to corner, unlike every other radius-based shape).
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_polygon_poly(sx, sy, radius, self._host._draw_polygon_sides)
            kind = "polygon"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "sides": self._host._draw_polygon_sides,
                "rotation": 0.0,
            }
        elif self._host._draw_primitive == "star":
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_star_poly(sx, sy, radius, self._host._draw_star_points)
            kind = "star"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "points": self._host._draw_star_points,
                "inner_ratio": 0.45,
                "rotation": -90.0,
            }

        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None

        if len(poly) >= 2:
            if (
                self._host._draw_split_enabled
                and not self._host._draw_construction_mode
                and self._host._is_poly_closed(poly)
            ):
                before = self._host._canvas_service.begin_preview()
                carved, count = self._host._carve_geometry_with_shape(poly)
                if carved:
                    self._host._entities.append(
                        EntityRecord(
                            points=list(poly),
                            kind=kind,
                            meta=meta,
                            layer=self._host._active_layer,
                        )
                    )
                    self._host._document.selection = {len(self._host._entities) - 1}
                    self._host._canvas_service.commit_preview(before)
                    self._host._notify()
                    self._host._fire_poly_change()
                    self._host._show_flash(f"Carved {count} region(s)", 1000)
                    self._host._refresh_draw_sidebar_state()
                    self._host._redraw()
                    return True
            self._host._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._host._show_flash(f"{self._host._draw_primitive.title()} created", 800)
            self._host._refresh_draw_sidebar_state()
            self._host._redraw()
            return True

        self._host._refresh_draw_sidebar_state()
        self._host._redraw()
        return False

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._host._draw_sidebar is None:
            return
        if not self._host._draw_sidebar_visible:
            self._host._draw_sidebar.hide()

    def _on_polyline_family_change(self, tool: str) -> None:
        if self._host._mode != "draw":
            self._host.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_shapes_family_change(self, tool: str) -> None:
        if self._host._mode != "draw":
            self._host.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_arc_mode_change(self, mode: str) -> None:
        if self._host._draw_primitive != "arc":
            self._set_draw_primitive("arc")
        self._host._draw_arc_mode = mode
        self._host._draw_arc_pts.clear()
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(
            "Arc: center-start-end" if mode == "center-start-end" else "Arc: three-point",
            900,
        )
        self._host._redraw()

    def _on_constraint_change(self, mode: str) -> None:
        self._host._draw_constraint_lock = None if mode == "Free" else mode
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(
            f"Constraint: {self._host._draw_constraint_lock}"
            if self._host._draw_constraint_lock
            else "Constraint: Free",
            900,
        )
        self._host._redraw()

    def _on_split_change(self, enabled: bool) -> None:
        self._host._draw_split_enabled = enabled
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash("Split: on" if enabled else "Split: off", 800)

    def _cancel_draw_points(self) -> None:
        if self._host._mode != "draw":
            return
        self._host._draw_pts.clear()
        self._host._draw_point_snap_types.clear()
        self._host._draw_snap = None
        self._host._draw_snap_type = None
        self._host._draw_constraint = None
        self._host._angle_snap_active = False
        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None
        self._host._draw_arc_pts.clear()
        if hasattr(self, "_dismiss_shape_dim_inputs"):
            self._host._dismiss_shape_dim_inputs()
        self._host._dismiss_dim_inputs()
        self._host._refresh_draw_sidebar_state()
        self._host._redraw()

    def _set_draw_primitive(self, tool: str) -> None:
        valid = {
            "polyline",
            "line",
            "arc",
            "spline",
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
            "text",
            "bezier",
        }
        if tool not in valid:
            return
        self._host._draw_primitive = tool
        self._host._draw_pts.clear()
        self._host._draw_arc_pts.clear()
        self._host._pen_pts.clear()
        self._host._pen_tangents.clear()
        self._host._pen_dragging = False
        self._host._pen_press_screen = None
        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None
        self._host._dismiss_dim_inputs()
        self._host._update_shape_size_fields_from_preview()
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(f"Tool: {tool}", 650)
        self._host._redraw()


# Construction and constraint state adaptation


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
            else:
                entity.kind = "polyline"
                entity.meta = None
            changed += 1
        return changed

    def add_geometric_constraint(self, kind: str) -> int:
        """Attach an explicit persistent constraint to selected line geometry."""
        line_indices = [
            index
            for index in self._host._mutable_selected_indices()
            if len(self._host._entities[index].points) == 2
        ]
        unary = {"horizontal", "vertical", "fixed"}
        binary = {"parallel", "perpendicular", "equal_length", "coincident"}
        if kind in unary and not line_indices:
            self._host._show_flash("Select one or more line segments", 1200)
            return 0
        if kind in binary and len(line_indices) != 2:
            self._host._show_flash("Select exactly two line segments", 1200)
            return 0
        before = self._host._canvas_service.begin_preview()
        additions: list[GeometricConstraint] = []
        constraint_kind = cast(ConstraintKind, kind)
        if kind in {"horizontal", "vertical"}:
            additions = [
                GeometricConstraint(
                    kind=constraint_kind, entity_ids=(self._host._entities[index].id,)
                )
                for index in line_indices
            ]
        elif kind == "fixed":
            additions = [
                GeometricConstraint(
                    kind="fixed",
                    entity_ids=(self._host._entities[index].id,),
                    parameters={
                        "points": [list(point) for point in self._host._entities[index].points]
                    },
                )
                for index in line_indices
            ]
        elif kind in binary:
            first, second = (self._host._entities[index] for index in line_indices)
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
        else:
            return 0
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
            self._host._entities[index].id
            for index in self._host._selected_indices()
            if 0 <= index < len(self._host._entities)
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
            for index in self._host._mutable_selected_indices()
            if len(self._host._entities[index].points) == 2
        ]
        if len(indices) != 1:
            self._host._show_flash("Select exactly one line segment", 1100)
            return 0
        start, end = self._host._entities[indices[0]].points
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
        from src.backend.cad.construction import angle_bisector

        lines = [
            self._host._entities[index].points
            for index in self._host._mutable_selected_indices()
            if len(self._host._entities[index].points) == 2
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
        from src.backend.cad.construction import centerline

        lines = [
            self._host._entities[index].points
            for index in self._host._mutable_selected_indices()
            if len(self._host._entities[index].points) == 2
        ]
        if len(lines) != 2:
            self._host._show_flash("Select exactly two edges", 1100)
            return 0
        result = centerline((lines[0][0], lines[0][1]), (lines[1][0], lines[1][1]))
        return self._commit_construction_entities([(list(result), "line", None)])

    def create_circle_through_three_points(self) -> int:
        from src.backend.cad.construction import circumcircle

        selected = self._host._mutable_selected_indices()
        candidates: list[tuple[float, float]] = []
        if len(selected) == 1:
            candidates = list(self._host._entities[selected[0]].points[:3])
        elif len(selected) == 3:
            candidates = [
                self._host._entities[index].points[0]
                for index in selected
                if self._host._entities[index].points
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
        from src.backend.cad.construction import tangents_from_point

        selected = [self._host._entities[index] for index in self._host._mutable_selected_indices()]
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
        from src.backend.cad.construction import common_circle_tangents

        circles = [
            self._host._entities[index]
            for index in self._host._mutable_selected_indices()
            if self._host._entities[index].kind == "circle" and self._host._entities[index].meta
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
    #    mixin-inlining refactor; callers in dxf_canvas.py/render.py remained). ──

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
