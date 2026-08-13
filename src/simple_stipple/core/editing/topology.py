"""Pure path topology operations: decomposition, merging, splitting, trim/extend."""

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
from shapely.ops import linemerge, split, unary_union

PointTuple = tuple[float, float]
PATH_DEGENERACY_TOLERANCE = 1e-8
PATH_CLOSURE_TOLERANCE = 1e-6


# -- Decomposition and connectivity merging (formerly merge_explode.py) --


@dataclass(frozen=True, slots=True)
class PathInput:
    points: list[PointTuple]
    construction: bool = False


def _points_equal_within(first: PointTuple, second: PointTuple, tolerance: float) -> bool:
    return abs(first[0] - second[0]) < tolerance and abs(first[1] - second[1]) < tolerance


def explode_path(points: list[PointTuple]) -> list[list[PointTuple]]:
    """Decompose a multi-vertex path into non-degenerate segments."""
    vertices = list(points)
    closed = len(vertices) >= 3 and math.dist(vertices[0], vertices[-1]) < PATH_CLOSURE_TOLERANCE
    if closed:
        vertices.pop()
    count = len(vertices) if closed else len(vertices) - 1
    return [
        [vertices[index], vertices[(index + 1) % len(vertices)]]
        for index in range(max(0, count))
        if math.dist(vertices[index], vertices[(index + 1) % len(vertices)])
        >= PATH_DEGENERACY_TOLERANCE
    ]


def _attach_segment(
    chain: list[PointTuple],
    first: PointTuple,
    second: PointTuple,
    tolerance: float,
) -> bool:
    if _points_equal_within(chain[-1], first, tolerance):
        chain.append(second)
    elif _points_equal_within(chain[-1], second, tolerance):
        chain.append(first)
    elif _points_equal_within(chain[0], second, tolerance):
        chain.insert(0, first)
    elif _points_equal_within(chain[0], first, tolerance):
        chain.insert(0, second)
    else:
        return False
    return True


def _normalize_chain(chain: list[PointTuple]) -> list[PointTuple]:
    normalized = [chain[0]]
    normalized.extend(
        point for point in chain[1:] if math.dist(normalized[-1], point) >= PATH_CLOSURE_TOLERANCE
    )
    if len(normalized) >= 3 and _points_equal_within(
        normalized[0], normalized[-1], PATH_CLOSURE_TOLERANCE
    ):
        normalized[-1] = normalized[0]
    return normalized


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
                if not _attach_segment(chain, first, second, tolerance):
                    continue
                used[index] = True
                construction |= is_construction
                changed = True
                break
        output.append(PathInput(_normalize_chain(chain), construction))
    return output


# -- Splitting (formerly split.py) --


