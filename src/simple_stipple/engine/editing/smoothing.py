"""Pure path simplification, smoothing, and arc-length resampling entry points."""

from __future__ import annotations

import math

import numpy as np
from shapely.errors import GEOSException
from shapely.geometry import LineString

from simple_stipple.engine.geometry.jit import resample_path

Point = tuple[float, float]

_GAUSSIAN_KERNEL = (0.06136, 0.24477, 0.38774, 0.24477, 0.06136)


def smooth(
    points: list[Point], method: str = "chaikin", iterations: int = 2, *, closed: bool = False
) -> list[Point]:
    """Return a smoothed copy of a path without mutating its input."""
    vertices = list(points[:-1] if closed and points and points[0] == points[-1] else points)
    if len(vertices) < 3:
        return list(points)
    if method == "gaussian":
        for _ in range(max(1, iterations)):
            vertices = gaussian_pass(vertices, closed=closed)
    elif method == "catmull_rom":
        vertices = catmull_rom(
            vertices, closed=closed, samples_per_segment=max(2, int(iterations) * 4)
        )
    elif method == "chaikin":
        for _ in range(max(1, iterations)):
            vertices = chaikin_pass(vertices, closed=closed)
    else:
        raise ValueError(f"Unknown smoothing method: {method}")
    if closed and vertices:
        vertices.append(vertices[0])
    return vertices


def chaikin_pass(points: list[Point], *, closed: bool, corner_angle: float = 110.0) -> list[Point]:
    n = len(points)

    def sharp(index: int) -> bool:
        if closed:
            previous, current, following = (
                points[(index - 1) % n],
                points[index],
                points[(index + 1) % n],
            )
        elif index in (0, n - 1):
            return True
        else:
            previous, current, following = points[index - 1], points[index], points[index + 1]
        first = (current[0] - previous[0], current[1] - previous[1])
        second = (following[0] - current[0], following[1] - current[1])
        lengths = math.hypot(*first) * math.hypot(*second)
        if lengths < 1e-9:
            return False
        cosine = max(-1.0, min(1.0, (first[0] * second[0] + first[1] * second[1]) / lengths))
        return math.degrees(math.acos(cosine)) > corner_angle

    output: list[Point] = []
    for index in range(n if closed else n - 1):
        following = (index + 1) % n
        first, second = points[index], points[following]
        output.extend(
            (
                first
                if sharp(index)
                else (first[0] * 0.75 + second[0] * 0.25, first[1] * 0.75 + second[1] * 0.25),
                second
                if sharp(following)
                else (first[0] * 0.25 + second[0] * 0.75, first[1] * 0.25 + second[1] * 0.75),
            )
        )
    deduped: list[Point] = []
    for point in output:
        if not deduped or math.dist(point, deduped[-1]) > 1e-9:
            deduped.append(point)
    return deduped


def gaussian_pass(points: list[Point], *, closed: bool) -> list[Point]:
    output: list[Point] = []
    radius = len(_GAUSSIAN_KERNEL) // 2
    for index in range(len(points)):
        if not closed and index in (0, len(points) - 1):
            output.append(points[index])
            continue
        sx = sy = total = 0.0
        for offset, weight in enumerate(_GAUSSIAN_KERNEL):
            neighbor = index + offset - radius
            if closed:
                neighbor %= len(points)
            elif not 0 <= neighbor < len(points):
                continue
            sx += points[neighbor][0] * weight
            sy += points[neighbor][1] * weight
            total += weight
        output.append((sx / total, sy / total))
    return output


def _centripetal_point(p0: Point, p1: Point, p2: Point, p3: Point, amount: float) -> Point:
    def knot(previous: float, first: Point, second: Point) -> float:
        return previous + max(math.dist(first, second), 1e-6) ** 0.5

    def interpolate(first: Point, second: Point, start: float, end: float, value: float) -> Point:
        ratio = 0.0 if end - start <= 1e-9 else (value - start) / (end - start)
        return first[0] + (second[0] - first[0]) * ratio, first[1] + (second[1] - first[1]) * ratio

    t0 = 0.0
    t1, t2 = knot(t0, p0, p1), 0.0
    t2 = knot(t1, p1, p2)
    t3 = knot(t2, p2, p3)
    value = t1 + amount * (t2 - t1)
    a1 = interpolate(p0, p1, t0, t1, value)
    a2 = interpolate(p1, p2, t1, t2, value)
    a3 = interpolate(p2, p3, t2, t3, value)
    return interpolate(
        interpolate(a1, a2, t0, t2, value),
        interpolate(a2, a3, t1, t3, value),
        t1,
        t2,
        value,
    )


def catmull_rom(points: list[Point], *, closed: bool, samples_per_segment: int = 8) -> list[Point]:
    def at(index: int) -> Point:
        return (
            points[index % len(points)] if closed else points[max(0, min(len(points) - 1, index))]
        )

    output: list[Point] = []
    segment_count = len(points) if closed else len(points) - 1
    total_length = 0.0
    for index in range(segment_count):
        p0, p1, p2, p3 = at(index - 1), at(index), at(index + 1), at(index + 2)
        total_length += math.dist(p1, p2)
        output.extend(
            _centripetal_point(p0, p1, p2, p3, sample / samples_per_segment)
            for sample in range(samples_per_segment)
        )
    if not closed:
        output.append(points[-1])
    tolerance = max(total_length / max(1, segment_count) * 0.05, 1e-4)
    return simplify(output, tolerance)


def simplify(points: list[Point], tolerance: float, *, closed: bool = False) -> list[Point]:
    if tolerance < 0:
        raise ValueError("Tolerance must be non-negative")
    if len(points) < 2:
        return list(points)
    try:
        result = [(float(x), float(y)) for x, y in LineString(points).simplify(tolerance).coords]
    except (GEOSException, ValueError):
        return list(points)
    if closed and result and result[0] != result[-1]:
        result.append(result[0])
    return result


def _cumulative(points: list[Point]) -> tuple[list[float], float]:
    distances = [0.0]
    for first, second in zip(points, points[1:]):
        distances.append(distances[-1] + math.dist(first, second))
    return distances, distances[-1]


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
