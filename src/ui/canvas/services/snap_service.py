"""Object, grid, and guide snap resolution composed by the canvas view."""

from __future__ import annotations

import math

from src.ui.canvas.constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas.constants import SNAP_DIST as _SNAP_DIST


class SnapService:
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
            center = self._host._entity_center(e.id)
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
                lock = SnapService._edge_axis_lock(ax, ay, bx, by)
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
        thresh = _SNAP_DIST
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
        thresh = _SNAP_DIST
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
