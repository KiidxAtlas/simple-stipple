"""Organic pattern generators (stipple, voronoi, topographic)."""

from __future__ import annotations

import math
import random
from typing import Any, cast

import numpy as np
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

from src.backend.generators._shared import (
    _clip_to_outline,
    _collect_lines,
    _coords_to_polyline,
    LOGGER,
)


def gen_stipple_dots(
    outline_poly, radius: float, spacing: float
) -> list[list[tuple[float, float]]]:
    """Poisson-Disk sampled filled circles clipped to the outline."""
    if radius <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    scale = max(w, h)
    r_scaled = spacing / scale

    engine = PoissonDisk(d=2, radius=r_scaled, rng=np.random.default_rng(42))
    n_candidates = max(64, int(w * h / (spacing**2) * 4))
    samples = engine.random(n_candidates)

    centres_world = [(minx + s[0] * w, miny + s[1] * h) for s in samples]

    prep = prepared.prep(outline_poly)
    n_seg = 32
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
            if not g.is_empty and g.area >= math.pi * radius * radius * 0.5:
                result.append(_coords_to_polyline(g.exterior.coords))
    return result


def gen_stipple_interlaced(
    outline_poly, radius: float, spacing: float
) -> list[list[tuple[float, float]]]:
    """Interlaced (offset grid) filled circles clipped to the outline."""
    if radius <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    n_seg = 32
    result: list[list[tuple[float, float]]] = []

    col_spacing = spacing
    row_spacing = spacing
    n_cols = int((w / col_spacing) + 2)
    n_rows = int((h / row_spacing) + 2)

    for row in range(n_rows):
        for col in range(n_cols):
            x = minx + col * col_spacing
            if row % 2 == 1:
                x += col_spacing / 2.0
            y = miny + row * row_spacing
            cx, cy = x, y

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
                if not g.is_empty and g.area >= math.pi * radius * radius * 0.5:
                    result.append(_coords_to_polyline(g.exterior.coords))

    return result


def gen_voronoi(
    outline_poly, n_cells: int, gap: float = 0.1, seed: int = 42
) -> list[list[tuple[float, float]]]:
    """Random-seed Voronoi cells clipped to the outline."""
    if n_cells < 2:
        return []
    rng = random.Random(seed)
    minx, miny, maxx, maxy = outline_poly.bounds
    pts = [
        Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy)) for _ in range(n_cells)
    ]
    mp = MultiPoint(pts)
    envelope = outline_poly.convex_hull.buffer(max(maxx - minx, maxy - miny) * 0.1)
    try:
        diagram = voronoi_diagram(mp, envelope=envelope)
    except (TypeError, ValueError, RuntimeError):
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
            if shrink > 0:
                shrunk = g.buffer(-shrink)
                if shrunk is None or shrunk.is_empty:
                    shrunk = g
            else:
                shrunk = g
            if isinstance(shrunk, Polygon):
                result.append(_coords_to_polyline(shrunk.exterior.coords))
            elif hasattr(shrunk, "geoms"):
                for s in cast(Any, shrunk).geoms:
                    if not s.is_empty and s.area >= 0.001:
                        result.append(_coords_to_polyline(s.exterior.coords))
    return result


def gen_topographic(outline_poly, spacing: float) -> list[list[tuple[float, float]]]:
    """Topographic elevation contour lines pattern."""
    if spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    max_distance = math.sqrt(w * w + h * h) / 2.0
    num_contours = max(1, int(max_distance / spacing))

    for contour_idx in range(1, num_contours + 1):
        distance = contour_idx * spacing

        try:
            contour_line = outline_poly.buffer(-distance, resolution=16)

            if contour_line.is_empty or not contour_line.is_valid:
                continue

            if hasattr(contour_line, "exterior"):
                coords = list(contour_line.exterior.coords)
                if len(coords) > 2:
                    _clip_to_outline(Polygon(coords), outline_poly, prep, result)

                for interior in contour_line.interiors:
                    interior_coords = list(interior.coords)
                    if len(interior_coords) > 2:
                        line = LineString(interior_coords)
                        buffered = line.buffer(spacing * 0.1, resolution=8)
                        if buffered.is_valid and not buffered.is_empty:
                            _clip_to_outline(buffered, outline_poly, prep, result)
            elif hasattr(contour_line, "geoms"):
                for geom in contour_line.geoms:
                    if hasattr(geom, "exterior"):
                        coords = list(geom.exterior.coords)
                        if len(coords) > 2:
                            _clip_to_outline(
                                Polygon(coords), outline_poly, prep, result
                            )
        except (TypeError, ValueError, RuntimeError) as exc:
            LOGGER.debug(
                "Skipping topographic contour at distance %.3f: %s", distance, exc
            )

    return result
