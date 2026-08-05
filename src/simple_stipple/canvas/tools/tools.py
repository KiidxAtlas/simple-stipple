# pyright: reportAttributeAccessIssue=false
"""Canvas interaction tools.

Each mode's mouse behavior lives in a Tool object with press/move/release/
double_click hooks operating on the shared view state (``self.v``). The
view's Qt event handlers keep only the mode-independent plumbing (panning,
right-click, gizmo/overlay priority) and dispatch to the active tool —
replacing the former 350–500-line if/elif mode ladders.

Tools are stateless strategies: all interaction state stays on the view so
undo snapshots, session persistence, and cross-tool handoffs keep working
unchanged.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.canvas.constants import DRAG_THRESH
from simple_stipple.engine.cad.editor_geometry import transform_entity_metadata
from simple_stipple.engine.cad.geometry import (
    arc_from_center_start_end,
    arc_from_three_points,
)
from simple_stipple.engine.editing.transform import rotate, translate
from simple_stipple.platform.config import DEFAULT_RADIAL_MENU_TOOLS, RADIAL_MENU_SHORT_LABELS

if TYPE_CHECKING:
    from simple_stipple.canvas.view.main import CanvasView


class CanvasTool:
    """Base tool: hooks return True when the event was fully handled."""

    def __init__(self, view: CanvasView) -> None:
        self.v = view

    def press(self, event: QMouseEvent) -> bool:
        return False

    def move(self, event: QMouseEvent) -> bool:
        return False

    def release(self, event: QMouseEvent) -> bool:
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        return False

    def key(self, event) -> bool:
        """Tool-specific key handling; runs before the command registry."""
        return False

    def paint_overlay(self, painter) -> None:
        """Draw tool-specific overlays on top of the rendered canvas."""


def _seg_hits_rect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    rx1: float,
    ry1: float,
    rx2: float,
    ry2: float,
) -> bool:
    """Liang-Barsky segment/rect intersection (canvas coordinates)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, p1[0] - rx1),
        (dx, rx2 - p1[0]),
        (-dy, p1[1] - ry1),
        (dy, ry2 - p1[1]),
    ):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
                if r < t0:
                    return False
    return True


def apply_edit_drag(v: CanvasView, event: QMouseEvent) -> bool:
    """Shared vertex-drag update used by both Edit mode and select-mode
    direct vertex editing. Returns True when a drag consumed the event."""
    if not (v._edit_dragging and v._edit_poly is not None and v._edit_vert is not None):
        return False
    pos = event.position()
    wx, wy = v._c2w(pos.x(), pos.y())
    if v._shift_drag and v._band_start:
        v._lmb_prev = pos
        v._redraw()
        return True
    allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
    edit_entity = v._document.entity_for_id(v._edit_poly) if v._edit_poly is not None else None
    if edit_entity is None:
        return True
    drag_snap_result = v._resolve_drag_snap(
        pos.x(),
        pos.y(),
        wx,
        wy,
        allow_polyline=allow_snap,
        allow_grid=allow_snap,
        exclude_vertices=v._edit_drag_targets,
        exclude_segments=v._immediate_segments_for_vertices(v._edit_drag_targets),
        exclude_polys={v._edit_poly} if v._edit_poly is not None else set(),
        reference_point=edit_entity.points[v._edit_vert],
    )
    snap_wx, snap_wy = wx, wy
    snap_type = ""
    if drag_snap_result is not None:
        snap_wx, snap_wy, snap_type = drag_snap_result

    anchor_for_constraint = v._edit_drag_anchor

    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        anchor_pt = anchor_for_constraint
        if anchor_pt is not None:
            dx = snap_wx - anchor_pt[0]
            dy = snap_wy - anchor_pt[1]
            if abs(dx) >= abs(dy):
                snap_wy = anchor_pt[1]
                snap_type = "horizontal"
            else:
                snap_wx = anchor_pt[0]
                snap_type = "vertical"

    cur_pt = edit_entity.points[v._edit_vert]
    if abs(cur_pt[0] - snap_wx) > 1e-9 or abs(cur_pt[1] - snap_wy) > 1e-9:
        if not v._edit_undo_pushed:
            v._edit_command_snapshot = v._canvas_service.begin_preview()
            v._edit_undo_pushed = True
        v._edit_drag_moved = True

    v._apply_edit_vertex_position(snap_wx, snap_wy)
    v._cursor_wx, v._cursor_wy = snap_wx, snap_wy
    if snap_type:
        v._hover_snap = (snap_wx, snap_wy)
        v._hover_snap_type = snap_type
    v._redraw()
    return True


def release_edit_drag(v: CanvasView) -> None:
    """Finish a vertex drag (Edit mode or select-mode direct editing)."""
    v._edit_dragging = False
    v._edit_linked_verts = set()
    v._edit_drag_targets = set()
    v._edit_drag_anchor = None
    v._redraw()
    v._notify()
    if v._edit_drag_moved:
        v._canvas_service.commit_preview(v._edit_command_snapshot)
        v._fire_poly_change()
    v._edit_drag_moved = False
    v._edit_undo_pushed = False
    v._edit_command_snapshot = None


def start_bezier_handle_drag(v: CanvasView, hit: tuple[str, int, str]) -> bool:
    entity_id, _anchor_index, _side = hit
    entity = v._document.entity_for_id(entity_id)
    if entity is None or entity.locked:
        return False
    v._bezier_handle_drag = (entity_id, _anchor_index, _side)
    v._bezier_handle_drag_moved = False
    v._bezier_handle_undo_pushed = False
    v._redraw()
    return True


def apply_bezier_handle_drag(v: CanvasView, event: QMouseEvent) -> bool:
    hit = v._bezier_handle_drag
    if hit is None:
        return False
    entity_id, anchor_index, side = hit
    pos = event.position()
    wx, wy = v._c2w(pos.x(), pos.y())
    entity = v._document.entity_for_id(entity_id)
    if entity is None:
        return False
    anchor = entity.points[anchor_index]
    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        wx, wy = v._angle_snap(*anchor, wx, wy)
    current = next(
        tip
        for vi, handle_side, tip in v._bezier_handles(entity_id)
        if vi == anchor_index and handle_side == side
    )
    if math.dist(current, (wx, wy)) > 1e-9:
        if not v._bezier_handle_undo_pushed:
            v._bezier_command_snapshot = v._canvas_service.begin_preview()
            v._bezier_handle_undo_pushed = True
        v._bezier_handle_drag_moved = True
        v._set_bezier_handle(
            entity_id,
            anchor_index,
            side,
            (wx, wy),
            break_pair=bool(event.modifiers() & Qt.KeyboardModifier.AltModifier),
        )
    v._redraw()
    return True


