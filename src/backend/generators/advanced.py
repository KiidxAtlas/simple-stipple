"""Advanced pattern generators (Penrose, Hilbert curve, reaction-diffusion)."""

from __future__ import annotations

import math

import numpy as np
from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    MultiPoint,
    Polygon,
)
from shapely.ops import linemerge  # type: ignore[import-untyped]

from src.backend.generators._shared import _collect_lines, _extract_polys

# ── Penrose Tiling (P2 kite/dart subdivision) ────────────────────────────────


def _penrose_subdivide(
    triangles: list[tuple[int, complex, complex, complex]],
) -> list[tuple[int, complex, complex, complex]]:
    """One step of Robinson triangle subdivision for P2 Penrose tiling."""
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    result: list[tuple[int, complex, complex, complex]] = []
    for colour, A, B, C in triangles:
        if colour == 0:
            P = A + (B - A) / phi
            result.append((0, C, P, B))
            result.append((1, P, C, A))
        else:
            Q = B + (A - B) / phi
            R = B + (C - B) / phi
            result.append((1, Q, R, B))
            result.append((1, R, Q, A))
            result.append((0, R, C, A))
    return result


def gen_penrose_tiling(
    outline_poly, scale: float, gap: float = 0.1
) -> list[list[tuple[float, float]]]:
    """Aperiodic Penrose P2 kite-and-dart tiling clipped to the outline."""
    if scale <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    diag = math.hypot(maxx - minx, maxy - miny) + scale * 2
    centre = complex(cx, cy)

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

    phi = (1.0 + math.sqrt(5.0)) / 2.0
    n_sub = max(1, round(math.log(diag / max(scale, 0.01)) / math.log(phi)))
    n_sub = min(n_sub, 10)
    for _ in range(n_sub):
        triangles = _penrose_subdivide(triangles)

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
            if not shape.is_valid:
                shape = MultiPoint([(v[0], v[1]) for v in verts]).convex_hull
                if not isinstance(shape, Polygon):
                    continue
            if shape.is_empty or shape.area < 0.0001:
                continue
        except (TypeError, ValueError):
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


# ── Hilbert Space-Filling Curve ───────────────────────────────────────────────


