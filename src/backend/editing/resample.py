"""Pure arc-length path resampling operations."""

from __future__ import annotations

import math

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
    if closed:
        samples = [_sample(points, distances, total * index / count) for index in range(count)]
        return samples + [samples[0]]
    return [_sample(points, distances, total * index / (count - 1)) for index in range(count)]


def resample_by_spacing(points: list[Point], spacing: float) -> list[Point]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive")
    _distances, total = _cumulative(points)
    closed = len(points) >= 2 and points[0] == points[-1]
    count = max(3, round(total / spacing)) if closed else max(2, round(total / spacing) + 1)
    return resample_by_count(points, count)


__all__ = ["resample_by_count", "resample_by_spacing"]
