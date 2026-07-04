"""Composite/image-driven and complex tile generators."""

from __future__ import annotations

import math
from typing import Any, cast

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (
    LineString,  # type: ignore[import-untyped]
    Polygon,
)
from shapely.ops import unary_union  # type: ignore[import-untyped]

from src.backend.generators._shared import (
    _PIL_OK,
    _clip_to_outline,
    _collect_lines,
    _extract_all_rings,
    _hex_verts,
    _PIL_Image,
    merge_and_classify_outlines,
    nested_polygon_region,
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
    """Tile an arbitrary DXF shape across the outline, clipped to it.

    Multiple CLOSED pieces among ``tile_polys`` nest via even-odd rules
    (a ring fully inside another becomes a hole, same convention as the
    main outline's fill region) rather than being drawn as independent
    overlapping solids. Any OPEN piece (e.g. a shape Exploded into
    individual segments, or a deliberately-opened outline) additionally
    acts as a cutout inside the tile — its area is punched out at every
    repetition, instead of being silently dropped for not being a valid
    >=3-point closed ring on its own.
    """
    if not tile_polys:
        return []
    closed_tile_polys, open_tile_cutouts = merge_and_classify_outlines(tile_polys)
    valid_tiles: list[list[tuple[float, float]]] = []
    for tp in closed_tile_polys:
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

            def _place(px: float, py: float) -> tuple[float, float]:
                dx = -(px - t_cx) if flip_row else (px - t_cx)
                dy = py - t_cy
                return (x + dx * ca - dy * sa, y + dx * sa + dy * ca)

            transformed_tiles = [
                [_place(px, py) for px, py in tile_pts] for tile_pts in tile_polys
            ]
            tile_region = nested_polygon_region(transformed_tiles)
            if tile_region is None or tile_region.is_empty:
                x += col_step
                continue

            cutout_shapes: list[Polygon] = []
            for cut_pts in open_tile_cutouts:
                try:
                    cut_shape = Polygon([_place(px, py) for px, py in cut_pts])
                    if cut_shape.is_valid and not cut_shape.is_empty:
                        cutout_shapes.append(cut_shape)
                except (TypeError, ValueError):
                    continue
            if cutout_shapes:
                tile_region = tile_region.difference(unary_union(cutout_shapes))
                if tile_region.is_empty:
                    x += col_step
                    continue

            # Use _extract_all_rings (not _clip_to_outline's own _extract_polys)
            # so a hole punched by nesting/cutouts above is preserved as its
            # own separate closed polyline in the output — _extract_polys only
            # keeps each polygon's EXTERIOR ring, which would silently discard
            # every hole we just took care to compute.
            if not prep.intersects(tile_region):
                x += col_step
                continue
            if prep.contains(tile_region):
                _extract_all_rings(tile_region, result)
            else:
                _extract_all_rings(outline_poly.intersection(tile_region), result)
            x += col_step
        y += row_step
        row += 1
    return result
