"""Tiling pattern generators (honeycomb, brick, basketweave, etc.)."""

from __future__ import annotations

import math

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import Point, Polygon

from src.backend.pattern._shared import (
    _clip_to_outline,
    _extract_polys,
    _hex_verts,
)
from src.backend.pattern.cancellation import cancellation_checkpoint


def gen_honeycomb(outline_poly, r: float, gap: float) -> list[list[tuple[float, float]]]:
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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


def gen_mesh(
    outline_poly, r: float, spacing: float, *, quality: str = "high"
) -> list[list[tuple[float, float]]]:
    """Regular orthogonal grid of small circles clipped to the outline."""
    if r <= 0 or spacing <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = r * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    y = miny - pad
    while y <= maxy + pad:
        cancellation_checkpoint()
        x = minx - pad
        while x <= maxx + pad:
            quad_segs = {"fast": 4, "balanced": 12}.get(quality, 24)
            circle = Point(x, y).buffer(r, quad_segs=quad_segs)
            _clip_to_outline(circle, outline_poly, prep, result)
            x += spacing
        y += spacing
    return result
