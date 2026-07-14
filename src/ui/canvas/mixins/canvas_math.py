"""CanvasMathMixin family — hit-testing, snap resolution, and layer
management for PolylineView.

Three previously-separate mixins merged here (``HitTestMixin``,
``SnapGlueMixin``, ``LayerMixin``) — all are query/computation-oriented
(cursor hit-testing, drag/resize snap resolution, layer bookkeeping) rather
than direct user-facing operations, each individually small enough that a
dedicated file didn't pay for itself.

PolylineView inherits these via
``class PolylineView(QWidget, CanvasRenderer, ..., HitTestMixin,
SnapGlueMixin, LayerMixin)``. Since methods are resolved through the normal
MRO, every ``self.*`` reference works without modification — same pattern
as ``CanvasRenderer`` in ``render.py``.

Extracted from ``view.py`` as part of shrinking that file's ~5,900 lines.
Every method here was verified to have zero external callers other than
``self``/other-mixin references before each move (the whole-codebase grep
this repo's git history shows a prior "mixin-inlining" refactor silently
dropped ~40 still-referenced methods — see commit 9a7d3a5 — so this file
exists specifically to NOT repeat that).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal, overload

from src.ui.canvas.constants import EDGE_HIT as _EDGE_HIT
from src.ui.canvas.constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas.constants import SNAP_DIST as _SNAP_DIST
from src.ui.canvas.constants import VERT_HIT as _VERT_HIT

if TYPE_CHECKING:
    from typing import Protocol

    class _HitTestHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``mixins/render.py``'s ``_RendererHost`` for why
        this exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _entities: list[Any]
        _active_layer: str | None
        _guides: list[tuple[str, float]]
        _ghost_polys: list[list[tuple[float, float]]]
        _ghost_visible: bool

        def _entity_selectable(self, idx: int) -> bool: ...
        def _on_active_layer(self, e: Any) -> bool: ...
        def _w2c(self, x: float, y: float) -> tuple[float, float]: ...
        def _c2w(self, cx: float, cy: float) -> tuple[float, float]: ...

    _HitTestBase = _HitTestHost
else:
    _HitTestBase = object


class HitTestMixin(_HitTestBase):
    """Mixin providing cursor hit-testing for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    @staticmethod
    def _segment_intersection_point(
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> tuple[float, float] | None:
        """Return proper segment intersection point, excluding near-endpoint overlap noise."""
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

        # Ignore intersections that are effectively just a shared endpoint; those
        # are covered by endpoint snap already and otherwise make labels noisy.
        for ex, ey in (a1, a2, b1, b2):
            if math.hypot(px - ex, py - ey) < 1e-6:
                return None

        return (px, py)

    def _find_nearest_endpoint(self, cx: float, cy: float) -> tuple[float, float] | None:
        """Find the nearest start/end point of existing polylines within snap distance.

        Used to connect new drawings to existing polyline endpoints (Fusion 360 behavior).
        """
        best_dist = _SNAP_DIST
        best_pt: tuple[float, float] | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            if len(poly) < 2:
                continue
            for pt in (poly[0], poly[-1]):
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
        return best_pt

    def _find_nearest_vertex(self, cx: float, cy: float) -> tuple[int, int] | None:
        best_dist = _VERT_HIT
        best = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            for vi, pt in enumerate(poly):
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best = (pi, vi)
        return best

    @staticmethod
    def _poly_closed_n(poly: list[tuple[float, float]]) -> int:
        """Return segment count: n if closed, n-1 if open.  0 for tiny polys."""
        n = len(poly)
        if n < 2:
            return 0
        if n >= 3 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01:
            return n
        return n - 1

    @overload
    def _closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: Literal[False] = False,
    ) -> float | None: ...

    @overload
    def _closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: Literal[True],
    ) -> tuple[float | None, tuple[int, tuple[float, float]] | None]: ...

    def _closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: bool = False,
    ) -> float | None | tuple[float | None, tuple[int, tuple[float, float]] | None]:
        """Compute closest point on every segment of *poly* to world point (wx,wy).

        Returns the screen-distance and, depending on *return_segment*, either
        just the poly index (``int | None``) or a tuple ``(pi, seg_idx,
        closest_world_pt) | None``.  This is the single source of truth for
        all segment-distance hit-testing.
        """
        best_dist = float("inf")
        best: Any = None
        n = len(poly)
        if n < 2:
            return (None, None) if return_segment else None
        seg_count = self._poly_closed_n(poly)
        for vi in range(seg_count):
            ax, ay = poly[vi]
            bx, by = poly[(vi + 1) % n]
            dx, dy = bx - ax, by - ay
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-12:
                px_py = (ax, ay)
                scx, scy = self._w2c(ax, ay)
                d = math.hypot(cx - scx, cy - scy)
            else:
                t = max(0.0, min(1.0, ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq))
                px_py = (ax + t * dx, ay + t * dy)
                scx, scy = self._w2c(*px_py)
                d = math.hypot(cx - scx, cy - scy)
            if d < best_dist:
                best_dist = d
                if return_segment:
                    best = (vi, px_py)
                else:
                    best = vi
        if return_segment:
            return best_dist, best
        return best_dist if best is not None else None

    def _find_nearest_edge(
        self, cx: float, cy: float
    ) -> tuple[int, int, tuple[float, float]] | None:
        best_dist = _EDGE_HIT
        wx, wy = self._c2w(cx, cy)
        best: tuple[int, int, tuple[float, float]] | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            dist, result = self._closest_point_on_poly(poly, wx, wy, cx, cy, return_segment=True)
            if dist is not None and dist < best_dist and result is not None:
                best_dist = dist
                seg_idx, closest_pt = result
                best = (pi, seg_idx, closest_pt)
        return best

    def _find_poly_at(self, cx: float, cy: float) -> int | None:
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            dist = self._closest_point_on_poly(poly, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    def _find_guide_at(self, cx: float, cy: float) -> int | None:
        """Guide index within grab distance of the cursor (screen px)."""
        best: int | None = None
        best_d = 6.0
        for i, (orient, coord) in enumerate(self._guides):
            if orient == "v":
                gx, _ = self._w2c(coord, 0.0)
                d = abs(cx - gx)
            else:
                _, gy = self._w2c(0.0, coord)
                d = abs(cy - gy)
            if d < best_d:
                best_d = d
                best = i
        return best

    def _find_inactive_poly_at(self, cx: float, cy: float) -> int | None:
        """Hit-test entities on non-active layers; returns entity index."""
        if self._active_layer is None:
            return None
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, e in enumerate(self._entities):
            if e.hidden or self._on_active_layer(e):
                continue
            dist = self._closest_point_on_poly(e.points, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    def _find_ghost_poly_at(self, cx: float, cy: float) -> int | None:
        """Hit-test the ghost overlay polys; returns ghost-list index or None."""
        if not self._ghost_polys or not self._ghost_visible:
            return None
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, poly in enumerate(self._ghost_polys):
            dist = self._closest_point_on_poly(poly, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best


# ════════════════════════════════════════════════════════════════════════════
# Snap resolution for drag/resize
# ════════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from typing import Protocol

    class _SnapGlueHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``mixins/render.py``'s ``_RendererHost`` for why
        this exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _entities: list[Any]
        _sel: set[int]
        _scale: float
        _grid_snap: bool
        _grid_spacing: float
        _guides: list[tuple[str, float]]
        _move_start_pts: list[tuple[float, float]]

        def _is_poly_closed(self, poly: list[tuple[float, float]]) -> bool: ...
        def _entity_center(self, idx: int) -> tuple[float, float] | None: ...

    _SnapGlueBase = _SnapGlueHost
else:
    _SnapGlueBase = object


class SnapGlueMixin(_SnapGlueBase):
    """Mixin providing object/grid/guide snap resolution for drag and
    resize on :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    def _static_snap_geometry(
        self, *, exclude: set[int] | None = None
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
        for i, e in enumerate(self._entities):
            if i in excluded or e.hidden:
                continue
            poly = e.points
            pts.extend(poly)
            n = len(poly)
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for k in range(seg_count):
                segs.append((poly[k], poly[(k + 1) % n]))
            center = self._entity_center(i)
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
                lock = SnapGlueMixin._edge_axis_lock(ax, ay, bx, by)
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
        pts = self._move_start_pts
        if not pts:
            return None
        scale = max(self._scale, _MIN_SCALE)
        thresh = _SNAP_DIST
        world_r = thresh / scale

        static_pts, static_segs, static_centers = self._static_snap_geometry(exclude=self._sel)

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

            if self._grid_snap:
                gx = round(mx / self._grid_spacing) * self._grid_spacing
                gy = round(my / self._grid_spacing) * self._grid_spacing
                d = math.hypot(gx - mx, gy - my) * scale
                if d <= thresh:
                    matches.append((d, gx - mx, gy - my, (gx, gy), origin, "grid"))

            for orient, coord in self._guides:
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
        scale = max(self._scale, _MIN_SCALE)
        thresh = _SNAP_DIST
        world_r = thresh / scale
        static_pts, static_segs, static_centers = self._static_snap_geometry(exclude=self._sel)
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
        if self._grid_snap:
            gx = round(wx / self._grid_spacing) * self._grid_spacing
            gy = round(wy / self._grid_spacing) * self._grid_spacing
            d = math.hypot(gx - wx, gy - wy) * scale
            if d <= thresh and (best is None or d < best[0]):
                best = (d, (gx, gy), "grid")
        for orient, coord in self._guides:
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


# ════════════════════════════════════════════════════════════════════════════
# Layer management
# ════════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from typing import Any, Protocol

    from src.ui.canvas.undo import EntityRecord

    class _LayerHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``mixins/render.py``'s ``_RendererHost`` for why
        this exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _entities: list[Any]
        _active_layer: str | None
        _layer_order: list[str]
        _layer_colors: dict[str, str]
        _sel: set[int]

        def _push_undo(self, coalesce: str | None = None) -> None: ...
        def _redraw(self) -> None: ...
        def _notify(self) -> None: ...
        def _fire_poly_change(self) -> None: ...
        def _compact_entities(self, drop: set[int]) -> None: ...
        def _reset_edit_interaction_state(self) -> None: ...

    _LayerBase = _LayerHost
else:
    _LayerBase = object


class LayerMixin(_LayerBase):
    """Mixin providing layer management for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    @property
    def active_layer(self) -> str | None:
        return self._active_layer

    def layer_names(self) -> list[str]:
        names = list(self._layer_order)
        for e in self._entities:
            if e.layer is not None and e.layer not in names:
                names.append(e.layer)
        return names

    def set_layer_model(self, order: list[str], active: str | None) -> None:
        """Install the layer list + active layer (used on load/restore)."""
        self._layer_order = [str(n) for n in order if str(n)]
        if active is not None and str(active) not in self._layer_order:
            self._layer_order.append(str(active))
        self._active_layer = str(active) if active is not None else None
        if self._active_layer is not None:
            for e in self._entities:
                if e.layer is None:
                    e.layer = self._active_layer
        self._drop_inactive_selection()
        self._redraw()

    def set_active_layer(self, name: str) -> None:
        name = str(name)
        if name not in self._layer_order:
            self._layer_order.append(name)
        if self._active_layer == name:
            return
        self._active_layer = name
        self._drop_inactive_selection()
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()

    def add_layer(self, name: str, *, activate: bool = False) -> None:
        name = str(name)
        if name not in self._layer_order:
            self._push_undo()
            self._layer_order.append(name)
        if activate:
            self.set_active_layer(name)
        else:
            self._redraw()

    def rename_layer(self, old: str, new: str) -> None:
        old, new = str(old), str(new).strip()
        if not new or old == new or new in self._layer_order:
            return
        self._push_undo()
        self._layer_order = [new if n == old else n for n in self._layer_order]
        for e in self._entities:
            if e.layer == old:
                e.layer = new
        if self._active_layer == old:
            self._active_layer = new
        old_color = self._layer_colors.pop(old, None)
        if old_color is not None:
            self._layer_colors[new] = old_color
        self._redraw()

    def delete_layer(self, name: str) -> None:
        """Delete a layer and every entity on it (undoable)."""
        name = str(name)
        self._push_undo()
        drop = {i for i, e in enumerate(self._entities) if e.layer == name}
        if drop:
            self._compact_entities(drop)
            self._sel = set()
        self._layer_order = [n for n in self._layer_order if n != name]
        if not self._layer_order:
            self._layer_order = [
                self._active_layer
                if self._active_layer is not None and self._active_layer != name
                else "Layer 1"
            ]
        if self._active_layer == name:
            self._active_layer = self._layer_order[0]
        self._layer_colors.pop(name, None)
        self._redraw()
        self._notify()
        if drop:
            self._fire_poly_change()

    def layer_color(self, name: str) -> str | None:
        return self._layer_colors.get(str(name))

    def consolidate_layers(self, source_layers: list[str], target_layer: str) -> int:
        """Move every shape on ``source_layers`` onto ``target_layer``, then
        remove those (now-empty) source layers. Single undo step. Returns
        the number of entities moved."""
        target_layer = str(target_layer)
        sources = [str(s) for s in source_layers if str(s) and str(s) != target_layer]
        if not sources:
            return 0
        src_set = set(sources)
        self._push_undo()
        moved = 0
        if target_layer not in self._layer_order:
            self._layer_order.append(target_layer)
        for e in self._entities:
            if e.layer in src_set:
                e.layer = target_layer
                moved += 1
        self._layer_order = [n for n in self._layer_order if n not in src_set]
        if not self._layer_order:
            self._layer_order = [target_layer]
        if self._active_layer in src_set:
            self._active_layer = target_layer
        for name in sources:
            self._layer_colors.pop(name, None)
        self._drop_inactive_selection()
        self._redraw()
        self._notify()
        if moved:
            self._fire_poly_change()
        return moved

    def set_layer_color(self, name: str, color: str | None) -> None:
        """Assign (or clear, with ``color=None``) a layer's display color."""
        name = str(name)
        if color is None:
            self._layer_colors.pop(name, None)
        else:
            self._layer_colors[name] = str(color)
        self._redraw()

    def move_layer(self, name: str, new_index: int) -> None:
        name = str(name)
        names = self.layer_names()
        if name not in names:
            return
        self._push_undo()
        names.remove(name)
        names.insert(max(0, min(int(new_index), len(names))), name)
        self._layer_order = names
        self._redraw()

    def move_indices_to_layer(self, indices: list[int], layer: str) -> int:
        """Reassign entities to ``layer``; returns how many moved."""
        layer = str(layer)
        if layer not in self._layer_order:
            self._layer_order.append(layer)
        moved = 0
        pushed = False
        for idx in indices:
            if not (0 <= idx < len(self._entities)):
                continue
            e = self._entities[idx]
            if e.layer == layer:
                continue
            if not pushed:
                self._push_undo()
                pushed = True
            e.layer = layer
            moved += 1
        if moved:
            self._drop_inactive_selection()
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return moved

    def _on_active_layer(self, e: EntityRecord) -> bool:
        return self._document.on_active_layer(e)

    def _entity_selectable(self, idx: int) -> bool:
        return self._document.entity_selectable(idx)

    def _noninteractive_indices(self) -> set[int]:
        """Hidden entities plus entities on non-active layers."""
        return {i for i, e in enumerate(self._entities) if e.hidden or not self._on_active_layer(e)}

    def _drop_inactive_selection(self) -> None:
        if self._document.drop_inactive_selection():
            self._notify()
