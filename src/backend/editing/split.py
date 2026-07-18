"""Pure polyline and polygon splitting operations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.errors import GEOSException
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
)
from shapely.ops import split

PointTuple = tuple[float, float]


@dataclass(frozen=True, slots=True)
class SplitPath:
    source_index: int
    points: list[PointTuple]
    changed: bool


@dataclass(frozen=True, slots=True)
class SplitResult:
    paths: tuple[SplitPath, ...]
    closed_splits: int = 0
    open_splits: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.closed_splits or self.open_splits)


def _equal(first: PointTuple, second: PointTuple) -> bool:
    return math.dist(first, second) < 1e-6


def _extend(line: LineString, amount: float) -> LineString:
    coordinates = list(line.coords)
    for end, neighbor in ((0, 1), (-1, -2)):
        x, y = coordinates[end]
        nx, ny = coordinates[neighbor]
        dx, dy = x - nx, y - ny
        length = math.hypot(dx, dy)
        if length > 1e-12:
            coordinates[end] = x + dx / length * amount, y + dy / length * amount
    return LineString(coordinates)


def _intersection_points(geometry: object) -> list[PointTuple]:
    if isinstance(geometry, Point):
        return [(float(geometry.x), float(geometry.y))]
    if isinstance(geometry, MultiPoint):
        return [(float(point.x), float(point.y)) for point in geometry.geoms]
    if isinstance(geometry, (LineString, MultiLineString)):
        lines = geometry.geoms if isinstance(geometry, MultiLineString) else [geometry]
        return [
            point
            for line in lines
            for point in (
                (float(line.coords[0][0]), float(line.coords[0][1])),
                (float(line.coords[-1][0]), float(line.coords[-1][1])),
            )
        ]
    if isinstance(geometry, GeometryCollection):
        return [point for item in geometry.geoms for point in _intersection_points(item)]
    return []


def _split_segment(
    first: PointTuple, second: PointTuple, cutter: LineString
) -> list[list[PointTuple]]:
    segment = LineString([first, second])
    if _equal(first, second) or not cutter.intersects(segment):
        return [[first, second]]
    intersection = segment.intersection(cutter)
    points = [first, second]
    for point in _intersection_points(intersection):
        if not any(_equal(point, existing) for existing in points):
            points.append(point)
    dx, dy = second[0] - first[0], second[1] - first[1]
    denominator = dx * dx + dy * dy
    points.sort(
        key=lambda point: ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator
    )
    return [[a, b] for a, b in zip(points, points[1:]) if not _equal(a, b)]


def _polygon_pieces(polygon: Polygon, cutter: LineString) -> list[list[PointTuple]]:
    if not cutter.intersects(polygon):
        return []
    overlap = cutter.intersection(polygon.boundary)
    if isinstance(overlap, (LineString, MultiLineString)) and overlap.length > 1e-6:
        return []
    inner = polygon.buffer(-1e-6)
    if (inner if not inner.is_empty else polygon).intersection(cutter).is_empty:
        return []
    minx, miny, maxx, maxy = polygon.bounds
    extended = _extend(cutter, max(math.hypot(maxx - minx, maxy - miny) * 2.0, 1.0))
    candidates: list[tuple[int, list[Polygon]]] = []
    for order, candidate in enumerate((cutter, extended)):
        pieces = [item for item in split(polygon, candidate).geoms if isinstance(item, Polygon)]
        if len(pieces) >= 2:
            candidates.append((order, pieces))
    if not candidates:
        return []
    geometries = min(candidates, key=lambda item: (len(item[1]), item[0]))[1]
    return [[(float(x), float(y)) for x, y in geometry.exterior.coords] for geometry in geometries]


def split_paths(paths: list[list[PointTuple]], cutter_points: list[PointTuple]) -> SplitResult:
    """Split paths with a cutter, retaining source-index provenance."""
    unchanged = tuple(SplitPath(index, list(points), False) for index, points in enumerate(paths))
    if len(cutter_points) < 2:
        return SplitResult(unchanged)
    try:
        cutter = LineString(cutter_points)
        if cutter.is_empty or cutter.length < 1e-9:
            return SplitResult(unchanged)
    except (TypeError, ValueError, GEOSException):
        return SplitResult(unchanged)

    output: list[SplitPath] = []
    closed_count = open_count = 0
    for source_index, path in enumerate(paths):
        if len(path) < 2:
            output.append(SplitPath(source_index, list(path), False))
            continue
        try:
            closed = len(path) >= 3 and _equal(path[0], path[-1])
            if closed:
                polygon = Polygon(path)
                if not polygon.is_valid:
                    polygon = polygon.buffer(0)
                pieces = _polygon_pieces(polygon, cutter) if not polygon.is_empty else []
                if pieces:
                    output.extend(SplitPath(source_index, points, True) for points in pieces)
                    closed_count += 1
                    continue
                vertices = list(path[:-1])
                rebuilt = [vertices[0]]
                changed = False
                for index, first in enumerate(vertices):
                    pieces = _split_segment(first, vertices[(index + 1) % len(vertices)], cutter)
                    changed |= len(pieces) > 1
                    rebuilt.extend(
                        piece[1] for piece in pieces if not _equal(rebuilt[-1], piece[1])
                    )
                if changed:
                    if not _equal(rebuilt[0], rebuilt[-1]):
                        rebuilt.append(rebuilt[0])
                    output.append(SplitPath(source_index, rebuilt, True))
                    closed_count += 1
                    continue
            else:
                chains: list[list[PointTuple]] = [[path[0]]]
                changed = False
                for first, second in zip(path, path[1:]):
                    pieces = _split_segment(first, second, cutter)
                    if len(pieces) > 1:
                        changed = True
                        chains[-1].append(pieces[0][1])
                        chains.extend(list(piece) for piece in pieces[1:])
                    else:
                        chains[-1].append(second)
                if changed:
                    output.extend(
                        SplitPath(source_index, chain, True) for chain in chains if len(chain) >= 2
                    )
                    open_count += 1
                    continue
        except (TypeError, ValueError, GEOSException):
            pass
        output.append(SplitPath(source_index, list(path), False))
    return SplitResult(tuple(output), closed_count, open_count)
