"""Organic pattern generators (stipple, voronoi, topographic)."""

from __future__ import annotations

import math
import random

import numpy as np
import shapely  # type: ignore[import-untyped]
from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (
    GeometryCollection,
    LineString,  # type: ignore[import-untyped]
    MultiLineString,
    MultiPolygon,
    Polygon,
)

from src.backend.jit import poisson_disk_points, tessellate_circles
from src.backend.pattern._shared import (
    LOGGER,
    _clip_to_outline,
    _coords_to_polyline,
    _extract_polys,
)
from src.backend.pattern.cancellation import cancellation_checkpoint
from src.backend.voronoi import voronoi_diagram


def gen_flow_lines(
    outline_poly,
    spacing: float,
    amplitude: float,
    wavelength: float,
    angle: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Generate smooth, parallel wave-flow lines clipped to an outline."""
    if spacing <= 0 or wavelength <= 0 or outline_poly.is_empty:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    radius = math.hypot(maxx - minx, maxy - miny) * 0.75 + abs(amplitude) + spacing
    radians = math.radians(angle)
    cos_a, sin_a = math.cos(radians), math.sin(radians)

    def _rotate(x: float, y: float) -> tuple[float, float]:
        return (cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)

    samples = max(32, min(1024, int(4.0 * radius / max(wavelength / 16.0, 0.05))))
    result: list[list[tuple[float, float]]] = []
    row = -radius
    while row <= radius + 1e-9:
        cancellation_checkpoint()
        points = []
        for index in range(samples + 1):
            x = -radius + 2.0 * radius * index / samples
            y = row + amplitude * math.sin(2.0 * math.pi * x / wavelength)
            points.append(_rotate(x, y))
        clipped = outline_poly.intersection(LineString(points))
        geoms = (
            list(clipped.geoms)
            if isinstance(clipped, (MultiLineString, GeometryCollection))
            else [clipped]
        )
        for geom in geoms:
            if isinstance(geom, LineString) and geom.length > 1e-8:
                result.append([(float(x), float(y)) for x, y in geom.coords])
        row += spacing
    return result


def gen_stipple_dots(
    outline_poly,
    radius: float,
    spacing: float,
    *,
    seed: int | None = None,
    quality: str = "high",
) -> list[list[tuple[float, float]]]:
    """Poisson-Disk sampled filled circles clipped to the outline.

    ``seed`` controls the RNG used for sampling; when ``None`` a stable
    default is used so renders remain deterministic across runs.
    """
    if radius <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return []

    rng_seed = 42 if seed is None else int(seed)
    centers_array = poisson_disk_points(minx, miny, maxx, maxy, spacing, rng_seed)
    centres_world = [(float(x), float(y)) for x, y in centers_array]

    prep = prepared.prep(outline_poly)
    n_seg = {"fast": 12, "balanced": 24}.get(quality, 48)
    result: list[list[tuple[float, float]]] = []

    circles = tessellate_circles(np.asarray(centres_world), radius, n_seg)
    center_geometries = shapely.points(np.asarray(centres_world))
    fully_inside = np.asarray(shapely.contains(outline_poly, center_geometries)) & (
        np.asarray(shapely.distance(outline_poly.boundary, center_geometries)) >= radius
    )
    for points, contained in zip(circles, fully_inside):
        cancellation_checkpoint()
        if contained:
            result.append([(float(x), float(y)) for x, y in points])
        else:
            result.extend(_circle_segments(points, radius, outline_poly, prep))
    return result


def _circle_segments(
    points,
    radius: float,
    outline_poly,
    prep,
) -> list[list[tuple[float, float]]]:
    """Create a circle polygon at (cx,cy) and clip it to outline_poly.

    Returns the exterior coordinate polylines of all valid clipped pieces.
    """
    pts = [(float(x), float(y)) for x, y in points]
    circ = Polygon(pts)
    if not prep.intersects(circ):
        return []
    clipped = outline_poly.intersection(circ)
    if clipped.is_empty:
        return []
    geoms = (
        [clipped]
        if isinstance(clipped, Polygon)
        else list(clipped.geoms)
        if isinstance(clipped, MultiPolygon)
        else []
    )
    result: list[list[tuple[float, float]]] = []
    for g in geoms:
        if not g.is_empty and g.area >= math.pi * radius * radius * 0.5:
            result.append(_coords_to_polyline(g.exterior.coords))
    return result


def gen_stipple_interlaced(
    outline_poly, radius: float, spacing: float, *, quality: str = "high"
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
    n_seg = {"fast": 12, "balanced": 24}.get(quality, 48)
    result: list[list[tuple[float, float]]] = []

    col_spacing = spacing
    row_spacing = spacing
    n_cols = int((w / col_spacing) + 2)
    n_rows = int((h / row_spacing) + 2)

    centers: list[tuple[float, float]] = []
    for row in range(n_rows):
        cancellation_checkpoint()
        for col in range(n_cols):
            x = minx + col * col_spacing
            if row % 2 == 1:
                x += col_spacing / 2.0
            y = miny + row * row_spacing
            centers.append((x, y))

    circles = tessellate_circles(np.asarray(centers), radius, n_seg)
    center_geometries = shapely.points(np.asarray(centers))
    fully_inside = np.asarray(shapely.contains(outline_poly, center_geometries)) & (
        np.asarray(shapely.distance(outline_poly.boundary, center_geometries)) >= radius
    )
    for points, contained in zip(circles, fully_inside):
        cancellation_checkpoint()
        if contained:
            result.append([(float(x), float(y)) for x, y in points])
        else:
            result.extend(_circle_segments(points, radius, outline_poly, prep))

    return result


def gen_voronoi(
    outline_poly, n_cells: int, gap: float = 0.1, seed: int = 42
) -> list[list[tuple[float, float]]]:
    """Random-seed Voronoi cells clipped to the outline."""
    if n_cells < 2 or not math.isfinite(gap):
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    if not all(math.isfinite(value) for value in (minx, miny, maxx, maxy)):
        return []
    extent = max(maxx - minx, maxy - miny)
    if extent <= 0:
        return []
    rng = random.Random(seed)
    pts = [(rng.uniform(minx, maxx), rng.uniform(miny, maxy)) for _ in range(n_cells)]
    try:
        cells = voronoi_diagram(pts, radius=extent * 4.0)
    except (TypeError, ValueError, RuntimeError):
        return []
    result: list[list[tuple[float, float]]] = []
    shrink = gap / 2.0
    for coordinates in cells:
        cancellation_checkpoint()
        if len(coordinates) < 3:
            continue
        cell = Polygon(coordinates)
        clipped = outline_poly.intersection(cell)
        if clipped.is_empty:
            continue
        if shrink > 0:
            shrunk = clipped.buffer(-shrink)
            if shrunk is None or shrunk.is_empty:
                shrunk = clipped
        else:
            shrunk = clipped
        _extract_polys(shrunk, result)
    return result


def gen_topographic(
    outline_poly, spacing: float, *, quality: str = "high"
) -> list[list[tuple[float, float]]]:
    """Topographic elevation contour lines pattern."""
    if not math.isfinite(spacing) or spacing <= 0:
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
        cancellation_checkpoint()
        distance = contour_idx * spacing

        try:
            quad_segs = {"fast": 4, "balanced": 12}.get(quality, 24)
            contour_line = outline_poly.buffer(-distance, quad_segs=quad_segs)

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
                        buffered = line.buffer(spacing * 0.1, quad_segs=8)
                        if buffered.is_valid and not buffered.is_empty:
                            _clip_to_outline(buffered, outline_poly, prep, result)
            elif hasattr(contour_line, "geoms"):
                for geom in contour_line.geoms:
                    if hasattr(geom, "exterior"):
                        coords = list(geom.exterior.coords)
                        if len(coords) > 2:
                            _clip_to_outline(Polygon(coords), outline_poly, prep, result)
        except (TypeError, ValueError, RuntimeError) as exc:
            LOGGER.debug("Skipping topographic contour at distance %.3f: %s", distance, exc)

    return result
