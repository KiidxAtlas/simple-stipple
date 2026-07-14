"""Pure path-direction, resampling, and primitive-fitting operations."""

from __future__ import annotations

import math

import numpy as np

Point = tuple[float, float]


def reverse_path(points: list[Point]) -> list[Point]:
    if len(points) >= 2 and points[0] == points[-1]:
        vertices = list(reversed(points[:-1]))
        return vertices + [vertices[0]]
    return list(reversed(points))


def set_closed_start(points: list[Point], index: int) -> list[Point]:
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError("Path must be closed")
    vertices = points[:-1]
    index %= len(vertices)
    rotated = vertices[index:] + vertices[:index]
    return rotated + [rotated[0]]


def _cumulative(points: list[Point]) -> tuple[list[float], float]:
    distances = [0.0]
    for first, second in zip(points, points[1:]):
        distances.append(distances[-1] + math.dist(first, second))
    return distances, distances[-1]


def _sample(points: list[Point], distances: list[float], target: float) -> Point:
    for index in range(len(distances) - 1):
        if target <= distances[index + 1] or index == len(distances) - 2:
            span = distances[index + 1] - distances[index]
            if span <= 1e-12:
                return points[index]
            ratio = (target - distances[index]) / span
            return (
                points[index][0] + (points[index + 1][0] - points[index][0]) * ratio,
                points[index][1] + (points[index + 1][1] - points[index][1]) * ratio,
            )
    return points[-1]


def resample_by_count(points: list[Point], count: int) -> list[Point]:
    if len(points) < 2 or count < 2:
        raise ValueError("Path and count must contain at least two points")
    closed = points[0] == points[-1]
    distances, total = _cumulative(points)
    if total <= 1e-12:
        raise ValueError("Cannot resample a zero-length path")
    if closed:
        samples = [_sample(points, distances, total * i / count) for i in range(count)]
        return samples + [samples[0]]
    return [_sample(points, distances, total * i / (count - 1)) for i in range(count)]


def resample_by_spacing(points: list[Point], spacing: float) -> list[Point]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive")
    _distances, total = _cumulative(points)
    closed = len(points) >= 2 and points[0] == points[-1]
    count = max(3, round(total / spacing)) if closed else max(2, round(total / spacing) + 1)
    return resample_by_count(points, count)


def fit_line(points: list[Point]) -> tuple[Point, Point] | None:
    if len(points) < 2:
        return None
    values = np.asarray(points, dtype=float)
    center = values.mean(axis=0)
    _u, _s, vh = np.linalg.svd(values - center, full_matrices=False)
    direction = vh[0]
    projections = (values - center) @ direction
    start = center + direction * projections.min()
    end = center + direction * projections.max()
    return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))


def fit_circle(points: list[Point]) -> tuple[Point, float] | None:
    vertices = points[:-1] if len(points) >= 2 and points[0] == points[-1] else points
    if len(vertices) < 3:
        return None
    values = np.asarray(vertices, dtype=float)
    matrix = np.column_stack((2 * values[:, 0], 2 * values[:, 1], np.ones(len(values))))
    rhs = values[:, 0] ** 2 + values[:, 1] ** 2
    try:
        cx, cy, constant = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    radius_sq = constant + cx * cx + cy * cy
    if radius_sq <= 1e-12:
        return None
    return (float(cx), float(cy)), float(math.sqrt(radius_sq))


def morph_paths(first: list[Point], second: list[Point], amount: float) -> list[Point]:
    """Interpolate two paths after arc-length resampling to a shared count."""
    if not 0.0 <= amount <= 1.0:
        raise ValueError("Morph amount must be between 0 and 1")
    first_closed = len(first) >= 3 and first[0] == first[-1]
    second_closed = len(second) >= 3 and second[0] == second[-1]
    if first_closed != second_closed:
        raise ValueError("Paths must both be open or both be closed")
    count = max(len(first) - int(first_closed), len(second) - int(second_closed), 2)
    a = resample_by_count(first, count)
    b = resample_by_count(second, count)
    result = [
        (x1 + (x2 - x1) * amount, y1 + (y2 - y1) * amount) for (x1, y1), (x2, y2) in zip(a, b)
    ]
    if first_closed and result[-1] != result[0]:
        result[-1] = result[0]
    return result


__all__ = [
    "fit_circle",
    "fit_line",
    "morph_paths",
    "resample_by_count",
    "resample_by_spacing",
    "reverse_path",
    "set_closed_start",
]
