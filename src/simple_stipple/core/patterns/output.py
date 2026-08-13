"""Fabrication-oriented path cleanup, ordering, and diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from shapely.geometry import Polygon  # type: ignore[import-untyped]


@dataclass(frozen=True)
class OutputDiagnostics:
    paths: int
    points: int
    total_length: float
    travel_length: float
    minimum_segment: float | None
    minimum_area: float | None


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _closed(poly: list[tuple[float, float]], tolerance: float = 0.01) -> bool:
    return len(poly) >= 3 and _distance(poly[0], poly[-1]) < tolerance


def clean_output(
    polys: list[list[tuple[float, float]]],
    *,
    minimum_segment: float = 0.0,
    minimum_area: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Remove manufacturing-noise vertices and closed micro-islands."""
    result: list[list[tuple[float, float]]] = []
    for source in polys:
        if len(source) < 2:
            continue
        points = [source[0]]
        for point in source[1:]:
            if minimum_segment <= 0 or _distance(points[-1], point) >= minimum_segment:
                points.append(point)
        was_closed = _closed(source)
        if was_closed and points[-1] != points[0]:
            points.append(points[0])
        if len(points) < (4 if was_closed else 2):
            continue
        if was_closed and minimum_area > 0:
            try:
                shape = Polygon(points)
            except (TypeError, ValueError):
                continue
            if shape.is_empty or abs(float(shape.area)) < minimum_area:
                continue
        result.append(points)
    return result


def _path_depths(polys: list[list[tuple[float, float]]]) -> list[int]:
    shapes: list[Polygon | None] = []
    for poly in polys:
        try:
            shape = Polygon(poly) if _closed(poly) else None
            shapes.append(shape if shape is not None and shape.is_valid else None)
        except (TypeError, ValueError):
            shapes.append(None)

    depths: list[int] = []
    for index, shape in enumerate(shapes):
        if shape is None:
            depths.append(-1)
            continue
        marker = shape.representative_point()
        depths.append(
            sum(
                other_index != index
                and other is not None
                and other.area > shape.area
                and other.contains(marker)
                for other_index, other in enumerate(shapes)
            )
        )
    return depths


def _nearest_path(
    remaining: list[list[tuple[float, float]]],
    cursor: tuple[float, float],
) -> list[tuple[float, float]]:
    best_index = 0
    best_reverse = False
    best_distance = math.inf
    for index, poly in enumerate(remaining):
        start_distance = _distance(cursor, poly[0])
        if start_distance < best_distance:
            best_index, best_reverse, best_distance = index, False, start_distance
        if _closed(poly):
            continue
        end_distance = _distance(cursor, poly[-1])
        if end_distance < best_distance:
            best_index, best_reverse, best_distance = index, True, end_distance
    selected = remaining.pop(best_index)
    if best_reverse:
        selected.reverse()
    return selected


def order_paths(polys: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Order inner closed paths first, then minimize travel within each depth.

    Open paths may be reversed to use their nearest endpoint. Closed paths keep
    their winding because some CAM tools use winding for operation semantics.
    """
    if len(polys) < 2:
        return [list(poly) for poly in polys]
    # Exact containment + nearest-neighbor ordering is quadratic. Keep export
    # latency bounded for very large generated jobs; those already have strong
    # locality from generator traversal order.
    if len(polys) > 5000:
        return [list(poly) for poly in polys]

    depths = _path_depths(polys)
    ordered: list[list[tuple[float, float]]] = []
    cursor = (0.0, 0.0)
    for depth in sorted(set(depths), reverse=True):
        remaining = [list(poly) for poly, value in zip(polys, depths) if value == depth]
        while remaining:
            selected = _nearest_path(remaining, cursor)
            ordered.append(selected)
            cursor = selected[-1]
    return ordered


def prepare_output(polys: list[list[tuple[float, float]]], options: dict[str, Any] | None) -> list:
    options = options or {}
    cleaned = clean_output(
        polys,
        minimum_segment=max(0.0, float(options.get("minimum_segment", 0.0) or 0.0)),
        minimum_area=max(0.0, float(options.get("minimum_area", 0.0) or 0.0)),
    )
    return order_paths(cleaned) if options.get("optimize_order", False) else cleaned


def diagnose_output(polys: list[list[tuple[float, float]]]) -> OutputDiagnostics:
    total = 0.0
    travel = 0.0
    minimum_segment: float | None = None
    minimum_area: float | None = None
    previous: tuple[float, float] | None = None
    for poly in polys:
        if not poly:
            continue
        if previous is not None:
            travel += _distance(previous, poly[0])
        previous = poly[-1]
        for start, end in zip(poly, poly[1:]):
            length = _distance(start, end)
            total += length
            if length > 0:
                minimum_segment = (
                    length if minimum_segment is None else min(minimum_segment, length)
                )
        if _closed(poly):
            try:
                area = abs(float(Polygon(poly).area))
                if area > 0:
                    minimum_area = area if minimum_area is None else min(minimum_area, area)
            except (TypeError, ValueError):
                # Diagnostics must not reject otherwise exportable linework;
                # an invalid closed ring simply has no trustworthy area.
                continue
    return OutputDiagnostics(
        paths=len(polys),
        points=sum(len(poly) for poly in polys),
        total_length=total,
        travel_length=travel,
        minimum_segment=minimum_segment,
        minimum_area=minimum_area,
    )
