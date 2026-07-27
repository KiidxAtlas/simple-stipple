"""Pure boolean operations on closed polylines."""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

from simple_stipple.engine.editing.clipper_engine import (
    clipper_difference,
    clipper_intersection,
    clipper_union,
)

Point = tuple[float, float]
Polyline = list[Point]


def _rings(geometry) -> list[Polyline]:
    result: list[Polyline] = []
    if geometry.is_empty:
        return result
    if isinstance(geometry, Polygon):
        exterior = [(float(x), float(y)) for x, y in geometry.exterior.coords]
        if len(exterior) >= 4:
            result.append(exterior)
        result.extend(
            ring
            for interior in geometry.interiors
            if len(ring := [(float(x), float(y)) for x, y in interior.coords]) >= 4
        )
    elif isinstance(geometry, (MultiPolygon, GeometryCollection)):
        for child in geometry.geoms:
            result.extend(_rings(child))
    return result


def boolean_polylines(polylines: Iterable[Polyline], operation: str) -> list[Polyline]:
    shapes = []
    for points in polylines:
        if len(points) < 4:
            continue
        shape = Polygon(points).buffer(0)
        if not shape.is_empty:
            shapes.append(shape)
    if len(shapes) < 2:
        return []
    valid_paths = [_rings(shape)[0] for shape in shapes]
    if operation == "union":
        return clipper_union(valid_paths)
    if operation == "subtract":
        return clipper_difference(valid_paths[:1], valid_paths[1:])
    if operation == "intersect":
        result = valid_paths[:1]
        for path in valid_paths[1:]:
            result = clipper_intersection(result, [path])
        return result
    if operation == "divide":
        geometry = MultiPolygon(list(polygonize(unary_union([shape.boundary for shape in shapes]))))
    else:
        raise ValueError(f"Unsupported boolean operation: {operation}")
    return _rings(geometry)
