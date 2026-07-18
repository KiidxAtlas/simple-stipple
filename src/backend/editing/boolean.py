"""Pure boolean operations on closed polylines."""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

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
    if operation == "union":
        geometry = unary_union(shapes)
    elif operation == "subtract":
        geometry = shapes[0]
        for shape in shapes[1:]:
            geometry = geometry.difference(shape)
    elif operation == "intersect":
        geometry = shapes[0]
        for shape in shapes[1:]:
            geometry = geometry.intersection(shape)
    elif operation == "divide":
        geometry = MultiPolygon(list(polygonize(unary_union([shape.boundary for shape in shapes]))))
    else:
        raise ValueError(f"Unsupported boolean operation: {operation}")
    return _rings(geometry)
