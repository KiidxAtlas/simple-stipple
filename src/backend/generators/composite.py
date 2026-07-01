"""Composite/image-driven and complex tile generators."""

from __future__ import annotations

import math
from typing import Any, cast

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]

from src.backend.generators._shared import (
    _PIL_OK,
    LOGGER,
    _clip_to_outline,
    _collect_lines,
    _extract_polys,
    _hex_verts,
    _PIL_Image,
)


def gen_braid(
    outline_poly, strip_width: float, spacing: float
) -> list[list[tuple[float, float]]]:
    """Interlocking diagonal weave pattern at ±45° angles.

    Two families of diagonal strips (slope +1 and slope -1) cross over each
    other to form a tessellating braid. ``strip_width`` is the on-screen width
    of each strip; ``spacing`` is the perpendicular gap between adjacent
    strips. The output is a set of polylines clipped to ``outline_poly``.
    """
    if strip_width <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    sqrt2 = math.sqrt(2.0)
    # Perpendicular distance between adjacent strip centres.
    pitch = strip_width + spacing
    # Half-length of each diagonal segment so it always overshoots the bounds.
    half_len = (math.hypot(w, h) + pitch * 4.0) / 2.0
    # Centre of the bounding box — every diagonal is built around this point.
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    # Range of perpendicular offsets needed to cover every corner.
    span = (math.hypot(w, h) / 2.0) + pitch * 2.0

    result: list[list[tuple[float, float]]] = []

    def _emit(slope_sign: int) -> None:
        # Unit direction along the diagonal and its perpendicular.
        dx, dy = 1.0 / sqrt2, slope_sign * 1.0 / sqrt2
        nx, ny = -dy, dx  # 90° rotation
        offset = -span
        while offset <= span:
            for sub in (-strip_width / 2.0, strip_width / 2.0):
                ox = cx + (offset + sub) * nx
                oy = cy + (offset + sub) * ny
                p1 = (ox - dx * half_len, oy - dy * half_len)
                p2 = (ox + dx * half_len, oy + dy * half_len)
                _collect_lines(outline_poly.intersection(LineString([p1, p2])), result)
            offset += pitch

    _emit(+1)
    _emit(-1)
    return result


def gen_image_halftone(
    outline_poly,
    image_path: str,
    r_min: float,
    r_max: float,
    spacing: float,
    invert: bool = False,
) -> list[list[tuple[float, float]]]:
    """Map image brightness to hex cell radius tiled across the outline."""
    if not _PIL_OK:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")
    if spacing <= 0 or r_max <= 0:
        return []
    pil_image = cast(Any, _PIL_Image)
    img = pil_image.open(image_path).convert("L")
    img_w, img_h = img.size
    pix = img.load()
    if pix is None:
        raise RuntimeError("Unable to access image pixel data.")
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
            pix_val = pix[px, py]
            if isinstance(pix_val, tuple):
                pix_val = pix_val[0]
            brightness = float(pix_val) / 255.0
            if invert:
                brightness = 1.0 - brightness
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
    interlock: bool = False,
) -> list[list[tuple[float, float]]]:
    """Tile an arbitrary DXF shape across the outline, clipped to it."""
    if not tile_polys:
        return []
    valid_tiles: list[list[tuple[float, float]]] = []
    for tp in tile_polys:
        if len(tp) < 3:
            continue
        try:
            p = Polygon(tp)
            if p.is_valid and not p.is_empty:
                valid_tiles.append(tp)
        except (TypeError, ValueError):
            pass
    if not valid_tiles:
        return []
    tile_polys = valid_tiles
    all_pts = [pt for p in tile_polys for pt in p]
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
    row_step = max((th * 0.75 + gap) if interlock else (th + gap), 0.01)
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = max(tw, th) * 2.0 + gap
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    row = 0
    y = miny - pad
    while y <= maxy + pad:
        off = col_step / 2.0 if (interlock and (row & 1)) else 0.0
        x = minx - pad + off
        while x <= maxx + pad:
            flip_row = interlock and (row & 1)
            for tile_pts in tile_polys:
                if len(tile_pts) < 3:
                    continue
                transformed = [
                    (
                        x
                        + (
                            (-(px - t_cx) if flip_row else (px - t_cx)) * ca
                            - (py - t_cy) * sa
                        ),
                        y
                        + (
                            (-(px - t_cx) if flip_row else (px - t_cx)) * sa
                            + (py - t_cy) * ca
                        ),
                    )
                    for px, py in tile_pts
                ]
                try:
                    shape = Polygon(transformed)
                    if not shape.is_valid or shape.is_empty:
                        continue
                except (TypeError, ValueError):
                    continue
                _clip_to_outline(shape, outline_poly, prep, result)
            x += col_step
        y += row_step
        row += 1
    return result
