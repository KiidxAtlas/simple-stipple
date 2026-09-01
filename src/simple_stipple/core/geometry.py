"""Spatial-index helpers for canvas snapping and hit testing."""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numba import njit, prange  # type: ignore[import-untyped]
from scipy.spatial import (  # type: ignore[import-untyped]
    Delaunay,
    KDTree,  # type: ignore[import-untyped]
    QhullError,
    Voronoi,
)

Point = tuple[float, float]


@dataclass(frozen=True)
class SnapTree:
    """KD-tree plus the stable point ordering needed to decode query results."""

    points: tuple[Point, ...]
    tree: KDTree | None


@lru_cache(maxsize=16)
def _cached_snap_tree(normalized: tuple[Point, ...]) -> SnapTree:
    tree = KDTree(np.asarray(normalized, dtype=np.float64)) if normalized else None
    return SnapTree(normalized, tree)


def build_snap_tree(points: Sequence[Point]) -> SnapTree:
    """Build or reuse an index for the exact stable point sequence."""
    normalized = tuple((float(x), float(y)) for x, y in points)
    return _cached_snap_tree(normalized)


def find_nearest_index(tree: SnapTree, query_point: Point, max_dist: float) -> int | None:
    if tree.tree is None or max_dist < 0:
        return None
    distance, index = tree.tree.query(query_point, k=1, distance_upper_bound=max_dist)
    if not np.isfinite(distance) or int(index) >= len(tree.points):
        return None
    return int(index)


def find_nearest(tree: SnapTree, query_point: Point, max_dist: float) -> Point | None:
    """Return the nearest point no farther than ``max_dist`` from the query."""
    index = find_nearest_index(tree, query_point, max_dist)
    return tree.points[index] if index is not None else None


@dataclass(frozen=True)
class VertexIndex:
    """KD-tree over every vertex of paths, keyed back to owner/vertex indices."""

    coords: tuple[Point, ...]
    owners: tuple[tuple[int, int], ...]
    tree: KDTree | None


def build_vertex_index(paths: Sequence[Sequence[Point]]) -> VertexIndex:
    """Flatten every vertex of ``paths`` into one KD-tree with owner mapping."""
    coords: list[Point] = []
    owners: list[tuple[int, int]] = []
    for path_index, points in enumerate(paths):
        for vertex_index, point in enumerate(points):
            coords.append((float(point[0]), float(point[1])))
            owners.append((path_index, vertex_index))
    tree = KDTree(np.asarray(coords, dtype=np.float64)) if coords else None
    return VertexIndex(tuple(coords), tuple(owners), tree)


def query_within_radius(index: VertexIndex, query_point: Point, radius: float) -> list[int]:
    """Return indices of every vertex within the inclusive ``radius``."""
    if index.tree is None or radius < 0:
        return []
    return [int(item) for item in index.tree.query_ball_point(query_point, radius)]


__all__ = [
    "Point",
    "Polygon",
    "SnapTree",
    "VertexIndex",
    "build_snap_tree",
    "build_vertex_index",
    "delaunay_triangulation",
    "find_nearest",
    "find_nearest_index",
    "poisson_disk_points",
    "prewarm",
    "query_within_radius",
    "tessellate_arc",
    "tessellate_circles",
    "voronoi_diagram",
]


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


# Numba's file-backed cache needs a real source-file locator. Frozen
# applications load this module from PyInstaller's archive, where enabling the
# cache raises during import before the application can even show a window.
_CACHE_ENABLED = not bool(getattr(sys, "frozen", False))


@njit(cache=_CACHE_ENABLED)
def tessellate_arc(center_x, center_y, radius, start_angle, end_angle, segments):
    points = np.empty((segments + 1, 2), dtype=np.float64)
    for index in range(segments + 1):
        angle = start_angle + (end_angle - start_angle) * index / segments
        points[index, 0] = center_x + radius * math.cos(angle)
        points[index, 1] = center_y + radius * math.sin(angle)
    return points


@njit(cache=_CACHE_ENABLED, parallel=True)
def tessellate_circles(centers, radius, segments):
    """Tessellate all equal-radius circles in one compiled batch."""
    points = np.empty((len(centers), segments + 1, 2), dtype=np.float64)
    for circle in prange(len(centers)):  # type: ignore[attr-defined]
        for index in range(segments + 1):
            angle = math.tau * index / segments
            points[circle, index, 0] = centers[circle, 0] + radius * math.cos(angle)
            points[circle, index, 1] = centers[circle, 1] + radius * math.sin(angle)
    return points


@njit(cache=_CACHE_ENABLED)
def poisson_disk_points(min_x, min_y, max_x, max_y, min_distance, seed):
    """Fast deterministic dart-throwing sampler with a neighbor grid."""
    width = max_x - min_x
    height = max_y - min_y
    cell_size = min_distance / math.sqrt(2.0)
    columns = max(1, int(math.ceil(width / cell_size)))
    rows = max(1, int(math.ceil(height / cell_size)))
    grid = np.full(columns * rows, -1, dtype=np.int64)
    target = max(1, int(width * height / (min_distance * min_distance) * 0.68))
    points = np.empty((target, 2), dtype=np.float64)
    count = 0
    np.random.seed(seed)
    for _attempt in range(target * 40):
        x = min_x + np.random.random() * width
        y = min_y + np.random.random() * height
        column = min(columns - 1, int((x - min_x) / cell_size))
        row = min(rows - 1, int((y - min_y) / cell_size))
        valid = True
        for neighbor_y in range(max(0, row - 2), min(rows, row + 3)):
            for neighbor_x in range(max(0, column - 2), min(columns, column + 3)):
                index = grid[neighbor_y * columns + neighbor_x]
                if index < 0:
                    continue
                dx = x - points[index, 0]
                dy = y - points[index, 1]
                if dx * dx + dy * dy < min_distance * min_distance:
                    valid = False
                    break
            if not valid:
                break
        if valid:
            points[count, 0] = x
            points[count, 1] = y
            grid[row * columns + column] = count
            count += 1
            if count == target:
                break
    return points[:count]


def prewarm() -> None:
    """Compile the small core signatures without retaining generated data."""
    tessellate_arc(0.0, 0.0, 1.0, 0.0, math.tau, 8)
    tessellate_circles(np.asarray(((0.0, 0.0),)), 1.0, 8)
    poisson_disk_points(0.0, 0.0, 2.0, 2.0, 0.5, 42)
