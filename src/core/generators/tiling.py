"""Tiling pattern generators (honeycomb, brick, basketweave, etc.)."""

from __future__ import annotations

import math

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]

from src.core.generators._shared import (
    _clip_to_outline,
    _collect_lines,
    _extract_polys,
    _hex_verts,
)


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


def gen_gradient_honeycomb(
    outline_poly, r_min: float, r_max: float, gap: float, angle_deg: float = 0.0
) -> list[list[tuple[float, float]]]:
    """Honeycomb where cell radius interpolates from r_min to r_max along angle_deg."""
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


def gen_diamond_checkering(
    outline_poly, cell_size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Grid of closed diamond (rotated-square) cell outlines clipped to the outline."""
    hs = (cell_size - gap) / 2.0
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
        y += scale_h * 0.5
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


def gen_square_grid(outline_poly, spacing: float) -> list[list[tuple[float, float]]]:
    """Orthogonal grid of horizontal and vertical lines clipped to outline."""
    return (
        gen_diagonal_lines(outline_poly, spacing, 0.0)
        + gen_diagonal_lines(outline_poly, spacing, 90.0)
    )


def gen_triangle_grid(
    outline_poly, size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Equilateral triangle tessellation clipped to the outline."""
    if size <= 0:
        return []
    h = size * math.sqrt(3) / 2.0
    col_step = size + gap
    row_step = h + gap * math.sqrt(3) / 2.0
    if col_step <= 0 or row_step <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = size * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            s2 = (size - gap) / 2.0
            h2 = s2 * math.sqrt(3)
            if h2 > 0 and s2 > 0:
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
