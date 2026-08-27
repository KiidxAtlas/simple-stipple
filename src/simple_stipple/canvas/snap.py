"""Unified snap engine for the canvas.

One entry point (``query``) merges every snap source — polyline vertices,
midpoints, edges, intersections (via the pure candidate functions in
src/simple_stipple/core/cad/snapping.py), parametric-shape points (circle centers, arc
endpoints, …), the grid, and guide lines — and returns the best candidate in
screen space. Previously this logic was split across three modules plus four
glue methods on the view, and drag vs. hover snapping threaded 13+ parameters
each.

``ShapeSnapEngine`` (merged in from the former ``shape_snapping.py`` — its
only consumer was this module) provides the shape-aware candidate points
(circle/arc/ellipse/spline centers and control points) that ``_shape_candidate``
below draws from.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simple_stipple.canvas.constants import MIN_SCALE as _MIN_SCALE
from simple_stipple.canvas.constants import SNAP_DIST as _DRAG_SNAP_DIST
from simple_stipple.canvas.hit_testing import HitTestService
from simple_stipple.core.cad.constants import SNAP_DIST
from simple_stipple.core.cad.primitives import SplineShape
from simple_stipple.core.cad.shapes import (
    ArcShape,
    CircleShape,
    EllipseShape,
    PolygonShape,
    RectangleShape,
    RoundedRectangleShape,
    SlotShape,
    StarShape,
)
from simple_stipple.core.cad.snapping import (
    angle_snap,
    resolve_drag_snap,
    resolve_snap,
)
from simple_stipple.core.document.geometry import entity_center

if TYPE_CHECKING:
    from simple_stipple.canvas.view.main import CanvasView
    from simple_stipple.core.cad.shape_base import Shape

SnapResult = tuple[float, float, str]
RelationshipReference = tuple[str, int, tuple[float, float], tuple[float, float]]
_ACTIVE_DRAW_REFERENCE = "__active_draw__"


class _DragSnapResolver:
    """Resolve move and resize snapping against canvas model state."""

    def __init__(self, host) -> None:
        self._host = host

    def _static_snap_geometry(
        self, *, exclude: set[str] | None = None
    ) -> tuple[
        list[tuple[float, float]],
        list[tuple[tuple[float, float], tuple[float, float]]],
        list[tuple[float, float]],
    ]:
        """Vertices, edge segments, and shape centers of every entity NOT
        excluded — the universal snap-target set for drag/resize. Centers
        use the exact meta-defined center for circle/arc/ellipse shapes
        (so an open arc's center is still a valid target, not just closed
        polygons) or the centroid for other closed polygons. Shapes on
        non-active layers are included; only the excluded (usually the
        selection being manipulated) and hidden entities are skipped.
        """
        excluded = exclude or set()
        pts: list[tuple[float, float]] = []
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        centers: list[tuple[float, float]] = []
        for e in self._host._entities:
            if e.id in excluded or e.hidden:
                continue
            poly = e.points
            pts.extend(poly)
            n = len(poly)
            closed = self._host._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for k in range(seg_count):
                segs.append((poly[k], poly[(k + 1) % n]))
            center = entity_center(e)
            if center is not None:
                centers.append(center)
        if len(segs) > 4000:
            segs = []  # keep drags/resizes responsive on huge documents
        return pts, segs, centers

    _EDGE_AXIS_EPS = 1e-6

    @classmethod
    def _edge_axis_lock(cls, ax: float, ay: float, bx: float, by: float) -> str | None:
        """Which axis of a segment's "closest point" is stable/independent
        of the other axis — i.e. safe to combine with an unrelated match's
        other-axis correction during multi-touch snapping.

        A horizontal segment's Y is constant along its whole length, so
        Y is meaningful on its own (returns "y"); a vertical segment's X is
        likewise stable (returns "x"). A DIAGONAL segment's closest point
        has both X and Y depend on the projection of the incoming (mx, my),
        so neither coordinate means anything if paired with a different Y/X
        from another match — returns None ("coupled": only usable as a full
        2D touch from this same segment, never split across two matches).
        """
        if abs(by - ay) <= cls._EDGE_AXIS_EPS:
            return "y"
        if abs(bx - ax) <= cls._EDGE_AXIS_EPS:
            return "x"
        return None

    @staticmethod
    def _nearest_snap_candidate(
        mx: float,
        my: float,
        pts: list[tuple[float, float]],
        segs: list[tuple[tuple[float, float], tuple[float, float]]],
        centers: list[tuple[float, float]],
        *,
        world_r: float,
        scale: float,
        thresh: float,
    ) -> tuple[float, tuple[float, float], str, str | None] | None:
        """Best (dist_px, pos, kind, axis_lock) among vertex/midpoint/edge/
        center candidates near (mx, my). Priority: vertex > midpoint > edge >
        center.

        ``axis_lock`` is only meaningful for kind == "edge": "x"/"y" means
        only that coordinate of the returned point is stable independent of
        the other axis (vertical/horizontal segment); None means the point
        is only valid as a full 2D touch (diagonal segment) and must not be
        split across two independently-chosen matches — see
        ``_edge_axis_lock``. All other kinds are literal fixed points, so
        their axis_lock is always None but they ARE freely decomposable
        (unlike a diagonal edge's None, which means the opposite).

        Midpoint gets a small preference window (like draw-mode snapping):
        the generic "closest point on edge" is by definition always at least
        as close as the exact midpoint, so without a bias midpoint would
        only ever win at the single infinitesimal point where they tie —
        effectively unreachable with a mouse.
        """
        best_vertex: tuple[float, tuple[float, float]] | None = None
        best_midpoint: tuple[float, tuple[float, float]] | None = None
        best_edge: tuple[float, tuple[float, float], str | None] | None = None
        best_center: tuple[float, tuple[float, float]] | None = None

        for qx, qy in pts:
            if abs(qx - mx) > world_r or abs(qy - my) > world_r:
                continue
            d = math.hypot(qx - mx, qy - my) * scale
            if d <= thresh and (best_vertex is None or d < best_vertex[0]):
                best_vertex = (d, (qx, qy))

        for (ax, ay), (bx, by) in segs:
            mxm, mym = (ax + bx) / 2.0, (ay + by) / 2.0
            if abs(mxm - mx) <= world_r and abs(mym - my) <= world_r:
                d = math.hypot(mxm - mx, mym - my) * scale
                if d <= thresh and (best_midpoint is None or d < best_midpoint[0]):
                    best_midpoint = (d, (mxm, mym))
            sdx, sdy = bx - ax, by - ay
            seg_len_sq = sdx * sdx + sdy * sdy
            if seg_len_sq < 1e-12:
                continue
            t = max(0.0, min(1.0, ((mx - ax) * sdx + (my - ay) * sdy) / seg_len_sq))
            cxp, cyp = ax + t * sdx, ay + t * sdy
            if abs(cxp - mx) > world_r or abs(cyp - my) > world_r:
                continue
            d = math.hypot(cxp - mx, cyp - my) * scale
            if d <= thresh and (best_edge is None or d < best_edge[0]):
                lock = _DragSnapResolver._edge_axis_lock(ax, ay, bx, by)
                best_edge = (d, (cxp, cyp), lock)

        for cx_, cy_ in centers:
            if abs(cx_ - mx) > world_r or abs(cy_ - my) > world_r:
                continue
            d = math.hypot(cx_ - mx, cy_ - my) * scale
            if d <= thresh and (best_center is None or d < best_center[0]):
                best_center = (d, (cx_, cy_))

        if best_vertex is not None:
            return (best_vertex[0], best_vertex[1], "vertex", None)

        others: list[tuple[float, tuple[float, float], str, str | None]] = []
        if best_edge is not None:
            others.append((best_edge[0], best_edge[1], "edge", best_edge[2]))
        if best_center is not None:
            others.append((best_center[0], best_center[1], "center", None))

        MIDPOINT_BIAS = 4.0
        if best_midpoint is not None:
            other_best = min((d for d, _, _, _ in others), default=None)
            if other_best is None or best_midpoint[0] <= other_best + MIDPOINT_BIAS:
                return (best_midpoint[0], best_midpoint[1], "midpoint", None)
            others.append((best_midpoint[0], best_midpoint[1], "midpoint", None))

        if not others:
            return None
        return min(others, key=lambda c: c[0])

    def _object_snap_adjust(
        self, dx: float, dy: float
    ) -> tuple[float, float, list[tuple[tuple[float, float], str, tuple[float, float]]]] | None:
        """Snap for a whole-selection drag, allowing MULTIPLE simultaneous
        touches — e.g. the shape's bottom can be touching one thing while
        its right side independently touches something else, and both
        should be visible, not just whichever is closest overall.

        Every candidate considered here is a genuine 2D-proximity match
        (full distance to a real vertex/midpoint/edge/center/grid-point/
        guide is within the snap threshold) — unlike the old "smart guide"
        approach, a feature can never qualify just because it happens to
        share an X or Y coordinate from far away. Among all the qualifying
        touches (one candidate per moved point), the closest X-correcting
        one and the closest Y-correcting one are applied independently —
        which is what lets two different real touches (e.g. bottom edge to
        one shape, right edge to another) both take effect at once.

        Returns (adj_dx, adj_dy, indicators) — indicators has zero, one, or
        two (target_point, kind, dragged_point) entries (deduplicated when
        the same match supplies both axes) for the caller to render.
        """
        pts = self._host._move_start_pts
        if not pts:
            return None
        scale = max(self._host._scale, _MIN_SCALE)
        thresh = _DRAG_SNAP_DIST
        world_r = thresh / scale

        static_pts, static_segs, static_centers = self._static_snap_geometry(
            exclude=self._host._sel
        )

        # Every entry is a genuinely-nearby (real 2D distance <= thresh)
        # touch: (d_screen, adj_dx, adj_dy, target_point, origin_point, kind).
        # adj_dx/adj_dy is None when that match doesn't actually constrain
        # that axis (a horizontal guide only constrains Y, for instance) —
        # using 0.0 as a placeholder there would make guides look like the
        # "best" (smallest) possible X-correction and wrongly win every time.
        #
        # A diagonal edge's "closest point" is only valid as a FULL 2D touch
        # (both dx and dy from the same match) — its X and Y both depend on
        # the projection of the incoming point, so pairing just one of its
        # coordinates with a different match's other axis lands nowhere near
        # the edge. Such matches go into `coupled_matches` instead of
        # `matches`, and are only used (as a whole) when nothing axis-safe
        # (vertex/midpoint/center/grid/guide/horizontal-or-vertical edge) was
        # found for EITHER axis.
        matches: list[
            tuple[
                float,
                float | None,
                float | None,
                tuple[float, float],
                tuple[float, float],
                str,
            ]
        ] = []
        coupled_matches: list[
            tuple[
                float,
                float,
                float,
                tuple[float, float],
                tuple[float, float],
                str,
            ]
        ] = []

        for px, py in pts:
            mx, my = px + dx, py + dy
            # NOTE: `origin` here is the point's CURRENT (raw-dragged, i.e.
            # (px, py) + dx/dy) position, NOT its drag-start position — the
            # final dragged_pt computed below is `origin + adj_dx/adj_dy`,
            # so using drag-start (px, py) instead would silently drop the
            # raw drag delta and show the indicator ring in the wrong place
            # (this was a real bug: fixed by using (mx, my) here).
            origin = (mx, my)

            candidate = self._nearest_snap_candidate(
                mx,
                my,
                static_pts,
                static_segs,
                static_centers,
                world_r=world_r,
                scale=scale,
                thresh=thresh,
            )
            if candidate is not None:
                d_screen, (qx, qy), kind, axis_lock = candidate
                if kind == "edge" and axis_lock is None:
                    coupled_matches.append(
                        (
                            d_screen,
                            qx - mx,
                            qy - my,
                            (qx, qy),
                            origin,
                            kind,
                        )
                    )
                elif kind == "edge" and axis_lock == "x":
                    matches.append((d_screen, qx - mx, None, (qx, qy), origin, kind))
                elif kind == "edge" and axis_lock == "y":
                    matches.append((d_screen, None, qy - my, (qx, qy), origin, kind))
                else:
                    matches.append((d_screen, qx - mx, qy - my, (qx, qy), origin, kind))

            if self._host._grid_snap:
                gx = round(mx / self._host._grid_spacing) * self._host._grid_spacing
                gy = round(my / self._host._grid_spacing) * self._host._grid_spacing
                d = math.hypot(gx - mx, gy - my) * scale
                if d <= thresh:
                    matches.append((d, gx - mx, gy - my, (gx, gy), origin, "grid"))

            for orient, coord in self._host._guides:
                if orient == "v":
                    d = abs(coord - mx) * scale
                    if d <= thresh:
                        matches.append(
                            (
                                d,
                                coord - mx,
                                None,
                                (coord, my),
                                origin,
                                "guide",
                            )
                        )
                else:
                    d = abs(coord - my) * scale
                    if d <= thresh:
                        matches.append(
                            (
                                d,
                                None,
                                coord - my,
                                (mx, coord),
                                origin,
                                "guide",
                            )
                        )

        if not matches and not coupled_matches:
            return None

        x_candidates = [m for m in matches if m[1] is not None]
        y_candidates = [m for m in matches if m[2] is not None]

        if not x_candidates and not y_candidates:
            if not coupled_matches:
                return None
            # Nothing axis-safe nearby at all — the only thing to snap to is
            # a diagonal edge, so use it as a single full 2D touch (both axes
            # from the same match), same as the classic single-nearest-point
            # snap. Do NOT mix it in below: it must never supply just one of
            # its two coordinates alongside an unrelated match's other axis.
            d_screen, cdx, cdy, target, origin, kind = min(coupled_matches, key=lambda m: m[0])
            dragged_pt = (origin[0] + cdx, origin[1] + cdy)
            return cdx, cdy, [(target, kind, dragged_pt)]

        best_x = min(x_candidates, key=lambda m: abs(m[1] or 0.0)) if x_candidates else None
        best_y = min(y_candidates, key=lambda m: abs(m[2] or 0.0)) if y_candidates else None
        adj_dx: float = best_x[1] if best_x is not None and best_x[1] is not None else 0.0
        adj_dy: float = best_y[2] if best_y is not None and best_y[2] is not None else 0.0

        indicators: list[tuple[tuple[float, float], str, tuple[float, float]]] = []
        seen_targets: set[tuple[float, float]] = set()
        for m in (best_x, best_y):
            if m is None:
                continue
            _, _, _, target, origin, kind = m
            if target in seen_targets:
                continue
            seen_targets.add(target)
            dragged_pt = (origin[0] + adj_dx, origin[1] + adj_dy)
            indicators.append((target, kind, dragged_pt))
        return adj_dx, adj_dy, indicators

    def _resize_handle_snap_adjust(self, wx: float, wy: float) -> tuple[float, float, str] | None:
        """Snap a dragged resize-handle position to nearby vertex/midpoint/
        edge/center of other shapes (any layer), plus grid/guides — mirrors
        the move-drag snap behavior so resizing feels consistent."""
        scale = max(self._host._scale, _MIN_SCALE)
        thresh = _DRAG_SNAP_DIST
        world_r = thresh / scale
        static_pts, static_segs, static_centers = self._static_snap_geometry(
            exclude=self._host._sel
        )
        candidate = self._nearest_snap_candidate(
            wx,
            wy,
            static_pts,
            static_segs,
            static_centers,
            world_r=world_r,
            scale=scale,
            thresh=thresh,
        )
        # A resize handle only ever moves as a single full 2D point, so the
        # edge axis-lock distinction (only relevant for splitting a match
        # across two independently-chosen touches) doesn't apply here.
        best: tuple[float, tuple[float, float], str] | None = (
            (candidate[0], candidate[1], candidate[2]) if candidate is not None else None
        )
        if self._host._grid_snap:
            gx = round(wx / self._host._grid_spacing) * self._host._grid_spacing
            gy = round(wy / self._host._grid_spacing) * self._host._grid_spacing
            d = math.hypot(gx - wx, gy - wy) * scale
            if d <= thresh and (best is None or d < best[0]):
                best = (d, (gx, gy), "grid")
        for orient, coord in self._host._guides:
            if orient == "v":
                d = abs(coord - wx) * scale
                if d <= thresh and (best is None or d < best[0]):
                    best = (d, (coord, wy), "guide")
            else:
                d = abs(coord - wy) * scale
                if d <= thresh and (best is None or d < best[0]):
                    best = (d, (wx, coord), "guide")
        if best is None:
            return None
        _, (sx, sy), kind = best
        return sx, sy, kind


class SnapEngine(_DragSnapResolver):
    """Snap resolution bound to one canvas view."""

    GUIDE_SNAP_PX = 8.0
    # Relationship snaps should be intentional, local references.  A broad
    # source search makes an unrelated segment elsewhere in the document win
    # simply because its length happens to put an equal-length endpoint under
    # the cursor.  Sources may be approached from either end of the new
    # stroke, so test both the anchor and the live pointer.
    RELATIONSHIP_REFERENCE_PX = 48.0
    # A combined relationship has one exact endpoint, but asking users to
    # land inside the generic 10px vertex radius defeats the point of an
    # intelligent constraint snap.  It gets a forgiving acquisition band
    # and a small hysteresis band after it has been acquired.
    COMBINED_RELATIONSHIP_ACQUIRE_PX = 28.0
    COMBINED_RELATIONSHIP_RETAIN_PX = 36.0
    # Equal length also resolves to one exact endpoint, so it needs the same
    # magnetic range as the combined relationship.  Directional constraints
    # resolve to a line rather than a point, and deliberately use a smaller
    # band to keep a freehand segment easy to draw.
    EQUAL_LENGTH_ACQUIRE_PX = 28.0
    EQUAL_LENGTH_RETAIN_PX = 36.0
    DIRECTIONAL_RELATIONSHIP_ACQUIRE_PX = 18.0
    DIRECTIONAL_RELATIONSHIP_RETAIN_PX = 24.0
    # Axis alignment and extensions are inferred construction lines.  Give
    # them a little more room than a precise vertex/curve target, without
    # allowing them to pull geometry from across the canvas.
    INFERRED_LINE_SNAP_PX = 18.0

    def __init__(self, view: CanvasView) -> None:
        super().__init__(view)
        self.v = view
        self.last_relationship_reference: (
            tuple[str, int, tuple[float, float], tuple[float, float]] | None
        ) = None
        self.last_relationship_type: str | None = None

    def _snap_strength(self) -> float:
        """Return the user-selected magnetic capture multiplier."""
        try:
            # A real canvas always supplies its persisted 50% default. Keep
            # the fallback at the historical full strength for lightweight
            # host adapters that do not expose snap configuration.
            value = float(getattr(self.v, "_snap_strength", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(2.0, value))

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
        allow_edge: bool = True,
        exclude_vertices: set[tuple[str, int]] | None = None,
        exclude_segments: set[tuple[str, int]] | None = None,
        exclude_polys: set[str] | None = None,
        reference_point: tuple[float, float] | None = None,
        allow_inferred: bool = True,
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_master_enabled", True):
            self.clear_relationship_reference()
            return None
        # Zero strength intentionally bypasses every snap family, including
        # grid points. The individual snap toggles stay untouched so a user
        # can temporarily draw freehand and then restore their setup.
        if self._snap_strength() <= 0.0:
            self.clear_relationship_reference()
            return None
        polylines = {e.id: e.points for e in v._entities}
        # Locked and non-active-layer entities remain useful references, but
        # explicitly hidden geometry must not create invisible snap targets.
        hidden_polys = v._flagged("hidden")
        snap_dist = SNAP_DIST * self._snap_strength()
        vertex_enabled = allow_vertex and getattr(v, "_snap_vertex_enabled", True)
        midpoint_enabled = allow_vertex and getattr(v, "_snap_midpoint_enabled", True)
        intersection_enabled = allow_vertex and getattr(v, "_snap_intersection_enabled", True)
        edge_enabled = allow_edge and getattr(v, "_snap_edge_enabled", True)
        if drag:
            best = resolve_drag_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                allow_vertex=vertex_enabled,
                allow_midpoint=midpoint_enabled,
                allow_intersection=intersection_enabled,
                allow_edge=edge_enabled,
                exclude_vertices=exclude_vertices,
                exclude_segments=exclude_segments,
                grid_snap_enabled=getattr(v, "_grid_snap", False),
                grid_spacing=getattr(v, "_grid_spacing", 1.0),
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=HitTestService.segment_intersection,
                mode=getattr(v, "_mode", None),
                reference_point=reference_point,
                draw_points=getattr(v, "_draw_pts", []),
                snap_dist=snap_dist,
            )
        else:
            best = resolve_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                allow_vertex=vertex_enabled,
                allow_midpoint=midpoint_enabled,
                allow_intersection=intersection_enabled,
                allow_edge=edge_enabled,
                grid_snap_enabled=getattr(v, "_grid_snap", False),
                grid_spacing=getattr(v, "_grid_spacing", 1.0),
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=HitTestService.segment_intersection,
                mode=getattr(v, "_mode", None),
                reference_point=reference_point,
                draw_points=getattr(v, "_draw_pts", []),
                snap_dist=snap_dist,
            )
        best = self._pick_better(cx, cy, best, self._shape_candidate(cx, cy, exclude=exclude_polys))
        # Tangent/extension are inferred edge snaps and deliberately lower
        # priority than explicit vertices, intersections, and finite edges.
        if (
            allow_polyline
            and edge_enabled
            and getattr(v, "_snap_tangent_enabled", True)
            and reference_point is not None
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._tangent_candidate(cx, cy, reference_point, exclude=exclude_polys),
            )
        # Nearest-curve is evaluated after tangent so an exact tangency keeps
        # its more informative role when both candidates are identical.
        if allow_polyline and edge_enabled:
            best = self._pick_better(
                cx, cy, best, self._curve_candidate(cx, cy, exclude=exclude_polys)
            )
        if (
            allow_polyline
            and edge_enabled
            and reference_point is not None
            and (
                getattr(v, "_snap_parallel_enabled", getattr(v, "_snap_angle_enabled", True))
                or getattr(
                    v, "_snap_perpendicular_enabled", getattr(v, "_snap_angle_enabled", True)
                )
                or getattr(v, "_snap_equal_length_enabled", True)
            )
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._relationship_candidate(
                    cx, cy, wx, wy, reference_point, exclude=exclude_polys
                ),
            )
        if (
            allow_inferred
            and allow_polyline
            and vertex_enabled
            and (
                getattr(
                    v, "_snap_align_x_enabled", getattr(v, "_snap_axis_alignment_enabled", True)
                )
                or getattr(
                    v, "_snap_align_y_enabled", getattr(v, "_snap_axis_alignment_enabled", True)
                )
            )
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._axis_alignment_candidate(
                    cx, cy, wx, wy, reference=reference_point, exclude=exclude_polys
                ),
            )
        if (
            allow_inferred
            and allow_polyline
            and edge_enabled
            and getattr(v, "_snap_extension_enabled", True)
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._extension_candidate(cx, cy, reference=reference_point, exclude=exclude_polys),
            )
        best = self._pick_better(cx, cy, best, self._guide_candidate(cx, cy, wx, wy))
        if best is None or best[2] not in {
            "parallel",
            "perpendicular",
            "equal_length",
            "parallel_equal_length",
            "perpendicular_equal_length",
        }:
            self.clear_relationship_reference()
        return best

    def _relationship_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        reference: tuple[float, float],
        *,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Infer parallel, perpendicular, and equal-length line endpoints.

        Only nearby source segments participate, and the selected source is
        retained so feedback can identify the exact referenced geometry.
        """
        # Spline controls do not form straight segments. Treating their
        # control polygon as line geometry produced false relationship hints.
        if getattr(self.v, "_draw_primitive", None) in {"spline", "bezier"}:
            self.clear_relationship_reference()
            return None
        locked_reference = self.last_relationship_reference
        locked_type = self.last_relationship_type
        ax, ay = reference
        pointer_angle = math.atan2(wy - ay, wx - ax)
        pointer_length = math.hypot(wx - ax, wy - ay)
        if pointer_length <= 1e-12:
            return None
        hidden_ids = self.v._flagged("hidden")
        reference_c = self.v._w2c(ax, ay)
        candidates: list[tuple[SnapResult, RelationshipReference]] = []
        equal_candidates: list[tuple[SnapResult, RelationshipReference]] = []
        combined_candidates: list[tuple[SnapResult, RelationshipReference]] = []
        sources = [
            (entity.id, entity.points, False)
            for entity in self.v._entities
            if entity.id not in (exclude or ())
            and entity.id not in hidden_ids
            and getattr(entity, "kind", "polyline") not in {"spline", "bezier"}
        ]
        draw_points = list(getattr(self.v, "_draw_pts", []))
        if len(draw_points) >= 2:
            # Committed segments of the in-progress polyline are always
            # relevant to the next segment, even though the unfinished shape
            # is not yet present in the document entity list.
            sources.append((_ACTIVE_DRAW_REFERENCE, draw_points, True))
        for entity_id, points, is_active_draw in sources:
            self._collect_relationship_candidates(
                entity_id,
                points,
                is_active_draw,
                cx=cx,
                cy=cy,
                reference_c=reference_c,
                reference=reference,
                pointer=(wx, wy),
                pointer_angle=pointer_angle,
                pointer_length=pointer_length,
                locked_reference=locked_reference,
                locked_type=locked_type,
                directional=candidates,
                equal=equal_candidates,
                combined=combined_candidates,
            )

        combined_radius = (
            self.COMBINED_RELATIONSHIP_RETAIN_PX
            if locked_type in {"parallel_equal_length", "perpendicular_equal_length"}
            else self.COMBINED_RELATIONSHIP_ACQUIRE_PX
        ) * self._snap_strength()
        combined = self._nearest_relationship_candidate(
            cx, cy, combined_radius, combined_candidates
        )
        if combined is not None:
            return combined
        equal_radius = (
            self.EQUAL_LENGTH_RETAIN_PX
            if locked_type == "equal_length"
            else self.EQUAL_LENGTH_ACQUIRE_PX
        ) * self._snap_strength()
        equal = self._nearest_relationship_candidate(cx, cy, equal_radius, equal_candidates)
        directional_radius = (
            self.DIRECTIONAL_RELATIONSHIP_RETAIN_PX
            if locked_type in {"parallel", "perpendicular"}
            else self.DIRECTIONAL_RELATIONSHIP_ACQUIRE_PX
        ) * self._snap_strength()
        result = (
            equal
            if equal is not None
            else self._nearest_relationship_candidate(cx, cy, directional_radius, candidates)
        )
        if result is not None:
            return result
        if locked_reference is not None:
            # The locked edge disappeared, became hidden, or ceased matching.
            # Release it and allow a nearby source to acquire in this query.
            self.clear_relationship_reference()
            return self._relationship_candidate(cx, cy, wx, wy, reference, exclude=exclude)
        self.clear_relationship_reference()
        return None

    def _collect_relationship_candidates(
        self,
        entity_id: str,
        points: list[tuple[float, float]],
        is_active_draw: bool,
        *,
        cx: float,
        cy: float,
        reference_c: tuple[float, float],
        reference: tuple[float, float],
        pointer: tuple[float, float],
        pointer_angle: float,
        pointer_length: float,
        locked_reference: RelationshipReference | None,
        locked_type: str | None,
        directional: list[tuple[SnapResult, RelationshipReference]],
        equal: list[tuple[SnapResult, RelationshipReference]],
        combined: list[tuple[SnapResult, RelationshipReference]],
    ) -> None:
        """Append every usable relationship candidate from one geometry source."""
        if len(points) < 2:
            return
        ax, ay = reference
        wx, wy = pointer
        parallel_enabled = getattr(
            self.v, "_snap_parallel_enabled", getattr(self.v, "_snap_angle_enabled", True)
        )
        perpendicular_enabled = getattr(
            self.v, "_snap_perpendicular_enabled", getattr(self.v, "_snap_angle_enabled", True)
        )
        equal_enabled = getattr(self.v, "_snap_equal_length_enabled", True)
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                continue
            start_c, end_c = self.v._w2c(*start), self.v._w2c(*end)
            if not self._relationship_source_is_eligible(
                cx,
                cy,
                reference_c,
                entity_id,
                segment_index,
                start_c,
                end_c,
                locked_reference,
                always_available=is_active_draw,
            ):
                continue
            source: RelationshipReference = (entity_id, segment_index, start, end)
            angle = math.atan2(dy, dx) % math.pi
            directions = tuple(
                direction
                for direction in (
                    (angle, "parallel") if parallel_enabled else None,
                    (angle + math.pi / 2, "perpendicular") if perpendicular_enabled else None,
                )
                if direction is not None
            )
            if directions:
                self._append_directional_candidates(
                    directional,
                    source,
                    directions,
                    ax,
                    ay,
                    pointer_angle,
                    pointer_length,
                    locked_type,
                )
            if equal_enabled and self._relationship_type_is_allowed("equal_length", locked_type):
                ux, uy = (wx - ax) / pointer_length, (wy - ay) / pointer_length
                equal.append(((ax + length * ux, ay + length * uy, "equal_length"), source))
            if directions and equal_enabled:
                self._append_combined_candidates(
                    combined,
                    source,
                    directions,
                    ax,
                    ay,
                    pointer_angle,
                    length,
                    locked_type,
                )

    def _append_directional_candidates(
        self,
        options: list[tuple[SnapResult, RelationshipReference]],
        source: RelationshipReference,
        directions: tuple[tuple[float, str], ...],
        ax: float,
        ay: float,
        pointer_angle: float,
        pointer_length: float,
        locked_type: str | None,
    ) -> None:
        for target_angle, role in directions:
            if not self._relationship_type_is_allowed(role, locked_type):
                continue
            if math.cos(pointer_angle - target_angle) < 0:
                target_angle += math.pi
            options.append(
                (
                    (
                        ax + pointer_length * math.cos(target_angle),
                        ay + pointer_length * math.sin(target_angle),
                        role,
                    ),
                    source,
                )
            )

    def _append_combined_candidates(
        self,
        options: list[tuple[SnapResult, RelationshipReference]],
        source: RelationshipReference,
        directions: tuple[tuple[float, str], ...],
        ax: float,
        ay: float,
        pointer_angle: float,
        length: float,
        locked_type: str | None,
    ) -> None:
        """Add exact angle-and-length candidates for a source segment."""
        for target_angle, role in directions:
            combined_role = f"{role}_equal_length"
            if not self._relationship_type_is_allowed(combined_role, locked_type):
                continue
            if math.cos(pointer_angle - target_angle) < 0:
                target_angle += math.pi
            options.append(
                (
                    (
                        ax + length * math.cos(target_angle),
                        ay + length * math.sin(target_angle),
                        combined_role,
                    ),
                    source,
                )
            )

    @staticmethod
    def _relationship_type_is_allowed(role: str, locked_type: str | None) -> bool:
        """Keep one acquired relationship stable without hiding a paired one."""
        if locked_type is None:
            return True
        if role == locked_type:
            return True
        if role == "equal_length":
            return locked_type in {"parallel_equal_length", "perpendicular_equal_length"}
        if role in {"parallel_equal_length", "perpendicular_equal_length"}:
            # A line already held parallel/perpendicular should promote to
            # the paired length relationship when its endpoint reaches the
            # source length. This keeps the intended direction while still
            # making the exact length easy to acquire.
            return locked_type in {role.removesuffix("_equal_length"), "equal_length"}
        return False

    def _nearest_relationship_candidate(
        self,
        cx: float,
        cy: float,
        radius: float,
        options: list[tuple[SnapResult, RelationshipReference]],
    ) -> SnapResult | None:
        best: SnapResult | None = None
        best_reference: RelationshipReference | None = None
        best_distance = radius
        for candidate, source in options:
            pcx, pcy = self.v._w2c(candidate[0], candidate[1])
            distance = math.hypot(cx - pcx, cy - pcy)
            if distance < best_distance:
                best, best_reference, best_distance = candidate, source, distance
        if best is not None:
            self.last_relationship_reference = best_reference
            self.last_relationship_type = best[2]
        return best

    def clear_relationship_reference(self) -> None:
        """Release relationship hysteresis after commit/cancel or invalidation."""
        self.last_relationship_reference = None
        self.last_relationship_type = None

    def _relationship_source_is_eligible(
        self,
        cx: float,
        cy: float,
        reference_c: tuple[float, float],
        entity_id: str,
        segment_index: int,
        start_c: tuple[float, float],
        end_c: tuple[float, float],
        locked: tuple[str, int, tuple[float, float], tuple[float, float]] | None,
        *,
        always_available: bool,
    ) -> bool:
        """Keep new relationship references local to the active stroke.

        The source can be near either the stroke anchor or its live endpoint:
        the former supports starting beside a reference and drawing away from
        it, while the latter supports approaching a reference to use it.
        """
        is_locked = locked is not None and locked[0] == entity_id and locked[1] == segment_index
        if locked is not None:
            return is_locked
        if always_available:
            return True
        return (
            min(
                self._screen_distance_to_segment(cx, cy, start_c, end_c),
                self._screen_distance_to_segment(*reference_c, start_c, end_c),
            )
            <= self.RELATIONSHIP_REFERENCE_PX
        )

    @staticmethod
    def _screen_distance_to_segment(
        px: float,
        py: float,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        """Return the shortest screen-space distance to a finite segment."""
        ax, ay = start
        bx, by = end
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _axis_alignment_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        reference: tuple[float, float] | None = None,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Align the moving endpoint's X or Y coordinate to visible endpoints."""
        hidden_ids = self.v._flagged("hidden")
        best: SnapResult | None = None
        best_distance = self.INFERRED_LINE_SNAP_PX * self._snap_strength()
        align_x_enabled = getattr(
            self.v, "_snap_align_x_enabled", getattr(self.v, "_snap_axis_alignment_enabled", True)
        )
        align_y_enabled = getattr(
            self.v, "_snap_align_y_enabled", getattr(self.v, "_snap_axis_alignment_enabled", True)
        )
        reference_c = self.v._w2c(*reference) if reference is not None else None
        for entity in self.v._entities:
            if entity.id in (exclude or ()) or entity.id in hidden_ids or not entity.points:
                continue
            # Open-path endpoints are the primary intent. Closed paths have no
            # topological endpoint, so their vertices remain regular vertex
            # snaps rather than creating alignment guides everywhere.
            points = (
                (entity.points[0], entity.points[-1])
                if not self.v._is_poly_closed(entity.points)
                else ()
            )
            for px, py in points:
                endpoint_c = self.v._w2c(px, py)
                if not self._source_is_local(cx, cy, endpoint_c, endpoint_c, reference_c):
                    continue
                pcx, _ = self.v._w2c(px, wy)
                x_distance = abs(cx - pcx)
                if align_x_enabled and x_distance < best_distance:
                    best_distance = x_distance
                    best = (px, wy, "axis_x")
                _, pcy = self.v._w2c(wx, py)
                y_distance = abs(cy - pcy)
                if align_y_enabled and y_distance < best_distance:
                    best_distance = y_distance
                    best = (wx, py, "axis_y")
        return best

    def angle(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        if not getattr(self.v, "_snap_angle_enabled", True):
            return (wx, wy)
        return angle_snap(
            ax,
            ay,
            wx,
            wy,
            getattr(self.v, "_rotation_snap_increment", 15.0),
        )

    # ── Candidate sources ─────────────────────────────────────────────────

    def _shape_candidate(
        self, cx: float, cy: float, exclude: set[str] | None = None
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_vertex_enabled", True):
            return None  # shape center/start/end points are "vertex family"
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        excluded = exclude or ()
        hidden_ids = self.v._flagged("hidden")
        # Shape snapping works across visible layers — shapes on non-active
        # layers remain valid targets even when not selectable/editable.
        # ``exclude`` skips the entity being dragged itself — otherwise its
        # OWN (stale, pre-drag) cached shape stays a valid snap target and
        # the drag can stick to a "ghost" of where it started.
        for eid, shape in v._snap_shapes().items():
            if eid in excluded or eid in hidden_ids:
                continue
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = v._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= best_dist:
                    best_dist = dist
                    best = (sx, sy, snap_type)
        return best

    def _guide_candidate(self, cx: float, cy: float, wx: float, wy: float) -> SnapResult | None:
        """Snap to user guide lines (see the rulers/guides feature)."""
        v = self.v
        guides = getattr(v, "_guides", None)
        if not guides:
            return None
        best: SnapResult | None = None
        best_dist = self.GUIDE_SNAP_PX * self._snap_strength()
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

    @staticmethod
    def _angle_on_arc(angle: float, shape: ArcShape) -> bool:
        """Whether an angle lies on the arc's counter-clockwise sweep."""
        sweep = (shape.end_angle - shape.start_angle) % 360.0
        offset = (angle - shape.start_angle) % 360.0
        return offset <= sweep + 1e-9

    def _curve_candidate(
        self, cx: float, cy: float, *, exclude: set[str] | None = None
    ) -> SnapResult | None:
        """Nearest analytic point on circles, arcs, and rotated ellipses."""
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_distance = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        for eid, shape in self.v._snap_shapes().items():
            if eid in (exclude or ()) or eid in hidden_ids:
                continue
            point: tuple[float, float] | None = None
            if isinstance(shape, (CircleShape, ArcShape)):
                angle = math.degrees(math.atan2(wy - shape.center[1], wx - shape.center[0])) % 360.0
                if isinstance(shape, ArcShape) and not self._angle_on_arc(angle, shape):
                    continue
                radians = math.radians(angle)
                point = (
                    shape.center[0] + shape.radius * math.cos(radians),
                    shape.center[1] + shape.radius * math.sin(radians),
                )
            elif isinstance(shape, EllipseShape) and shape.rx > 0 and shape.ry > 0:
                rotation = math.radians(shape.rotation)
                cosine, sine = math.cos(rotation), math.sin(rotation)
                dx, dy = wx - shape.center[0], wy - shape.center[1]
                lx, ly = dx * cosine + dy * sine, -dx * sine + dy * cosine
                t = math.atan2(ly * shape.rx, lx * shape.ry)
                # Newton refinement of squared-distance derivative.
                for _ in range(8):
                    ct, st = math.cos(t), math.sin(t)
                    ex, ey = shape.rx * ct, shape.ry * st
                    first = (ex - lx) * (-shape.rx * st) + (ey - ly) * (shape.ry * ct)
                    second = (
                        shape.rx * shape.rx * st * st
                        + shape.ry * shape.ry * ct * ct
                        + (ex - lx) * (-shape.rx * ct)
                        + (ey - ly) * (-shape.ry * st)
                    )
                    if abs(second) < 1e-12:
                        break
                    t -= first / second
                ex, ey = shape.rx * math.cos(t), shape.ry * math.sin(t)
                point = (
                    shape.center[0] + ex * cosine - ey * sine,
                    shape.center[1] + ex * sine + ey * cosine,
                )
            if point is None:
                continue
            pcx, pcy = self.v._w2c(*point)
            distance = math.hypot(cx - pcx, cy - pcy)
            if distance < best_distance:
                best_distance = distance
                best = (*point, "edge")
        return best

    def _tangent_candidate(
        self,
        cx: float,
        cy: float,
        reference: tuple[float, float] | None,
        *,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Tangency points from the active draw/reference point to circles."""
        if reference is None or not getattr(self.v, "_snap_tangent_enabled", True):
            return None
        ax, ay = reference
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        for eid, shape in self.v._snap_shapes().items():
            if eid in (exclude or ()) or eid in hidden_ids or not isinstance(shape, CircleShape):
                continue
            dx, dy = ax - shape.center[0], ay - shape.center[1]
            distance_sq = dx * dx + dy * dy
            radius_sq = shape.radius * shape.radius
            if distance_sq <= radius_sq + 1e-12:
                continue
            base = radius_sq / distance_sq
            turn = shape.radius * math.sqrt(distance_sq - radius_sq) / distance_sq
            for sign in (-1.0, 1.0):
                tx = shape.center[0] + base * dx - sign * turn * dy
                ty = shape.center[1] + base * dy + sign * turn * dx
                tcx, tcy = self.v._w2c(tx, ty)
                distance = math.hypot(cx - tcx, cy - tcy)
                if distance < best_dist:
                    best_dist = distance
                    best = (tx, ty, "tangent")
        return best

    def _extension_candidate(
        self,
        cx: float,
        cy: float,
        *,
        reference: tuple[float, float] | None = None,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Project onto the infinite extension of visible straight segments."""
        if not getattr(self.v, "_snap_extension_enabled", True):
            return None
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_dist = self.INFERRED_LINE_SNAP_PX * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        reference_c = self.v._w2c(*reference) if reference is not None else None
        for entity in self.v._entities:
            if entity.id in (exclude or ()) or entity.id in hidden_ids:
                continue
            points = entity.points
            for start, end in zip(points, points[1:]):
                if not self._source_is_local(
                    cx,
                    cy,
                    self.v._w2c(*start),
                    self.v._w2c(*end),
                    reference_c,
                ):
                    continue
                dx, dy = end[0] - start[0], end[1] - start[1]
                length_sq = dx * dx + dy * dy
                if length_sq <= 1e-12:
                    continue
                t = ((wx - start[0]) * dx + (wy - start[1]) * dy) / length_sq
                if -1e-6 <= t <= 1.0 + 1e-6:
                    continue
                px, py = start[0] + t * dx, start[1] + t * dy
                pcx, pcy = self.v._w2c(px, py)
                distance = math.hypot(cx - pcx, cy - pcy)
                if distance < best_dist:
                    best_dist = distance
                    best = (px, py, "extension")
        return best

    def _source_is_local(
        self,
        cx: float,
        cy: float,
        start_c: tuple[float, float],
        end_c: tuple[float, float],
        reference_c: tuple[float, float] | None,
    ) -> bool:
        """Whether an inferred source belongs to the active drawing area."""
        distances = [self._screen_distance_to_segment(cx, cy, start_c, end_c)]
        if reference_c is not None:
            distances.append(self._screen_distance_to_segment(*reference_c, start_c, end_c))
        return min(distances) <= self.RELATIONSHIP_REFERENCE_PX

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
        first_priority = self._snap_priority(first[2])
        second_priority = self._snap_priority(second[2])
        first_explicit = self._is_explicit_finite_geometry(first[2])
        second_explicit = self._is_explicit_finite_geometry(second[2])
        if first_explicit != second_explicit:
            # Explicit finite geometry always beats inferred construction
            # when both candidates are inside their acquisition radii.
            return first if first_explicit else second
        if not first_explicit and first_priority != second_priority:
            return first if first_priority > second_priority else second
        # A point has a small magnetic core over an edge, but outside that
        # core competing explicit targets resolve by proximity. This avoids a
        # distant circle quadrant stealing an exact tangent/curve hit.
        magnetic_core = 6.0 * self._snap_strength()
        if self._is_magnetic_point(first[2]) and second_priority < 105 and fd <= magnetic_core:
            return first
        if self._is_magnetic_point(second[2]) and first_priority < 105 and sd <= magnetic_core:
            return second
        # Preserve source priority for visually coincident candidates. Tiny
        # floating-point differences must not relabel a tangent as generic
        # "On Edge" or make overlapping snap roles flicker frame-to-frame.
        if abs(fd - sd) <= 0.25:
            return first
        return second if sd < fd else first

    @staticmethod
    def _snap_priority(snap_type: str) -> int:
        """CAD-style hierarchy: exact geometry before inferred relationships."""
        if snap_type == "intersection":
            return 120
        if snap_type == "vertex" or snap_type.startswith(
            ("vertex_", "spline_control_", "arc_start", "arc_end")
        ):
            return 115
        if snap_type == "center" or snap_type.startswith(("circle_", "ellipse_", "quadrant_")):
            return 110
        if snap_type == "midpoint":
            return 105
        if snap_type == "edge":
            return 100
        if snap_type == "tangent":
            return 95
        if snap_type == "grid":
            return 80
        if snap_type == "guide":
            return 70
        if snap_type == "extension":
            return 60
        if snap_type in {
            "equal_length",
            "parallel",
            "perpendicular",
        }:
            # Constraint relationships should not be masked by a visible
            # grid. Explicit finite geometry still wins in _pick_better.
            return 90
        if snap_type in {"parallel_equal_length", "perpendicular_equal_length"}:
            return 92
        if snap_type in {"axis_x", "axis_y"}:
            return 75
        return 50

    @staticmethod
    def _is_explicit_finite_geometry(snap_type: str) -> bool:
        """Whether a candidate represents an existing finite geometry target."""
        return (
            snap_type in {"intersection", "center", "midpoint", "edge", "tangent"}
            or snap_type == "vertex"
            or snap_type.startswith(
                (
                    "vertex_",
                    "spline_control_",
                    "arc_start",
                    "arc_end",
                    "circle_",
                    "ellipse_",
                    "quadrant_",
                )
            )
        )

    @staticmethod
    def _is_magnetic_point(snap_type: str) -> bool:
        return snap_type in {"intersection", "vertex", "midpoint"} or snap_type.startswith(
            ("vertex_", "spline_control_", "arc_start", "arc_end")
        )


class ShapeSnapEngine:
    """Shape-aware snapping for precise alignment and positioning."""

    # Match the core CAD snap distance so analytic shapes and polyline
    # geometry feel equally reachable.
    SNAP_RADIUS = 14.0  # Screen pixels

    @staticmethod
    def get_snap_candidates(shape: Shape) -> list[tuple[float, float, str]]:
        """Get snap points for a shape.

        Returns list of (x, y, snap_type) where snap_type describes the point.
        """
        points: list[tuple[float, float, str]] = []

        if isinstance(shape, ArcShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Start point
            start_rad = shape.start_angle * math.pi / 180
            start_x = shape.center[0] + shape.radius * math.cos(start_rad)
            start_y = shape.center[1] + shape.radius * math.sin(start_rad)
            points.append((start_x, start_y, "arc_start"))
            # End point
            end_rad = shape.end_angle * math.pi / 180
            end_x = shape.center[0] + shape.radius * math.cos(end_rad)
            end_y = shape.center[1] + shape.radius * math.sin(end_rad)
            points.append((end_x, end_y, "arc_end"))
            for angle, label in (
                (0.0, "quadrant_east"),
                (90.0, "quadrant_north"),
                (180.0, "quadrant_west"),
                (270.0, "quadrant_south"),
            ):
                if SnapEngine._angle_on_arc(angle, shape):
                    radians = math.radians(angle)
                    points.append(
                        (
                            shape.center[0] + shape.radius * math.cos(radians),
                            shape.center[1] + shape.radius * math.sin(radians),
                            label,
                        )
                    )

        elif isinstance(shape, CircleShape):
            # Center (primary snap point)
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points
            points.append(
                (
                    shape.center[0],
                    shape.center[1] + shape.radius,
                    "circle_north",
                )
            )
            points.append(
                (
                    shape.center[0],
                    shape.center[1] - shape.radius,
                    "circle_south",
                )
            )
            points.append(
                (
                    shape.center[0] + shape.radius,
                    shape.center[1],
                    "circle_east",
                )
            )
            points.append(
                (
                    shape.center[0] - shape.radius,
                    shape.center[1],
                    "circle_west",
                )
            )

        elif isinstance(shape, EllipseShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points (approximate for rotation)
            cos_r = math.cos(shape.rotation * math.pi / 180)
            sin_r = math.sin(shape.rotation * math.pi / 180)
            # North
            points.append(
                (
                    shape.center[0] + shape.ry * sin_r,
                    shape.center[1] + shape.ry * cos_r,
                    "ellipse_north",
                )
            )
            # South
            points.append(
                (
                    shape.center[0] - shape.ry * sin_r,
                    shape.center[1] - shape.ry * cos_r,
                    "ellipse_south",
                )
            )
            # East — perpendicular to the north/south axis above, scaled by
            # the other radius, so all four cardinal points sit on the
            # rotated ellipse boundary (circle already has all 4; ellipse
            # was missing this pair).
            points.append(
                (
                    shape.center[0] + shape.rx * cos_r,
                    shape.center[1] - shape.rx * sin_r,
                    "ellipse_east",
                )
            )
            # West
            points.append(
                (
                    shape.center[0] - shape.rx * cos_r,
                    shape.center[1] + shape.rx * sin_r,
                    "ellipse_west",
                )
            )

        elif isinstance(shape, SplineShape):
            # Control points are primary snap targets for splines
            for i, (x, y) in enumerate(shape.control_points):
                points.append((x, y, f"spline_control_{i}"))

        elif isinstance(
            shape,
            (PolygonShape, RectangleShape, RoundedRectangleShape, SlotShape, StarShape),
        ):
            points.append((shape.center[0], shape.center[1], "center"))
            for i, (x, y) in enumerate(shape.points[:-1]):
                points.append((x, y, f"vertex_{i}"))

        # Fallback: tessellation points (lower priority)
        if not points:
            for i, (x, y) in enumerate(shape.points):
                points.append((x, y, f"tessellation_{i}"))

        return points
