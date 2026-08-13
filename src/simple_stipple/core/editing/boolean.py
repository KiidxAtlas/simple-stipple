"""Polygon boolean and offset operations."""

from __future__ import annotations

import math
from collections.abc import Iterable

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union
from collections.abc import Iterable, Sequence
from typing import Any


Point = tuple[float, float]
Polyline = list[Point]


def is_closed(points: list[Point], tolerance: float = 0.01) -> bool:
    return len(points) >= 3 and math.dist(points[0], points[-1]) < tolerance


def offset_polyline(points: list[Point], distance: float) -> list[Point] | None:
    if len(points) < 2:
        return None
    try:
        results = clipper_offset(points, distance, closed=is_closed(points))
        return max(results, key=len) if results else None
    except (RuntimeError, TypeError, ValueError):
        return None


def _rings(geometry) -> list[Polyline]:
    result: list[Polyline] = []
    if geometry.is_empty:
        return result
    if isinstance(geometry, Polygon):
        exterior = [(float(x), float(y)) for x, y in geometry.exterior.coords]
        if len(exterior) >= 4:
            result.append(exterior)
        result.extend(
            ring
            for interior in geometry.interiors
            if len(ring := [(float(x), float(y)) for x, y in interior.coords]) >= 4
        )
    elif isinstance(geometry, (MultiPolygon, GeometryCollection)):
        for child in geometry.geoms:
            result.extend(_rings(child))
    return result


def boolean_polylines(polylines: Iterable[Polyline], operation: str) -> list[Polyline]:
    shapes = []
    for points in polylines:
        if len(points) < 4:
            continue
        shape = Polygon(points).buffer(0)
        if not shape.is_empty:
            shapes.append(shape)
    if len(shapes) < 2:
        return []
    valid_paths = [_rings(shape)[0] for shape in shapes]
    if operation == "union":
        return clipper_union(valid_paths)
    if operation == "subtract":
        return clipper_difference(valid_paths[:1], valid_paths[1:])
    if operation == "intersect":
        result = valid_paths[:1]
        for path in valid_paths[1:]:
            result = clipper_intersection(result, [path])
        return result
    if operation == "divide":
        geometry = MultiPolygon(list(polygonize(unary_union([shape.boundary for shape in shapes]))))
    else:
        raise ValueError(f"Unsupported boolean operation: {operation}")
    return _rings(geometry)


__all__ = [
    "Path",
    "Point",
    "boolean_polylines",
    "clipper_difference",
    "clipper_intersection",
    "clipper_offset",
    "clipper_union",
    "is_closed",
    "native_available",
    "offset_polyline",
]


Point = tuple[float, float]
Path = list[Point]
_SCALE = 100_000.0

try:
    import pyclipper as _clipper_module  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised indirectly on unsupported platforms
    _clipper_module = None

_clipper: Any = _clipper_module


def native_available() -> bool:
    return _clipper is not None


def _paths(paths: Iterable[Sequence[Point]]) -> list[Path]:
    return [[(float(x), float(y)) for x, y in path] for path in paths if len(path) >= 3]


def _scaled(paths: list[Path]) -> list[list[tuple[int, int]]]:
    return [[(round(x * _SCALE), round(y * _SCALE)) for x, y in path] for path in paths]


def _unscaled(paths) -> list[Path]:
    result = [[(float(x) / _SCALE, float(y) / _SCALE) for x, y in path] for path in paths]
    for path in result:
        if path and path[0] != path[-1]:
            path.append(path[0])
    return result


def _native_boolean(subjects: list[Path], clips: list[Path], operation: int) -> list[Path]:
    engine = _clipper.Pyclipper()
    engine.AddPaths(_scaled(subjects), _clipper.PT_SUBJECT, True)
    if clips:
        engine.AddPaths(_scaled(clips), _clipper.PT_CLIP, True)
    return _unscaled(engine.Execute(operation, _clipper.PFT_NONZERO, _clipper.PFT_NONZERO))


def _fallback_boolean(subjects: list[Path], clips: list[Path], operation: str) -> list[Path]:
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
    from shapely.ops import unary_union

    def rings(geometry) -> list[Path]:
        if geometry.is_empty:
            return []
        if isinstance(geometry, Polygon):
            result = [[(float(x), float(y)) for x, y in geometry.exterior.coords]]
            result.extend(
                [[(float(x), float(y)) for x, y in ring.coords] for ring in geometry.interiors]
            )
            return result
        if isinstance(geometry, (MultiPolygon, GeometryCollection)):
            return [ring for child in geometry.geoms for ring in rings(child)]
        return []

    subject = unary_union([Polygon(path) for path in subjects])
    clip = unary_union([Polygon(path) for path in clips]) if clips else None
    if operation == "union":
        geometry = unary_union([subject, clip]) if clip is not None else subject
    elif operation == "difference":
        geometry = subject.difference(clip) if clip is not None else subject
    else:
        geometry = subject.intersection(clip) if clip is not None else subject
    return rings(geometry)


def _boolean(
    subjects: Iterable[Sequence[Point]], clips: Iterable[Sequence[Point]], operation: str
) -> list[Path]:
    subject_paths, clip_paths = _paths(subjects), _paths(clips)
    if not subject_paths:
        return []
    if _clipper is not None:
        kind = {
            "union": _clipper.CT_UNION,
            "difference": _clipper.CT_DIFFERENCE,
            "intersection": _clipper.CT_INTERSECTION,
        }[operation]
        return _native_boolean(subject_paths, clip_paths, kind)
    return _fallback_boolean(subject_paths, clip_paths, operation)


def clipper_union(paths: Iterable[Sequence[Point]]) -> list[Path]:
    return _boolean(paths, (), "union")


def clipper_difference(
    subjects: Iterable[Sequence[Point]], clips: Iterable[Sequence[Point]]
) -> list[Path]:
    return _boolean(subjects, clips, "difference")


def clipper_intersection(
    subjects: Iterable[Sequence[Point]], clips: Iterable[Sequence[Point]]
) -> list[Path]:
    return _boolean(subjects, clips, "intersection")


def clipper_offset(path: Sequence[Point], distance: float, *, closed: bool = True) -> list[Path]:
    points = _paths((path,))
    if not points:
        return []
    if _clipper is not None and closed:
        engine = _clipper.PyclipperOffset()
        engine.AddPath(_scaled(points)[0], _clipper.JT_ROUND, _clipper.ET_CLOSEDPOLYGON)
        return _unscaled(engine.Execute(distance * _SCALE))
    from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

    geometry = (
        Polygon(points[0]).buffer(distance, join_style="round")
        if closed
        else LineString(points[0]).parallel_offset(
            abs(distance), "left" if distance >= 0 else "right", join_style="round"
        )
    )
    geometries = (
        list(geometry.geoms)
        if isinstance(geometry, (MultiPolygon, MultiLineString))
        else [geometry]
    )
    result: list[Path] = []
    for item in geometries:
        if item.is_empty:
            continue
        coords = item.exterior.coords if isinstance(item, Polygon) else item.coords
        result.append([(float(x), float(y)) for x, y in coords])
    return result
