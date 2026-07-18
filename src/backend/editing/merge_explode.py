"""Pure path decomposition and connectivity merging."""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class PathInput:
    points: list[Point]
    construction: bool = False


def _equal(first: Point, second: Point, tolerance: float) -> bool:
    return abs(first[0] - second[0]) < tolerance and abs(first[1] - second[1]) < tolerance


def explode_path(points: list[Point]) -> list[list[Point]]:
    """Decompose a multi-vertex path into non-degenerate segments."""
    vertices = list(points)
    closed = len(vertices) >= 3 and math.dist(vertices[0], vertices[-1]) < 1e-6
    if closed:
        vertices.pop()
    count = len(vertices) if closed else len(vertices) - 1
    return [
        [vertices[index], vertices[(index + 1) % len(vertices)]]
        for index in range(max(0, count))
        if math.dist(vertices[index], vertices[(index + 1) % len(vertices)]) >= 1e-6
    ]


def merge_paths(paths: list[PathInput], tolerance: float = 0.01) -> list[PathInput]:
    """Merge all connected segments in paths into maximal chains."""
    segments = [
        (segment[0], segment[1], path.construction)
        for path in paths
        for segment in explode_path(path.points)
    ]
    used = [False] * len(segments)
    output: list[PathInput] = []
    for source, segment in enumerate(segments):
        if used[source]:
            continue
        used[source] = True
        chain = [segment[0], segment[1]]
        construction = segment[2]
        changed = True
        while changed:
            changed = False
            for index, (first, second, is_construction) in enumerate(segments):
                if used[index]:
                    continue
                if _equal(chain[-1], first, tolerance):
                    chain.append(second)
                elif _equal(chain[-1], second, tolerance):
                    chain.append(first)
                elif _equal(chain[0], second, tolerance):
                    chain.insert(0, first)
                elif _equal(chain[0], first, tolerance):
                    chain.insert(0, second)
                else:
                    continue
                used[index] = True
                construction |= is_construction
                changed = True
                break
        normalized = [chain[0]]
        normalized.extend(point for point in chain[1:] if math.dist(normalized[-1], point) >= 1e-6)
        if len(normalized) >= 3 and _equal(normalized[0], normalized[-1], 1e-6):
            normalized[-1] = normalized[0]
        output.append(PathInput(normalized, construction))
    return output
