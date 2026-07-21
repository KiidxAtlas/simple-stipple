"""Small, typed wrappers around :mod:`scipy.spatial` nearest-neighbour APIs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.spatial import KDTree  # type: ignore[import-untyped]

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
    """KD-tree over every vertex of a set of paths, keyed back to its owner.

    ``owners[i]`` is the ``(path_index, vertex_index)`` that produced
    ``coords[i]``, so a spatial query can be decoded to the entity/vertex the
    caller actually reasons about.
    """

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
    """Return indices of every vertex within ``radius`` of ``query_point``.

    ``radius`` is an inclusive upper bound; callers that need a strict bound
    re-check the exact distance on the (typically tiny) returned candidate set.
    """
    if index.tree is None or radius < 0:
        return []
    return [int(i) for i in index.tree.query_ball_point(query_point, radius)]


__all__ = [
    "Point",
    "SnapTree",
    "VertexIndex",
    "build_snap_tree",
    "build_vertex_index",
    "find_nearest",
    "find_nearest_index",
    "query_within_radius",
]
