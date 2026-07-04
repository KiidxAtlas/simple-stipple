"""Unified snap engine for the canvas.

One entry point (``query``) merges every snap source — polyline vertices,
midpoints, edges, intersections (via the pure candidate functions in
src/backend/behaviors/snapping.py), parametric-shape points (circle
centers, arc endpoints, …), the grid, and guide lines — and returns the
best candidate in screen space. Previously this logic was split across
three modules plus four glue methods on the view, and drag vs. hover
snapping threaded 13+ parameters each.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from src.backend.behaviors.snapping import angle_snap, resolve_drag_snap, resolve_snap
from src.ui.canvas.shape_snapping import ShapeSnapEngine

if TYPE_CHECKING:
    from src.ui.canvas.view import PolylineView

SnapResult = tuple[float, float, str]


class SnapEngine:
    """Snap resolution bound to one canvas view."""

    GUIDE_SNAP_PX = 8.0

    def __init__(self, view: PolylineView) -> None:
        self.v = view

    # ── Public API ────────────────────────────────────────────────────────

    def query(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        drag: bool = False,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        allow_vertex: bool = True,
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> SnapResult | None:
        v = self.v
        polylines = [e.points for e in v._entities]
        # Snapping targets include ALL entities, even those on non-active
        # layers. Users should be able to snap TO shapes on other layers
        # while still only being able to SELECT on the active layer.
        hidden_polys: set[int] = set()  # no exclusions for snapping
        if drag:
            best = resolve_drag_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                allow_vertex=allow_vertex,
                exclude_vertices=exclude_vertices,
                exclude_segments=exclude_segments,
                grid_snap_enabled=v._grid_snap,
                grid_spacing=v._grid_spacing,
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=v._segment_intersection_point,
                mode=v._mode,
                reference_point=reference_point,
                draw_points=v._draw_pts,
            )
        else:
            best = resolve_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                grid_snap_enabled=v._grid_snap,
                grid_spacing=v._grid_spacing,
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=v._segment_intersection_point,
                mode=v._mode,
                reference_point=reference_point,
                draw_points=v._draw_pts,
            )
        best = self._pick_better(cx, cy, best, self._shape_candidate(cx, cy))
        best = self._pick_better(cx, cy, best, self._guide_candidate(cx, cy, wx, wy))
        return best

    def angle(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        return angle_snap(ax, ay, wx, wy)

    # ── Candidate sources ─────────────────────────────────────────────────

    def _shape_candidate(self, cx: float, cy: float) -> SnapResult | None:
        v = self.v
        best: SnapResult | None = None
        best_dist = float("inf")
        # Shape snapping works across ALL layers — shapes on non-active
        # Shape snapping works across ALL layers — shapes on non-active
        # layers are valid snap targets even when not selectable/editable.
        for shape in v._snap_shapes():
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = v._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= ShapeSnapEngine.SNAP_RADIUS and dist < best_dist:
                    best_dist = dist
                    best = (sx, sy, snap_type)
        return best

    def _guide_candidate(
        self, cx: float, cy: float, wx: float, wy: float
    ) -> SnapResult | None:
        """Snap to user guide lines (see the rulers/guides feature)."""
        v = self.v
        guides = getattr(v, "_guides", None)
        if not guides:
            return None
        best: SnapResult | None = None
        best_dist = self.GUIDE_SNAP_PX
        for orient, coord in guides:
            if orient == "v":
                gx, _ = v._w2c(coord, wy)
                d = abs(cx - gx)
                if d < best_dist:
                    best_dist = d
                    best = (coord, wy, "guide")
            else:
                _, gy = v._w2c(wx, coord)
                d = abs(cy - gy)
                if d < best_dist:
                    best_dist = d
                    best = (wx, coord, "guide")
        return best

    def _pick_better(
        self,
        cx: float,
        cy: float,
        first: SnapResult | None,
        second: SnapResult | None,
    ) -> SnapResult | None:
        if first is None:
            return second
        if second is None:
            return first
        v = self.v
        fcx, fcy = v._w2c(first[0], first[1])
        scx, scy = v._w2c(second[0], second[1])
        fd = math.hypot(cx - fcx, cy - fcy)
        sd = math.hypot(cx - scx, cy - scy)
        return second if sd < fd else first
