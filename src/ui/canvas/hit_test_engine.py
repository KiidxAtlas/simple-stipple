"""HitTestEngine — geometry hit testing for polylines."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF


class HitTestEngine:
    """Stateless hit-testing operations for polyline rendering."""

    VERT_HIT = 6.0
    EDGE_HIT = 8.0

    @staticmethod
    def segment_intersection_point(
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> tuple[float, float] | None:
        """Return proper segment intersection point."""
        x1, y1 = a1
        x2, y2 = a2
        x3, y3 = b1
        x4, y4 = b2
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-9:
            return None

        det_a = x1 * y2 - y1 * x2
        det_b = x3 * y4 - y3 * x4
        px = (det_a * (x3 - x4) - (x1 - x2) * det_b) / denom
        py = (det_a * (y3 - y4) - (y1 - y2) * det_b) / denom

        def _within(p: float, a: float, b: float) -> bool:
            return min(a, b) - 1e-6 <= p <= max(a, b) + 1e-6

        if not (
            _within(px, x1, x2)
            and _within(py, y1, y2)
            and _within(px, x3, x4)
            and _within(py, y3, y4)
        ):
            return None

        for ex, ey in (a1, a2, b1, b2):
            if math.hypot(px - ex, py - ey) < 1e-6:
                return None

        return (px, py)

    @staticmethod
    def poly_closed_n(poly: list[tuple[float, float]]) -> int:
        """Return segment count: n if closed, n-1 if open."""
        n = len(poly)
        if n < 2:
            return 0
        if n >= 3 and math.hypot(
            poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]
        ) < 0.01:
            return n
        return n - 1

    @staticmethod
    def poly_bounds(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
        if not poly:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = zip(*poly)
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def poly_rect_for_culling(
        poly: list[tuple[float, float]], *, epsilon: float = 1e-6
    ) -> QRectF:
        """Return a non-degenerate world rect for robust viewport culling."""
        if not poly:
            return QRectF(QPointF(0.0, 0.0), QPointF(epsilon, epsilon))
        x0, y0, x1, y1 = HitTestEngine.poly_bounds(poly)
        if abs(x1 - x0) < epsilon:
            x0 -= epsilon
            x1 += epsilon
        if abs(y1 - y0) < epsilon:
            y0 -= epsilon
            y1 += epsilon
        return QRectF(QPointF(x0, y0), QPointF(x1, y1))

    def closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        w2c: callable[[float, float], tuple[float, float]],
        return_segment: bool = False,
    ):
        """Compute closest point on every segment of poly to world point."""
        best_dist = float("inf")
        best: tuple[int, tuple[float, float]] | int | None = None
        n = len(poly)
        if n < 2:
            return (None, (None, None, None)) if return_segment else None

        # We need poly_closed_n from the static method
        seg_count = self._poly_closed_n(poly)

        for vi in range(seg_count):
            ax, ay = poly[vi]
            bx, by = poly[(vi + 1) % n]
            dx, dy = bx - ax, by - ay
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-12:
                scx, scy = w2c(ax, ay)
                d = math.hypot(cx - scx, cy - scy)
            else:
                t = max(0.0, min(1.0, ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq))
                px, py_ = ax + t * dx, ay + t * dy
                scx, scy = w2c(px, py_)
                d = math.hypot(cx - scx, cy - scy)
            if d < best_dist:
                best_dist = d
                px_py = (ax + t * dx, ay + t * dy) if seg_len_sq >= 1e-12 else (ax, ay)
                if return_segment:
                    best = (vi, px_py)
                else:
                    best = vi
        if return_segment and best is not None:
            return best_dist, best
        return (best_dist if best is not None else None, None)

    def find_nearest_vertex(
        self, cx: float, cy: float, polys: list, hidden_polys: set[int], w2c: callable[[float, float], tuple[float, float]]
    ) -> tuple[int, int] | None:
        best_dist = self.VERT_HIT
        best = None
        for pi, poly in enumerate(polys):
            if pi in hidden_polys:
                continue
            for vi, pt in enumerate(poly):
                sx, sy = w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best = (pi, vi)
        return best

    def find_nearest_endpoint(
        self, cx: float, cy: float, polys: list, hidden_polys: set[int], w2c: callable[[float, float], tuple[float, float]], snap_dist: float
    ) -> tuple[float, float] | None:
        """Find nearest start/end point within snap distance."""
        best_dist = snap_dist
        best_pt: tuple[float, float] | None = None
        for pi, poly in enumerate(polys):
            if pi in hidden_polys:
                continue
            if len(poly) < 2:
                continue
            for pt in (poly[0], poly[-1]):
                sx, sy = w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
        return best_pt

    def find_nearest_edge(
        self, cx: float, cy: float, polys: list, hidden_polys: set[int], w2c: callable[[float, float], tuple[float, float]], edge_hit: float
    ) -> tuple[int, int, tuple[float, float]] | None:
        best_dist = edge_hit
        best: tuple[int, int, tuple[float, float]] | None = None
        for pi, poly in enumerate(polys):
            if pi in hidden_polys:
                continue
            dist, result = self.closest_point_on_poly(
                poly, 0.0, 0.0, cx, cy, w2c, return_segment=True
            )
            if dist is not None and dist < best_dist and result is not None:
                best_dist = dist
                seg_idx, closest_pt = result
                best = (pi, seg_idx, closest_pt)
        return best

    def find_poly_at(
        self, cx: float, cy: float, polys: list, hidden_polys: set[int], w2c: callable[[float, float], tuple[float, float]]
    ) -> int | None:
        best_dist = 8.0
        best = None
        for pi, poly in enumerate(polys):
            if pi in hidden_polys:
                continue
            dist = self.closest_point_on_poly(poly, 0.0, 0.0, cx, cy, w2c)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    def find_ghost_poly_at(
        self, cx: float, cy: float, ghost_polys: list, ghost_visible: bool, w2c: callable[[float, float], tuple[float, float]]
    ) -> int | None:
        if not ghost_polys or not ghost_visible:
            return None
        best_dist = 8.0
        for pi, poly in enumerate(ghost_polys):
            dist = self.closest_point_on_poly(poly, 0.0, 0.0, cx, cy, w2c)
            if dist is not None and dist < best_dist:
                return pi
        return None
