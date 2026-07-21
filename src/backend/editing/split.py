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
    # A cutter must actually cross the region boundary and divide it. Never
    # extend a short line invisibly: that made a line drawn merely inside a
    # shape cut the entire region in half.
    geometries = [item for item in split(polygon, cutter).geoms if isinstance(item, Polygon)]
    if len(geometries) < 2:
        return []
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
                # A touch or partial entry may add an intersection vertex,
                # but it does not divide the region. Leave the region intact
                # and let the newly drawn line remain normal geometry.
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
