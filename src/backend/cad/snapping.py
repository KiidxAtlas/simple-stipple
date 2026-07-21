"""Pure snapping helpers for canvas interactions.

These helpers are intentionally UI-agnostic: callers supply coordinate conversion
callbacks and geometry accessors, and the functions return the resolved snap
position plus a string describing the snap kind.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from src.backend.cad.geometry import (
    MIN_SCALE as _MIN_SCALE,
)
from src.backend.cad.geometry import (
    SNAP_DIST as _SNAP_DIST,
)
from src.backend.spatial import build_snap_tree, find_nearest_index

# Geometry epsilons (kept local to avoid pulling backend.geometry into the
# UI-only behaviors layer; values mirror src/backend/cad/geometry.py).
_EPS = 1e-6
_EPS_SQ_DEGENERATE = 1e-12

Point = tuple[float, float]
Polyline = list[Point]
SnapResult = tuple[float, float, str]

WorldToCanvas = Callable[[float, float], Point]
CanvasToWorld = Callable[[float, float], Point]
PolylineBounds = Callable[[Polyline], tuple[float, float, float, float]]
IsPolylineClosed = Callable[[Polyline], bool]
SegmentIntersectionPoint = Callable[[Point, Point, Point, Point], Point | None]


def polygon_centroid(poly: Sequence[Point]) -> Point:
    """Area-weighted centroid of a closed polygon.

    Falls back to the plain vertex average when the polygon has fewer than
    3 distinct vertices or is degenerate (zero area, e.g. collinear points)
    — this keeps "center" snap meaningful even for skinny/degenerate shapes,
    and matches what users expect (visual center of mass), not the
    bounding-box midpoint which can sit far outside an irregular polygon.
    """
    pts = list(poly)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area2) < 1e-9:
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    area6 = area2 * 3.0
    return (cx / area6, cy / area6)


def snap_to_grid(wx: float, wy: float, spacing: float) -> Point:
    spacing = max(spacing, 0.001)
    return (round(wx / spacing) * spacing, round(wy / spacing) * spacing)


def angle_snap(
    ax: float, ay: float, wx: float, wy: float, increment_degrees: float = 45.0
) -> Point:
    """Snap a point to the nearest configured angle ray from an anchor."""
    dxx = wx - ax
    dyy = wy - ay
    dist = math.hypot(dxx, dyy)
    if dist < 1e-9:
        return (wx, wy)
    ang = math.degrees(math.atan2(dyy, dxx))
    increment = max(0.1, min(180.0, float(increment_degrees)))
    snapped_ang = round(ang / increment) * increment
    rad = math.radians(snapped_ang)
    return (ax + math.cos(rad) * dist, ay + math.sin(rad) * dist)


def find_nearest_vertex_snap(
    cx: float,
    cy: float,
    polylines: Sequence[Polyline],
    hidden_polys: set[int],
    w2c: WorldToCanvas,
    *,
    snap_dist: float = _SNAP_DIST,
    exclude: set[tuple[int, int]] | None = None,
) -> Point | None:
    """Return nearest vertex world position within snap distance."""
    canvas_points: list[Point] = []
    world_points: list[Point] = []
    excluded = exclude or set()
    for pi, poly in enumerate(polylines):
        if pi in hidden_polys:
            continue
        for vi, pt in enumerate(poly):
            if (pi, vi) in excluded:
                continue
            canvas_points.append(w2c(*pt))
            world_points.append(pt)
    nearest = find_nearest_index(build_snap_tree(canvas_points), (cx, cy), snap_dist)
    if nearest is None:
        return None
    return world_points[nearest]


def _candidate_polylines(
    polylines: Sequence[Polyline],
    hidden_polys: set[int],
    *,
    cwx: float,
    cwy: float,
    world_r: float,
    poly_bounds: PolylineBounds,
) -> list[tuple[int, Polyline]]:
    candidate_polys: list[tuple[int, Polyline]] = []
    for pi, poly in enumerate(polylines):
        if pi in hidden_polys or len(poly) < 2:
            continue
        x0, y0, x1, y1 = poly_bounds(poly)
        if cwx < x0 - world_r or cwx > x1 + world_r:
            continue
        if cwy < y0 - world_r or cwy > y1 + world_r:
            continue
        candidate_polys.append((pi, poly))
    return candidate_polys


def snap_to_polyline(
    cx: float,
    cy: float,
    polylines: Sequence[Polyline],
    hidden_polys: set[int],
    scale: float,
    w2c: WorldToCanvas,
    c2w: CanvasToWorld,
    poly_bounds: PolylineBounds,
    is_poly_closed: IsPolylineClosed,
    segment_intersection_point: SegmentIntersectionPoint,
    *,
    reference_point: Point | None = None,
    draw_points: Sequence[Point] | None = None,
    exclude_vertices: set[tuple[int, int]] | None = None,
    mode: str = "select",
    snap_dist: float = _SNAP_DIST,
    min_scale: float = _MIN_SCALE,
    allow_vertex: bool = True,
    allow_edge: bool = True,
) -> SnapResult | None:
    """Return the nearest semantic snap on any polyline within snap distance.

    Priority order: vertices, midpoints, intersections, centers, perpendicular
    (when drawing), and finally generic edges. ``allow_vertex`` gates the
    vertex/midpoint/intersection/center "point family"; ``allow_edge`` gates
    the perpendicular/generic-edge fallback family.
    """
    cwx, cwy = c2w(cx, cy)
    world_r = (snap_dist / max(scale, min_scale)) * 1.6
    # At high zoom (large scale), world_r shrinks and can become too small to
    # capture large-radius arcs.  Clamp to at least 1.0 world unit so arcs
    # with R > 1000 remain snappable when the user zooms in closely on arc
    # edges (the bug: snap points jumping / disappearing at 500 %+ zoom).
    world_r = max(world_r, 1.0)
    candidate_polys = _candidate_polylines(
        polylines,
        hidden_polys,
        cwx=cwx,
        cwy=cwy,
        world_r=world_r,
        poly_bounds=poly_bounds,
    )
    excluded = exclude_vertices or set()

    if allow_vertex:
        canvas_points: list[Point] = []
        world_points: list[Point] = []
        for pi, poly in candidate_polys:
            for vi, pt in enumerate(poly):
                if (pi, vi) in excluded:
                    continue
                canvas_points.append(w2c(*pt))
                world_points.append(pt)
        nearest = find_nearest_index(build_snap_tree(canvas_points), (cx, cy), snap_dist)
        if nearest is not None:
            vertex_pt = world_points[nearest]
            return (vertex_pt[0], vertex_pt[1], "vertex")

        best_dist = snap_dist
        best_pt: Point | None = None
        for _pi, poly in candidate_polys:
            n = len(poly)
            closed = is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
                sx, sy = w2c(mx, my)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = (mx, my)
        if best_pt is not None:
            return (best_pt[0], best_pt[1], "midpoint")

        best_dist = snap_dist
        best_pt = None
        segments: list[tuple[Point, Point]] = []
        for _pi, poly in candidate_polys:
            n = len(poly)
            closed = is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for vi in range(seg_count):
                segments.append((poly[vi], poly[(vi + 1) % n]))

        for i in range(len(segments)):
            a1, a2 = segments[i]
            for j in range(i + 1, len(segments)):
                b1, b2 = segments[j]
                ipt = segment_intersection_point(a1, a2, b1, b2)
                if ipt is None:
                    continue
                sx, sy = w2c(*ipt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = ipt
        if best_pt is not None:
            return (best_pt[0], best_pt[1], "intersection")

        best_dist = snap_dist
        best_pt = None
        for _pi, poly in candidate_polys:
            if not is_poly_closed(poly):
                continue
            center = polygon_centroid(poly)
            sx, sy = w2c(*center)
            d = math.hypot(cx - sx, cy - sy)
            if d < best_dist:
                best_dist = d
                best_pt = center
        if best_pt is not None:
            return (best_pt[0], best_pt[1], "center")

    if allow_edge:
        perp_ref = reference_point
        if perp_ref is None and mode == "draw" and draw_points:
            perp_ref = draw_points[-1]
        if perp_ref is not None:
            best_dist = snap_dist
            best_pt = None
            last_wx, last_wy = perp_ref
            for _pi, poly in candidate_polys:
                n = len(poly)
                closed = is_poly_closed(poly)
                seg_count = n if closed else n - 1
                for vi in range(seg_count):
                    eax, eay = poly[vi]
                    ebx, eby = poly[(vi + 1) % n]
                    edx, edy = ebx - eax, eby - eay
                    seg_len_sq = edx * edx + edy * edy
                    if seg_len_sq < 1e-12:
                        continue
                    t_perp = ((last_wx - eax) * edx + (last_wy - eay) * edy) / seg_len_sq
                    if 0.0 <= t_perp <= 1.0:
                        foot_x = eax + t_perp * edx
                        foot_y = eay + t_perp * edy
                        sx, sy = w2c(foot_x, foot_y)
                        d = math.hypot(cx - sx, cy - sy)
                        if d < best_dist:
                            best_dist = d
                            best_pt = (foot_x, foot_y)
            if best_pt is not None:
                return (best_pt[0], best_pt[1], "perpendicular")

        best_dist = snap_dist
        best_pt = None
        for _pi, poly in candidate_polys:
            n = len(poly)
            closed = is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                dx, dy = bx - ax, by - ay
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-12:
                    continue
                wwx, wwy = c2w(cx, cy)
                t = max(0.0, min(1.0, ((wwx - ax) * dx + (wwy - ay) * dy) / seg_len_sq))
                px, py_ = ax + t * dx, ay + t * dy
                scx, scy = w2c(px, py_)
                d = math.hypot(cx - scx, cy - scy)
                if d < best_dist:
                    best_dist = d
                    best_pt = (px, py_)
        if best_pt is not None:
            return (best_pt[0], best_pt[1], "edge")
    return None


def resolve_snap(
    cx: float,
    cy: float,
    wx: float,
    wy: float,
    *,
    allow_polyline: bool,
    allow_grid: bool,
    grid_snap_enabled: bool,
    grid_spacing: float,
    polylines: Sequence[Polyline],
    hidden_polys: set[int],
    scale: float,
    w2c: WorldToCanvas,
    c2w: CanvasToWorld,
    poly_bounds: PolylineBounds,
    is_poly_closed: IsPolylineClosed,
    segment_intersection_point: SegmentIntersectionPoint,
    mode: str,
    exclude_vertices: set[tuple[int, int]] | None = None,
    reference_point: Point | None = None,
    draw_points: Sequence[Point] | None = None,
    allow_vertex: bool = True,
    allow_edge: bool = True,
) -> SnapResult | None:
    candidates: list[tuple[float, SnapResult]] = []
    if allow_polyline:
        poly_snap = snap_to_polyline(
            cx,
            cy,
            polylines,
            hidden_polys,
            scale,
            w2c,
            c2w,
            poly_bounds,
            is_poly_closed,
            segment_intersection_point,
            reference_point=reference_point,
            draw_points=draw_points,
            exclude_vertices=exclude_vertices,
            mode=mode,
            allow_vertex=allow_vertex,
            allow_edge=allow_edge,
        )
        if poly_snap is not None:
            sx, sy = w2c(poly_snap[0], poly_snap[1])
            candidates.append((math.hypot(cx - sx, cy - sy), poly_snap))
    if allow_grid and grid_snap_enabled:
        grid_x, grid_y = snap_to_grid(wx, wy, grid_spacing)
        sx, sy = w2c(grid_x, grid_y)
        candidates.append((math.hypot(cx - sx, cy - sy), (grid_x, grid_y, "grid")))
    if not candidates:
        return None

    # Midpoint snaps are often visually desirable while dragging but can be
    # narrowly out-ranked by nearby endpoint snaps. Give midpoint a small
    # preference window so it remains reachable in practice.
    best_dist = min(dist for dist, _ in candidates)
    midpoint_candidates = [(dist, snap) for dist, snap in candidates if snap[2] == "midpoint"]
    if midpoint_candidates:
        mid_dist, mid_snap = min(midpoint_candidates, key=lambda item: item[0])
        if mid_dist <= best_dist + 2.0:
            return mid_snap

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def resolve_drag_snap(
    cx: float,
    cy: float,
    wx: float,
    wy: float,
    *,
    allow_polyline: bool,
    allow_grid: bool,
    grid_snap_enabled: bool,
    grid_spacing: float,
    polylines: Sequence[Polyline],
    hidden_polys: set[int],
    scale: float,
    w2c: WorldToCanvas,
    c2w: CanvasToWorld,
    poly_bounds: PolylineBounds,
    is_poly_closed: IsPolylineClosed,
    segment_intersection_point: SegmentIntersectionPoint,
    mode: str,
    allow_vertex: bool = True,
    allow_edge: bool = True,
    exclude_vertices: set[tuple[int, int]] | None = None,
    exclude_segments: set[tuple[int, int]] | None = None,
    reference_point: Point | None = None,
    draw_points: Sequence[Point] | None = None,
) -> SnapResult | None:
    candidates: list[tuple[float, SnapResult]] = []
    excluded_segments = exclude_segments or set()
    excluded_vertices = set(exclude_vertices or set())

    # If a segment is excluded, also exclude its endpoint vertices from vertex-snap
    # so immediate connected-segment endpoints do not generate drag snap labels.
    for pi, si in excluded_segments:
        if pi < 0 or pi >= len(polylines):
            continue
        poly = polylines[pi]
        n = len(poly)
        if n < 2:
            continue
        closed = is_poly_closed(poly)
        seg_count = n if closed else n - 1
        if seg_count <= 0 or si < 0 or si >= seg_count:
            continue
        excluded_vertices.add((pi, si))
        excluded_vertices.add((pi, (si + 1) % n))

    if allow_polyline:
        cwx, cwy = c2w(cx, cy)
        world_r = (_SNAP_DIST / max(scale, _MIN_SCALE)) * 1.6
        # At high zoom (large scale), world_r shrinks and can become too small to
        # capture large-radius arcs.  Clamp to at least 1.0 world unit so arcs
        # with R > 1000 remain snappable when the user zooms in closely.
        world_r = max(world_r, 1.0)
        candidate_polys = _candidate_polylines(
            polylines,
            hidden_polys,
            cwx=cwx,
            cwy=cwy,
            world_r=world_r,
            poly_bounds=poly_bounds,
        )

        if allow_vertex:
            vertex_snap = find_nearest_vertex_snap(
                cx,
                cy,
                polylines,
                hidden_polys,
                w2c,
                exclude=excluded_vertices,
            )
            if vertex_snap is not None:
                sx, sy = w2c(*vertex_snap)
                candidates.append(
                    (
                        math.hypot(cx - sx, cy - sy),
                        (vertex_snap[0], vertex_snap[1], "vertex"),
                    )
                )

        if allow_vertex:
            best_dist: float = float(_SNAP_DIST)
            best_midpoint: Point | None = None
            for _pi, poly in candidate_polys:
                n = len(poly)
                closed = is_poly_closed(poly)
                seg_count = n if closed else n - 1
                for vi in range(seg_count):
                    if (_pi, vi) in excluded_segments:
                        continue
                    ax, ay = poly[vi]
                    bx, by = poly[(vi + 1) % n]
                    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
                    sx, sy = w2c(mx, my)
                    d = math.hypot(cx - sx, cy - sy)
                    if d < best_dist:
                        best_dist = d
                        best_midpoint = (mx, my)
            if best_midpoint is not None:
                candidates.append(
                    (
                        best_dist,
                        (best_midpoint[0], best_midpoint[1], "midpoint"),
                    )
                )

        if allow_vertex:
            # Same "point family" as vertex/midpoint above — dragging a
            # shape onto a line crossing should snap there too, not just
            # the hover/draw path (resolve_snap already has this).
            best_dist = float(_SNAP_DIST)
            best_intersection: Point | None = None
            segments: list[tuple[Point, Point]] = []
            for _pi, poly in candidate_polys:
                n = len(poly)
                closed = is_poly_closed(poly)
                seg_count = n if closed else n - 1
                for vi in range(seg_count):
                    if (_pi, vi) in excluded_segments:
                        continue
                    segments.append((poly[vi], poly[(vi + 1) % n]))
            for i in range(len(segments)):
                a1, a2 = segments[i]
                for j in range(i + 1, len(segments)):
                    b1, b2 = segments[j]
                    ipt = segment_intersection_point(a1, a2, b1, b2)
                    if ipt is None:
                        continue
                    sx, sy = w2c(*ipt)
                    d = math.hypot(cx - sx, cy - sy)
                    if d < best_dist:
                        best_dist = d
                        best_intersection = ipt
            if best_intersection is not None:
                candidates.append(
                    (
                        best_dist,
                        (best_intersection[0], best_intersection[1], "intersection"),
                    )
                )

        if allow_edge:
            best_dist = float(_SNAP_DIST)
            best_edge: Point | None = None
            for _pi, poly in candidate_polys:
                n = len(poly)
                closed = is_poly_closed(poly)
                seg_count = n if closed else n - 1
                for vi in range(seg_count):
                    if (_pi, vi) in excluded_segments:
                        continue
                    ax, ay = poly[vi]
                    bx, by = poly[(vi + 1) % n]
                    dx, dy = bx - ax, by - ay
                    seg_len_sq = dx * dx + dy * dy
                    if seg_len_sq < 1e-12:
                        continue
                    t = max(0.0, min(1.0, ((cwx - ax) * dx + (cwy - ay) * dy) / seg_len_sq))
                    px, py_ = ax + t * dx, ay + t * dy
                    scx, scy = w2c(px, py_)
                    d = math.hypot(cx - scx, cy - scy)
                    if d < best_dist:
                        best_dist = d
                        best_edge = (px, py_)
            if best_edge is not None:
                candidates.append(
                    (
                        best_dist,
                        (best_edge[0], best_edge[1], "edge"),
                    )
                )
    if allow_grid and grid_snap_enabled:
        grid_x, grid_y = snap_to_grid(wx, wy, grid_spacing)
        sx, sy = w2c(grid_x, grid_y)
        candidates.append((math.hypot(cx - sx, cy - sy), (grid_x, grid_y, "grid")))
    if not candidates:
        return None

    # In edit drag mode, make midpoint snaps easier to acquire: if midpoint is
    # nearly as close as the best candidate, prefer midpoint.
    if mode == "edit":
        midpoint_candidates = [(dist, snap) for dist, snap in candidates if snap[2] == "midpoint"]
        if midpoint_candidates:
            mid_dist, mid_snap = min(midpoint_candidates, key=lambda item: item[0])
            best_dist = min(float(dist) for dist, _ in candidates)
            if mid_dist <= best_dist + 6.0:
                return mid_snap

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
