"""Pattern generators — return lists of polyline coord-lists.

Each generator clips its output to the provided Shapely outline polygon.
"""

from __future__ import annotations

import math
import random

from scipy.stats.qmc import PoissonDisk  # type: ignore[import-untyped]
from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import voronoi_diagram  # type: ignore[import-untyped]

try:
    from PIL import Image as _PIL_Image  # type: ignore[import-untyped]

    _PIL_OK = True
except ImportError:
    _PIL_OK = False


# ── Internal helpers ──────────────────────────────────────────────────────────


def _hex_verts(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.pi / 6 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _clip_to_outline(
    shape: Polygon,
    outline_poly,
    prep,
    result: list[list[tuple[float, float]]],
    *,
    shrink: float = 0.0,
) -> None:
    """Clip a polygon to the outline boundary and append valid pieces to result.

    This consolidates the intersect→contains→clip pattern used across all
    grid-based generators.  When *shrink* > 0 the shape is inset by that
    amount before clipping (used for gap between tiles).
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


def _collect_lines(geom, out: list) -> None:
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        c = list(geom.coords)
        if len(c) >= 2:
            out.append(c)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            _collect_lines(g, out)


def apply_interlace(
    polylines: list[list[tuple[float, float]]], spacing: float = 1.0
) -> list[list[tuple[float, float]]]:
    """Apply interlacing offset to pattern polylines.

    Partitions polylines into rows based on Y-coordinate and offsets alternating
    rows horizontally by spacing/2, creating a tessellating interlaced effect.
    """
    if not polylines or spacing <= 0:
        return polylines

    # Get Y bounds to define row spacing
    all_y = []
    for poly in polylines:
        for x, y in poly:
            all_y.append(y)

    if not all_y:
        return polylines

    min_y = min(all_y)
    max_y = max(all_y)
    y_range = max_y - min_y
    if y_range < 1e-6:
        return polylines

    # Group polylines into rows based on Y-coordinate
    row_height = spacing
    n_rows = max(1, int(y_range / row_height) + 1)

    result = []
    for poly in polylines:
        # Determine which row this polyline belongs to (use median Y)
        poly_y = sum(y for x, y in poly) / len(poly) if poly else min_y
        row_idx = int((poly_y - min_y) / row_height)

        # Apply offset for odd rows
        if row_idx % 2 == 1:
            offset_x = spacing / 2.0
            offset_poly = [(x + offset_x, y) for x, y in poly]
            result.append(offset_poly)
        else:
            result.append(poly)

    return result


# ── Public generators ─────────────────────────────────────────────────────────


def gen_honeycomb(
    outline_poly, r: float, gap: float
) -> list[list[tuple[float, float]]]:
    if r <= 0:
        return []
    col_step = 2.0 * (math.sqrt(3) / 2.0 * r) + gap
    row_step = 1.5 * r + gap * math.sqrt(3) / 2.0
    if col_step <= 0 or row_step <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = r * 2.0
    nc = int((maxx - minx + pad * 2) / col_step) + 2
    nr = int((maxy - miny + pad * 2) / row_step) + 2
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    for row in range(nr):
        for col in range(nc):
            off = col_step / 2.0 if row & 1 else 0.0
            cx = minx - pad + col * col_step + off
            cy = miny - pad + row * row_step
            verts = _hex_verts(cx, cy, r)
            _clip_to_outline(Polygon(verts), outline_poly, prep, result)
    return result


def gen_diamond_checkering(
    outline_poly, cell_size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Grid of closed diamond (rotated-square) cell outlines clipped to the outline.

    Each diamond is a square rotated 45° with its bounding box cell_size × cell_size.
    gap controls the space between adjacent diamonds.
    """
    hs = (cell_size - gap) / 2.0  # half-span from centre to tip along each axis
    if hs <= 0:
        return []
    step = cell_size
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = cell_size * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            # Diamond vertices: top, right, bottom, left
            verts = [
                (x, y + hs),
                (x + hs, y),
                (x, y - hs),
                (x - hs, y),
            ]
            _clip_to_outline(Polygon(verts), outline_poly, prep, result)
            x += step
        y += step
    return result


def gen_fish_scale(
    outline_poly, scale_w: float, scale_h: float, n_pts: int = 24
) -> list[list[tuple[float, float]]]:
    """Overlapping half-ellipse arcs forming a scallop / fish-scale pattern."""
    if scale_w <= 0 or scale_h <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = max(scale_w, scale_h) * 1.5
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        offset = scale_w / 2.0 if row & 1 else 0.0
        x = minx - pad + offset
        while x <= maxx + pad:
            pts = [
                (
                    x + (scale_w / 2.0) * math.cos(math.pi - math.pi * i / n_pts),
                    y + scale_h * math.sin(math.pi * i / n_pts),
                )
                for i in range(n_pts + 1)
            ]
            _collect_lines(outline_poly.intersection(LineString(pts)), result)
            x += scale_w
        y += scale_h
        row += 1
    return result


def gen_stipple_dots(
    outline_poly, radius: float, spacing: float
) -> list[list[tuple[float, float]]]:
    """Poisson-Disk sampled filled circles clipped to the outline.

    Uses ``scipy.stats.qmc.PoissonDisk`` to generate a blue-noise distribution
    of centre points — each pair of dots is guaranteed to be at least
    ``spacing`` apart.  This eliminates the grid artifacts and clumping that
    occur with the previous jittered-grid strategy.
    """
    if radius <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    # PoissonDisk works in the [0, 1]^2 unit hypercube; we scale radius
    # proportionally to the shorter dimension.
    scale = max(w, h)
    r_scaled = spacing / scale

    engine = PoissonDisk(d=2, radius=r_scaled, seed=42)
    # Request enough candidates to fill the bounding box densely.
    n_candidates = max(64, int(w * h / (spacing**2) * 4))
    samples = engine.random(n_candidates)  # shape (n, 2) in [0, 1)

    # Map unit-square samples back to world coordinates.
    centres_world = [(minx + s[0] * w, miny + s[1] * h) for s in samples]

    prep = prepared.prep(outline_poly)
    n_seg = 32  # circle approximation quality
    result: list[list[tuple[float, float]]] = []

    for cx, cy in centres_world:
        pts = [
            (
                cx + radius * math.cos(2 * math.pi * i / n_seg),
                cy + radius * math.sin(2 * math.pi * i / n_seg),
            )
            for i in range(n_seg)
        ]
        pts.append(pts[0])
        circ = Polygon(pts)
        if not prep.intersects(circ):
            continue
        clipped = outline_poly.intersection(circ)
        if clipped.is_empty:
            continue
        geoms = (
            [clipped]
            if isinstance(clipped, Polygon)
            else list(clipped.geoms)
            if isinstance(clipped, MultiPolygon)
            else []
        )
        for g in geoms:
            if not g.is_empty and g.area >= radius * 0.05:
                result.append(list(g.exterior.coords))
    return result


def gen_stipple_interlaced(
    outline_poly, radius: float, spacing: float
) -> list[list[tuple[float, float]]]:
    """Interlaced (offset grid) filled circles clipped to the outline.

    Arranges circles in rows where every other row is offset horizontally by
    half the spacing, creating a brick-like or honeycomb visual effect.
    This produces a regular, predictable pattern ideal for tessellation.
    """
    if radius <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    n_seg = 32  # circle approximation quality
    result: list[list[tuple[float, float]]] = []

    # Calculate row and column spacing for interlaced grid
    col_spacing = spacing  # horizontal spacing between columns
    row_spacing = (
        spacing * math.sqrt(3) / 2.0
    )  # vertical spacing between rows (for ~equilateral triangle arrangement)

    # Calculate number of rows and columns needed
    n_cols = int((w / col_spacing) + 2)
    n_rows = int((h / row_spacing) + 2)

    # Generate dot centers in interlaced grid
    for row in range(n_rows):
        for col in range(n_cols):
            # X coordinate: base + offset for every other row
            x = minx + col * col_spacing
            if row % 2 == 1:
                x += col_spacing / 2.0

            # Y coordinate
            y = miny + row * row_spacing

            cx, cy = x, y

            # Create circle polygon
            pts = [
                (
                    cx + radius * math.cos(2 * math.pi * i / n_seg),
                    cy + radius * math.sin(2 * math.pi * i / n_seg),
                )
                for i in range(n_seg)
            ]
            pts.append(pts[0])
            circ = Polygon(pts)

            # Clip to outline
            if not prep.intersects(circ):
                continue
            clipped = outline_poly.intersection(circ)
            if clipped.is_empty:
                continue
            geoms = (
                [clipped]
                if isinstance(clipped, Polygon)
                else list(clipped.geoms)
                if isinstance(clipped, MultiPolygon)
                else []
            )
            for g in geoms:
                if not g.is_empty and g.area >= radius * 0.05:
                    result.append(list(g.exterior.coords))

    return result


def gen_gradient_honeycomb(
    outline_poly, r_min: float, r_max: float, gap: float, angle_deg: float = 0.0
) -> list[list[tuple[float, float]]]:
    """Honeycomb where cell radius interpolates from r_min to r_max along angle_deg.
    0°=left(small)→right(large).  90°=bottom→top."""
    if r_min <= 0 and r_max <= 0:
        return []
    if r_min > r_max:
        r_min, r_max = r_max, r_min
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    minx, miny, maxx, maxy = outline_poly.bounds
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    projs = [x * dx + y * dy for x, y in corners]
    p_min, p_max = min(projs), max(projs)
    p_range = max(p_max - p_min, 1e-9)
    r_avg = (r_min + r_max) / 2
    col_step = 2.0 * (math.sqrt(3) / 2.0 * r_avg) + gap
    row_step = 1.5 * r_avg + gap * math.sqrt(3) / 2.0
    if col_step <= 0 or row_step <= 0:
        return []
    pad = r_max * 2.5
    nc = int((maxx - minx + pad * 2) / col_step) + 2
    nr = int((maxy - miny + pad * 2) / row_step) + 2
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    for row in range(nr):
        for col in range(nc):
            off = col_step / 2.0 if row & 1 else 0.0
            cx = minx - pad + col * col_step + off
            cy = miny - pad + row * row_step
            t = max(0.0, min(1.0, (cx * dx + cy * dy - p_min) / p_range))
            r = r_min + t * (r_max - r_min)
            if r < 0.05:
                continue
            verts = _hex_verts(cx, cy, r)
            hp = Polygon(verts)
            _clip_to_outline(hp, outline_poly, prep, result)
    return result


def gen_image_halftone(
    outline_poly,
    image_path: str,
    r_min: float,
    r_max: float,
    spacing: float,
    invert: bool = False,
) -> list[list[tuple[float, float]]]:
    """Map image brightness to hex cell radius tiled across the outline.
    Dark pixels → large cells, light pixels → small cells (swap with invert=True)."""
    if not _PIL_OK:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")
    if spacing <= 0 or r_max <= 0:
        return []
    img = _PIL_Image.open(image_path).convert("L")
    img_w, img_h = img.size
    pix = img.load()
    minx, miny, maxx, maxy = outline_poly.bounds
    col_step = spacing
    row_step = spacing * math.sqrt(3) / 2.0
    pad = r_max
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        off = col_step / 2.0 if row & 1 else 0.0
        x = minx - pad + off
        while x <= maxx + pad:
            tx = (x - minx) / max(maxx - minx, 1e-9)
            ty = 1.0 - (y - miny) / max(maxy - miny, 1e-9)
            px = int(max(0, min(img_w - 1, tx * (img_w - 1))))
            py = int(max(0, min(img_h - 1, ty * (img_h - 1))))
            brightness = pix[px, py] / 255.0
            if invert:
                brightness = 1.0 - brightness
            # dark (0) → r_max, light (1) → r_min
            r = r_max - brightness * (r_max - r_min)
            if r >= r_min * 0.3:
                hp = Polygon(_hex_verts(x, y, r))
                _clip_to_outline(hp, outline_poly, prep, result)
            x += col_step
        y += row_step
        row += 1
    return result


def gen_custom_tile(
    outline_poly,
    tile_polys: list[list[tuple[float, float]]],
    gap: float,
    angle_deg: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Tile an arbitrary DXF shape across the outline, clipped to it.
    Tiles are offset in brickwork rows. angle_deg rotates each instance."""
    all_pts = [pt for p in tile_polys for pt in p]
    if not all_pts or not tile_polys:
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
    row_step = max(th + gap, 0.01)
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = max(tw, th) * 2.0 + gap
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        off = col_step / 2.0 if row & 1 else 0.0
        x = minx - pad + off
        while x <= maxx + pad:
            for tile_pts in tile_polys:
                if len(tile_pts) < 3:
                    continue
                transformed = [
                    (
                        x + (px - t_cx) * ca - (py - t_cy) * sa,
                        y + (px - t_cx) * sa + (py - t_cy) * ca,
                    )
                    for px, py in tile_pts
                ]
                try:
                    shape = Polygon(transformed)
                    if not shape.is_valid or shape.is_empty:
                        continue
                except Exception:
                    continue
                _clip_to_outline(shape, outline_poly, prep, result)
            x += col_step
        y += row_step
        row += 1
    return result


def gen_brick(
    outline_poly, brick_w: float, brick_h: float, gap: float
) -> list[list[tuple[float, float]]]:
    """Staggered rectangular bricks clipped to the outline."""
    if brick_w <= 0 or brick_h <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = max(brick_w, brick_h) * 2.0
    col_step = brick_w + gap
    row_step = brick_h + gap
    if col_step <= 0 or row_step <= 0:
        return []
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        offset = col_step / 2.0 if row & 1 else 0.0
        x = minx - pad + offset
        while x <= maxx + pad:
            hw, hh = brick_w / 2.0, brick_h / 2.0
            verts = [
                (x - hw, y - hh),
                (x + hw, y - hh),
                (x + hw, y + hh),
                (x - hw, y + hh),
            ]
            bp = Polygon(verts)
            _clip_to_outline(bp, outline_poly, prep, result)
            x += col_step
        y += row_step
        row += 1
    return result


def gen_basketweave(
    outline_poly, strip_w: float, strip_l: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Classic basketweave strips in a repeating 2×2 module clipped to the outline."""
    if strip_w <= 0 or strip_l <= 0 or gap < 0:
        return []
    module_step = strip_l + strip_w + gap * 2.0
    if module_step <= 0:
        return []
    inset = gap / 2.0
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = module_step + max(strip_l, strip_w)
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            left = x + inset
            right = x + module_step - inset
            bottom = y + inset
            top = y + module_step - inset
            rects = [
                [
                    (left, top - strip_w),
                    (left + strip_l, top - strip_w),
                    (left + strip_l, top),
                    (left, top),
                ],
                [
                    (right - strip_w, top - strip_l),
                    (right, top - strip_l),
                    (right, top),
                    (right - strip_w, top),
                ],
                [
                    (left, bottom),
                    (left + strip_w, bottom),
                    (left + strip_w, bottom + strip_l),
                    (left, bottom + strip_l),
                ],
                [
                    (right - strip_l, bottom),
                    (right, bottom),
                    (right, bottom + strip_w),
                    (right - strip_l, bottom + strip_w),
                ],
            ]
            for verts in rects:
                rect_poly = Polygon(verts)
                if not prep.intersects(rect_poly):
                    continue
                if prep.contains(rect_poly):
                    result.append(verts + [verts[0]])
                    continue
                _extract_polys(outline_poly.intersection(rect_poly), result)
            x += module_step
        y += module_step
    return result


def gen_diagonal_lines(
    outline_poly, spacing: float, angle_deg: float = 45.0
) -> list[list[tuple[float, float]]]:
    """Single family of evenly-spaced parallel lines at angle_deg, clipped to outline."""
    if spacing <= 0:
        return []
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    minx, miny, maxx, maxy = outline_poly.bounds
    diag = math.hypot(maxx - minx, maxy - miny) + spacing * 4
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    projs = [x * nx + y * ny for x, y in corners]
    p = min(projs) - spacing
    p_max = max(projs) + spacing
    result: list[list[tuple[float, float]]] = []
    while p <= p_max:
        ox, oy = p * nx, p * ny
        ln = LineString([
            (ox - dx * diag, oy - dy * diag),
            (ox + dx * diag, oy + dy * diag),
        ])
        _collect_lines(outline_poly.intersection(ln), result)
        p += spacing
    return result


def gen_sunburst(outline_poly, spacing_deg: float) -> list[list[tuple[float, float]]]:
    """Lines radiating through the bounding-box centre, clipped to outline.

    spacing_deg is the angular step between each through-line.  E.g. 5.0 gives
    36 full-diameter spokes (72 rays).  Each line passes through the centre in
    both directions so a single increment covers two opposing rays.
    """
    if spacing_deg <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    diag = math.hypot(maxx - minx, maxy - miny)
    n = max(1, round(180.0 / spacing_deg))
    result: list[list[tuple[float, float]]] = []
    for i in range(n):
        a = math.radians(i * 180.0 / n)
        sdx, sdy = math.cos(a), math.sin(a)
        ln = LineString([
            (cx - sdx * diag, cy - sdy * diag),
            (cx + sdx * diag, cy + sdy * diag),
        ])
        _collect_lines(outline_poly.intersection(ln), result)
    return result


def gen_concentric_rings(
    outline_poly, spacing: float, n_seg: int = 72
) -> list[list[tuple[float, float]]]:
    """Concentric circles radiating from the bounding-box centre, clipped to outline.

    n_seg controls tessellation quality of each ring (higher = smoother circles).
    """
    if spacing <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    # Radius must reach farthest corner from the centre
    max_r = math.hypot(maxx - minx, maxy - miny) / 2.0 + spacing
    result: list[list[tuple[float, float]]] = []
    r = spacing
    while r <= max_r:
        ring = Point(cx, cy).buffer(r, resolution=n_seg // 4).exterior
        _collect_lines(outline_poly.intersection(ring), result)
        r += spacing
    return result


def gen_wave_fill(
    outline_poly, spacing: float, amplitude: float, wavelength: float
) -> list[list[tuple[float, float]]]:
    """Parallel horizontal sine-wave lines clipped to the outline.

    spacing    — row-to-row distance (mm)
    amplitude  — wave height above/below the baseline (mm)
    wavelength — horizontal distance per full cycle (mm)
    """
    if spacing <= 0 or wavelength <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    width = (maxx - minx) + wavelength * 4
    # 40 sample points per wavelength keeps curves smooth without exploding point count
    n_pts = max(4, int(width / max(wavelength, 1e-6) * 40))
    result: list[list[tuple[float, float]]] = []
    y = miny + spacing / 2.0
    while y <= maxy + spacing / 2.0:
        x0 = minx - wavelength * 2
        pts = [
            (
                x0 + i * width / n_pts,
                y
                + amplitude
                * math.sin(2.0 * math.pi * (x0 + i * width / n_pts) / wavelength),
            )
            for i in range(n_pts + 1)
        ]
        _collect_lines(outline_poly.intersection(LineString(pts)), result)
        y += spacing
    return result


def gen_square_grid(outline_poly, spacing: float) -> list[list[tuple[float, float]]]:
    """Orthogonal grid of horizontal and vertical lines clipped to outline."""
    return (
        gen_diagonal_lines(outline_poly, spacing, 0.0)  # horizontal
        + gen_diagonal_lines(outline_poly, spacing, 90.0)  # vertical
    )


def gen_voronoi(
    outline_poly, n_cells: int, gap: float = 0.1, seed: int = 42
) -> list[list[tuple[float, float]]]:
    """Random-seed Voronoi cells clipped to the outline.

    n_cells  — approximate number of cells (actual may vary slightly due to clipping)
    gap      — stroke gap around each cell edge (cell is shrunk by gap/2 before output)
    seed     — random seed for reproducible layouts
    """
    if n_cells < 2:
        return []
    rng = random.Random(seed)
    minx, miny, maxx, maxy = outline_poly.bounds
    # Seed points inside the bounding box; Voronoi is clipped to outline afterwards
    pts = [
        Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy)) for _ in range(n_cells)
    ]
    mp = MultiPoint(pts)
    envelope = outline_poly.convex_hull.buffer(max(maxx - minx, maxy - miny) * 0.1)
    try:
        diagram = voronoi_diagram(mp, envelope=envelope)
    except Exception:
        return []
    result: list[list[tuple[float, float]]] = []
    shrink = gap / 2.0
    for cell in diagram.geoms:
        clipped = outline_poly.intersection(cell)
        if clipped.is_empty:
            continue
        geoms = (
            [clipped]
            if isinstance(clipped, Polygon)
            else list(clipped.geoms)
            if isinstance(clipped, MultiPolygon)
            else []
        )
        for g in geoms:
            if g.is_empty or g.area < 0.001:
                continue
            shrunk = g.buffer(-shrink) if shrink > 0 else g
            if shrunk is None or shrunk.is_empty:
                continue
            if isinstance(shrunk, Polygon):
                result.append(list(shrunk.exterior.coords))
            elif isinstance(shrunk, MultiPolygon):
                for s in shrunk.geoms:
                    if not s.is_empty and s.area >= 0.001:
                        result.append(list(s.exterior.coords))
    return result


def gen_triangle_grid(
    outline_poly, size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Equilateral triangle tessellation clipped to the outline.

    size  — side length of each triangle (mm)
    gap   — space between adjacent triangles (mm)
    """
    if size <= 0:
        return []
    h = size * math.sqrt(3) / 2.0  # height of equilateral triangle
    col_step = size + gap
    row_step = h + gap * math.sqrt(3) / 2.0
    if col_step <= 0 or row_step <= 0:
        return []
    half_gap = gap / 2.0
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = size * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            # Two triangles per cell: upward and downward
            # Upward triangle: apex at top
            s2 = (size - gap) / 2.0  # half-base after gap
            h2 = s2 * math.sqrt(3)  # corresponding height
            if h2 > 0 and s2 > 0:
                # Upward ▲ centred at (x, y)
                up = [
                    (x, y + h2 * 2.0 / 3.0),
                    (x + s2, y - h2 / 3.0),
                    (x - s2, y - h2 / 3.0),
                ]
                tp = Polygon(up)
                if prep.intersects(tp):
                    if prep.contains(tp):
                        result.append(up + [up[0]])
                    else:
                        clipped = outline_poly.intersection(tp)
                        _extract_polys(clipped, result)
                # Downward ▽ — offset half a column_step to the right and y same row
                dx_off = col_step / 2.0
                dn = [
                    (x + dx_off, y - h2 * 2.0 / 3.0),
                    (x + dx_off - s2, y + h2 / 3.0),
                    (x + dx_off + s2, y + h2 / 3.0),
                ]
                tp2 = Polygon(dn)
                if prep.intersects(tp2):
                    if prep.contains(tp2):
                        result.append(dn + [dn[0]])
                    else:
                        clipped2 = outline_poly.intersection(tp2)
                        _extract_polys(clipped2, result)
            x += col_step
        y += row_step
        row += 1
    return result


def _extract_polys(geom, out: list[list[tuple[float, float]]]) -> None:
    """Append exterior coords of Polygon(s) from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(list(geom.exterior.coords))
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if not g.is_empty and g.area >= 0.001:
                out.append(list(g.exterior.coords))


# ── Penrose Tiling (P2 kite/dart subdivision) ────────────────────────────────


def _penrose_subdivide(
    triangles: list[tuple[int, complex, complex, complex]],
) -> list[tuple[int, complex, complex, complex]]:
    """One step of Robinson triangle subdivision for P2 Penrose tiling.

    Each triangle is (colour, A, B, C) where colour 0 = thin, 1 = thick.
    Returns a new list of smaller Robinson triangles.
    """
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    result: list[tuple[int, complex, complex, complex]] = []
    for colour, A, B, C in triangles:
        if colour == 0:
            # Thin half-kite → subdivide into 1 thin + 1 thick
            P = A + (B - A) / phi
            result.append((0, C, P, B))
            result.append((1, P, C, A))
        else:
            # Thick half-kite → subdivide into 2 thick + 1 thin
            Q = B + (A - B) / phi
            R = B + (C - B) / phi
            result.append((1, Q, R, B))
            result.append((1, R, Q, A))
            result.append((0, R, C, A))
    return result


def gen_penrose_tiling(
    outline_poly, scale: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Aperiodic Penrose P2 kite-and-dart tiling clipped to the outline.

    scale — approximate size of each tile (mm)
    gap   — space between adjacent tiles (mm)
    """
    if scale <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    diag = math.hypot(maxx - minx, maxy - miny) + scale * 2
    centre = complex(cx, cy)

    # Seed: 10 Robinson triangles forming a decagon (sun configuration)
    triangles: list[tuple[int, complex, complex, complex]] = []
    for i in range(10):
        a0 = (2 * i - 1) * math.pi / 10.0
        a1 = (2 * i + 1) * math.pi / 10.0
        B = centre + diag * complex(math.cos(a0), math.sin(a0))
        C = centre + diag * complex(math.cos(a1), math.sin(a1))
        if i % 2 == 0:
            triangles.append((0, centre, B, C))
        else:
            triangles.append((0, centre, C, B))

    # Subdivide until tiles are roughly at the desired scale
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    n_sub = max(1, round(math.log(diag / max(scale, 0.01)) / math.log(phi)))
    n_sub = min(n_sub, 10)  # cap to prevent runaway
    for _ in range(n_sub):
        triangles = _penrose_subdivide(triangles)

    # Pair half-triangles into kites and darts by merging pairs that share
    # an edge. For simplicity, just output each Robinson triangle as a polygon.
    # Merge triangles that share the same (B, C) edge into quadrilaterals.
    # Map each triangle by its (B, C) edge so matching halves can be merged
    # into quadrilaterals (kites/darts).  Track by list index, not id().
    edge_map: dict[tuple[complex, complex], list[int]] = {}
    for idx, tri in enumerate(triangles):
        _colour, _A, B, C = tri
        key = (B, C) if (B.real, B.imag) <= (C.real, C.imag) else (C, B)
        edge_map.setdefault(key, []).append(idx)

    shapes: list[list[tuple[float, float]]] = []
    seen: set[int] = set()

    for _key, tri_indices in edge_map.items():
        if len(tri_indices) == 2:
            i0, i1 = tri_indices
            if i0 in seen or i1 in seen:
                continue
            seen.add(i0)
            seen.add(i1)
            _, A1, B1, C1 = triangles[i0]
            _, A2, B2, C2 = triangles[i1]
            quad = [
                (A1.real, A1.imag),
                (B1.real, B1.imag),
                (A2.real, A2.imag),
                (C1.real, C1.imag),
            ]
            shapes.append(quad)

    # Any un-merged triangles
    for idx, tri in enumerate(triangles):
        if idx not in seen:
            _, A, B, C = tri
            shapes.append([
                (A.real, A.imag),
                (B.real, B.imag),
                (C.real, C.imag),
            ])

    prep = prepared.prep(outline_poly)
    shrink = gap / 2.0
    result: list[list[tuple[float, float]]] = []

    for verts in shapes:
        try:
            shape = Polygon(verts)
            if not shape.is_valid or shape.is_empty or shape.area < 0.0001:
                continue
        except Exception:
            continue
        if not prep.intersects(shape):
            continue
        shrunk = shape.buffer(-shrink) if shrink > 0 else shape
        if shrunk is None or shrunk.is_empty:
            continue
        clipped = outline_poly.intersection(shrunk)
        if clipped.is_empty:
            continue
        _extract_polys(clipped, result)

    return result


# ── Archimedean Spiral ───────────────────────────────────────────────────────


def gen_spiral(
    outline_poly, spacing: float, direction: str = "cw"
) -> list[list[tuple[float, float]]]:
    """A single continuous Archimedean spiral filling the outline from centre.

    spacing   — gap between successive spiral arms (mm)
    direction — "cw" for clockwise, "ccw" for counter-clockwise
    """
    if spacing <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    max_r = math.hypot(maxx - minx, maxy - miny) / 2.0 + spacing
    # Growth rate: spacing per full revolution
    b = spacing / (2.0 * math.pi)
    # Angular step that keeps chord length ~0.3 mm for smoothness
    total_revs = max_r / max(spacing, 1e-9)
    total_angle = total_revs * 2.0 * math.pi
    # Approximate point count: ensure neighbouring points are close
    n_pts = max(100, int(total_angle / 0.05))
    sign = -1.0 if direction == "cw" else 1.0
    pts: list[tuple[float, float]] = []
    for i in range(n_pts + 1):
        theta = i * total_angle / n_pts
        r = b * theta
        if r > max_r:
            break
        x = cx + r * math.cos(sign * theta)
        y = cy + r * math.sin(sign * theta)
        pts.append((x, y))
    if len(pts) < 2:
        return []
    result: list[list[tuple[float, float]]] = []
    _collect_lines(outline_poly.intersection(LineString(pts)), result)
    return result


# ── Celtic Knot ──────────────────────────────────────────────────────────────


def gen_celtic_knot(
    outline_poly, cell_size: float, line_width: float = 1.0, gap: float = 0.2
) -> list[list[tuple[float, float]]]:
    """Interlocking knot/weave pattern on a grid with over-under crossings.

    cell_size  — grid cell size (mm)
    line_width — width of the knot band (mm)
    gap        — gap at crossings to create the over-under illusion (mm)
    """
    if cell_size <= 0 or line_width <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = cell_size * 2.0
    half_w = line_width / 2.0
    n_seg = 12  # points per quarter-arc for smooth curves

    result: list[list[tuple[float, float]]] = []

    # Grid of crossing points
    cols = int((maxx - minx + pad * 2) / cell_size) + 2
    rows = int((maxy - miny + pad * 2) / cell_size) + 2

    # Generate diagonal bands in two directions (NE and NW)
    # Each band is a strip of width line_width following a 45-degree path
    # with rounded turns at the grid boundary.

    # We'll create arcs at each grid intersection that connect diagonals.
    # At each node, two diagonal lines cross. We draw arcs that curve around
    # the node centre, creating the woven/knot look.

    for row in range(rows + 1):
        for col in range(cols + 1):
            nx = minx - pad + col * cell_size
            ny = miny - pad + row * cell_size
            # Determine over-under: checkerboard decides which diagonal is on top
            ne_on_top = (row + col) % 2 == 0

            # NE diagonal arcs (bottom-left to top-right through this node)
            # NW diagonal arcs (top-left to bottom-right through this node)
            half_cell = cell_size / 2.0

            # Arc from SW entry to NE exit
            arc_ne: list[tuple[float, float]] = []
            for i in range(n_seg + 1):
                t = i / n_seg
                angle = math.pi + t * (-math.pi / 2.0)  # 180° to 90°
                ax = nx + half_w * math.cos(angle)
                ay = ny + half_w * math.sin(angle)
                arc_ne.append((ax, ay))

            # Arc from NW entry to SE exit
            arc_nw: list[tuple[float, float]] = []
            for i in range(n_seg + 1):
                t = i / n_seg
                angle = math.pi / 2.0 + t * (-math.pi / 2.0)  # 90° to 0°
                ax = nx + half_w * math.cos(angle)
                ay = ny + half_w * math.sin(angle)
                arc_nw.append((ax, ay))

            # Draw two arcs connecting the diagonals at this node
            # The one "on top" is drawn in full; the one "below" is split with a gap
            if ne_on_top:
                # NE arc: full — draw the SW-to-NE connection
                sw_to_ne = (
                    [
                        (nx - half_cell, ny - half_cell),
                    ]
                    + arc_ne
                    + [
                        (nx + half_cell, ny + half_cell),
                    ]
                )
                _collect_lines(outline_poly.intersection(LineString(sw_to_ne)), result)
                # NW arc: broken (gap in the middle) — NW-to-SE
                mid_idx = n_seg // 2
                gap_pts = max(1, int(n_seg * gap / cell_size))
                start_idx = max(0, mid_idx - gap_pts)
                end_idx = min(n_seg, mid_idx + gap_pts)
                seg1 = [
                    (nx + half_cell, ny - half_cell),
                ] + arc_nw[: start_idx + 1]
                seg2 = arc_nw[end_idx:] + [
                    (nx - half_cell, ny + half_cell),
                ]
                if len(seg1) >= 2:
                    _collect_lines(outline_poly.intersection(LineString(seg1)), result)
                if len(seg2) >= 2:
                    _collect_lines(outline_poly.intersection(LineString(seg2)), result)
            else:
                # NW arc: full
                nw_to_se = (
                    [
                        (nx + half_cell, ny - half_cell),
                    ]
                    + arc_nw
                    + [
                        (nx - half_cell, ny + half_cell),
                    ]
                )
                _collect_lines(outline_poly.intersection(LineString(nw_to_se)), result)
                # NE arc: broken
                mid_idx = n_seg // 2
                gap_pts = max(1, int(n_seg * gap / cell_size))
                start_idx = max(0, mid_idx - gap_pts)
                end_idx = min(n_seg, mid_idx + gap_pts)
                seg1 = [
                    (nx - half_cell, ny - half_cell),
                ] + arc_ne[: start_idx + 1]
                seg2 = arc_ne[end_idx:] + [
                    (nx + half_cell, ny + half_cell),
                ]
                if len(seg1) >= 2:
                    _collect_lines(outline_poly.intersection(LineString(seg1)), result)
                if len(seg2) >= 2:
                    _collect_lines(outline_poly.intersection(LineString(seg2)), result)

    return result


# ── Lissajous ────────────────────────────────────────────────────────────────


def gen_lissajous(
    outline_poly,
    freq_x: int = 3,
    freq_y: int = 2,
    spacing: float = 2.0,
    amplitude: float = 5.0,
) -> list[list[tuple[float, float]]]:
    """Lissajous curve fill — repeated Lissajous figures offset vertically.

    freq_x    — horizontal frequency (integer)
    freq_y    — vertical frequency (integer)
    spacing   — vertical offset between repeated curves (mm)
    amplitude — peak amplitude of the Lissajous figure (mm)
    """
    if spacing <= 0 or amplitude <= 0 or freq_x < 1 or freq_y < 1:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    width = maxx - minx
    # Horizontal amplitude fits the bounding box width
    amp_x = width / 2.0
    amp_y = amplitude
    # Number of sample points per curve — enough for smooth rendering
    n_pts = max(200, (freq_x + freq_y) * 60)
    result: list[list[tuple[float, float]]] = []
    y_offset = miny
    while y_offset <= maxy + spacing:
        pts: list[tuple[float, float]] = []
        for i in range(n_pts + 1):
            t = 2.0 * math.pi * i / n_pts
            x = cx + amp_x * math.sin(freq_x * t)
            y = y_offset + amp_y * math.sin(freq_y * t)
            pts.append((x, y))
        if len(pts) >= 2:
            _collect_lines(outline_poly.intersection(LineString(pts)), result)
        y_offset += spacing + amplitude * 2.0
    return result


# ── Moroccan Zellige (Islamic 8-pointed star) ────────────────────────────────


def gen_moroccan_zellige(
    outline_poly, size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Islamic geometric pattern of 8-pointed stars with cross shapes.

    Constructed from two overlapping squares rotated 45 degrees, creating
    an 8-pointed star at each grid node with cross/kite shapes between.

    size — tile cell size (mm)
    gap  — space between tiles (mm)
    """
    if size <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = size * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    # The pattern repeats on a square grid of side `size`.
    # At each grid node we place an 8-pointed star.
    # The star is formed by the intersection outline of two overlapping squares
    # rotated 45 degrees relative to each other.
    # Star tip length ratio
    r = size / 2.0  # half-cell
    # Inner square has side = size, outer rotated square also has side = size
    # The 8-pointed star vertices alternate between outer tips and inner notches
    s = r * (math.sqrt(2.0) - 1.0)  # distance from centre to inner notch
    shrink = gap / 2.0

    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            # 8-pointed star at (x, y)
            # 8 outer tips at 0, 45, 90, ... degrees
            # 8 inner notch points between each pair of tips
            star_pts: list[tuple[float, float]] = []
            for i in range(8):
                # Outer tip
                angle_tip = i * math.pi / 4.0
                tx = x + r * math.cos(angle_tip)
                ty = y + r * math.sin(angle_tip)
                star_pts.append((tx, ty))
                # Inner notch
                angle_notch = (i + 0.5) * math.pi / 4.0
                nx_pt = x + s * math.cos(angle_notch)
                ny_pt = y + s * math.sin(angle_notch)
                star_pts.append((nx_pt, ny_pt))
            star_pts.append(star_pts[0])  # close

            try:
                star = Polygon(star_pts)
                if not star.is_valid or star.is_empty:
                    x += size
                    continue
                if shrink > 0:
                    star = star.buffer(-shrink)
                    if star is None or star.is_empty:
                        x += size
                        continue
            except Exception:
                x += size
                continue

            if not prep.intersects(star):
                x += size
                continue
            clipped = outline_poly.intersection(star)
            _extract_polys(clipped, result)

            # Cross/kite shapes fill the gaps between stars.
            # They sit at the midpoints between adjacent star centres.
            # Only add the right and bottom crosses to avoid duplicates.
            for dx_off, dy_off in [(size / 2.0, 0.0), (0.0, size / 2.0)]:
                kx = x + dx_off
                ky = y + dy_off
                # The cross shape is a small square rotated 45 degrees
                cross_r = r - s  # approximate radius
                cross_pts: list[tuple[float, float]] = []
                for ci in range(4):
                    ca = ci * math.pi / 2.0 + math.pi / 4.0
                    cross_pts.append((
                        kx + cross_r * math.cos(ca),
                        ky + cross_r * math.sin(ca),
                    ))
                cross_pts.append(cross_pts[0])
                try:
                    cross = Polygon(cross_pts)
                    if not cross.is_valid or cross.is_empty:
                        continue
                    if shrink > 0:
                        cross = cross.buffer(-shrink)
                        if cross is None or cross.is_empty:
                            continue
                except Exception:
                    continue
                if not prep.intersects(cross):
                    continue
                clipped_c = outline_poly.intersection(cross)
                _extract_polys(clipped_c, result)

            x += size
        y += size

    return result


# ── Tri-Weave (interlocking triangular pattern) ────────────────────────────


def gen_tri_weave(
    outline_poly, cell_size: float, stroke_width: float
) -> list[list[tuple[float, float]]]:
    """Triskelion Y-tile tessellation pattern (Escher-style tri-arm interlocking).

    Creates a pattern of three-armed Y-shaped tiles arranged with 6-fold
    rotational symmetry. Each Y-shape has three arms that rotate 120° apart,
    creating a seamless interlocking tessellation.

    cell_size     — approximate size of each Y-tile unit
    stroke_width  — line thickness of the Y-shape arms
    """
    if cell_size <= 0 or stroke_width <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    # Hexagonal grid for triskelion tiling
    # Use hexagonal lattice spacing
    hex_width = cell_size
    hex_height = cell_size * math.sqrt(3) / 2.0

    col_spacing = hex_width * 0.75
    row_spacing = hex_height

    pad = cell_size * 2.0
    n_cols = int((w + pad * 2) / col_spacing) + 2
    n_rows = int((h + pad * 2) / row_spacing) + 2

    def _make_y_tile(cx, cy, radius, rotation=0):
        """Create a three-armed Y-shaped tile centered at (cx, cy).

        Each arm is a rounded wedge shape extending from the center.
        The Y has 3 arms at 120° angles (rotated by 'rotation' degrees).
        """
        arms = []

        # Create 3 arms, each 120° apart
        for arm_idx in range(3):
            arm_angle = arm_idx * (2 * math.pi / 3.0) + math.radians(rotation)

            # Arm extends from center outward
            arm_radius = radius * 0.8
            inner_radius = radius * 0.3

            # Create a wedge shape for this arm
            # The wedge has a small inner circle and tapers outward
            wedge_angle = math.pi / 3.0  # 60° wide wedge

            arm_pts = []

            # Outer arc of the arm
            num_arc_pts = 8
            for i in range(num_arc_pts + 1):
                angle_offset = (i / num_arc_pts - 0.5) * wedge_angle
                pt_angle = arm_angle + angle_offset
                x = cx + arm_radius * math.cos(pt_angle)
                y = cy + arm_radius * math.sin(pt_angle)
                arm_pts.append((x, y))

            # Inner arc (tapers toward center)
            for i in range(num_arc_pts, -1, -1):
                angle_offset = (i / num_arc_pts - 0.5) * wedge_angle
                pt_angle = arm_angle + angle_offset
                x = cx + inner_radius * math.cos(pt_angle)
                y = cy + inner_radius * math.sin(pt_angle)
                arm_pts.append((x, y))

            # Close the arm polygon
            arm_pts.append(arm_pts[0])
            arms.append(arm_pts)

        return arms

    # Generate Y-tiles in hexagonal grid
    for row in range(n_rows):
        for col in range(n_cols):
            # Hexagonal grid position
            x = minx + col * col_spacing
            y = miny + row * row_spacing

            # Offset odd rows
            if row % 2 == 1:
                x += col_spacing / 2.0

            # Create Y-tile with rotation based on position for interlocking effect
            rotation = (col + row) * 60.0  # Rotate by 60° increments
            arms = _make_y_tile(x, y, cell_size / 2.0, rotation)

            # Add each arm as a polygon
            for arm_pts in arms:
                if len(arm_pts) > 2:
                    try:
                        arm_poly = Polygon(arm_pts)
                        if arm_poly.is_valid and not arm_poly.is_empty:
                            _clip_to_outline(arm_poly, outline_poly, prep, result)
                    except Exception:
                        pass

    return result


# ── Topographic (elevation contour lines) ────────────────────────────


def gen_topographic(outline_poly, spacing: float) -> list[list[tuple[float, float]]]:
    """Topographic elevation contour lines pattern.

    Creates concentric contour lines based on distance from the polygon edge,
    simulating elevation levels on a topographic map.

    spacing   — distance between contour lines
    """
    if spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    # Generate contour lines at increasing distances from the outline
    max_distance = math.sqrt(w * w + h * h) / 2.0
    num_contours = max(1, int(max_distance / spacing))

    for contour_idx in range(1, num_contours + 1):
        distance = contour_idx * spacing

        try:
            # Create a buffer inward (negative buffer) to get contour line
            # Use segments_per_quadrant for smoother curves
            contour_line = outline_poly.buffer(-distance, resolution=16)

            if contour_line.is_empty or not contour_line.is_valid:
                continue

            # Extract the exterior ring and any interiors
            if hasattr(contour_line, "exterior"):
                # It's a Polygon
                coords = list(contour_line.exterior.coords)
                if len(coords) > 2:
                    _clip_to_outline(Polygon(coords), outline_poly, prep, result)

                # Add holes (interior rings) if present
                for interior in contour_line.interiors:
                    interior_coords = list(interior.coords)
                    if len(interior_coords) > 2:
                        # Create thin line for the interior
                        line = LineString(interior_coords)
                        buffered = line.buffer(spacing * 0.1, resolution=8)
                        if buffered.is_valid and not buffered.is_empty:
                            _clip_to_outline(buffered, outline_poly, prep, result)
            elif hasattr(contour_line, "geoms"):
                # It's a MultiPolygon or GeometryCollection
                for geom in contour_line.geoms:
                    if hasattr(geom, "exterior"):
                        coords = list(geom.exterior.coords)
                        if len(coords) > 2:
                            _clip_to_outline(
                                Polygon(coords), outline_poly, prep, result
                            )
        except Exception:
            pass

    return result