@dataclass(frozen=True, slots=True)
class SplitPath:
    source_id: str
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
            if not line.is_empty and len(line.coords) >= 2
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
    boundary_points = _intersection_points(cutter.intersection(polygon.boundary))
    unique_boundary_points: list[PointTuple] = []
    for point in boundary_points:
        if not any(_equal(point, existing) for existing in unique_boundary_points):
            unique_boundary_points.append(point)
    # A knife needs two boundary crossings to divide a closed shape. This
    # deliberately still rejects a short stroke wholly inside the shape.
    if len(unique_boundary_points) < 2:
        return []
    inner = polygon.buffer(-1e-6)
    if (inner if not inner.is_empty else polygon).intersection(cutter).is_empty:
        return []
    # GEOS can refuse to split when endpoints sit exactly on a polygon edge
    # or vertex. Extend only this proven boundary-to-boundary cutter by a
    # geometry-relative amount.  Crucially, extend its *end tangents* rather
    # than replacing the cutter with its endpoint chord: a drawn spline or
    # Bézier must cut along its actual sampled curve.
    coordinates = [(float(x), float(y)) for x, y in cutter.coords]
    if len(coordinates) < 2:
        return []
    start, second = coordinates[0], coordinates[1]
    penultimate, end = coordinates[-2], coordinates[-1]

    def _extend_endpoint(endpoint: PointTuple, adjacent: PointTuple, amount: float) -> PointTuple:
        dx, dy = endpoint[0] - adjacent[0], endpoint[1] - adjacent[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return endpoint
        return (endpoint[0] + dx / length * amount, endpoint[1] + dy / length * amount)

    min_x, min_y, max_x, max_y = polygon.bounds
    extension = max(math.hypot(max_x - min_x, max_y - min_y), 1.0) * 1e-6
    extended = LineString(
        [
            _extend_endpoint(start, second, extension),
            *coordinates,
            _extend_endpoint(end, penultimate, extension),
        ]
    )
    geometries = [item for item in split(polygon, extended).geoms if isinstance(item, Polygon)]
    if len(geometries) < 2:
        return []
    return [[(float(x), float(y)) for x, y in geometry.exterior.coords] for geometry in geometries]


def split_paths(
    paths: list[list[PointTuple]],
    cutter_points: list[PointTuple],
    entity_ids: list[str] | None = None,
) -> SplitResult:
    """Split paths with a cutter, retaining source-ID provenance."""
    ids = entity_ids if entity_ids is not None else [str(i) for i in range(len(paths))]
    unchanged = tuple(
        SplitPath(ids[index], list(points), False) for index, points in enumerate(paths)
    )
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
        source_id = ids[source_index]
        if len(path) < 2:
            output.append(SplitPath(source_id, list(path), False))
            continue
        try:
            closed = len(path) >= 3 and _equal(path[0], path[-1])
            if closed:
                polygon = Polygon(path)
                if not polygon.is_valid:
                    polygon = polygon.buffer(0)
                pieces = _polygon_pieces(polygon, cutter) if not polygon.is_empty else []
                if pieces:
                    output.extend(SplitPath(source_id, points, True) for points in pieces)
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
                        SplitPath(source_id, chain, True) for chain in chains if len(chain) >= 2
                    )
                    open_count += 1
                    continue
        except (TypeError, ValueError, GEOSException):
            pass
        output.append(SplitPath(source_id, list(path), False))
    return SplitResult(tuple(output), closed_count, open_count)


# -- Trim and extend (formerly trim_extend.py) --


def trim_polyline(
    target: list[PointTuple], cutters: list[list[PointTuple]], click: PointTuple
) -> list[list[PointTuple]]:
    cutter_geometry = unary_union([LineString(points) for points in cutters if len(points) >= 2])
    pieces = [
        item
        for item in split(LineString(target), cutter_geometry).geoms
        if isinstance(item, LineString) and len(item.coords) >= 2
    ]
    if len(pieces) < 2:
        return []
    removed = min(pieces, key=lambda item: item.distance(Point(click)))
    kept = [item for item in pieces if item is not removed]
    merged = linemerge(kept) if len(kept) > 1 else kept[0]
    outputs = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
    return [[(float(x), float(y)) for x, y in item.coords] for item in outputs]


def trim_preview(
    target: list[PointTuple], cutters: list[list[PointTuple]], click: PointTuple
) -> list[PointTuple] | None:
    """Return only the portion that a trim operation would remove."""
    cutter_geometry = unary_union([LineString(points) for points in cutters if len(points) >= 2])
    pieces = [
        item
        for item in split(LineString(target), cutter_geometry).geoms
        if isinstance(item, LineString) and len(item.coords) >= 2
    ]
    if len(pieces) < 2:
        return None
    removed = min(pieces, key=lambda item: item.distance(Point(click)))
    return [(float(x), float(y)) for x, y in removed.coords]


def extension_point(
    points: list[PointTuple], cutters: list[list[PointTuple]], *, start: bool, reach: float
) -> PointTuple | None:
    if len(points) < 2:
        return None
    tip = points[0] if start else points[-1]
    neighbor = points[1] if start else points[-2]
    dx, dy = tip[0] - neighbor[0], tip[1] - neighbor[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    ray = LineString([tip, (tip[0] + dx / length * reach, tip[1] + dy / length * reach)])
    intersection = ray.intersection(
        unary_union([LineString(item) for item in cutters if len(item) >= 2])
    )
    candidates: list[tuple[float, PointTuple]] = []
    for item in getattr(intersection, "geoms", [intersection]):
        coordinates = (
            [(item.x, item.y)] if isinstance(item, Point) else list(getattr(item, "coords", []))
        )
        for x, y in coordinates:
            distance = math.hypot(x - tip[0], y - tip[1])
            if distance > 1e-6:
                candidates.append((distance, (float(x), float(y))))
    return min(candidates)[1] if candidates else None
