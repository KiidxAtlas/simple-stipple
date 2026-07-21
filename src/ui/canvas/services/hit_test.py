"""Canvas hit-testing queries independent of widget inheritance."""

from __future__ import annotations

import math
from typing import Any, cast

from src.ui.canvas.constants import EDGE_HIT, SNAP_DIST, VERT_HIT

Point = tuple[float, float]


class HitTestService:
    def __init__(self, host) -> None:
        self._host = host

    @staticmethod
    def segment_intersection(a1: Point, a2: Point, b1: Point, b2: Point) -> Point | None:
        x1, y1 = a1
        x2, y2 = a2
        x3, y3 = b1
        x4, y4 = b2
        denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denominator) < 1e-9:
            return None
        first = x1 * y2 - y1 * x2
        second = x3 * y4 - y3 * x4
        px = (first * (x3 - x4) - (x1 - x2) * second) / denominator
        py = (first * (y3 - y4) - (y1 - y2) * second) / denominator

        def within(value: float, start: float, end: float) -> bool:
            return min(start, end) - 1e-6 <= value <= max(start, end) + 1e-6

        if not all(
            (
                within(px, x1, x2),
                within(py, y1, y2),
                within(px, x3, x4),
                within(py, y3, y4),
            )
        ):
            return None
        return (
            None
            if any(math.hypot(px - x, py - y) < 1e-6 for x, y in (a1, a2, b1, b2))
            else (px, py)
        )

    @staticmethod
    def segment_count(points: list[Point]) -> int:
        if len(points) < 2:
            return 0
        return (
            len(points)
            if len(points) >= 3 and math.dist(points[0], points[-1]) < 0.01
            else len(points) - 1
        )

    def nearest_endpoint(self, cx: float, cy: float) -> Point | None:
        host = self._host
        best_distance: float = SNAP_DIST
        best: Point | None = None
        for index, entity in enumerate(host._entities):
            if not host._entity_selectable(index) or len(entity.points) < 2:
                continue
            for point in (entity.points[0], entity.points[-1]):
                distance = math.dist((cx, cy), host._w2c(*point))
                if distance < best_distance:
                    best_distance, best = distance, point
        return best

    def nearest_vertex(self, cx: float, cy: float) -> tuple[int, int] | None:
        host = self._host
        best_distance: float = VERT_HIT
        best: tuple[int, int] | None = None
        for path_index, entity in enumerate(host._entities):
            if not host._entity_selectable(path_index):
                continue
            for vertex_index, point in enumerate(entity.points):
                distance = math.dist((cx, cy), host._w2c(*point))
                if distance < best_distance:
                    best_distance, best = distance, (path_index, vertex_index)
        return best

    def closest_point(
        self,
        points: list[Point],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: bool = False,
    ) -> float | None | tuple[float | None, tuple[int, Point] | None]:
        if len(points) < 2:
            return (None, None) if return_segment else None
        best_distance = float("inf")
        best: Any = None
        for index in range(self.segment_count(points)):
            start, end = points[index], points[(index + 1) % len(points)]
            dx, dy = end[0] - start[0], end[1] - start[1]
            length_squared = dx * dx + dy * dy
            ratio = (
                0.0
                if length_squared < 1e-12
                else max(
                    0.0, min(1.0, ((wx - start[0]) * dx + (wy - start[1]) * dy) / length_squared)
                )
            )
            point = start[0] + ratio * dx, start[1] + ratio * dy
            distance = math.dist((cx, cy), self._host._w2c(*point))
            if distance < best_distance:
                best_distance = distance
                best = (index, point) if return_segment else index
        return (
            (best_distance, best)
            if return_segment
            else (best_distance if best is not None else None)
        )

    def nearest_edge(self, cx: float, cy: float) -> tuple[int, int, Point] | None:
        host = self._host
        wx, wy = host._c2w(cx, cy)
        best_distance: float = float(EDGE_HIT)
        best: tuple[int, int, Point] | None = None
        for path_index, entity in enumerate(host._entities):
            if not host._entity_selectable(path_index):
                continue
            distance, result = cast(
                tuple[float | None, tuple[int, Point] | None],
                self.closest_point(entity.points, wx, wy, cx, cy, return_segment=True),
            )
            if distance is not None and distance < best_distance and result is not None:
                best_distance = distance
                best = path_index, result[0], result[1]
        return best

    def entity_at(self, cx: float, cy: float) -> int | None:
        host = self._host
        wx, wy = host._c2w(cx, cy)
        best_distance: float = 8.0
        best: int | None = None
        for index, entity in enumerate(host._entities):
            if not host._entity_selectable(index):
                continue
            distance = self.closest_point(entity.points, wx, wy, cx, cy)
            if isinstance(distance, float) and distance < best_distance:
                best_distance, best = distance, index
        return best

    def entities_at(self, cx: float, cy: float) -> list[int]:
        """Return every selectable entity under the pointer, nearest first."""
        host = self._host
        wx, wy = host._c2w(cx, cy)
        hits: list[tuple[float, int]] = []
        for index, entity in enumerate(host._entities):
            if not host._entity_selectable(index):
                continue
            distance = self.closest_point(entity.points, wx, wy, cx, cy)
            if isinstance(distance, float) and distance < 8.0:
                hits.append((distance, index))
        return [index for _distance, index in sorted(hits)]

    def profile_at(self, cx: float, cy: float) -> set[int]:
        """Find entities bounding the smallest enclosed profile at a point."""
        from shapely.geometry import LineString
        from shapely.geometry import Point as ShapelyPoint
        from shapely.ops import polygonize, unary_union

        host = self._host
        wx, wy = host._c2w(cx, cy)
        lines: list[tuple[int, LineString]] = []
        for index in range(len(host._entities)):
            if not host._entity_selectable(index):
                continue
            points = host._flattened_points(index)
            if len(points) >= 2:
                lines.append((index, LineString(points)))
        if not lines:
            return set()
        merged = unary_union([line for _index, line in lines])
        candidates = [
            polygon for polygon in polygonize(merged) if polygon.covers(ShapelyPoint(wx, wy))
        ]
        if not candidates:
            return set()
        profile = min(candidates, key=lambda polygon: polygon.area)
        return {index for index, line in lines if line.intersection(profile.boundary).length > 1e-7}

    def guide_at(self, cx: float, cy: float) -> int | None:
        best, best_distance = None, 6.0
        for index, (orientation, coordinate) in enumerate(self._host._guides):
            screen = (
                self._host._w2c(coordinate, 0.0)
                if orientation == "v"
                else self._host._w2c(0.0, coordinate)
            )
            distance = abs(cx - screen[0]) if orientation == "v" else abs(cy - screen[1])
            if distance < best_distance:
                best, best_distance = index, distance
        return best

    def inactive_entity_at(self, cx: float, cy: float) -> int | None:
        host = self._host
        if host._active_layer is None:
            return None
        wx, wy = host._c2w(cx, cy)
        best, best_distance = None, 8.0
        for index, entity in enumerate(host._entities):
            if entity.hidden or host._on_active_layer(entity):
                continue
            distance = self.closest_point(entity.points, wx, wy, cx, cy)
            if isinstance(distance, float) and distance < best_distance:
                best, best_distance = index, distance
        return best

    def ghost_at(self, cx: float, cy: float) -> int | None:
        host = self._host
        if not host._ghost_visible:
            return None
        wx, wy = host._c2w(cx, cy)
        best, best_distance = None, 8.0
        for index, points in enumerate(host._ghost_polys):
            distance = self.closest_point(points, wx, wy, cx, cy)
            if isinstance(distance, float) and distance < best_distance:
                best, best_distance = index, distance
        return best
