"""Numba kernels for frequently repeated numeric geometry loops.

The public wrappers keep NumPy/Numba details out of domain modules. Compilation
is lazy and cached on disk; :func:`prewarm` can move the first-call cost to app
startup when desired.
"""

from __future__ import annotations

import math
import sys

import numpy as np
from numba import njit, prange  # type: ignore[import-untyped]

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


@njit(cache=_CACHE_ENABLED)
def resample_path(points, targets):
    cumulative = np.empty(len(points), dtype=np.float64)
    cumulative[0] = 0.0
    for index in range(1, len(points)):
        dx = points[index, 0] - points[index - 1, 0]
        dy = points[index, 1] - points[index - 1, 1]
        cumulative[index] = cumulative[index - 1] + math.sqrt(dx * dx + dy * dy)
    output = np.empty((len(targets), 2), dtype=np.float64)
    segment = 0
    for sample, target in enumerate(targets):
        while segment < len(points) - 2 and target > cumulative[segment + 1]:
            segment += 1
        span = cumulative[segment + 1] - cumulative[segment]
        ratio = 0.0 if span <= 1e-12 else (target - cumulative[segment]) / span
        output[sample, 0] = points[segment, 0] + (points[segment + 1, 0] - points[segment, 0]) * ratio
        output[sample, 1] = points[segment, 1] + (points[segment + 1, 1] - points[segment, 1]) * ratio
    return output, cumulative[-1]


def prewarm() -> None:
    """Compile the small core signatures without retaining generated data."""
    tessellate_arc(0.0, 0.0, 1.0, 0.0, math.tau, 8)
    tessellate_circles(np.asarray(((0.0, 0.0),)), 1.0, 8)
    poisson_disk_points(0.0, 0.0, 2.0, 2.0, 0.5, 42)
    resample_path(np.asarray(((0.0, 0.0), (1.0, 0.0))), np.asarray((0.0, 1.0)))


__all__ = [
    "poisson_disk_points",
    "prewarm",
    "resample_path",
    "tessellate_arc",
    "tessellate_circles",
]
