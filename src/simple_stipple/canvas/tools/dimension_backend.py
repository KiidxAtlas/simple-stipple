"""Associative dimension backend for sketch-dimension workflows.

The subclass in :mod:`dimension_tool` owns staged target selection and
placement. This backend owns segment references, dimension refresh, and
driving edits shared by that workflow.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from shapely.geometry import LineString
from shapely.ops import nearest_points

from simple_stipple.canvas.tools.base import CanvasTool
from simple_stipple.core.cad.editor_geometry import (
    angle_between_rays,
    transform_entity_metadata,
)
from simple_stipple.core.editing.transform import rotate, translate


class DimensionTool(CanvasTool):
    """Persistent drafting-dimension placement (any mode): click p1, click
    p2 (same snap + Shift-angle-snap as ScaleTool), then click again to
    set how far the dimension line sits from the measured segment and
    finalize it into ``v._dimensions``. Angular mode uses three snapped
    points; clicking a parametric circle creates a diameter dimension."""

    def _segment_at(self, cx: float, cy: float) -> dict | None:
        """Return the exact visible segment under the pointer."""
        v = self.v
        wx, wy = v._c2w(cx, cy)
        best: dict | None = None
        best_distance = 9.0
        for eid in v._document.entity_ids():
            if not v._entity_selectable_by_id(eid):
                continue
            points = v._flattened_points_by_id(eid)
            entity = v._document.entity_for_id(eid)
            if entity is None:
                continue
            distance, result = cast(
                tuple[float | None, tuple[int, tuple[float, float]] | None],
                v._closest_point_on_poly(points, wx, wy, cx, cy, return_segment=True),
            )
            if distance is None or result is None or distance >= best_distance:
                continue
            segment_index, _closest = result
            end_index = (segment_index + 1) % len(points)
            if not (0 <= segment_index < len(points) and 0 <= end_index < len(points)):
                continue
            first, second = points[segment_index], points[end_index]
            if math.dist(first, second) < 1e-9:
                continue
            best_distance = distance
            best = {
                "key": (eid, segment_index),
                "entity_id": eid,
                "segment_index": segment_index,
                "p1": first,
                "p2": second,
            }
        return best

    def _segment_from_ref(self, reference: dict) -> dict | None:
        v = self.v
        entity_id = str(reference.get("entity_id", ""))
        segment_index = int(reference.get("segment_index", -1))
        entity = v._document.entity_for_id(entity_id)
        if entity is None:
            return None
        points = entity.points
        if not (0 <= segment_index < len(points) - 1):
            return None
        return {
            "key": (entity_id, segment_index),
            "entity_id": entity_id,
            "segment_index": segment_index,
            "p1": points[segment_index],
            "p2": points[segment_index + 1],
        }

    @staticmethod
    def _reference(segment: dict) -> dict:
        return {
            "entity_id": segment["key"][0],
            "segment_index": segment["segment_index"],
        }

    @staticmethod
    def _inclusive_intersection(first: dict, second: dict) -> tuple[float, float] | None:
        (x1, y1), (x2, y2) = first["p1"], first["p2"]
        (x3, y3), (x4, y4) = second["p1"], second["p2"]
        denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denominator) < 1e-9:
            return None
        a = x1 * y2 - y1 * x2
        b = x3 * y4 - y3 * x4
        point = (
            (a * (x3 - x4) - (x1 - x2) * b) / denominator,
            (a * (y3 - y4) - (y1 - y2) * b) / denominator,
        )
        if all(
            min(start[i], end[i]) - 1e-6 <= point[i] <= max(start[i], end[i]) + 1e-6
            for start, end in ((first["p1"], first["p2"]), (second["p1"], second["p2"]))
            for i in (0, 1)
        ):
            return point
        return None

    @staticmethod
    def _away_from(segment: dict, point: tuple[float, float]) -> tuple[float, float]:
        return max((segment["p1"], segment["p2"]), key=lambda item: math.dist(item, point))

    def _finish_segment_dimension(self, second: dict) -> None:
        v = self.v
        first = v._dim_selected_segments[0]
        if first["key"] == second["key"]:
            v._append_dimension(
                {
                    "type": "linear",
                    "p1": first["p1"],
                    "p2": first["p2"],
                    "offset": 5.0,
                    "precision": 2,
                    "driving": {"kind": "segment_length", "sources": [self._reference(first)]},
                }
            )
            result = "Segment length dimension placed"
        else:
            intersection = self._inclusive_intersection(first, second)
            first_dx = first["p2"][0] - first["p1"][0]
            first_dy = first["p2"][1] - first["p1"][1]
            second_dx = second["p2"][0] - second["p1"][0]
            second_dy = second["p2"][1] - second["p1"][1]
            cross = first_dx * second_dy - first_dy * second_dx
            if intersection is not None and abs(cross) > 1e-9:
                v._append_dimension(
                    {
                        "type": "angle",
                        "points": [
                            self._away_from(first, intersection),
                            intersection,
                            self._away_from(second, intersection),
                        ],
                        "p1": self._away_from(first, intersection),
                        "p2": intersection,
                        "p3": self._away_from(second, intersection),
                        "offset": 0.0,
                        "precision": 1,
                        "driving": {
                            "kind": "angle",
                            "sources": [self._reference(first), self._reference(second)],
                        },
                    }
                )
                result = "Intersecting segments · angular dimension placed"
            else:
                point_a, point_b = nearest_points(
                    LineString([first["p1"], first["p2"]]),
                    LineString([second["p1"], second["p2"]]),
                )
                p1 = (float(point_a.x), float(point_a.y))
                p2 = (float(point_b.x), float(point_b.y))
                if math.dist(p1, p2) < 1e-9:
                    v._show_flash("These segments meet; choose a different pair", 1500)
                    return
                v._append_dimension(
                    {
                        "type": "spacing" if abs(cross) <= 1e-9 else "distance",
                        "p1": p1,
                        "p2": p2,
                        "offset": 0.0,
                        "precision": 2,
                        "driving": {
                            "kind": "spacing" if abs(cross) <= 1e-9 else "distance",
                            "sources": [self._reference(first), self._reference(second)],
                        },
                    }
                )
                result = (
                    "Parallel segments · perpendicular spacing placed"
                    if abs(cross) <= 1e-9
                    else "Separate segments · shortest distance placed"
                )
        v._dim_selected_segments.clear()
        v._dim_hover_segment = None
        v._show_flash(result, 1500)
        v._notify()
        v._redraw()

    def value(self, dimension: dict) -> float:
        if dimension.get("type") == "angle" and "p3" in dimension:
            p1, vertex, p3 = dimension["p1"], dimension["p2"], dimension["p3"]
            return angle_between_rays(vertex, p1, p3)
        return math.dist(dimension["p1"], dimension["p2"])

    def refresh_driving_dimension(self, dimension: dict) -> bool:
        driving = dimension.get("driving")
        if not isinstance(driving, dict):
            return False
        sources = [
            segment
            for reference in driving.get("sources", [])
            if isinstance(reference, dict)
            and (segment := self._segment_from_ref(reference)) is not None
        ]
        if driving.get("kind") == "segment_length" and len(sources) == 1:
            dimension["p1"], dimension["p2"] = sources[0]["p1"], sources[0]["p2"]
            return True
        if len(sources) != 2:
            return False
        intersection = self._inclusive_intersection(sources[0], sources[1])
        if driving.get("kind") == "angle" and intersection is not None:
            dimension["p1"] = self._away_from(sources[0], intersection)
            dimension["p2"] = intersection
            dimension["p3"] = self._away_from(sources[1], intersection)
            dimension["points"] = [dimension["p1"], dimension["p2"], dimension["p3"]]
            return True
        point_a, point_b = nearest_points(
            LineString([sources[0]["p1"], sources[0]["p2"]]),
            LineString([sources[1]["p1"], sources[1]["p2"]]),
        )
        dimension["p1"] = (float(point_a.x), float(point_a.y))
        dimension["p2"] = (float(point_b.x), float(point_b.y))
        return True

    def set_value(self, index: int, target: float) -> bool:  # noqa: C901 - driving modes
        v = self.v
        if not (0 <= index < len(v._dimensions)) or not math.isfinite(target) or target <= 0:
            return False
        dimension = v._dimensions[index]
        driving = dimension.get("driving")
        if not isinstance(driving, dict):
            return False
        references = driving.get("sources", [])
        sources = [
            self._segment_from_ref(reference)
            for reference in references
            if isinstance(reference, dict)
        ]
        if any(segment is None for segment in sources):
            return False
        segments = [segment for segment in sources if segment is not None]
        kind = str(driving.get("kind", ""))
        if kind == "segment_length" and len(segments) == 1:
            segment = segments[0]
            entity = deepcopy(v._document.entity_for_id(segment["entity_id"]))
            if entity is None:
                return False
            start, end = segment["p1"], segment["p2"]
            current = math.dist(start, end)
            if current < 1e-9:
                return False
            ux, uy = (end[0] - start[0]) / current, (end[1] - start[1]) / current
            new_end = (start[0] + ux * target, start[1] + uy * target)
            end_index = segment["segment_index"] + 1
            closed = (
                len(entity.points) > 2 and math.dist(entity.points[0], entity.points[-1]) < 0.01
            )
            entity.points[end_index] = new_end
            if closed and end_index == len(entity.points) - 1:
                entity.points[0] = new_end
            if entity.kind == "line" and len(entity.points) == 2:
                entity.meta = {"start": entity.points[0], "end": entity.points[1]}
            else:
                entity.kind = "polyline"
                entity.meta = None
            result = v._canvas_service.update_entities([entity])
        elif kind in {"spacing", "distance"} and len(segments) == 2:
            current = self.value(dimension)
            if current < 1e-9:
                return False
            dx = (dimension["p2"][0] - dimension["p1"][0]) / current * (target - current)
            dy = (dimension["p2"][1] - dimension["p1"][1]) / current * (target - current)
            entity = deepcopy(v._document.entity_for_id(segments[1]["entity_id"]))
            if entity is None:
                return False
            if segments[0]["entity_id"] == segments[1]["entity_id"]:
                # Opposite edges of one profile are a shape-size dimension.
                # Translating the whole entity moves both references equally
                # and cannot change their spacing, so move only the selected
                # second edge (and therefore its two adjoining corners).
                segment_index = int(segments[1]["segment_index"])
                endpoint_indices = (segment_index, segment_index + 1)
                if endpoint_indices[1] >= len(entity.points):
                    return False
                was_closed = (
                    len(entity.points) > 2 and math.dist(entity.points[0], entity.points[-1]) < 0.01
                )
                for endpoint_index in endpoint_indices:
                    x, y = entity.points[endpoint_index]
                    entity.points[endpoint_index] = (x + dx, y + dy)
                if was_closed:
                    if 0 in endpoint_indices:
                        entity.points[-1] = entity.points[0]
                    elif len(entity.points) - 1 in endpoint_indices:
                        entity.points[0] = entity.points[-1]
                # A local edge move generally cannot be represented by the
                # original rectangle/circle procedural metadata. Keep the
                # edited outline canonical so a later redraw cannot restore
                # the old dimensions from stale parameters.
                entity.kind = "polyline"
                entity.meta = None
            else:
                entity.points = translate(entity.points, dx, dy)
                transform_entity_metadata(
                    entity, transform="translate", center=(0.0, 0.0), dx=dx, dy=dy
                )
            result = v._canvas_service.update_entities([entity])
        elif kind == "angle" and len(segments) == 2:
            vertex = dimension["p2"]
            first_ray = dimension["p1"]
            second_ray = dimension["p3"]
            first_angle = math.atan2(first_ray[1] - vertex[1], first_ray[0] - vertex[0])
            second_angle = math.atan2(second_ray[1] - vertex[1], second_ray[0] - vertex[0])
            signed_current = math.degrees(
                (second_angle - first_angle + math.pi) % math.tau - math.pi
            )
            # The label displays the minor angle as a positive value. Preserve
            # which side of the first ray the second ray occupies so editing a
            # clockwise angle does not unexpectedly flip it to the supplement.
            desired_signed = math.copysign(target, signed_current or 1.0)
            delta = desired_signed - signed_current
            entity = deepcopy(v._document.entity_for_id(segments[1]["entity_id"]))
            if entity is None:
                return False
            if segments[0]["entity_id"] == segments[1]["entity_id"]:
                # Rotating the whole polyline rotates both measured rays and
                # leaves their angle unchanged. On an open connected sketch,
                # rotate the complete branch beyond the second ray so later
                # segment lengths and angles remain rigid. A closed profile has
                # no free branch, so only its second-ray corner can move and
                # every other dimension is refreshed below to show the result.
                segment = segments[1]
                endpoints = (segment["segment_index"], segment["segment_index"] + 1)
                shared = min(endpoints, key=lambda i: math.dist(entity.points[i], vertex))
                if math.dist(entity.points[shared], vertex) > 1e-6:
                    return False
                moving = endpoints[1] if shared == endpoints[0] else endpoints[0]
                closed = (
                    len(entity.points) > 2 and math.dist(entity.points[0], entity.points[-1]) < 0.01
                )
                if not closed and shared == endpoints[0]:
                    moving_indices = list(range(moving, len(entity.points)))
                elif not closed and shared == endpoints[1]:
                    moving_indices = list(range(0, moving + 1))
                else:
                    moving_indices = [moving]
                for point_index in moving_indices:
                    entity.points[point_index] = rotate(
                        [entity.points[point_index]], vertex, delta
                    )[0]
                if closed:
                    if moving == 0:
                        entity.points[-1] = entity.points[0]
                    elif moving == len(entity.points) - 1:
                        entity.points[0] = entity.points[-1]
                entity.kind = "polyline"
                entity.meta = None
            else:
                entity.points = rotate(entity.points, vertex, delta)
                transform_entity_metadata(
                    entity, transform="rotate", center=vertex, angle_degrees=delta
                )
            result = v._canvas_service.update_entities([entity])
        else:
            return False
        if not result.changed:
            return False
        driving["target"] = float(target)
        # Constraints run as part of the geometry-change boundary. They must
        # settle before annotation endpoints are refreshed; the reverse order
        # allowed a fixed 10 mm line to display a stale edited value of 20 mm.
        v._fire_poly_change()
        # One geometry edit can affect several annotations that share an edge
        # or vertex. Refresh all of them in the same frame; otherwise the other
        # angle badges continue displaying stale values until another edit.
        v._refresh_driving_dimensions()
        v._redraw()
        # Driving edits are committed from a focused HUD field. Repaint now so
        # the arc, rays, and badge cannot remain on the pre-edit frame until a
        # later mouse event happens to invalidate the canvas.
        v.repaint()
        v._notify()
        v._show_flash(f"Driving dimension updated to {target:g}", 1500)
        return True

    def press(self, event: QMouseEvent) -> bool:  # noqa: C901 - staged CAD input
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())

        if (
            v._dimension_kind == "angle"
            and v._dim_pending_p1 is not None
            and v._dim_pending_p2 is not None
        ):
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap_result = v._resolve_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
                reference_point=v._dim_pending_p2,
            )
            if snap_result is not None:
                wx, wy = snap_result[0], snap_result[1]
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                wx, wy = v._angle_snap(*v._dim_pending_p2, wx, wy)
            first_ray = math.dist(v._dim_pending_p1, v._dim_pending_p2)
            second_ray = math.dist(v._dim_pending_p2, (wx, wy))
            if first_ray < 1e-6 or second_ray < 1e-6:
                v._show_flash("Angular dimension rays must have a visible length", 1700)
                return True
            v._append_dimension(
                {
                    "type": "angle",
                    "points": [v._dim_pending_p1, v._dim_pending_p2, (wx, wy)],
                    "p1": v._dim_pending_p1,
                    "p2": v._dim_pending_p2,
                    "p3": (wx, wy),
                    "offset": 0.0,
                    "precision": 1,
                }
            )
            v._dim_pending_p1 = None
            v._dim_pending_p2 = None
            v._notify()
            v._redraw()
            return True

        # A parametric circle is unambiguous, so one click creates a diameter
        # annotation without requiring endpoint placement. Prefer a circle
        # whose radius matches the click precisely (within 8px), falling
        # back to whatever entity is under the cursor.
        if v._dim_pending_p1 is None:
            entity_id = next(
                (
                    candidate.id
                    for candidate in v._entities
                    if candidate.kind == "circle"
                    and isinstance(candidate.meta, dict)
                    and "center" in candidate.meta
                    and "radius" in candidate.meta
                    and abs(
                        math.hypot(
                            pos.x() - v._w2c(*candidate.meta["center"])[0],
                            pos.y() - v._w2c(*candidate.meta["center"])[1],
                        )
                        - abs(float(candidate.meta["radius"]) * v._scale)
                    )
                    <= 8.0
                ),
                v._find_poly_at(pos.x(), pos.y()),
            )
            if entity_id is not None:
                entity = v._document.entity_for_id(entity_id)
                if entity is None:
                    return True
                metadata = entity.meta if isinstance(entity.meta, dict) else {}
                if entity.kind == "circle" and "center" in metadata and "radius" in metadata:
                    cx, cy = metadata["center"]
                    radius = float(metadata["radius"])
                    if radius > 0:
                        radial_x, radial_y = wx - float(cx), wy - float(cy)
                        radial_length = math.hypot(radial_x, radial_y)
                        if radial_length < 1e-9:
                            radial_x, radial_y, radial_length = 1.0, 0.0, 1.0
                        ux, uy = radial_x / radial_length, radial_y / radial_length
                        v._append_dimension(
                            {
                                "type": "diameter",
                                "p1": (float(cx) - ux * radius, float(cy) - uy * radius),
                                "p2": (float(cx) + ux * radius, float(cy) + uy * radius),
                                "offset": 0.0,
                                "precision": 2,
                            }
                        )
                        v._notify()
                        v._redraw()
                        return True

        # Smart linear workflow: explicit vertex clicks retain point-to-point
        # dimensions, while clicking an edge selects its exact segment.
        if v._dimension_kind == "linear" and v._dim_pending_p1 is None:
            vertex_hit = v._find_nearest_vertex(pos.x(), pos.y())
            segment = self._segment_at(pos.x(), pos.y())
            if segment is not None and (v._dim_selected_segments or vertex_hit is None):
                if not v._dim_selected_segments:
                    v._dim_selected_segments.append(segment)
                    v._dim_hover_segment = segment
                    v._show_flash(
                        "Segment selected · choose another segment, or click it again for length",
                        1800,
                    )
                    v._redraw()
                else:
                    self._finish_segment_dimension(segment)
                return True

        if v._dim_pending_p1 is not None and v._dim_pending_p2 is not None:
            pending = {
                "p1": v._dim_pending_p1,
                "p2": v._dim_pending_p2,
                "offset": v._dim_pending_offset,
            }
            v._append_dimension(
                {
                    "p1": v._dim_pending_p1,
                    "p2": v._dim_pending_p2,
                    "offset": v._dimension_offset_at(pending, wx, wy),
                    "precision": 2,
                }
            )
            v._dim_pending_p1 = None
            v._dim_pending_p2 = None
            v._notify()
            v._redraw()
            return True

        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._dim_pending_p1,
        )
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift and v._dim_pending_p1 is not None:
            wx, wy = v._angle_snap(*v._dim_pending_p1, wx, wy)

        if v._dim_pending_p1 is None:
            v._dim_pending_p1 = (wx, wy)
        else:
            if math.dist(v._dim_pending_p1, (wx, wy)) < 1e-6:
                v._show_flash("Dimension points must be different · pick another point", 1700)
                return True
            v._dim_pending_p2 = (wx, wy)
            v._dim_pending_offset = 5.0
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        v._dim_hover_segment = (
            self._segment_at(pos.x(), pos.y()) if v._dimension_kind == "linear" else None
        )
        if (
            v._dimension_kind == "linear"
            and v._dim_pending_p1 is not None
            and v._dim_pending_p2 is not None
        ):
            # Third stage: live-preview the offset as the cursor moves.
            pending = {
                "p1": v._dim_pending_p1,
                "p2": v._dim_pending_p2,
                "offset": v._dim_pending_offset,
            }
            v._dim_pending_offset = v._dimension_offset_at(pending, wx, wy)
            v._redraw()
            return True

        # Placing p1 or p2: resolve + preview the same snap press() will
        # commit, so the rubber-band line (and the snap-ring indicator)
        # show exactly where the click will land — matching ScaleTool.
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._dim_pending_p1,
        )
        if snap_result is not None:
            mx, my = snap_result[0], snap_result[1]
            v._hover_snap = (mx, my)
            v._hover_snap_type = snap_result[2]
        else:
            mx, my = wx, wy
            v._hover_snap = None
            v._hover_snap_type = None
        if (
            bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and v._dim_pending_p1 is not None
        ):
            mx, my = v._angle_snap(*v._dim_pending_p1, mx, my)
        v._cursor_wx, v._cursor_wy = mx, my
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True

    def paint_overlay(self, painter: QPainter) -> None:
        v = self.v
        segments = list(v._dim_selected_segments)
        hover = v._dim_hover_segment
        if hover is not None and not any(item["key"] == hover["key"] for item in segments):
            segments.append(hover)
        for segment in segments:
            selected = any(item["key"] == segment["key"] for item in v._dim_selected_segments)
            color = QColor("#f5a623") if selected else QColor("#a371f7")
            width = 4.0 if selected else 2.5
            painter.setPen(QPen(color, width))
            painter.drawLine(QPointF(*v._w2c(*segment["p1"])), QPointF(*v._w2c(*segment["p2"])))
