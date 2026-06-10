"""_SnapMixin — snap/grid/angle helper methods for PolylineView."""

from __future__ import annotations

import math

from src.backend.behaviors import snapping as snap_behaviors
from src.ui.canvas.shape_snapping import ShapeSnapEngine


class _SnapMixin:
    """Mixin providing snap resolution methods for PolylineView."""

    def _shape_snap_candidate(
        self,
        cx: float,
        cy: float,
    ) -> tuple[float, float, str] | None:
        """Return nearest shape-aware snap candidate in screen snap radius."""
        best: tuple[float, float, str] | None = None
        best_dist = float("inf")

        for shape in self._shape_storage.get_all_shapes():
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = self._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= ShapeSnapEngine.SNAP_RADIUS and dist < best_dist:
                    best_dist = dist
                    best = (sx, sy, snap_type)

        return best

    def _pick_better_snap(
        self,
        cx: float,
        cy: float,
        first: tuple[float, float, str] | None,
        second: tuple[float, float, str] | None,
    ) -> tuple[float, float, str] | None:
        """Choose the closer snap point in canvas-pixel space."""
        if first is None:
            return second
        if second is None:
            return first

        fcx, fcy = self._w2c(first[0], first[1])
        scx, scy = self._w2c(second[0], second[1])
        fd = math.hypot(cx - fcx, cy - fcy)
        sd = math.hypot(cx - scx, cy - scy)
        return second if sd < fd else first

    def _snap_to_polyline(
        self,
        cx: float,
        cy: float,
        *,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return snap_behaviors.snap_to_polyline(
            cx,
            cy,
            self._polys,
            self._hidden_polys,
            self._scale,
            self._w2c,
            self._c2w,
            self._poly_bounds,
            self._is_poly_closed,
            self._segment_intersection_point,
            reference_point=reference_point,
            draw_points=self._draw_pts,
            mode=self._mode,
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
        legacy = snap_behaviors.resolve_snap(
            cx,
            cy,
            wx,
            wy,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            grid_snap_enabled=self._grid_snap,
            grid_spacing=self._grid_spacing,
            polylines=self._polys,
            hidden_polys=self._hidden_polys,
            scale=self._scale,
            w2c=self._w2c,
            c2w=self._c2w,
            poly_bounds=self._poly_bounds,
            is_poly_closed=self._is_poly_closed,
            segment_intersection_point=self._segment_intersection_point,
            mode=self._mode,
            reference_point=reference_point,
            draw_points=self._draw_pts,
        )
        shape_candidate = self._shape_snap_candidate(cx, cy)
        return self._pick_better_snap(cx, cy, legacy, shape_candidate)

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
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        legacy = snap_behaviors.resolve_drag_snap(
            cx,
            cy,
            wx,
            wy,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            allow_vertex=allow_vertex,
            grid_snap_enabled=self._grid_snap,
            grid_spacing=self._grid_spacing,
            polylines=self._polys,
            hidden_polys=self._hidden_polys,
            scale=self._scale,
            w2c=self._w2c,
            c2w=self._c2w,
            poly_bounds=self._poly_bounds,
            is_poly_closed=self._is_poly_closed,
            segment_intersection_point=self._segment_intersection_point,
            mode=self._mode,
            exclude_vertices=exclude_vertices,
            exclude_segments=exclude_segments,
            reference_point=reference_point,
            draw_points=self._draw_pts,
        )
        shape_candidate = self._shape_snap_candidate(cx, cy)
        return self._pick_better_snap(cx, cy, legacy, shape_candidate)

    def _immediate_segments_for_vertices(
        self,
        vertices: set[tuple[int, int]],
    ) -> set[tuple[int, int]]:
        """Return segment keys ``(poly_idx, seg_idx)`` touching the given vertices."""
        excluded: set[tuple[int, int]] = set()
        for pi, vi in vertices:
            if not (0 <= pi < len(self._polys)):
                continue
            poly = self._polys[pi]
            n = len(poly)
            if n < 2:
                continue
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            if seg_count <= 0:
                continue
            if closed:
                excluded.add((pi, vi % seg_count))
                excluded.add((pi, (vi - 1) % seg_count))
            else:
                if 0 <= vi < seg_count:
                    excluded.add((pi, vi))
                if 0 <= (vi - 1) < seg_count:
                    excluded.add((pi, vi - 1))
        return excluded

    def _vertices_for_polylines(self, poly_indices: set[int]) -> set[tuple[int, int]]:
        vertices: set[tuple[int, int]] = set()
        for pi in poly_indices:
            if 0 <= pi < len(self._polys):
                vertices.update((pi, vi) for vi in range(len(self._polys[pi])))
        return vertices

    def _segments_for_polylines(self, poly_indices: set[int]) -> set[tuple[int, int]]:
        segments: set[tuple[int, int]] = set()
        for pi in poly_indices:
            if not (0 <= pi < len(self._polys)):
                continue
            poly = self._polys[pi]
            n = len(poly)
            if n < 2:
                continue
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            segments.update((pi, si) for si in range(max(0, seg_count)))
        return segments

    def _angle_snap(
        self, ax: float, ay: float, wx: float, wy: float
    ) -> tuple[float, float]:
        return snap_behaviors.angle_snap(ax, ay, wx, wy)
