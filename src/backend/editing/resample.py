"""Pure arc-length path resampling operations."""

from __future__ import annotations

import math

import numpy as np

from src.backend.jit import resample_path

Point = tuple[float, float]


def _cumulative(points: list[Point]) -> tuple[list[float], float]:
    distances = [0.0]
    for first, second in zip(points, points[1:]):
        distances.append(distances[-1] + math.dist(first, second))
    return distances, distances[-1]


def _sample(points: list[Point], distances: list[float], target: float) -> Point:
    for index, end in enumerate(distances[1:]):
        if target <= end or index == len(distances) - 2:
            span = end - distances[index]
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
    sample_count = count if closed else count
    denominator = count if closed else count - 1
    targets = np.linspace(0.0, total * (sample_count - 1) / denominator, sample_count)
    sampled, _ = resample_path(np.asarray(points, dtype=np.float64), targets)
    result = [(float(x), float(y)) for x, y in sampled]
    return result + [result[0]] if closed else result


def resample_by_spacing(points: list[Point], spacing: float) -> list[Point]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive")
    _distances, total = _cumulative(points)
    closed = len(points) >= 2 and points[0] == points[-1]
    count = max(3, round(total / spacing)) if closed else max(2, round(total / spacing) + 1)
    return resample_by_count(points, count)


__all__ = ["resample_by_count", "resample_by_spacing"]
