"""Shared geometry, topology, custom-tile, and modifier utilities."""

from __future__ import annotations

import logging
import math
from typing import Any

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

from simple_stipple.engine.patterns.cancellation import cancellation_checkpoint

OUTLINE_WELD_TOL = 0.01
LOGGER = logging.getLogger(__name__)


# clipping


def _hex_verts(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.pi / 6 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _coords_to_polyline(coords) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for c in coords:
        try:
            x = float(c[0])
            y = float(c[1])
        except (TypeError, ValueError, IndexError):
            # Skip any malformed coordinate entries
            continue
        out.append((x, y))
    return out


def _extract_polys(geom, out: list[list[tuple[float, float]]]) -> None:
    """Append exterior coords of Polygon(s) from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(_coords_to_polyline(geom.exterior.coords))
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if not g.is_empty and g.area >= 0.001:
                out.append(_coords_to_polyline(g.exterior.coords))


def _collect_lines(geom, out: list) -> None:
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        c = list(geom.coords)
        if len(c) >= 2:
            try:
                out.append([(float(x), float(y)) for x, y in c])
            except (TypeError, ValueError):
                # Fallback: append raw coords if conversion fails
                out.append(c)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            _collect_lines(g, out)


def _clip_to_outline(
    shape: Polygon,
    outline_poly,
    prep,
    result: list[list[tuple[float, float]]],
    *,
    shrink: float = 0.0,
) -> None:
    """Clip a polygon to the outline boundary and append valid pieces to result.

    When *shrink* > 0 the shape is inset by that amount before clipping.
    """
    if not prep.intersects(shape):
        return
    if shrink > 0:
        shape = shape.buffer(-shrink)
        if shape is None or shape.is_empty:
            return
    if prep.contains(shape):
        _extract_polys(shape, result)
        return
    _extract_polys(outline_poly.intersection(shape), result)


def _extract_all_rings(geom, out: list[list[tuple[float, float]]]) -> None:
    """Extract exterior AND interior (hole) rings from a Shapely geometry.

    Unlike _extract_polys which only keeps exterior rings, this function also
    appends the boundary of each hole so callers receive all closed loops that
    describe the full topology of the geometry (needed for negative-space fills).
    """
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(_coords_to_polyline(geom.exterior.coords))
            for interior in geom.interiors:
                pts = _coords_to_polyline(interior.coords)
                if len(pts) >= 3:
                    out.append(pts)
    elif isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        for g in geom.geoms:
            _extract_all_rings(g, out)


# topology


def is_open_polyline(poly: list[tuple[float, float]], tol: float = OUTLINE_WELD_TOL) -> bool:
    """True if ``poly`` is NOT a closed ring (first/last points not within
    ``tol`` of each other, or fewer than 3 points so it can never close)."""
    if len(poly) < 3:
        return True
    return math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) >= tol


def weld_outline_endpoints(
    polys: list[list[tuple[float, float]]], tol: float | None = None
) -> list[list[tuple[float, float]]]:
    """Snap near-coincident endpoints (within ``tol``) across ALL polylines
    to a shared point, so segments drawn by hand (whose shared vertices are
    close but not bit-identical) still reconnect via ``linemerge``. Only
    each polyline's first/last point is touched — interior vertices are
    left alone."""
    if tol is None:
        from simple_stipple.engine.cad.preflight import scale_tolerance

        tol = scale_tolerance(polys)
    endpoints: list[tuple[int, bool, tuple[float, float]]] = []
    for i, p in enumerate(polys):
        if len(p) < 2:
            continue
        endpoints.append((i, True, p[0]))
        endpoints.append((i, False, p[-1]))
    n = len(endpoints)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (_poly_idx, _is_first, (x, y)) in enumerate(endpoints):
        cell = (math.floor(x / tol), math.floor(y / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cell[0] + dx, cell[1] + dy), []):
                    xj, yj = endpoints[j][2]
                    if abs(x - xj) < tol and abs(y - yj) < tol:
                        union(i, j)
        buckets.setdefault(cell, []).append(i)

    clusters: dict[int, list[tuple[float, float]]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(endpoints[i][2])
    rep = {
        r: (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
        for r, pts in clusters.items()
    }

    result = [list(p) for p in polys]
    for i in range(n):
        idx, is_first, _ = endpoints[i]
        new_pt = rep[find(i)]
        if is_first:
            result[idx][0] = new_pt
        else:
            result[idx][-1] = new_pt
    return result


def merge_and_classify_outlines(
    polys: list[list[tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Weld near-coincident endpoints, merge end-to-end-connected pieces
    back into continuous paths, then classify each result as closed or
    open. Returns (closed_polys, open_polys).

    Without this, a shape broken into individual segments (via Explode, or
    just drawn as separate strokes) would be entirely lost: no single small
    piece is closed, and none has enough points to act as a cutout region
    on its own either.
    """
    from shapely.ops import linemerge  # type: ignore[import-untyped]

    welded = weld_outline_endpoints(polys)
    lines = [LineString(p) for p in welded if len(p) >= 2]
    if not lines:
        return [], []
    try:
        # Pass the plain list directly — linemerge() raises on a bare single
        # LineString (it wants a MultiLineString or a sequence of lines), and
        # a single already-closed ring is common enough (any ordinary
        # never-exploded outline) that this must not raise for it.
        merged = linemerge(lines)
    except (ValueError, TypeError):
        merged = None
    merged_geoms = getattr(merged, "geoms", None)
    geoms = (
        list(merged_geoms) if merged_geoms is not None else ([merged] if merged is not None else [])
    )
    closed: list[list[tuple[float, float]]] = []
    open_: list[list[tuple[float, float]]] = []
    for geom in geoms:
        coords = [(float(x), float(y)) for x, y in geom.coords]
        if len(coords) < 2:
            continue
        if len(coords) >= 3 and not is_open_polyline(coords):
            closed.append(coords)
        else:
            open_.append(coords)
    return closed, open_


def nested_polygon_region(polylines: list[list[tuple[float, float]]]):
    """Build a region from CLOSED polylines that respects nesting as holes.

    A polyline fully contained inside another becomes a hole (even-odd
    nesting, the standard SVG/DXF convention) instead of being silently
    merged into a solid region by a plain union — that's why a donut used
    to fill solid through the hole. Returns ``None`` if there are no usable
    closed rings. Shared by the pattern-outline fill region computation
    (``ui/pages/pattern/fill.py::build_fill_region``) and the custom-tile
    generator below, so a nested closed ring means "hole" consistently
    everywhere in the app, not just for the main outline.
    """

    rings: list[Polygon] = []
    for pl in polylines:
        if len(pl) < 3:
            continue
        try:
            poly = Polygon(pl)
        except (TypeError, ValueError):
            continue
        repaired: Any = poly.buffer(0) if not poly.is_valid else poly
        polygon_parts: list[Polygon]
        if isinstance(repaired, Polygon):
            polygon_parts = [repaired]
        elif isinstance(repaired, MultiPolygon):
            polygon_parts = list(repaired.geoms)
        elif isinstance(repaired, GeometryCollection):
            polygon_parts = [part for part in repaired.geoms if isinstance(part, Polygon)]
        else:
            polygon_parts = []
        rings.extend(part for part in polygon_parts if not part.is_empty and part.area > 0)

    if not rings:
        return None

    # Depth-of-nesting via a spatial index (STRtree) rather than an O(n^2)
    # all-pairs comparison — for a large tiled pattern (hundreds/thousands
    # of small rings from repeated tiles), naive all-pairs containment
    # checks took over 10 SECONDS; almost every one of those comparisons
    # was wasted work since a ring can only possibly be "inside" something
    # whose bounding box it falls within. The STRtree prefilters candidates
    # by bounding-box overlap first, so each ring only needs a handful of
    # exact `.contains()` checks against genuinely nearby rings, not every
    # other ring in the whole pattern. Even depth = solid, odd depth = hole
    # (standard even-odd/SVG nesting rule) — this is purely GEOMETRIC
    # nesting, not tied to whether a ring originated from an open shape.
    from shapely import STRtree  # type: ignore[import-untyped]

    tree = STRtree(rings)
    depths = [0] * len(rings)
    for i, p in enumerate(rings):
        rp = p.representative_point()
        for j in tree.query(rp):
            j = int(j)
            if j == i:
                continue
            other = rings[j]
            if other.area > p.area and other.contains(rp):
                depths[i] += 1

    solids = [p for p, d in zip(rings, depths) if d % 2 == 0]
    holes = [p for p, d in zip(rings, depths) if d % 2 == 1]

    if not solids:
        return None

    # Native integer clipping handles the expensive union/difference. Convert
    # its non-overlapping result contours back to Shapely only at the boundary
    # required by downstream line/polygon intersection APIs.
    from simple_stipple.engine.editing.clipper_engine import clipper_difference, clipper_union

    solid_paths = clipper_union(
        [[(float(x), float(y)) for x, y in poly.exterior.coords] for poly in solids]
    )
    result_paths = (
        clipper_difference(
            solid_paths,
            [[(float(x), float(y)) for x, y in poly.exterior.coords] for poly in holes],
        )
        if holes
        else solid_paths
    )
    result_rings = [Polygon(path) for path in result_paths if len(path) >= 4]
    if not result_rings:
        return None

    result_tree = STRtree(result_rings)
    result_depths = [0] * len(result_rings)
    parents: list[list[int]] = [[] for _ in result_rings]
    for i, polygon in enumerate(result_rings):
        point = polygon.representative_point()
        for candidate in result_tree.query(point):
            j = int(candidate)
            if i != j and result_rings[j].area > polygon.area and result_rings[j].contains(point):
                result_depths[i] += 1
                parents[i].append(j)

    output_polygons: list[Polygon] = []
    for i, polygon in enumerate(result_rings):
        if result_depths[i] % 2:
            continue
        interior_paths = [
            list(result_rings[j].exterior.coords)
            for j in range(len(result_rings))
            if result_depths[j] == result_depths[i] + 1 and i in parents[j]
        ]
        output_polygons.append(Polygon(polygon.exterior.coords, interior_paths))

    solid_union = output_polygons[0] if len(output_polygons) == 1 else MultiPolygon(output_polygons)

    if solid_union.is_empty:
        return None
    if isinstance(solid_union, (Polygon, MultiPolygon)):
        return solid_union
    # GeometryCollection fallback: keep only polygonal parts.
    polys = [g for g in getattr(solid_union, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else unary_union(polys)


# ─── Hatch infill (laser-fill) ─────────────────────────────────────────────


HATCH_MODES = ("none", "lines", "crosshatch", "racecar", "concentric")


def _polygon_from_polyline(
    poly: list[tuple[float, float]],
    *,
    force_close: bool = True,
) -> Polygon | None:
    """Build a Shapely polygon from a flattened polyline.

    ``force_close=True`` (default, matches historical behavior for callers
    like the pattern-fade centroid calculation) implicitly closes an open
    ring. Pass ``force_close=False`` to instead return None for a genuinely
    open polyline — used by pattern-cell fill so open strokes aren't
    silently treated as closed regions and filled unexpectedly.
    """
    if not poly or len(poly) < 3:
        return None
    pts = list(poly)
    if pts[0] != pts[-1]:
        if not force_close:
            return None
        pts = pts + [pts[0]]
    try:
        shape = Polygon(pts)
    except (TypeError, ValueError):
        return None
    if not shape.is_valid:
        try:
            shape = shape.buffer(0)
        except (TypeError, ValueError):
            return None
    if shape.is_empty or shape.area <= 1e-9:
        return None
    return shape


# custom tile


def _hatch_lines_for_polygon(
    shape: Polygon,
    spacing: float,
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Return parallel hatch lines clipped to ``shape`` at the given angle."""
    if spacing <= 0 or shape.is_empty:
        return []
    minx, miny, maxx, maxy = shape.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    diag = math.hypot(maxx - minx, maxy - miny) + spacing * 2.0
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx  # perpendicular direction
    half = diag
    n_lines = int(diag / spacing) + 2
    lines: list[list[tuple[float, float]]] = []
    for i in range(-n_lines, n_lines + 1):
        offset = i * spacing
        ox = cx + offset * nx
        oy = cy + offset * ny
        p1 = (ox - dx * half, oy - dy * half)
        p2 = (ox + dx * half, oy + dy * half)
        clipped = shape.intersection(LineString([p1, p2]))
        _collect_lines(clipped, lines)
    return [[(float(x), float(y)) for x, y in ln] for ln in lines]


def _serpentine_connect(
    lines: list[list[tuple[float, float]]],
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Stitch parallel hatch lines into a continuous racecar/zigzag path.

    Lines are sorted along the perpendicular axis, alternate ones are reversed,
    and successive endpoints are joined into a single polyline.
    """
    if not lines:
        return []
    a = math.radians(angle_deg)
    nx, ny = -math.sin(a), math.cos(a)

    def _key(ln: list[tuple[float, float]]) -> float:
        x = (ln[0][0] + ln[-1][0]) / 2.0
        y = (ln[0][1] + ln[-1][1]) / 2.0
        return x * nx + y * ny

    ordered = sorted(lines, key=_key)
    path: list[tuple[float, float]] = []
    flip = False
    for ln in ordered:
        seg = list(reversed(ln)) if flip else list(ln)
        if not path:
            path.extend(seg)
        else:
            # Connector from end of last segment to start of next.
            path.append(seg[0])
            path.extend(seg[1:])
        flip = not flip
    return [path] if len(path) >= 2 else []


def gen_custom_tile(
    outline_poly,
    tile_polys: list[list[tuple[float, float]]],
    gap: float,
    angle_deg: float = 0.0,
    interlock: bool = False,
    *,
    repeat_mode: str = "Straight",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Repeat every source path in an arbitrary motif, clipped to the outline.

    Custom Tile is a linework operation, not a boolean fill-mask operation:
    nested closed paths and open decorative strokes remain independent paths.
    This preserves detailed motifs containing internal geometry instead of
    collapsing them into an exterior plus alternating holes.
    """
    if not tile_polys:
        return []
    motif_paths = [list(path) for path in tile_polys if len(path) >= 2]
    if not motif_paths:
        return []
    all_pts = [pt for path in motif_paths for pt in path]
    if not all_pts:
        return []
    txs = [p[0] for p in all_pts]
    tys = [p[1] for p in all_pts]
    t_cx = (min(txs) + max(txs)) / 2
    t_cy = (min(tys) + max(tys)) / 2
    tw = max(txs) - min(txs)
    th = max(tys) - min(tys)
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    col_step = max(tw + gap, 0.01)
    mode = str(repeat_mode or "Straight").strip().lower().replace("_", " ")
    if interlock and mode == "straight":
        mode = "half drop"  # backward compatibility with saved workspaces
    row_step = max(th + gap, 0.01)
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = max(tw, th) * 2.0 + gap
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    phase_x = float(origin_x or 0.0) % col_step
    phase_y = float(origin_y or 0.0) % row_step
    y = miny - pad + phase_y
    while y <= maxy + pad:
        cancellation_checkpoint()
        if mode == "half drop":
            off = col_step / 2.0 if row & 1 else 0.0
        elif mode == "brick offset":
            off = col_step / 3.0 if row & 1 else 0.0
        else:
            off = 0.0
        x = minx - pad + phase_x + off
        col = 0
        while x <= maxx + pad:
            mirror_x = mode == "mirror rows" and bool(row & 1)
            mirror_y = mode == "mirror columns" and bool(col & 1)
            rotate_180 = mode == "alternate 180°" and bool((row + col) & 1)

            def _place(
                px: float,
                py: float,
                *,
                flip_x: bool = mirror_x,
                flip_y: bool = mirror_y,
                turn: bool = rotate_180,
                origin_x: float = x,
                origin_y: float = y,
            ) -> tuple[float, float]:
                dx = px - t_cx
                dy = py - t_cy
                if flip_x or turn:
                    dx = -dx
                if flip_y or turn:
                    dy = -dy
                return (
                    origin_x + dx * ca - dy * sa,
                    origin_y + dx * sa + dy * ca,
                )

            for source_path in motif_paths:
                transformed = [_place(px, py) for px, py in source_path]
                # Closed motif paths are cells, not merely linework. Clip them
                # as polygonal areas so a cell crossing the outline boundary
                # comes back as a closed ring that pattern-cell fill can use.
                # Open decorative strokes intentionally retain line clipping.
                if len(transformed) >= 4 and transformed[0] == transformed[-1]:
                    try:
                        cell = Polygon(transformed)
                        if not cell.is_valid:
                            cell = cell.buffer(0)
                    except (TypeError, ValueError):
                        cell = None
                    if cell is not None and not cell.is_empty and cell.area > 1e-9:
                        if not prep.intersects(cell):
                            continue
                        if prep.contains(cell):
                            result.append(transformed)
                        else:
                            _extract_polys(outline_poly.intersection(cell), result)
                        continue
                try:
                    line = LineString(transformed)
                except (TypeError, ValueError):
                    continue
                if line.is_empty or not prep.intersects(line):
                    continue
                if prep.contains(line):
                    result.append(transformed)
                else:
                    _collect_lines(outline_poly.intersection(line), result)
            x += col_step
            col += 1
        y += row_step
        row += 1
    return result


# modifiers


def apply_invert_fill(
    polys: list[list[tuple[float, float]]],
    outline_poly: BaseGeometry,
) -> list[list[tuple[float, float]]]:
    """Return the negative space: the outline region NOT covered by *polys*.

    Computes ``outline - union(pattern_shapes)`` and returns all boundary
    rings (exterior + holes) as separate closed polylines. This represents the
    *gaps between* the pattern elements within the outline — effectively
    inverting which parts of the fill area are drawn.
    """

    if not polys or outline_poly is None or outline_poly.is_empty:
        return polys

    shapes = []
    for poly in polys:
        if len(poly) < 3:
            continue
        pts = list(poly)
        if pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        try:
            s = Polygon(pts)
            if not s.is_valid:
                s = s.buffer(0)
            if s.is_valid and not s.is_empty:
                shapes.append(s)
        except (ValueError, TypeError):
            continue

    if not shapes:
        result: list[list[tuple[float, float]]] = []
        _extract_polys(outline_poly, result)
        return result

    try:
        from simple_stipple.engine.editing.clipper_engine import clipper_difference

        outline_rings: list[list[tuple[float, float]]] = []
        _extract_all_rings(outline_poly, outline_rings)
        return clipper_difference(outline_rings, polys)
    except (RuntimeError, ValueError, TypeError):
        return polys


def apply_border_fade(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    fade_width: float,
) -> list[list[tuple[float, float]]]:
    """Thin out pattern elements near the outline boundary.

    Elements whose centroids are within fade_width of the boundary are removed
    with probability proportional to their closeness to the edge (deterministic
    based on position hash, so the result is stable across re-generates).
    """
    if fade_width <= 0 or not polys:
        return polys
    from shapely.geometry import Point

    boundary = outline_poly.boundary
    result = []
    for poly in polys:
        if not poly:
            continue
        # Prefer geometric polygon centroid when possible for irregular shapes.
        try:
            shape = _polygon_from_polyline(poly)
        except (ValueError, TypeError):
            shape = None
        if shape is not None:
            _c = shape.centroid
            if not _c.is_empty:
                cx = float(_c.x)
                cy = float(_c.y)
            else:
                cx = sum(x for x, y in poly) / len(poly)
                cy = sum(y for x, y in poly) / len(poly)
        else:
            # Fallback: arithmetic mean of vertices.
            cx = sum(x for x, y in poly) / len(poly)
            cy = sum(y for x, y in poly) / len(poly)
        dist = Point(cx, cy).distance(boundary)
        if dist >= fade_width:
            result.append(poly)
            continue
        ratio = dist / fade_width
        # Deterministic hash based on rounded integer coordinates so the
        # border-fade pattern is stable across app restarts.
        ck = int(round(cx * 100))
        ck2 = int(round(cy * 100))
        h = ((ck * 73856093 ^ ck2 * 19349663) & 0xFFFF) / 0xFFFF
        if h < ratio:
            result.append(poly)
    return result


def apply_mirror(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    mirror_v: bool = False,
    mirror_h: bool = False,
) -> list[list[tuple[float, float]]]:
    """Mirror pattern elements to enforce symmetry across one or both axes.

    ``mirror_v`` reflects across the vertical centre axis (left ↔ right);
    ``mirror_h`` reflects across the horizontal centre axis (top ↔ bottom).
    Every source polyline is preserved and reflected copies are appended for
    each enabled axis (and the diagonal opposite when both are on). The
    output therefore always *contains* the source pattern plus its mirror
    images — guaranteed to be visibly symmetric even when the source already
    fills both halves of the outline.
    """
    if not mirror_v and not mirror_h:
        return polys
    if not polys:
        return polys

    # Centre of the outline bounding box is the mirror axis origin.
    bounds = outline_poly.bounds
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2

    def _reflect(poly: list[tuple[float, float]], rv: bool, rh: bool) -> list[tuple[float, float]]:
        return [
            (
                2 * cx - x if rv else x,
                2 * cy - y if rh else y,
            )
            for x, y in poly
        ]

    # Prepare outline for clipping mirrored pieces when an outline is provided.
    prep_outline = None
    try:
        if outline_poly is not None and not outline_poly.is_empty:
            prep_outline = prepared.prep(outline_poly)
    except (ValueError, TypeError):
        prep_outline = None

    result: list[list[tuple[float, float]]] = []
    for poly in polys:
        if not poly:
            continue
        # Always keep the source element.
        result.append(poly)
        # For each requested reflection, reflect then clip to outline.
        for rv, rh in ((True, False), (False, True), (True, True)):
            if (rv and not mirror_v) or (rh and not mirror_h):
                continue
            reflected = _reflect(poly, rv, rh)
            if prep_outline is None:
                result.append(reflected)
                continue
            # Closed polygon path handling.
            if len(reflected) >= 3:
                pts = list(reflected)
                if pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                try:
                    shape = Polygon(pts)
                    if not shape.is_valid:
                        shape = shape.buffer(0)
                    if shape.is_empty:
                        continue
                    _clip_to_outline(shape, outline_poly, prep_outline, result)
                except (ValueError, TypeError):
                    result.append(reflected)
            else:
                # Open polyline path handling.
                try:
                    ls = LineString(reflected)
                    if not ls.is_empty and prep_outline.intersects(ls):
                        clipped = outline_poly.intersection(ls)
                        _collect_lines(clipped, result)
                except (ValueError, TypeError):
                    result.append(reflected)
    return result


_INTERLACE_EPSILON = 1e-9


def _interlace_boundaries(
    p1: tuple[float, float],
    p2: tuple[float, float],
    min_y: float,
    row_height: float,
) -> list[float]:
    y_start, y_end = sorted((p1[1], p2[1]))
    first_row = math.ceil((y_start - min_y - _INTERLACE_EPSILON) / row_height)
    last_row = math.floor((y_end - min_y + _INTERLACE_EPSILON) / row_height)
    return [
        boundary
        for row in range(first_row, last_row + 1)
        if y_start < (boundary := min_y + row * row_height) < y_end
    ]


def _split_segment_at_rows(
    p1: tuple[float, float],
    p2: tuple[float, float],
    boundaries: list[float],
) -> list[tuple[float, float]]:
    points = [p1]
    for boundary_y in boundaries:
        fraction = (boundary_y - p1[1]) / (p2[1] - p1[1])
        points.append((p1[0] + fraction * (p2[0] - p1[0]), boundary_y))
    points.append(p2)
    return points


def _line_points(geometry: BaseGeometry) -> list[tuple[float, float]]:
    if geometry.geom_type == "LineString":
        return [(float(x), float(y)) for x, y in geometry.coords]  # type: ignore[attr-defined]
    if isinstance(geometry, MultiLineString):
        return [
            (float(x), float(y))
            for part in geometry.geoms
            for x, y in part.coords
        ]
    if isinstance(geometry, GeometryCollection):
        return [
            point
            for part in geometry.geoms
            if part.geom_type == "LineString" or isinstance(part, MultiLineString)
            for point in _line_points(part)
        ]
    return []


def _clip_interlaced_segment(
    segment: list[tuple[float, float]],
    outline_poly: BaseGeometry,
) -> list[tuple[float, float]]:
    try:
        line = LineString(segment)
        if line.is_empty:
            return []
        clipped = outline_poly.intersection(line)
        return [] if clipped.is_empty else _line_points(clipped)
    except Exception as exc:
        raise ValueError(
            "Pattern interlace clipping failed; no unclipped geometry was emitted."
        ) from exc


def _clean_interlaced_polyline(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not points:
        return []
    cleaned = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > _INTERLACE_EPSILON:
            cleaned.append(point)
    if (
        len(cleaned) >= 3
        and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1])
        < _INTERLACE_EPSILON
    ):
        cleaned.pop()
    return cleaned


def apply_interlace(
    polylines: list[list[tuple[float, float]]],
    outline_poly: BaseGeometry | None = None,
    *,
    spacing: float = 1.0,
) -> list[list[tuple[float, float]]]:
    """Apply interlacing offset to pattern polylines.

    Partitions polylines into rows based on Y-coordinate and offsets alternating
    rows horizontally by ``spacing``/2, creating a tessellating interlaced
    effect. Each shifted shape is clipped back to *outline_poly* so no element
    escapes the outline boundary.
    """
    if not polylines:
        return polylines

    all_y = [y for polyline in polylines for _, y in polyline]
    if not all_y:
        return polylines

    min_y = min(all_y)
    row_height = spacing
    clip_to_outline = outline_poly is not None and not outline_poly.is_empty

    result: list[list[tuple[float, float]]] = []
    for poly in polylines:
        if not poly:
            result.append([])
            continue

        new_poly: list[tuple[float, float]] = []
        for p1, p2 in zip(poly, poly[1:]):
            boundaries = _interlace_boundaries(p1, p2, min_y, row_height)
            sub_points = _split_segment_at_rows(p1, p2, boundaries)
            for s1, s2 in zip(sub_points, sub_points[1:]):
                mid_y = (s1[1] + s2[1]) / 2.0
                row_idx = int((mid_y - min_y) / row_height)
                seg = [s1, s2]
                odd_row = row_idx % 2 == 1
                if odd_row:
                    seg = [(x + spacing / 2.0, y) for x, y in seg]
                if odd_row and clip_to_outline:
                    new_poly.extend(_clip_interlaced_segment(seg, outline_poly))
                    continue
                new_poly.extend(seg)

        result.append(_clean_interlaced_polyline(new_poly))
    return result


# ─── Open/closed outline reconnection ──────────────────────────────────────
#
# Shared by the pattern page's outline handling (services.py) AND the custom-
# tile generator below — a shape "Exploded" into individual 2-point segments,
# or a hand-drawn shape whose edges were drawn as separate strokes, must
# still be recognized as one continuous (closed or open) path, not as a pile
# of disconnected pieces each too small to mean anything on its own.

# Endpoint weld tolerance: far below drawing scale, but real hand-drawn
# segments meant to share a vertex are essentially never bit-for-bit
# identical (unlike a programmatic "Explode", where pieces DO share exact
# coordinates) — without welding first, Shapely's `linemerge()` treats even
# a 0.0001 mm endpoint mismatch as two genuinely separate lines and never
# reconnects them. Mirrors `CanvasView.merge_selected_segments_to_objects`'s
# own `_MERGE_TOL` constant for consistency.
OUTLINE_WELD_TOL = 0.01