def gen_hilbert_curve(
    outline_poly,
    order: int = 5,
    margin_mm: float = 1.0,
) -> list[list[tuple[float, float]]]:
    """Generate a Hilbert space-filling curve clipped to the outline."""
    order = max(1, min(int(order), 8))
    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    x0 = minx + max(0.0, margin_mm)
    y0 = miny + max(0.0, margin_mm)
    x1 = maxx - max(0.0, margin_mm)
    y1 = maxy - max(0.0, margin_mm)
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = minx, miny, maxx, maxy

    n = 1 << order

    def _rot(side: int, x: int, y: int, rx: int, ry: int) -> tuple[int, int]:
        if ry == 0:
            if rx == 1:
                x = side - 1 - x
                y = side - 1 - y
            x, y = y, x
        return x, y

    def _d2xy(side: int, d: int) -> tuple[int, int]:
        x = 0
        y = 0
        t = d
        s = 1
        while s < side:
            rx = 1 & (t // 2)
            ry = 1 & (t ^ rx)
            x, y = _rot(s, x, y, rx, ry)
            x += s * rx
            y += s * ry
            t //= 4
            s *= 2
        return x, y

    pts: list[tuple[float, float]] = []
    denom = max(n - 1, 1)
    for d in range(n * n):
        gx, gy = _d2xy(n, d)
        wx = x0 + (x1 - x0) * (gx / denom)
        wy = y0 + (y1 - y0) * (gy / denom)
        pts.append((wx, wy))

    result: list[list[tuple[float, float]]] = []
    _collect_lines(outline_poly.intersection(LineString(pts)), result)
    return result


# ── Reaction-Diffusion (Gray-Scott) ──────────────────────────────────────────

# Named Gray-Scott presets: (feed, kill)
_RD_PRESETS: dict[str, tuple[float, float]] = {
    "labyrinth": (0.0367, 0.0649),
    "spots": (0.035, 0.065),
    "stripes": (0.026, 0.051),
    "maze": (0.029, 0.057),
}


def gen_reaction_diffuse(
    outline_poly,
    cell_mm: float = 0.8,
    iterations: int = 1200,
    threshold: float = 0.22,
    seed: int = 42,
    pattern: str = "labyrinth",
) -> list[list[tuple[float, float]]]:
    """Generate a reaction-diffusion style contour-line pattern (Gray-Scott inspired)."""
    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0 or cell_mm <= 0:
        return []

    nx = max(48, min(320, int(w / cell_mm) + 2))
    ny = max(48, min(320, int(h / cell_mm) + 2))

    rng = np.random.default_rng(int(seed))
    A = np.ones((ny, nx), dtype=np.float64)
    B = np.zeros((ny, nx), dtype=np.float64)

    cy, cx = ny // 2, nx // 2
    r = max(2, min(nx, ny) // 12)
    B[cy - r : cy + r, cx - r : cx + r] = 1.0
    for _ in range(max(8, (nx * ny) // 2500)):
        sy = int(rng.integers(0, ny))
        sx = int(rng.integers(0, nx))
        rr = max(1, r // 3)
        y0, y1 = max(0, sy - rr), min(ny, sy + rr)
        x0, x1 = max(0, sx - rr), min(nx, sx + rr)
        B[y0:y1, x0:x1] = 1.0

    feed, kill = _RD_PRESETS.get(pattern, _RD_PRESETS["labyrinth"])
    dA = 1.0
    dB = 0.5
    dt = 1.0

    iters = max(10, min(int(iterations), 8000))
    for _ in range(iters):
        lapA = (
            -A
            + 0.2
            * (
                np.roll(A, 1, 0)
                + np.roll(A, -1, 0)
                + np.roll(A, 1, 1)
                + np.roll(A, -1, 1)
            )
            + 0.05
            * (
                np.roll(np.roll(A, 1, 0), 1, 1)
                + np.roll(np.roll(A, 1, 0), -1, 1)
                + np.roll(np.roll(A, -1, 0), 1, 1)
                + np.roll(np.roll(A, -1, 0), -1, 1)
            )
        )
        lapB = (
            -B
            + 0.2
            * (
                np.roll(B, 1, 0)
                + np.roll(B, -1, 0)
                + np.roll(B, 1, 1)
                + np.roll(B, -1, 1)
            )
            + 0.05
            * (
                np.roll(np.roll(B, 1, 0), 1, 1)
                + np.roll(np.roll(B, 1, 0), -1, 1)
                + np.roll(np.roll(B, -1, 0), 1, 1)
                + np.roll(np.roll(B, -1, 0), -1, 1)
            )
        )

        AB2 = A * B * B
        A += (dA * lapA - AB2 + feed * (1.0 - A)) * dt
        B += (dB * lapB + AB2 - (kill + feed) * B) * dt
        np.clip(A, 0.0, 1.0, out=A)
        np.clip(B, 0.0, 1.0, out=B)

    t = float(threshold)
    t = max(0.01, min(0.99, t))
    xs = np.linspace(minx, maxx, nx)
    ys = np.linspace(miny, maxy, ny)

    raw_segments: list[list[tuple[float, float]]] = []

    def _interp(p1, p2, v1, v2):
        if abs(v2 - v1) < 1e-12:
            return ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        a = (t - v1) / (v2 - v1)
        a = max(0.0, min(1.0, a))
        return (p1[0] + a * (p2[0] - p1[0]), p1[1] + a * (p2[1] - p1[1]))

    for iy in range(ny - 1):
        y0, y1 = ys[iy], ys[iy + 1]
        for ix in range(nx - 1):
            x0, x1 = xs[ix], xs[ix + 1]

            v00 = B[iy, ix]
            v10 = B[iy, ix + 1]
            v01 = B[iy + 1, ix]
            v11 = B[iy + 1, ix + 1]

            pts = []
            if (v00 - t) * (v10 - t) < 0:
                pts.append(_interp((x0, y0), (x1, y0), v00, v10))
            if (v10 - t) * (v11 - t) < 0:
                pts.append(_interp((x1, y0), (x1, y1), v10, v11))
            if (v01 - t) * (v11 - t) < 0:
                pts.append(_interp((x0, y1), (x1, y1), v01, v11))
            if (v00 - t) * (v01 - t) < 0:
                pts.append(_interp((x0, y0), (x0, y1), v00, v01))

            if len(pts) == 2:
                _collect_lines(
                    outline_poly.intersection(LineString([pts[0], pts[1]])),
                    raw_segments,
                )
            elif len(pts) == 4:
                _collect_lines(
                    outline_poly.intersection(LineString([pts[0], pts[1]])),
                    raw_segments,
                )
                _collect_lines(
                    outline_poly.intersection(LineString([pts[2], pts[3]])),
                    raw_segments,
                )

    if not raw_segments:
        return []

    seg_lines = [LineString(seg) for seg in raw_segments if len(seg) >= 2]
    if not seg_lines:
        return []

    merged = linemerge(seg_lines)
    merged_coords: list[list[tuple[float, float]]] = []
    _collect_lines(merged, merged_coords)

    min_len = max(cell_mm * 0.6, 0.12)
    filtered: list[list[tuple[float, float]]] = []
    for coords in merged_coords:
        if len(coords) < 2:
            continue
        try:
            if LineString(coords).length >= min_len:
                filtered.append(coords)
        except (TypeError, ValueError):
            continue
    return filtered