def release_bezier_handle_drag(v: CanvasView) -> None:
    moved = v._bezier_handle_drag_moved
    v._bezier_handle_drag = None
    v._bezier_handle_drag_moved = False
    v._bezier_handle_undo_pushed = False
    v._redraw()
    v._notify()
    if moved:
        v._canvas_service.commit_preview(v._bezier_command_snapshot)
        v._fire_poly_change()
    v._bezier_command_snapshot = None


class ScaleTool(CanvasTool):
    """Two-click reference distance used to scale selected geometry."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._measure_locked:
            # Start a fresh reference after the previous scale is locked.
            v._measure_locked = False
            v._measure_anchor = None
            v._measure_hover = None
            v._measure_end = None
            v._measure_snapped_a = False
            v._measure_snapped_b = False
            v._dismiss_measure_edit()
            v._redraw()
            return True
        wx, wy = v._c2w(pos.x(), pos.y())

        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            reference_point=v._measure_anchor,
        )
        snapped = snap_result is not None
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        # Angle snap with Shift
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift and v._measure_anchor is not None:
            wx, wy = v._angle_snap(*v._measure_anchor, wx, wy)
        if v._measure_anchor is None:
            v._measure_anchor = (wx, wy)
            v._measure_hover = (wx, wy)
            v._measure_snapped_a = snapped
        else:
            if math.hypot(wx - v._measure_anchor[0], wy - v._measure_anchor[1]) < 1e-6:
                v._show_flash(
                    "Reference length must be greater than zero · pick another point", 1800
                )
                v._measure_hover = (wx, wy)
                v._redraw()
                return True
            v._measure_end = (wx, wy)
            v._measure_hover = (wx, wy)
            v._measure_snapped_b = snapped
            v._measure_locked = True
            v._show_measure_edit()
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._measure_locked:
            return True
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
        )
        if v._measure_anchor is None:
            # Pre-first-click: just track snap indicator
            v._measure_hover_pre = (snap_result[0], snap_result[1]) if snap_result else None
            if snap_result is not None:
                v._cursor_wx, v._cursor_wy = snap_result[0], snap_result[1]
                v._hover_snap = (snap_result[0], snap_result[1])
                v._hover_snap_type = snap_result[2]
            v._redraw()
            return True
        # After anchor placed — compute hover with snap + optional angle snap
        if snap_result is not None:
            mx, my = snap_result[0], snap_result[1]
            v._hover_snap = (mx, my)
            v._hover_snap_type = snap_result[2]
        else:
            mx, my = wx, wy
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            mx, my = v._angle_snap(*v._measure_anchor, mx, my)
        v._measure_hover = (mx, my)
        v._cursor_wx, v._cursor_wy = mx, my
        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        return True


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
            first = math.atan2(p1[1] - vertex[1], p1[0] - vertex[0])
            second = math.atan2(p3[1] - vertex[1], p3[0] - vertex[0])
            return abs(math.degrees((second - first + math.pi) % math.tau - math.pi))
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

    def set_value(self, index: int, target: float) -> bool:
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

    def press(self, event: QMouseEvent) -> bool:
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
        hit = v._find_nearest_vertex_by_id(pos.x(), pos.y())

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
        v._hover_vert = v._find_nearest_vertex_by_id(pos.x(), pos.y())
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
                v._closest_point_on_poly(
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


class DrawTool(CanvasTool):
    """Point placement for polylines, primitives, arcs, splines, text, and
    the bezier pen — bezier is a ``_draw_primitive`` like any other, not a
    separate mode, so switching to/from it doesn't hide the draw sidebar or
    require its own mode-transition bookkeeping."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            return self._bezier_press(event)
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())

        if v._draw_snap is not None:
            wx, wy = v._draw_snap

        if v._draw_primitive == "text":
            # Click chooses the anchor; the dialog does the rest.
            v.prompt_add_text(wx, wy)
            v.set_mode("select")
            return True

        if v._draw_primitive in {
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
        } or v._draw_primitive in getattr(v, "_PROCEDURAL_QUICK_SHAPES", set()):
            if not v._draw_shape_preview_active:
                v._draw_shape_preview_active = True
                v._draw_shape_anchor_w = (wx, wy)
                v._draw_shape_cursor_w = (wx, wy)
                if v._draw_shape_w_edit is not None:
                    v._draw_shape_w_edit.setFocus()
                    v._draw_shape_w_edit.selectAll()
            else:
                v._draw_shape_cursor_w = (wx, wy)
                v._commit_shape_preview()
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        if v._draw_primitive == "line":
            if not v._draw_pts:
                v._draw_pts = [(wx, wy)]
                v._draw_point_snap_types = [v._draw_snap_type or None]
                v._refresh_draw_sidebar_state()
                v._redraw()
                return True
            p0 = v._draw_pts[0]
            v._draw_pts = [p0, (wx, wy)]
            first_snap = v._draw_point_snap_types[0] if v._draw_point_snap_types else None
            v._draw_point_snap_types = [first_snap, v._draw_snap_type or None]
            v._finish_draw(close=False)
            v._draw_pts.clear()
            v._draw_point_snap_types.clear()
            v._refresh_draw_sidebar_state()
            return True

        if v._draw_primitive == "arc":
            v._draw_arc_pts.append((wx, wy))
            if len(v._draw_arc_pts) >= 3:
                p0, p1, p2 = v._draw_arc_pts[:3]
                if v._draw_arc_mode == "center-start-end":
                    arc_poly = arc_from_center_start_end(p0, p1, p2, 24)
                else:
                    arc_poly = arc_from_three_points(p0, p1, p2, 24)
                v._commit_drawn_polyline(
                    arc_poly,
                    primitive="arc",
                    created_flash="Arc created",
                )
                v._draw_arc_pts.clear()
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        if v._draw_primitive == "spline":
            if v._is_near_start() and len(v._draw_pts) >= 3:
                v._finish_draw(close=True)
                return True
            v._draw_pts.append((wx, wy))
            v._draw_point_snap_types.append(v._draw_snap_type or None)
            # A spline point controls a curve; it is not the end of a
            # dimensionable line segment.
            v._dismiss_dim_inputs()
            v._snap_engine.clear_relationship_reference()
            v._dim_distance_dirty = False
            v._dim_angle_dirty = False
            v._refresh_draw_sidebar_state()
            v._redraw()
            return True

        # B. If dim inputs have user-typed values, compute point from those
        if v._draw_pts and (v._dim_distance_dirty or v._dim_angle_dirty):
            v._apply_dim_input()
            # Show dim inputs again for the next segment
            v._show_dim_inputs()
            return True
        # Apply H/V constraint to the placed point
        if v._draw_constraint == "H" and v._draw_pts:
            wy = v._draw_pts[-1][1]
        elif v._draw_constraint == "V" and v._draw_pts:
            wx = v._draw_pts[-1][0]
        # Close polygon when clicking near start point
        if v._is_near_start():
            v._finish_draw(close=True)
            return True
        # Connect to existing polyline endpoint when starting a new draw
        if not v._draw_pts:
            endpoint_snap = v._find_nearest_endpoint(pos.x(), pos.y())
            if endpoint_snap is not None:
                wx, wy = endpoint_snap
                v._draw_snap_type = "vertex"
        v._draw_pts.append((wx, wy))
        v._draw_point_snap_types.append(v._draw_snap_type or None)
        # A click commits the relationship for this segment. The next segment
        # starts a fresh acquisition instead of inheriting a stale source/type
        # lock from the segment that just finished.
        v._snap_engine.clear_relationship_reference()
        # B. Show dim inputs after first point is placed
        if len(v._draw_pts) == 1:
            v._show_dim_inputs()
        # Reset dirty flags for the new segment
        v._dim_distance_dirty = False
        v._dim_angle_dirty = False
        v._refresh_draw_sidebar_state()
        v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            return self._bezier_move(event)
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._draw_shape_preview_active:
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap_result = v._resolve_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
            )
            if snap_result is not None:
                wx, wy = snap_result[0], snap_result[1]
                v._draw_snap = (wx, wy)
                v._draw_snap_type = snap_result[2]
            else:
                v._draw_snap = None
                v._draw_snap_type = None
            v._draw_shape_cursor_w = (wx, wy)
            v._cursor_wx = wx
            v._cursor_wy = wy
            v._update_shape_size_fields_from_preview()
            v._redraw()
            return True
        # 1. Resolve snap target
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        spline_mode = v._draw_primitive == "spline"
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            # Keep explicit point/grid snapping for spline controls, but do
            # not apply line-only inference such as parallel or extension.
            reference_point=v._draw_pts[-1] if v._draw_pts and not spline_mode else None,
            allow_inferred=not spline_mode,
        )
        if snap_result is not None:
            v._draw_snap = (snap_result[0], snap_result[1])
            v._draw_snap_type = snap_result[2]
        else:
            v._draw_snap = None
            v._draw_snap_type = None

        # 2. Determine effective position (snap or raw cursor)
        eff_x = v._draw_snap[0] if v._draw_snap else wx
        eff_y = v._draw_snap[1] if v._draw_snap else wy

        if spline_mode:
            v._snap_engine.clear_relationship_reference()
            v._draw_constraint = None
            v._angle_snap_active = False
            v._cursor_wx = eff_x
            v._cursor_wy = eff_y
            v._dismiss_dim_inputs()
            v._redraw()
            return True

        # 3. Angle snap with Shift
        shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift_held and v._draw_pts:
            anchor = v._draw_pts[-1]
            eff_x, eff_y = v._angle_snap(anchor[0], anchor[1], eff_x, eff_y)
            v._draw_snap = (eff_x, eff_y)
            v._angle_snap_active = True
        else:
            v._angle_snap_active = False

        # 4. Explicit draw constraint locks, then fallback auto-detection.
        #    A user-typed angle takes highest precedence: the segment locks
        #    to that ray with live feedback, and the pointer sets the length.
        v._draw_constraint = None
        typed_angle = v._typed_draw_angle()
        if v._draw_pts and typed_angle is not None:
            last_wx, last_wy = v._draw_pts[-1]
            ar = math.radians(typed_angle)
            dirx, diry = math.cos(ar), math.sin(ar)
            typed_dist = v._typed_draw_distance()
            if typed_dist is not None:
                length = typed_dist
            else:
                proj = (eff_x - last_wx) * dirx + (eff_y - last_wy) * diry
                length = max(0.0, proj)
            eff_x = last_wx + dirx * length
            eff_y = last_wy + diry * length
            v._draw_snap = (eff_x, eff_y)
            v._angle_snap_active = True
            v._draw_constraint = f"∠{typed_angle:g}°"
        elif v._draw_pts:
            last_wx, last_wy = v._draw_pts[-1]
            if v._draw_constraint_lock == "H":
                v._draw_constraint = "H"
                eff_y = last_wy
                if v._draw_snap is not None:
                    v._draw_snap = (v._draw_snap[0], last_wy)
            elif v._draw_constraint_lock == "V":
                v._draw_constraint = "V"
                eff_x = last_wx
                if v._draw_snap is not None:
                    v._draw_snap = (last_wx, v._draw_snap[1])
            elif v._draw_constraint_lock == "45":
                v._draw_constraint = "45"
                eff_x, eff_y = v._angle_snap(last_wx, last_wy, eff_x, eff_y)
                v._draw_snap = (eff_x, eff_y)
                v._angle_snap_active = True
            else:
                seg_dx = eff_x - last_wx
                seg_dy = eff_y - last_wy
                seg_dist = math.hypot(seg_dx, seg_dy)
                if seg_dist > 1e-9:
                    seg_angle = math.degrees(math.atan2(seg_dy, seg_dx)) % 360
                    if seg_angle < 3 or seg_angle > 357 or (177 < seg_angle < 183):
                        v._draw_constraint = "H"
                        eff_y = last_wy
                        if v._draw_snap is not None:
                            v._draw_snap = (v._draw_snap[0], last_wy)
                    elif 87 < seg_angle < 93 or 267 < seg_angle < 273:
                        v._draw_constraint = "V"
                        eff_x = last_wx
                        if v._draw_snap is not None:
                            v._draw_snap = (last_wx, v._draw_snap[1])

        # 5. Update cursor to final effective position (all modifications applied)
        v._cursor_wx = eff_x
        v._cursor_wy = eff_y

        # 6. Update dimension HUD position and values
        if v._draw_pts:
            last_wx, last_wy = v._draw_pts[-1]
            eff_wx = v._cursor_wx if v._cursor_wx is not None else wx
            eff_wy = v._cursor_wy if v._cursor_wy is not None else wy
            seg_dist = math.hypot(eff_wx - last_wx, eff_wy - last_wy)
            seg_angle = math.degrees(math.atan2(eff_wy - last_wy, eff_wx - last_wx))
            cur_cx, cur_cy = v._w2c(eff_wx, eff_wy)
            v._update_dim_positions(cur_cx, cur_cy)
            v._update_dim_values(seg_dist, seg_angle)

        v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        if self.v._draw_primitive == "bezier":
            return self._bezier_release(event)
        return True

    def double_click(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._draw_primitive == "bezier":
            v._finish_pen()
            return True
        # Double-click finishes and closes the polygon (Fusion 360 behavior)
        if len(v._draw_pts) >= 3:
            v._finish_draw(close=True)
        else:
            v._finish_draw()
        return True

    def key(self, event) -> bool:
        if self.v._draw_primitive != "bezier":
            return False
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.v._finish_pen()
            return True
        return False

    def paint_overlay(self, painter) -> None:
        if self.v._draw_primitive == "bezier":
            self._paint_bezier_overlay(painter)

    # ── Bezier pen: click places a corner anchor (straight segments in/out);
    # click-and-drag places a smooth anchor with a symmetric tangent handle
    # sized by the drag vector. Enter/double-click finalizes the curve as a
    # ``kind="bezier"`` entity; Escape (via the shared draw-mode Escape
    # handler) discards the in-progress curve. ──────────────────────────────

    def _bezier_press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        snap_result = v._resolve_snap(
            pos.x(),
            pos.y(),
            wx,
            wy,
            allow_polyline=allow_snap,
            allow_grid=allow_snap,
            allow_inferred=False,
        )
        if snap_result is not None:
            wx, wy = snap_result[0], snap_result[1]
        if len(v._pen_pts) >= 3:
            start_cx, start_cy = v._w2c(*v._pen_pts[0])
            if math.hypot(pos.x() - start_cx, pos.y() - start_cy) <= 10.0:
                v._finish_pen(close=True)
                return True
        v._pen_pts.append((wx, wy))
        v._pen_tangents.append((0.0, 0.0))
        v._pen_dragging = True
        v._pen_press_screen = (pos.x(), pos.y())
        v._redraw()
        return True

    def _bezier_move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        wx, wy = v._c2w(pos.x(), pos.y())
        if v._pen_dragging and v._pen_pts:
            anchor = v._pen_pts[-1]
            v._pen_tangents[-1] = (wx - anchor[0], wy - anchor[1])
        v._cursor_wx, v._cursor_wy = wx, wy
        v._redraw()
        return True

    def _bezier_release(self, event: QMouseEvent) -> bool:
        v = self.v
        if v._pen_dragging and v._pen_press_screen is not None and v._pen_tangents:
            pos = event.position()
            dx = pos.x() - v._pen_press_screen[0]
            dy = pos.y() - v._pen_press_screen[1]
            if math.hypot(dx, dy) < DRAG_THRESH:
                # No real drag happened: a plain click makes a corner anchor.
                v._pen_tangents[-1] = (0.0, 0.0)
        v._pen_dragging = False
        v._pen_press_screen = None
        v._redraw()
        return True

    def _paint_bezier_overlay(self, painter) -> None:
        from simple_stipple.engine.cad.geometry import build_bezier_poly

        v = self.v
        pts = v._pen_pts
        if not pts:
            return
        pen_color = QColor("#2f81f7")
        painter.setPen(QPen(pen_color, 1.6))
        if len(pts) >= 2:
            preview = build_bezier_poly(pts, v._pen_tangents, segments=64)
            path_pts = [v._w2c(*p) for p in preview]
            for a, b in zip(path_pts, path_pts[1:]):
                painter.drawLine(QPointF(*a), QPointF(*b))
        # Rubber-band segment from the last anchor to the live cursor.
        if v._cursor_wx is not None and v._cursor_wy is not None:
            last = v._w2c(*pts[-1])
            cur = v._w2c(v._cursor_wx, v._cursor_wy)
            painter.setPen(QPen(pen_color, 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(*last), QPointF(*cur))
        # Anchors + tangent handles.
        for i, (ax, ay) in enumerate(pts):
            sx, sy = v._w2c(ax, ay)
            painter.setPen(QPen(pen_color, 1.4))
            painter.setBrush(QColor("#0d1117"))
            painter.drawEllipse(QPointF(sx, sy), 3.5, 3.5)
            tx, ty = v._pen_tangents[i]
            if abs(tx) > 1e-9 or abs(ty) > 1e-9:
                hx, hy = v._w2c(ax + tx, ay + ty)
                hx2, hy2 = v._w2c(ax - tx, ay - ty)
                painter.setPen(QPen(QColor("#56d4dd"), 1.0))
                painter.drawLine(QPointF(hx2, hy2), QPointF(hx, hy))
                painter.setBrush(QColor("#56d4dd"))
                painter.drawEllipse(QPointF(hx, hy), 2.5, 2.5)
                painter.drawEllipse(QPointF(hx2, hy2), 2.5, 2.5)


class TrimExtendTool(CanvasTool):
    """Trim (click the portion to remove) / Extend (click near an open
    end). Both cut/extend to the nearest intersection with other shapes."""

    def press(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._mode == "trim":
            v.trim_at(pos.x(), pos.y())
        else:
            v.extend_at(pos.x(), pos.y())
        return True

    def move(self, event: QMouseEvent) -> bool:
        v = self.v
        pos = event.position()
        if v._mode == "trim":
            v.preview_trim_at(pos.x(), pos.y())
        else:
            v.preview_extend_at(pos.x(), pos.y())
        hover = v._find_poly_at(pos.x(), pos.y())
        if hover != v._hover_poly:
            v._hover_poly = hover
            v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
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
            and len(v._find_polys_at(pos.x(), pos.y())) > 1
        ):
            # Alt-click is reserved for cycling overlapping geometry. Let the
            # selection tool see it instead of an existing selection gizmo.
            return False
        handle_hit = v._find_bezier_handle(pos.x(), pos.y())
        if handle_hit is not None and start_bezier_handle_drag(v, handle_hit):
            return True
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
        candidates = v._find_polys_at(pos.x(), pos.y())
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
        was_selected_before = target in v._sel if target is not None else False
        v._lmb_target = target

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
            gid = v._group_of(target)
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
            hit = v._find_nearest_vertex_by_id(pos.x(), pos.y())
            target_kind = target_entity.kind
            # Parametric shapes never vertex-drag in select mode: every rim
            # point of a circle/ellipse is a "vertex", which made plain
            # drag-to-move nearly impossible. Resize via the frame handles
            # or the properties panel; vertex editing lives in Edit mode.
            skip_vertex_drag = not v._selection_drag_edits or (
                hit is not None
                and hit[0] == target
                and was_selected_before
                and target_kind in {"arc", "circle", "ellipse", "rectangle", "polygon", "slot"}
            )
            if hit is not None and not skip_vertex_drag:
                eid, vi = hit
                entity = v._document.entity_for_id(eid)
                if entity is None:
                    return True
                if vi < 0 or vi >= len(entity.points):
                    return True
                v._edit_poly = eid
                v._edit_vert = vi
                v._edit_dragging = True
                v._edit_drag_moved = False
                v._edit_undo_pushed = False
                v._edit_drag_anchor = entity.points[vi]
                v._edit_selected_verts = {hit}
                v._edit_drag_targets = v._linked_vertices_by_id(eid, vi)
                v._edit_linked_verts = set(v._edit_drag_targets)
                v._redraw()
                return True
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

        if v._sel:
            old_hover = v._hover_vert
            hit = v._find_nearest_vertex_by_id(pos.x(), pos.y())
            if hit is not None and hit[0] in v._sel:
                v._hover_vert = hit
            else:
                v._hover_vert = None
            if v._hover_vert != old_hover:
                v._update_cursor()
                v._redraw()
                return True
        elif v._hover_vert is not None:
            v._hover_vert = None
            v._update_cursor()

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
                        adj = v._object_snap_adjust(raw_dx, raw_dy)
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
        hover = v._find_poly_at(pos.x(), pos.y()) if v._selectable else None
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
        hit_id = v._find_poly_at(pos.x(), pos.y())
        if hit_id is not None:
            entity = v._document.entity_for_id(hit_id)
            if entity is None:
                return True
            if v.text_params_at(entity.id) is not None:
                v.prompt_edit_text(entity.id)
                return True
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift:
                connected_ids = v._connected_entities(hit_id)
                v._sel = connected_ids
                v._show_flash(f"Object selected ({len(v._sel)})", 800)
            else:
                v._sel = {entity.id}
            v._redraw()
            v._notify()
        elif v._entities:
            profile = v._find_profile_at(pos.x(), pos.y())
            if profile:
                v._sel = profile
                v._show_flash(f"Selected enclosed profile · {len(v._sel)} edge(s)", 1200)
                v._redraw()
                v._notify()
            else:
                # Double-click outside a profile keeps the familiar fit shortcut.
                v.fit()
        return True


class KnifeTool(CanvasTool):
    """Split every crossed shape with a temporary two-point line."""

    def press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position()
        self.v._knife_start_w = self.v._c2w(pos.x(), pos.y())
        self.v._knife_end_w = self.v._knife_start_w
        self.v._redraw()
        return True

    def move(self, event: QMouseEvent) -> bool:
        if self.v._knife_start_w is None:
            return False
        pos = event.position()
        self.v._knife_end_w = self.v._c2w(pos.x(), pos.y())
        self.v._redraw()
        return True

    def release(self, event: QMouseEvent) -> bool:
        start = self.v._knife_start_w
        if start is None:
            return False
        pos = event.position()
        end = self.v._c2w(pos.x(), pos.y())
        self.v._knife_start_w = None
        self.v._knife_end_w = None
        self.v.knife_cut(start, end)
        self.v.set_mode("select")
        return True


class RadialMenuService:
    """Own radial-menu state, hit-testing, dispatch, and painting."""

    def __init__(self, host) -> None:
        self._host = host

    def _toggle_radial_menu(self) -> None:
        if self._host._radial_active:
            self._host._radial_active = False
            self._host._radial_hover_index = None
            self._host._redraw()
            return
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            cx, cy = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
        else:
            cx, cy = self._host.width() / 2.0, self._host.height() / 2.0
        self._host._radial_center_c = self._clamped_radial_center(cx, cy)
        self._host._radial_active = True
        self._host._radial_hover_index = None
        self._host._redraw()

    # A quick-launcher wheel: every wedge is a real canvas Command id (draw
    # primitives, edit/selection ops, booleans, view/grid toggles, ...) so
    # the available pool is exactly "everything commands.py knows how to
    # run" — no separate/parallel action list to keep in sync. Which ones
    # appear, and in what order, is user-customizable — see
    # set_radial_menu_tools() — so the wedge count/angle is computed from
    # len(self._host._radial_tools), not a fixed number.
    _RADIAL_OUTER = 104.0
    _RADIAL_INNER = 36.0
    _RADIAL_MIN_TOOLS = 3
    _RADIAL_MAX_TOOLS = 12

    @classmethod
    def _radial_geometry(cls, n: int) -> tuple[float, float]:
        """(outer, inner) radii — grows past 6 wedges so more items still
        leave each label enough room; shared by hit-testing and painting so
        the two can never disagree about where a wedge actually is."""
        grow = max(0, n - 6)
        return cls._RADIAL_OUTER + grow * 9.0, cls._RADIAL_INNER + grow * 2.0

    def _clamped_radial_center(self, cx: float, cy: float) -> QPoint:
        """Keep the cursor-launched wheel reachable at every canvas edge."""
        outer, _inner = self._radial_geometry(len(self._host._radial_tools))
        margin = int(math.ceil(outer + 4.0))

        def _axis(value: float, extent: int) -> int:
            if extent <= margin * 2:
                return extent // 2
            return max(margin, min(int(value), extent - margin))

        return QPoint(_axis(cx, self._host.width()), _axis(cy, self._host.height()))

    def set_radial_menu_tools(self, tools: list[str] | None) -> None:
        """Set which commands appear as radial-menu wedges, and in what order.

        Unknown/hidden ids are dropped and duplicates collapsed (first
        occurrence wins); if fewer than _RADIAL_MIN_TOOLS survive, falls back
        to the default set entirely rather than showing a degenerate menu.
        """
        valid = {c.id for c in canvas_commands.COMMANDS if not c.hidden}
        seen: set[str] = set()
        cleaned: list[str] = []
        for tool_id in tools or []:
            if tool_id in valid and tool_id not in seen:
                seen.add(tool_id)
                cleaned.append(tool_id)
        if len(cleaned) < self._RADIAL_MIN_TOOLS:
            cleaned = list(DEFAULT_RADIAL_MENU_TOOLS)
        self._host._radial_tools = cleaned[: self._RADIAL_MAX_TOOLS]
        if self._host._radial_active:
            self._host._radial_hover_index = None
            self._host._redraw()

    def _radial_index_at(self, x: float, y: float) -> int | None:
        n = len(self._host._radial_tools)
        if n == 0:
            return None
        outer, inner = self._radial_geometry(n)
        dx = x - self._host._radial_center_c.x()
        dy = y - self._host._radial_center_c.y()
        r = math.hypot(dx, dy)
        if r < inner - 4.0 or r > outer + 18.0:
            return None
        slice_deg = 360.0 / n
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        return int((angle + slice_deg / 2.0) // slice_deg) % n

    def _execute_radial_action(self, idx: int) -> None:
        if not (0 <= idx < len(self._host._radial_tools)):
            return
        canvas_commands.run(self._host, self._host._radial_tools[idx])

    def _draw_radial_icon(
        self,
        painter: QPainter,
        cmd_id: str,
        cx: float,
        cy: float,
        size: float,
        color: QColor,
        label: str = "",
    ) -> None:
        painter.save()
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = size / 2.0

        def _two_circles(mode: str) -> None:
            ra = half * 0.62
            ax, bx = cx - half * 0.32, cx + half * 0.32
            path_a, path_b = QPainterPath(), QPainterPath()
            path_a.addEllipse(QPointF(ax, cy), ra, ra)
            path_b.addEllipse(QPointF(bx, cy), ra, ra)
            if mode == "union":
                painter.fillPath(path_a.united(path_b), color)
            elif mode == "subtract":
                painter.fillPath(path_a.subtracted(path_b), color)
                painter.drawPath(path_b)
            elif mode == "intersect":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.fillPath(path_a.intersected(path_b), color)
            elif mode == "divide":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.drawLine(QPointF(cx, cy - ra), QPointF(cx, cy + ra))

        if cmd_id in ("canvas.rectangle",):
            painter.drawRoundedRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7), 2.0, 2.0)
        elif cmd_id == "canvas.circle":
            painter.drawEllipse(QPointF(cx, cy), half, half)
        elif cmd_id == "canvas.polygon":
            pts = [
                QPointF(
                    cx + math.cos(math.radians(60 * k - 90)) * half,
                    cy + math.sin(math.radians(60 * k - 90)) * half,
                )
                for k in range(6)
            ]
            painter.drawPolygon(QPolygonF(pts))
        elif cmd_id == "canvas.line":
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.6), QPointF(cx + half, cy - half * 0.6)
            )
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.6), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.6), 1.4, 1.4)
        elif cmd_id == "canvas.arc":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, 90 * 16)
        elif cmd_id == "canvas.ellipse":
            painter.drawEllipse(QRectF(cx - half, cy - half * 0.6, size, size * 0.6))
        elif cmd_id == "canvas.polyline":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.lineTo(cx - half * 0.15, cy - half * 0.6)
            path.lineTo(cx + half, cy + half * 0.2)
            painter.drawPath(path)
            painter.setBrush(color)
            for px, py in (
                (cx - half, cy + half * 0.5),
                (cx - half * 0.15, cy - half * 0.6),
                (cx + half, cy + half * 0.2),
            ):
                painter.drawEllipse(QPointF(px, py), 1.3, 1.3)
        elif cmd_id == "canvas.spline":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.3, cy - half, cx + half * 0.3, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "mode.pen":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.cubicTo(
                cx - half * 0.2, cy - half, cx + half * 0.2, cy + half, cx + half, cy - half * 0.5
            )
            painter.drawPath(path)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.5), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.5), 1.4, 1.4)
        elif cmd_id == "mode.draw":
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half * 0.4, cy - half))
            tip = QPolygonF(
                [
                    QPointF(cx + half * 0.4, cy - half),
                    QPointF(cx + half, cy - half * 0.7),
                    QPointF(cx + half * 0.7, cy - half * 0.1),
                ]
            )
            painter.setBrush(color)
            painter.drawPolygon(tip)
        elif cmd_id == "mode.edit":
            painter.drawRect(QRectF(cx - half, cy - half, size, size))
            painter.setBrush(color)
            for corner in (
                (cx - half, cy - half),
                (cx + half, cy - half),
                (cx - half, cy + half),
                (cx + half, cy + half),
            ):
                painter.drawRect(QRectF(corner[0] - 1.6, corner[1] - 1.6, 3.2, 3.2))
        elif cmd_id == "edit.undo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 30 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx - half * 0.75, cy - half * 0.55, 200, color)
        elif cmd_id == "edit.redo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 250 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx + half * 0.75, cy - half * 0.55, -20, color)
        elif cmd_id == "clipboard.cut":
            painter.drawLine(
                QPointF(cx - half, cy - half), QPointF(cx + half * 0.2, cy + half * 0.3)
            )
            painter.drawLine(
                QPointF(cx - half, cy + half), QPointF(cx + half * 0.2, cy - half * 0.3)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx - half, cy - half), 2.2, 2.2)
            painter.drawEllipse(QPointF(cx - half, cy + half), 2.2, 2.2)
            painter.drawLine(QPointF(cx + half * 0.2, cy), QPointF(cx + half, cy))
        elif cmd_id == "clipboard.copy":
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.75, size * 0.75, size * 0.75), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.25, cy - half * 0.15, size * 0.75, size * 0.75), 2.0, 2.0
            )
        elif cmd_id == "clipboard.paste":
            painter.drawRoundedRect(
                QRectF(cx - half * 0.7, cy - half * 0.8, size * 0.7, size), 1.5, 1.5
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.3, cy - half, size * 0.3, size * 0.25), 1.0, 1.0
            )
        elif cmd_id in ("edit.duplicate", "edit.duplicate_offset"):
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.2, size * 0.65, size * 0.65), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.35, cy - half, size * 0.65, size * 0.65), 2.0, 2.0
            )
            if cmd_id == "edit.duplicate_offset":
                self._draw_arrowhead(painter, cx + half * 0.55, cy - half * 0.55, -45, color)
        elif cmd_id == "edit.array_grid":
            for dx_ in (-half * 0.55, half * 0.55):
                for dy_ in (-half * 0.55, half * 0.55):
                    painter.drawRect(QRectF(cx + dx_ - 3.0, cy + dy_ - 3.0, 6.0, 6.0))
        elif cmd_id == "edit.array_radial":
            painter.setBrush(color)
            for k in range(5):
                a = math.radians(72 * k - 90)
                painter.drawEllipse(
                    QPointF(cx + math.cos(a) * half * 0.75, cy + math.sin(a) * half * 0.75),
                    2.0,
                    2.0,
                )
        elif cmd_id == "edit.delete":
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half, cy - half))
        elif cmd_id == "select.all":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
        elif cmd_id == "select.none":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
            painter.setPen(QPen(color, 1.4))
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.7), QPointF(cx + half, cy - half * 0.7)
            )
        elif cmd_id == "select.invert":
            painter.drawRect(QRectF(cx - half, cy - half * 0.5, size * 0.45, size * 0.9))
            painter.setBrush(color)
            painter.drawRect(QRectF(cx + half * 0.1, cy - half * 0.5, size * 0.45, size * 0.9))
        elif cmd_id in ("group.create", "group.dissolve"):
            gap = 3.0 if cmd_id == "group.dissolve" else 0.0
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx + gap, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
        elif cmd_id in ("path.close", "path.open"):
            span = 260 * 16 if cmd_id == "path.open" else 350 * 16
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, span)
            if cmd_id == "path.close":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx + half, cy), 1.6, 1.6)
        elif cmd_id == "path.offset":
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 2.0, 2.0)
            painter.drawRoundedRect(
                QRectF(cx - half * 0.55, cy - half * 0.55, size * 0.55, size * 0.55), 1.5, 1.5
            )
        elif cmd_id == "construction.toggle":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.5), QPointF(cx + half, cy - half * 0.5)
            )
        elif cmd_id in ("vertex.round", "vertex.chamfer"):
            path = QPainterPath()
            path.moveTo(cx - half, cy - half * 0.6)
            if cmd_id == "vertex.round":
                path.lineTo(cx - half * 0.35, cy - half * 0.6)
                path.quadTo(cx + half, cy - half * 0.6, cx + half, cy + half)
            else:
                path.lineTo(cx + half * 0.3, cy - half * 0.6)
                path.lineTo(cx + half, cy + half * 0.15)
                path.lineTo(cx + half, cy + half)
            painter.drawPath(path)
        elif cmd_id in ("text.add", "text.attach_to_path"):
            font = painter.font()
            font.setPointSizeF(size * 0.62)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, "A"
            )
            if cmd_id == "text.attach_to_path":
                painter.drawArc(QRectF(cx - half, cy + half * 0.3, size, size), 200 * 16, 140 * 16)
        elif cmd_id == "path.simplify":
            for k in range(4):
                a = math.radians(90 * k)
                painter.drawLine(
                    QPointF(cx + math.cos(a) * half * 0.4, cy + math.sin(a) * half * 0.4),
                    QPointF(cx + math.cos(a) * half, cy + math.sin(a) * half),
                )
        elif cmd_id == "path.smooth":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.5, cy - half, cx - half * 0.15, cy + half, cx, cy)
            path.cubicTo(cx + half * 0.15, cy - half, cx + half * 0.5, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "path.fit_curve":
            # Rough/dense original points (dots on a jagged path)...
            jagged = [
                (cx - half, cy + half * 0.3),
                (cx - half * 0.45, cy - half * 0.5),
                (cx + half * 0.1, cy + half * 0.6),
                (cx + half * 0.55, cy - half * 0.4),
                (cx + half, cy + half * 0.1),
            ]
            painter.setBrush(color)
            for px, py in jagged:
                painter.drawEllipse(QPointF(px, py), 1.1, 1.1)
            # ...replaced by one smooth fitted curve through the same span.
            path = QPainterPath()
            path.moveTo(jagged[0][0], jagged[0][1])
            path.cubicTo(
                cx - half * 0.3,
                cy - half * 0.7,
                cx + half * 0.3,
                cy + half * 0.7,
                jagged[-1][0],
                jagged[-1][1],
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif cmd_id == "boolean.union":
            _two_circles("union")
        elif cmd_id == "boolean.subtract":
            _two_circles("subtract")
        elif cmd_id == "boolean.intersect":
            _two_circles("intersect")
        elif cmd_id == "boolean.divide":
            _two_circles("divide")
        elif cmd_id == "mode.trim":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx - 3.0, cy))
            painter.drawLine(QPointF(cx + 3.0, cy), QPointF(cx + half, cy))
            painter.drawLine(QPointF(cx - 3.0, cy - 3.0), QPointF(cx + 3.0, cy + 3.0))
            painter.drawLine(QPointF(cx - 3.0, cy + 3.0), QPointF(cx + 3.0, cy - 3.0))
        elif cmd_id == "mode.extend":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half * 0.4, cy))
            self._draw_arrowhead(painter, cx + half * 0.75, cy, 0, color)
        elif cmd_id == "measure.toggle":
            painter.drawRect(QRectF(cx - half, cy - half * 0.45, size, size * 0.45))
            for k in range(1, 4):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(QPointF(x, cy - half * 0.45), QPointF(x, cy - half * 0.1))
        elif cmd_id == "mode.dimension":
            painter.drawLine(
                QPointF(cx - half, cy - half * 0.6), QPointF(cx - half, cy + half * 0.6)
            )
            painter.drawLine(
                QPointF(cx + half, cy - half * 0.6), QPointF(cx + half, cy + half * 0.6)
            )
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            self._draw_arrowhead(painter, cx - half, cy, 0, color, size=3.0)
            self._draw_arrowhead(painter, cx + half, cy, 180, color, size=3.0)
        elif cmd_id == "view.fit":
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                painter.drawLine(
                    QPointF(cx + sx * half * 0.25, cy + sy * half * 0.25),
                    QPointF(cx + sx * half, cy + sy * half),
                )
        elif cmd_id in ("view.zoom_in", "view.zoom_out"):
            painter.drawEllipse(
                QPointF(cx - half * 0.15, cy - half * 0.15), half * 0.55, half * 0.55
            )
            painter.drawLine(
                QPointF(cx + half * 0.25, cy + half * 0.25), QPointF(cx + half, cy + half)
            )
            r = half * 0.55 * 0.5
            painter.drawLine(
                QPointF(cx - half * 0.15 - r, cy - half * 0.15),
                QPointF(cx - half * 0.15 + r, cy - half * 0.15),
            )
            if cmd_id == "view.zoom_in":
                painter.drawLine(
                    QPointF(cx - half * 0.15, cy - half * 0.15 - r),
                    QPointF(cx - half * 0.15, cy - half * 0.15 + r),
                )
        elif cmd_id == "view.rulers":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            for k in range(5):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(
                    QPointF(x, cy), QPointF(x, cy - (half * 0.5 if k % 2 == 0 else half * 0.25))
                )
        elif cmd_id in ("grid.toggle", "grid.snap", "grid.coarser", "grid.finer"):
            step = size / (2.0 if cmd_id == "grid.coarser" else 4.0)
            x = cx - half
            while x <= cx + half + 0.01:
                painter.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))
                x += step
            y = cy - half
            while y <= cy + half + 0.01:
                painter.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
                y += step
            if cmd_id == "grid.snap":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        else:
            # Generic fallback: a rounded badge with the label's initials,
            # so every pool entry still gets *some* recognizable glyph.
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 3.0, 3.0)
            initials = "".join(w[0] for w in label.split()[:2]).upper() or "?"
            font = painter.font()
            font.setPointSizeF(size * 0.44)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, initials
            )
        painter.restore()

    @staticmethod
    def _radial_chord_half(ty: float, cy: float, outer: float) -> float:
        """Half-width of the disc's horizontal chord at label height ``ty`` —
        the widest a label can ever be at that height without spilling past
        the wheel's outer edge, regardless of angle or word length."""
        dy_from_center = ty - cy
        return math.sqrt(max(0.0, outer * outer - dy_from_center * dy_from_center))

    @staticmethod
    def _draw_arrowhead(
        painter: QPainter, x: float, y: float, angle_deg: float, color: QColor, size: float = 3.5
    ) -> None:
        a = math.radians(angle_deg)
        tip = QPointF(x, y)
        back = QPointF(x - math.cos(a) * size * 1.6, y - math.sin(a) * size * 1.6)
        perp = a + math.pi / 2.0
        p1 = QPointF(back.x() + math.cos(perp) * size * 0.6, back.y() + math.sin(perp) * size * 0.6)
        p2 = QPointF(back.x() - math.cos(perp) * size * 0.6, back.y() - math.sin(perp) * size * 0.6)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, p1, p2]))

    def _paint_radial_menu(self, painter: QPainter) -> None:
        tools = self._host._radial_tools
        n = len(tools)
        if n == 0:
            return
        slice_deg = 360.0 / n
        painter.save()
        cx = float(self._host._radial_center_c.x())
        cy = float(self._host._radial_center_c.y())
        outer, inner = self._radial_geometry(n)
        hover = self._host._radial_hover_index
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Soft drop shadow behind the disc.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawEllipse(QRectF(cx - outer + 2.0, cy - outer + 4.0, outer * 2, outer * 2))

        # Base disc.
        painter.setBrush(QColor(19, 23, 33, 235))
        painter.setPen(QPen(QColor("#2f81f7"), 1.4))
        painter.drawEllipse(QRectF(cx - outer, cy - outer, outer * 2, outer * 2))

        if hover is not None:
            # Highlight the wedge under the cursor — a filled pie slice
            # from center to the rim; the hub fill drawn right after
            # punches the middle back out, leaving a ring highlight
            # matching the actual clickable annulus (_radial_index_at).
            rect = QRectF(cx - outer, cy - outer, outer * 2, outer * 2)
            start_deg = hover * slice_deg - slice_deg / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(47, 129, 247, 110))
            painter.drawPie(rect, int(round(start_deg * 16)), int(round(slice_deg * 16)))

        # Thin spokes marking the wedge boundaries.
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        for i in range(n):
            ang = math.radians(i * slice_deg + slice_deg / 2.0)
            painter.drawLine(
                QPointF(cx + math.cos(ang) * inner, cy - math.sin(ang) * inner),
                QPointF(cx + math.cos(ang) * outer, cy - math.sin(ang) * outer),
            )

        # Center hub.
        painter.setBrush(QColor(12, 16, 24, 245))
        painter.setPen(QPen(QColor("#30363d"), 1.2))
        painter.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        painter.setPen(QColor("#8b949e"))
        painter.drawText(
            QRectF(cx - inner, cy - inner, inner * 2, inner * 2),
            Qt.AlignmentFlag.AlignCenter,
            "Q",
        )

        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_pad = 6.0
        for i, tool in enumerate(tools):
            label = RADIAL_MENU_SHORT_LABELS.get(tool) or canvas_commands.get(tool).label
            ang = math.radians(i * slice_deg)
            active = i == hover
            color = QColor("#ffffff") if active else QColor("#c9d1d9")
            icon_r = outer * 0.53
            label_r = outer * 0.77
            ix = cx + math.cos(ang) * icon_r
            iy = cy - math.sin(ang) * icon_r
            ty = cy - math.sin(ang) * label_r
            self._draw_radial_icon(painter, tool, ix, iy, 15.0, color, label=label)
            painter.setPen(color)

            # Hard cap on label width: the horizontal chord of the disc at
            # this label's height, so a long label can never spill past the
            # circle's edge regardless of angle or word length. Computed
            # *before* eliding (not as a post-hoc position clamp) so a too-
            # long label gets shorter rather than sliding back to overlap
            # its own icon.
            chord_half = self._radial_chord_half(ty, cy, outer)
            text_y = ty + fm.ascent() / 2.0
            cos_a = math.cos(ang)
            # Only truly-horizontal wedges need the icon-dodging side anchor
            # below — anywhere else, icon and label already sit at different
            # enough heights (icon_r vs label_r along the same spoke) that
            # centering doesn't collide, and gets a much bigger width budget.
            if cos_a > 0.97:
                # Due east: label reads outward from the icon, not centered
                # over it — a wide word like "Rectangle" would otherwise
                # overlap the icon since both sit on the same horizontal line.
                text_x = ix + 16.0
                max_w = max(20.0, (cx + chord_half - label_pad) - text_x)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
            elif cos_a < -0.97:
                # Due west: right-anchor so the label ends just before the icon.
                max_w = max(20.0, (ix - 16.0) - (cx - chord_half + label_pad))
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = ix - 16.0 - fm.horizontalAdvance(elided)
            else:
                max_w = max(20.0, chord_half * 2.0 - label_pad * 2.0)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = cx + cos_a * label_r - fm.horizontalAdvance(elided) / 2.0
            painter.drawText(QPointF(text_x, text_y), elided)
        painter.restore()
