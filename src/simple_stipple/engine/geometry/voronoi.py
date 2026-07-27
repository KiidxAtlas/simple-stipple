"""Finite 2-D Voronoi cells and Delaunay triangles backed by SciPy."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.spatial import Delaunay, QhullError, Voronoi  # type: ignore[import-untyped]

Point = tuple[float, float]
Polygon = list[Point]


def _array(points: Sequence[Point], minimum: int) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1:] != (2,) or len(array) < minimum:
        raise ValueError(f"At least {minimum} two-dimensional points are required")
    if not np.isfinite(array).all():
        raise ValueError("Points must be finite")
    return array


def voronoi_diagram(points: Sequence[Point], *, radius: float | None = None) -> list[Polygon]:
    """Return one finite polygon per input site, extending infinite ridges."""
    pts = _array(points, 2)
    try:
        vor = Voronoi(pts)
    except QhullError as exc:
        raise ValueError("Voronoi sites must span a two-dimensional region") from exc
    center = pts.mean(axis=0)
    extent = float(np.ptp(pts, axis=0).max())
    far_radius = float(radius) if radius is not None else max(extent * 4.0, 1.0)
    ridges: dict[int, list[tuple[int, int, int]]] = {}
    for (first, second), vertices in zip(vor.ridge_points, vor.ridge_vertices):
        if len(vertices) != 2:
            continue
        first_vertex, second_vertex = int(vertices[0]), int(vertices[1])
        ridges.setdefault(int(first), []).append((int(second), first_vertex, second_vertex))
        ridges.setdefault(int(second), []).append((int(first), first_vertex, second_vertex))

    finite_vertices = vor.vertices.tolist()
    regions: list[Polygon] = []
    for point_index, region_index in enumerate(vor.point_region):
        region = vor.regions[region_index]
        if region and all(vertex >= 0 for vertex in region):
            indices = list(region)
        else:
            indices = [vertex for vertex in region if vertex >= 0]
            for other, first, second in ridges.get(point_index, []):
                if first >= 0 and second >= 0:
                    continue
                finite = first if first >= 0 else second
                tangent = pts[other] - pts[point_index]
                length = float(np.linalg.norm(tangent))
                if length <= 1e-12:
                    continue
                tangent /= length
                normal = np.array((-tangent[1], tangent[0]))
                midpoint = pts[[point_index, other]].mean(axis=0)
                direction = normal * np.sign(np.dot(midpoint - center, normal))
                finite_vertices.append((vor.vertices[finite] + direction * far_radius).tolist())
                indices.append(len(finite_vertices) - 1)
        polygon = np.asarray([finite_vertices[index] for index in indices])
        if len(polygon) < 3:
            regions.append([])
            continue
        centroid = polygon.mean(axis=0)
        order = np.argsort(np.arctan2(polygon[:, 1] - centroid[1], polygon[:, 0] - centroid[0]))
        regions.append([(float(x), float(y)) for x, y in polygon[order]])
    return regions


def delaunay_triangulation(points: Sequence[Point]) -> list[tuple[int, int, int]]:
    pts = _array(points, 3)
    try:
        result = Delaunay(pts)
    except QhullError as exc:
        raise ValueError("Delaunay sites must span a two-dimensional region") from exc
    return [(int(simplex[0]), int(simplex[1]), int(simplex[2])) for simplex in result.simplices]


__all__ = ["Point", "Polygon", "delaunay_triangulation", "voronoi_diagram"]
