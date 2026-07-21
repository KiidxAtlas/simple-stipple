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


__all__ = ["Point", "SnapTree", "build_snap_tree", "find_nearest", "find_nearest_index"]
