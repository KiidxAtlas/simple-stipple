"""Curve-based pattern generators (Celtic knot, concentric rings, sunburst)."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point  # type: ignore[import-untyped]
from shapely.ops import linemerge  # type: ignore[import-untyped]

from src.backend.generators._shared import _collect_lines, _extract_polys


def gen_sunburst(outline_poly, spacing_deg: float) -> list[list[tuple[float, float]]]:
    """Lines radiating through the bounding-box centre, clipped to outline."""
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
    """Concentric circles radiating from the bounding-box centre, clipped to outline."""
    if spacing <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    max_r = math.hypot(maxx - minx, maxy - miny) / 2.0 + spacing
    result: list[list[tuple[float, float]]] = []
    r = spacing
    while r <= max_r:
        ring = Point(cx, cy).buffer(r, quad_segs=n_seg // 4).exterior
        _collect_lines(outline_poly.intersection(ring), result)
        r += spacing
    return result


def gen_celtic_knot(
    outline_poly, cell_size: float, line_width: float = 1.0, gap: float = 0.2
) -> list[list[tuple[float, float]]]:
    """Interlocking knot/weave pattern on a grid with over-under crossings."""
    if cell_size <= 0 or line_width <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    pad = cell_size * 2.0
    diag_len = cell_size * math.sqrt(2)
    gap_frac = min(0.45, gap / max(diag_len, 1e-9))

    result_lines: list[list[tuple[float, float]]] = []

    cols = int((maxx - minx + pad * 2) / cell_size) + 2
    rows = int((maxy - miny + pad * 2) / cell_size) + 2

    for row in range(rows + 1):
        for col in range(cols + 1):
            nx = minx - pad + col * cell_size
            ny = miny - pad + row * cell_size
            ne_on_top = (row + col) % 2 == 0
            half_cell = cell_size / 2.0

            sw = (nx - half_cell, ny - half_cell)
            ne = (nx + half_cell, ny + half_cell)
            se = (nx + half_cell, ny - half_cell)
            nw = (nx - half_cell, ny + half_cell)

            g = half_cell * gap_frac
            ne_gap1 = (nx - g, ny - g)
            ne_gap2 = (nx + g, ny + g)
            nw_gap1 = (nx + g, ny - g)
            nw_gap2 = (nx - g, ny + g)

            if ne_on_top:
                _collect_lines(
                    outline_poly.intersection(LineString([sw, ne])), result_lines
                )
                if gap_frac > 0:
                    _collect_lines(
                        outline_poly.intersection(LineString([se, nw_gap1])),
                        result_lines,
                    )
                    _collect_lines(
                        outline_poly.intersection(LineString([nw_gap2, nw])),
                        result_lines,
                    )
                else:
                    _collect_lines(
                        outline_poly.intersection(LineString([se, nw])), result_lines
                    )
            else:
                _collect_lines(
                    outline_poly.intersection(LineString([se, nw])), result_lines
                )
                if gap_frac > 0:
                    _collect_lines(
                        outline_poly.intersection(LineString([sw, ne_gap1])),
                        result_lines,
                    )
                    _collect_lines(
                        outline_poly.intersection(LineString([ne_gap2, ne])),
                        result_lines,
                    )
                else:
                    _collect_lines(
                        outline_poly.intersection(LineString([sw, ne])), result_lines
                    )

    if not result_lines:
        return []

    merged = linemerge([LineString(seg) for seg in result_lines if len(seg) >= 2])
    merged_lines: list[list[tuple[float, float]]] = []
    _collect_lines(merged, merged_lines)

    if line_width <= 1e-9:
        return merged_lines

    out_polys: list[list[tuple[float, float]]] = []
    half_w = line_width / 2.0
    for seg in merged_lines:
        if len(seg) < 2:
            continue
        try:
            ribbon = LineString(seg).buffer(
                half_w,
                cap_style="flat",
                join_style="mitre",
            )
        except (TypeError, ValueError):
            continue
        if ribbon.is_empty:
            continue
        _extract_polys(outline_poly.intersection(ribbon), out_polys)

    return out_polys if out_polys else merged_lines
