"""Coherent target → preview → placement workflow for sketch dimensions."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from shapely.geometry import LineString
from shapely.ops import nearest_points

from simple_stipple.canvas.tools.tools import DimensionTool as DimensionBackend
from simple_stipple.document.model import EntityRecord


class DimensionTool(DimensionBackend):
    """Dimension interaction with explicit acquisition and placement stages.

    The inherited class supplies associative refresh/edit operations. Creation
    is deliberately replaced here so inference never commits halfway through
    target selection and never shares state with free-point drawing.
    """

    def __init__(self, view) -> None:
        super().__init__(view)
        self.targets: list[dict[str, Any]] = []
        self.hover_target: dict[str, Any] | None = None
        self.candidate: dict[str, Any] | None = None

    @property
    def stage(self) -> str:
        return "place" if self.candidate is not None else "select"

    def reset(self) -> None:
        self.targets.clear()
        self.hover_target = None
        self.candidate = None
        self.v._dim_pending_p1 = None
        self.v._dim_pending_p2 = None
        self.v._dim_selected_segments.clear()
        self.v._dim_hover_segment = None

    def back(self) -> bool:
        if self.candidate is not None:
            self.candidate = None
            self.v.modeChanged.emit(self.v._mode)
            return True
        if self.targets:
            self.targets.pop()
            self.v.modeChanged.emit(self.v._mode)
            return True
        return False

    def guidance(self) -> str:
        if self.candidate is not None:
            return "Position the dimension and click to place · Esc exits"
        if not self.targets:
            return "Select a segment, vertex, or circle · Esc exits"
        return "Select the related segment or vertex · Right-click removes the target"

    def _circle_at(self, cx: float, cy: float) -> dict[str, Any] | None:
        v = self.v
        for entity in v._entities:
            meta = entity.meta if isinstance(entity.meta, dict) else {}
            if entity.kind != "circle" or "center" not in meta or "radius" not in meta:
                continue
            center = tuple(meta["center"])
            radius = abs(float(meta["radius"]))
            center_c = v._w2c(*center)
            if abs(math.dist((cx, cy), center_c) - radius * v._scale) <= 9.0:
                wx, wy = v._c2w(cx, cy)
                dx, dy = wx - center[0], wy - center[1]
                length = math.hypot(dx, dy) or 1.0
                return {
                    "kind": "circle",
                    "entity_id": entity.id,
                    "center": center,
                    "radius": radius,
                    "direction": (dx / length, dy / length),
                }
        return None

    def _vertex_at(self, cx: float, cy: float) -> dict[str, Any] | None:
        hit = self.v._find_nearest_vertex(cx, cy)
        if hit is None:
            return None
        entity_id, vertex_index = hit
        entity = self.v._entities_by_id.get(entity_id)
        if entity is None:
            return None
        return {
            "kind": "vertex",
            "entity_id": entity.id,
            "vertex_index": vertex_index,
            "point": entity.points[vertex_index],
        }

    def _target_at(self, cx: float, cy: float) -> dict[str, Any] | None:
        circle = self._circle_at(cx, cy)
        if circle is not None:
            return circle
        vertex = self._vertex_at(cx, cy)
        if vertex is not None:
            return vertex
        segment = self._segment_at(cx, cy)
        if segment is not None:
            return {"kind": "segment", **segment}
        return {"kind": "point", "point": self.v._c2w(cx, cy)}

    @staticmethod
    def _same(first: dict, second: dict) -> bool:
        if first["kind"] != second["kind"]:
            return False
        if first["kind"] == "segment":
            return first["key"] == second["key"]
        if first["kind"] == "vertex":
            return (first["entity_id"], first["vertex_index"]) == (
                second["entity_id"],
                second["vertex_index"],
            )
        if first["kind"] == "point":
            return math.dist(first["point"], second["point"]) < 1e-9
        return first["entity_id"] == second["entity_id"]

    def _segment_candidate(self, first: dict, second: dict) -> dict[str, Any] | None:
        if self._same(first, second):
            return {
                "type": "linear",
                "p1": first["p1"],
                "p2": first["p2"],
                "offset": 5.0,
                "precision": 2,
                "driving": {"kind": "segment_length", "sources": [self._reference(first)]},
            }
        intersection = self._inclusive_intersection(first, second)
        ax, ay = first["p2"][0] - first["p1"][0], first["p2"][1] - first["p1"][1]
        bx, by = second["p2"][0] - second["p1"][0], second["p2"][1] - second["p1"][1]
        cross = ax * by - ay * bx
        sources = [self._reference(first), self._reference(second)]
        if intersection is not None and abs(cross) > 1e-9:
            return {
                "type": "angle",
                "p1": self._away_from(first, intersection),
                "p2": intersection,
                "p3": self._away_from(second, intersection),
                "points": [
                    self._away_from(first, intersection),
                    intersection,
                    self._away_from(second, intersection),
                ],
                "offset": 0.0,
                "precision": 1,
                "driving": {"kind": "angle", "sources": sources},
            }
        point_a, point_b = nearest_points(
            LineString([first["p1"], first["p2"]]),
            LineString([second["p1"], second["p2"]]),
        )
        p1, p2 = (float(point_a.x), float(point_a.y)), (float(point_b.x), float(point_b.y))
        if math.dist(p1, p2) < 1e-9:
            return None
        kind = "spacing" if abs(cross) <= 1e-9 else "distance"
        return {
            "type": kind,
            "p1": p1,
            "p2": p2,
            "offset": 0.0,
            "precision": 2,
            "driving": {"kind": kind, "sources": sources},
        }

    def _build_candidate(self) -> dict[str, Any] | None:
        first = self.targets[0]
        if first["kind"] == "circle":
            cx, cy = first["center"]
            radius = first["radius"]
            ux, uy = first["direction"]
            return {
                "type": "diameter",
                "p1": (cx - ux * radius, cy - uy * radius),
                "p2": (cx + ux * radius, cy + uy * radius),
                "offset": 5.0,
                "precision": 2,
                "driving": {
                    "kind": "circle_diameter",
                    "sources": [{"entity_id": first["entity_id"]}],
                    "direction": first["direction"],
                },
            }
        if len(self.targets) < 2:
            return None
        second = self.targets[1]
        if first["kind"] == second["kind"] == "segment":
            return self._segment_candidate(first, second)
        point_kinds = {"vertex", "point"}
        if first["kind"] in point_kinds and second["kind"] in point_kinds:
            if self._same(first, second):
                return None
            if self.v._dimension_kind == "angle":
                if len(self.targets) < 3:
                    return None
                third = self.targets[2]
                if third["kind"] not in point_kinds:
                    return None
                return {
                    "type": "angle",
                    "p1": first["point"],
                    "p2": second["point"],
                    "p3": third["point"],
                    "points": [first["point"], second["point"], third["point"]],
                    "offset": 0.0,
                    "precision": 1,
                }
            dimension = {
                "type": "linear",
                "p1": first["point"],
                "p2": second["point"],
                "offset": 5.0,
                "precision": 2,
            }
            if first["kind"] == second["kind"] == "vertex":
                dimension["driving"] = {
                    "kind": "point_distance",
                    "sources": [
                        {
                            "entity_id": first["entity_id"],
                            "vertex_index": first["vertex_index"],
                        },
                        {
                            "entity_id": second["entity_id"],
                            "vertex_index": second["vertex_index"],
                        },
                    ],
                }
            return dimension
        return None

    def _vertex_from_ref(
        self, reference: dict
    ) -> tuple[EntityRecord, int, tuple[float, float]] | None:
        entity_id = str(reference.get("entity_id", ""))
        vertex_index = int(reference.get("vertex_index", -1))
        entity = self.v._entity_for_id(entity_id)
        if entity is None or not (0 <= vertex_index < len(entity.points)):
            return None
        return entity, vertex_index, entity.points[vertex_index]

    def refresh_driving_dimension(self, dimension: dict) -> bool:
        driving = dimension.get("driving")
        if isinstance(driving, dict) and driving.get("kind") == "circle_diameter":
            sources = driving.get("sources", [])
            entity_id = str(sources[0].get("entity_id", "")) if sources else ""
            entity = next((item for item in self.v._entities if item.id == entity_id), None)
            meta = entity.meta if entity is not None and isinstance(entity.meta, dict) else {}
            if "center" not in meta or "radius" not in meta:
                return False
            cx, cy = meta["center"]
            radius = float(meta["radius"])
            ux, uy = driving.get("direction", (1.0, 0.0))
            dimension["p1"] = (cx - ux * radius, cy - uy * radius)
            dimension["p2"] = (cx + ux * radius, cy + uy * radius)
            return True
        if isinstance(driving, dict) and driving.get("kind") == "point_distance":
            vertices = [
                self._vertex_from_ref(reference)
                for reference in driving.get("sources", [])
                if isinstance(reference, dict)
            ]
            if len(vertices) != 2 or any(vertex is None for vertex in vertices):
                return False
            dimension["p1"] = vertices[0][2]  # type: ignore[index]
            dimension["p2"] = vertices[1][2]  # type: ignore[index]
            return True
        return super().refresh_driving_dimension(dimension)

    def set_value(self, index: int, target: float) -> bool:
        if not (0 <= index < len(self.v._dimensions)):
            return False
        dimension = self.v._dimensions[index]
        driving = dimension.get("driving")
        if isinstance(driving, dict) and driving.get("kind") == "circle_diameter":
            sources = driving.get("sources", [])
            entity_id = str(sources[0].get("entity_id", "")) if sources else ""
            source_entity = self.v._entity_for_id(entity_id)
            if source_entity is None or target <= 0:
                return False
            entity = deepcopy(source_entity)
            if not isinstance(entity.meta, dict):
                return False
            entity.meta["radius"] = target / 2.0
            result = self.v._canvas_service.update_entities([entity])
            if not result.changed:
                return False
            self.v._fire_poly_change()
            driving["target"] = float(target)
            self.v._refresh_driving_dimensions()
            self.v._redraw()
            self.v._notify()
            return True
        if not isinstance(driving, dict) or driving.get("kind") != "point_distance":
            return super().set_value(index, target)
        vertices = [
            self._vertex_from_ref(reference)
            for reference in driving.get("sources", [])
            if isinstance(reference, dict)
        ]
        if len(vertices) != 2 or any(vertex is None for vertex in vertices) or target <= 0:
            return False
        first, second = vertices
        assert first is not None and second is not None
        current = math.dist(first[2], second[2])
        if current < 1e-9:
            return False
        ux = (second[2][0] - first[2][0]) / current
        uy = (second[2][1] - first[2][1]) / current
        new_point = (first[2][0] + ux * target, first[2][1] + uy * target)
        entity = deepcopy(self.v._entities_by_id[second[0].id])
        entity.points[second[1]] = new_point
        if len(entity.points) > 2 and math.dist(entity.points[0], entity.points[-1]) < 0.01:
            if second[1] == 0:
                entity.points[-1] = new_point
            elif second[1] == len(entity.points) - 1:
                entity.points[0] = new_point
        if entity.kind == "line" and len(entity.points) == 2:
            entity.meta = {"start": entity.points[0], "end": entity.points[1]}
        else:
            # Moving one vertex generally invalidates rectangle/ellipse/etc.
            # procedural parameters. Preserve the edited points as canonical
            # geometry so properties and export cannot resurrect the old shape.
            entity.kind = "polyline"
            entity.meta = None
        result = self.v._canvas_service.update_entities([entity])
        if not result.changed:
            return False
        self.v._fire_poly_change()
        driving["target"] = float(target)
        self.v._refresh_driving_dimensions()
        self.v._redraw()
        self.v._notify()
        self.v._show_flash(f"Driving dimension updated to {target:g}", 1500)
        return True

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if self.candidate is not None:
            wx, wy = v._c2w(pos.x(), pos.y())
            if self.candidate.get("type") != "angle":
                self.candidate["offset"] = v._dimension_offset_at(self.candidate, wx, wy)
            placed = dict(self.candidate)
            placed.setdefault("layer", v._active_layer)
            driving = placed.get("driving")
            if isinstance(driving, dict):
                driving = dict(driving)
                driving["target"] = self.value(placed)
                placed["driving"] = driving
            v._selected_dimension = v._append_dimension(placed)
            v._notify()
            self.reset()
            # Dimension placement is one-shot. Leaving the overlay armed here
            # intercepts every later click as another dimension target and
            # makes the newly dimensioned shape appear impossible to select.
            v._dimension_mode = False
            v._update_cursor()
            v._show_flash(
                "Dimension placed · Select mode restored · double-click its value to edit",
                1900,
            )
            v._redraw()
            v.modeChanged.emit(v._mode)
            return True
        target = self._target_at(pos.x(), pos.y())
        if target is None:
            v._show_flash("No dimension target here · choose a vertex, segment, or circle", 1500)
            return True
        if target["kind"] == "point":
            wx, wy = target["point"]
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap = v._resolve_snap(
                pos.x(), pos.y(), wx, wy, allow_polyline=allow_snap, allow_grid=allow_snap
            )
            if snap is not None:
                target["point"] = (snap[0], snap[1])
        self.targets.append(target)
        point_targets = [item for item in self.targets if item["kind"] in {"point", "vertex"}]
        v._dim_pending_p1 = point_targets[0]["point"] if point_targets else None
        v._dim_pending_p2 = point_targets[1]["point"] if len(point_targets) > 1 else None
        v._dim_selected_segments = [item for item in self.targets if item["kind"] == "segment"]
        self.candidate = self._build_candidate()
        required = 3 if self.v._dimension_kind == "angle" else 2
        if self.candidate is None and len(self.targets) >= required:
            self.targets.pop()
            v._show_flash("Those targets do not define a supported dimension", 1600)
        elif self.candidate is not None:
            v._show_flash("Dimension inferred · move to position it, then click", 1500)
        v._redraw()
        v.modeChanged.emit(v._mode)
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        v._cursor_wx, v._cursor_wy = wx, wy
        if self.candidate is not None and self.candidate.get("type") != "angle":
            self.candidate["offset"] = v._dimension_offset_at(self.candidate, wx, wy)
        else:
            self.hover_target = self._target_at(pos.x(), pos.y())
            if self.hover_target is not None and self.hover_target["kind"] == "point":
                snap = v._resolve_snap(
                    pos.x(), pos.y(), wx, wy, allow_polyline=True, allow_grid=True
                )
                if snap is not None:
                    self.hover_target["point"] = (snap[0], snap[1])
                    v._hover_snap = (snap[0], snap[1])
                    v._hover_snap_type = snap[2]
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True

    def paint_overlay(self, painter: QPainter) -> None:
        v = self.v
        targets = [*self.targets]
        if self.hover_target is not None and not any(
            self._same(self.hover_target, target) for target in targets
        ):
            targets.append(self.hover_target)
        for target in targets:
            selected = target in self.targets
            color = QColor("#f5a623") if selected else QColor("#a371f7")
            painter.setPen(QPen(color, 4.0 if selected else 2.5))
            if target["kind"] == "segment":
                painter.drawLine(QPointF(*v._w2c(*target["p1"])), QPointF(*v._w2c(*target["p2"])))
            elif target["kind"] == "vertex":
                painter.drawEllipse(QPointF(*v._w2c(*target["point"])), 7, 7)
            elif target["kind"] == "circle":
                center = v._w2c(*target["center"])
                radius = target["radius"] * v._scale
                painter.drawEllipse(QPointF(*center), radius, radius)
            elif target["kind"] == "point":
                painter.drawEllipse(QPointF(*v._w2c(*target["point"])), 5, 5)
        if self.candidate is None:
            return
        if self.candidate.get("type") == "angle":
            painter.setPen(QPen(QColor("#39c5cf"), 2.0, Qt.PenStyle.DashLine))
            vertex = QPointF(*v._w2c(*self.candidate["p2"]))
            painter.drawLine(vertex, QPointF(*v._w2c(*self.candidate["p1"])))
            painter.drawLine(vertex, QPointF(*v._w2c(*self.candidate["p3"])))
            p1 = v._w2c(*self.candidate["p1"])
            p3 = v._w2c(*self.candidate["p3"])
            a1 = math.atan2(p1[1] - vertex.y(), p1[0] - vertex.x())
            a2 = math.atan2(p3[1] - vertex.y(), p3[0] - vertex.x())
            sweep = (a2 - a1 + math.pi) % math.tau - math.pi
            radius = 28.0
            points = [
                QPointF(
                    vertex.x() + math.cos(a1 + sweep * step / 20) * radius,
                    vertex.y() + math.sin(a1 + sweep * step / 20) * radius,
                )
                for step in range(21)
            ]
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)
            angle = self.value(self.candidate)
            mid = a1 + sweep / 2.0
            v._renderer._draw_badge(
                painter,
                vertex.x() + math.cos(mid) * 48.0,
                vertex.y() + math.sin(mid) * 48.0,
                f"{angle:.1f}°",
                9,
            )
        else:
            label = v._renderer._format_dimension_value(self.candidate)
            v._renderer._paint_dimension_line(
                painter,
                self.candidate["p1"],
                self.candidate["p2"],
                self.candidate["offset"],
                label,
                color=QColor("#39c5cf"),
            )


__all__ = ["DimensionTool"]
