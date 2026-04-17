"""Composite/image-driven and complex tile generators."""

from __future__ import annotations

import math
from typing import Any, cast

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]

from src.core.generators._shared import (
    _PIL_Image,
    _PIL_OK,
    _clip_to_outline,
    _collect_lines,
    _extract_polys,
    _hex_verts,
    LOGGER,
)


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
                except (TypeError, ValueError):
                    continue
                _clip_to_outline(shape, outline_poly, prep, result)
            x += col_step
        y += row_step
        row += 1
    return result


def gen_moroccan_zellige(
    outline_poly, size: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Islamic geometric pattern of 8-pointed stars with cross shapes."""
    if size <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = size * 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    r = size / 2.0
    s = r * (math.sqrt(2.0) - 1.0)
    shrink = gap / 2.0

    y = miny - pad
    while y <= maxy + pad:
        x = minx - pad
        while x <= maxx + pad:
            star_pts: list[tuple[float, float]] = []
            for i in range(8):
                angle_tip = i * math.pi / 4.0
                tx = x + r * math.cos(angle_tip)
                ty = y + r * math.sin(angle_tip)
                star_pts.append((tx, ty))
                angle_notch = (i + 0.5) * math.pi / 4.0
                nx_pt = x + s * math.cos(angle_notch)
                ny_pt = y + s * math.sin(angle_notch)
                star_pts.append((nx_pt, ny_pt))
            star_pts.append(star_pts[0])

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
            except (TypeError, ValueError):
                x += size
                continue

            if not prep.intersects(star):
                x += size
                continue
            clipped = outline_poly.intersection(star)
            _extract_polys(clipped, result)

            for dx_off, dy_off in [(size / 2.0, 0.0), (0.0, size / 2.0)]:
                kx = x + dx_off
                ky = y + dy_off
                cross_r = r - s
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
                except (TypeError, ValueError):
                    continue
                if not prep.intersects(cross):
                    continue
                clipped_c = outline_poly.intersection(cross)
                _extract_polys(clipped_c, result)

            x += size
        y += size

    return result


def gen_tri_weave(
    outline_poly, cell_size: float, stroke_width: float
) -> list[list[tuple[float, float]]]:
    """Triskelion Y-tile tessellation pattern (Escher-style tri-arm interlocking)."""
    if cell_size <= 0 or stroke_width <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    hex_width = cell_size
    hex_height = cell_size * math.sqrt(3) / 2.0
    col_spacing = hex_width * 0.75
    row_spacing = hex_height

    pad = cell_size * 2.0
    n_cols = int((w + pad * 2) / col_spacing) + 2
    n_rows = int((h + pad * 2) / row_spacing) + 2

    def _make_y_tile(cx, cy, radius, rotation: float = 0.0):
        arms = []
        for arm_idx in range(3):
            arm_angle = arm_idx * (2 * math.pi / 3.0) + math.radians(rotation)
            arm_radius = radius * 0.8
            inner_radius = radius * 0.3
            wedge_angle = math.pi / 3.0
            arm_pts = []
            num_arc_pts = 8
            for i in range(num_arc_pts + 1):
                angle_offset = (i / num_arc_pts - 0.5) * wedge_angle
                pt_angle = arm_angle + angle_offset
                x = cx + arm_radius * math.cos(pt_angle)
                y = cy + arm_radius * math.sin(pt_angle)
                arm_pts.append((x, y))
            for i in range(num_arc_pts, -1, -1):
                angle_offset = (i / num_arc_pts - 0.5) * wedge_angle
                pt_angle = arm_angle + angle_offset
                x = cx + inner_radius * math.cos(pt_angle)
                y = cy + inner_radius * math.sin(pt_angle)
                arm_pts.append((x, y))
            arm_pts.append(arm_pts[0])
            arms.append(arm_pts)
        return arms

    for row in range(n_rows):
        for col in range(n_cols):
            x = minx + col * col_spacing
            y = miny + row * row_spacing
            if row % 2 == 1:
                x += col_spacing / 2.0
            rotation = (col + row) * 60.0
            arms = _make_y_tile(x, y, cell_size / 2.0, rotation)

            for arm_pts in arms:
                if len(arm_pts) > 2:
                    try:
                        arm_poly = Polygon(arm_pts)
                        if arm_poly.is_valid and not arm_poly.is_empty:
                            _clip_to_outline(arm_poly, outline_poly, prep, result)
                    except (TypeError, ValueError) as exc:
                        LOGGER.debug("Skipping invalid tri-weave arm polygon: %s", exc)

    return result
