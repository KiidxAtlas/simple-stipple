"""Shared helpers used across all generator sub-modules."""

from __future__ import annotations

import logging
import math
from typing import cast

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    MultiPolygon,
    Polygon,
)

try:
    from PIL import Image as _PIL_Image  # type: ignore[import-untyped]

    _PIL_OK = True
except ImportError:
    _PIL_Image = None
    _PIL_OK = False

LOGGER = logging.getLogger(__name__)


def _hex_verts(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.pi / 6 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _coords_to_polyline(coords) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y, *_ in coords]


def _extract_polys(geom, out: list[list[tuple[float, float]]]) -> None:
    """Append exterior coords of Polygon(s) from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(_coords_to_polyline(geom.exterior.coords))
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if not g.is_empty and g.area >= 0.001:
                out.append(_coords_to_polyline(g.exterior.coords))


def _collect_lines(geom, out: list) -> None:
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        c = list(geom.coords)
        if len(c) >= 2:
            out.append(c)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            _collect_lines(g, out)


def _clip_to_outline(
    shape: Polygon,
    outline_poly,
    prep,
    result: list[list[tuple[float, float]]],
    *,
    shrink: float = 0.0,
) -> None:
    """Clip a polygon to the outline boundary and append valid pieces to result.

    When *shrink* > 0 the shape is inset by that amount before clipping.
    """
    if not prep.intersects(shape):
        return
    if shrink > 0:
        shape = shape.buffer(-shrink)
        if shape is None or shape.is_empty:
            return
    if prep.contains(shape):
        _extract_polys(shape, result)
        return
    _extract_polys(outline_poly.intersection(shape), result)


def apply_invert_fill(outline_poly: Polygon, margin: float = 5.0) -> Polygon:
    """Return the area outside outline_poly within a padded bounding box.

    Inverts the fill region so the pattern covers the background instead of
    the interior of the outline.
    """
    from shapely.geometry import box as shapely_box

    bounds = outline_poly.bounds
    bbox = shapely_box(
        bounds[0] - margin,
        bounds[1] - margin,
        bounds[2] + margin,
        bounds[3] + margin,
    )
    return bbox.difference(outline_poly)


def apply_border_fade(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    fade_width: float,
) -> list[list[tuple[float, float]]]:
    """Thin out pattern elements near the outline boundary.

    Elements whose centroids are within fade_width of the boundary are removed
    with probability proportional to their closeness to the edge (deterministic
    based on position hash, so the result is stable across re-generates).
    """
    if fade_width <= 0 or not polys:
        return polys
    from shapely.geometry import Point

    boundary = outline_poly.boundary
    result = []
    for poly in polys:
        if not poly:
            continue
        cx = sum(x for x, y in poly) / len(poly)
        cy = sum(y for x, y in poly) / len(poly)
        dist = Point(cx, cy).distance(boundary)
        if dist >= fade_width:
            result.append(poly)
            continue
        ratio = dist / fade_width
        h = (hash((round(cx, 2), round(cy, 2))) & 0xFFFF) / 0xFFFF
        if h < ratio:
            result.append(poly)
    return result


def apply_mirror(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    mirror_v: bool = False,
    mirror_h: bool = False,
) -> list[list[tuple[float, float]]]:
    """Mirror pattern elements from one half to the other for symmetric output.

    mirror_v: reflect across the vertical center axis (left <-> right).
    mirror_h: reflect across the horizontal center axis (top <-> bottom).
    Elements in the reference half are kept; mirrored copies fill the other half.
    """
    if not mirror_v and not mirror_h:
        return polys
    bounds = outline_poly.bounds
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    result = []
    for poly in polys:
        if not poly:
            continue
        avg_x = sum(x for x, y in poly) / len(poly)
        avg_y = sum(y for x, y in poly) / len(poly)
        if mirror_v:
            if avg_x <= cx:
                result.append(poly)
                result.append([(2 * cx - x, y) for x, y in poly])
        elif mirror_h:
            if avg_y <= cy:
                result.append(poly)
                result.append([(x, 2 * cy - y) for x, y in poly])
    return result


def apply_interlace(
    polylines: list[list[tuple[float, float]]], spacing: float = 1.0
) -> list[list[tuple[float, float]]]:
    """Apply interlacing offset to pattern polylines.

    Partitions polylines into rows based on Y-coordinate and offsets alternating
    rows horizontally by spacing/2, creating a tessellating interlaced effect.
    """
    if not polylines or spacing <= 0:
        return polylines

    all_y = []
    for poly in polylines:
        for x, y in poly:
            all_y.append(y)

    if not all_y:
        return polylines

    min_y = min(all_y)
    max_y = max(all_y)
    y_range = max_y - min_y
    if y_range < 1e-6:
        return polylines

    row_height = spacing
    result = []
    for poly in polylines:
        # Round to 6 decimal places before the int() cast to prevent floating-point
        # noise from pushing a point exactly on a row boundary into the wrong row.
        poly_y = sum(y for x, y in poly) / len(poly) if poly else min_y
        poly_y = round(poly_y, 6)
        row_idx = int((poly_y - min_y) / row_height)

        if row_idx % 2 == 1:
            offset_x = spacing / 2.0
            result.append([(x + offset_x, y) for x, y in poly])
        else:
            result.append(poly)

    return result
