"""Curve-based pattern generators (waves, spirals, Celtic knot, Lissajous)."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point  # type: ignore[import-untyped]

from src.core.generators._shared import _collect_lines


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
        ring = Point(cx, cy).buffer(r, resolution=n_seg // 4).exterior
        _collect_lines(outline_poly.intersection(ring), result)
        r += spacing
    return result


def gen_wave_fill(
    outline_poly, spacing: float, amplitude: float, wavelength: float
) -> list[list[tuple[float, float]]]:
    """Parallel horizontal sine-wave lines clipped to the outline."""
    if spacing <= 0 or wavelength <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    width = (maxx - minx) + wavelength * 4
    n_pts = max(4, int(width / max(wavelength, 1e-6) * 40))
    result: list[list[tuple[float, float]]] = []
    y = miny + spacing / 2.0
    while y <= maxy + spacing / 2.0:
        x0 = minx - wavelength * 2
        pts = [
            (
                x0 + i * width / n_pts,
                y + amplitude * math.sin(2.0 * math.pi * (x0 + i * width / n_pts) / wavelength),
            )
            for i in range(n_pts + 1)
        ]
        _collect_lines(outline_poly.intersection(LineString(pts)), result)
        y += spacing
    return result


def gen_spiral(
    outline_poly, spacing: float, direction: str = "cw"
) -> list[list[tuple[float, float]]]:
    """A single continuous Archimedean spiral filling the outline from centre."""
    if spacing <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    max_r = math.hypot(maxx - minx, maxy - miny) / 2.0 + spacing
    b = spacing / (2.0 * math.pi)
    total_revs = max_r / max(spacing, 1e-9)
    total_angle = total_revs * 2.0 * math.pi
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

    result: list[list[tuple[float, float]]] = []

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
                _collect_lines(outline_poly.intersection(LineString([sw, ne])), result)
                if gap_frac > 0:
                    _collect_lines(outline_poly.intersection(LineString([se, nw_gap1])), result)
                    _collect_lines(outline_poly.intersection(LineString([nw_gap2, nw])), result)
                else:
                    _collect_lines(outline_poly.intersection(LineString([se, nw])), result)
            else:
                _collect_lines(outline_poly.intersection(LineString([se, nw])), result)
                if gap_frac > 0:
                    _collect_lines(outline_poly.intersection(LineString([sw, ne_gap1])), result)
                    _collect_lines(outline_poly.intersection(LineString([ne_gap2, ne])), result)
                else:
                    _collect_lines(outline_poly.intersection(LineString([sw, ne])), result)

    return result


def gen_lissajous(
    outline_poly,
    freq_x: int = 3,
    freq_y: int = 2,
    spacing: float = 2.0,
    amplitude: float = 5.0,
) -> list[list[tuple[float, float]]]:
    """Lissajous curve fill — repeated Lissajous figures offset vertically."""
    if spacing <= 0 or amplitude <= 0 or freq_x < 1 or freq_y < 1:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    width = maxx - minx
    amp_x = width / 2.0
    amp_y = amplitude
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
