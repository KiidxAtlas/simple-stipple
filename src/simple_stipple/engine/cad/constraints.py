"""Small persistent geometric-constraint model and deterministic line solver."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args
from uuid import uuid4

Point = tuple[float, float]
ConstraintKind = Literal[
    "horizontal",
    "vertical",
    "parallel",
    "perpendicular",
    "equal",
    "equal_length",
    "coincident",
    "collinear",
    "concentric",
    "tangent",
    "smooth",
    "symmetric",
    "midpoint",
    "intersection",
    "projection",
    "fixed",
]

_CONSTRAINT_KINDS: frozenset[str] = frozenset(get_args(ConstraintKind))


@dataclass
class GeometricConstraint:
    kind: ConstraintKind
    entity_ids: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "entity_ids": list(self.entity_ids),
            "parameters": self.parameters,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GeometricConstraint | None:
        kind = str(value.get("kind", ""))
        if kind not in _CONSTRAINT_KINDS:
            return None
        ids = tuple(str(item) for item in value.get("entity_ids", []) if item)
        if not ids:
            return None
        return cls(
            kind=cast(ConstraintKind, kind),
            entity_ids=ids,
            parameters=dict(value.get("parameters") or {}),
            id=str(value.get("id") or uuid4().hex),
            enabled=bool(value.get("enabled", True)),
        )


def _line(points: list[Point] | None) -> tuple[Point, Point] | None:
    if points is None or len(points) != 2 or math.dist(points[0], points[1]) <= 1e-12:
        return None
    return points[0], points[1]


def _segment(points: list[Point] | None, index: object) -> tuple[Point, Point, int, int] | None:
    """Resolve an edge reference while preserving the vertex slots to update."""
    if points is None or len(points) < 2:
        return None
    closed = len(points) >= 3 and points[0] == points[-1]
    segment_count = len(points) - 1 if closed else len(points) - 1
    try:
        start = int(cast(Any, index))
    except (TypeError, ValueError):
        return None
    if not 0 <= start < segment_count:
        return None
    end = start + 1
    if math.dist(points[start], points[end]) <= 1e-12:
        return None
    return points[start], points[end], start, end


def _constraint_line(
    geometry: dict[str, list[Point]], constraint: GeometricConstraint, position: int
) -> tuple[Point, Point, int, int] | None:
    if position >= len(constraint.entity_ids):
        return None
    points = geometry.get(constraint.entity_ids[position])
    key = "first_segment" if position == 0 else "second_segment"
    if key in constraint.parameters:
        return _segment(points, constraint.parameters[key])
    line = _line(points)
    return (*line, 0, 1) if line is not None else None


def _with_segment_end(points: list[Point], ref: tuple[Point, Point, int, int], value: Point) -> list[Point]:
    """Update an edge endpoint and preserve an explicit closed-ring seam."""
    updated = list(points)
    updated[ref[3]] = value
    if len(updated) >= 3 and updated[0] == updated[-1] and ref[3] == len(updated) - 1:
        updated[0] = value
    return updated


def _with_segment(
    points: list[Point], ref: tuple[Point, Point, int, int], start: Point, end: Point
) -> list[Point]:
    """Replace a referenced segment while preserving a closed-ring seam."""
    updated = list(points)
    updated[ref[2]] = start
    updated[ref[3]] = end
    if len(updated) >= 3 and updated[0] == updated[-1]:
        if ref[2] == 0:
            updated[-1] = start
        if ref[3] == len(updated) - 1:
            updated[0] = end
    return updated


def _directed(start: Point, length: float, angle: float) -> list[Point]:
    return [start, (start[0] + length * math.cos(angle), start[1] + length * math.sin(angle))]


def _line_intersection(first: tuple[Point, Point], second: tuple[Point, Point]) -> Point | None:
    """Return the infinite-line crossing, or None for parallel lines."""
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second
    denominator = (ax - bx) * (cy - dy) - (ay - by) * (cx - dx)
    if abs(denominator) <= 1e-12:
        return None
    cross_first = ax * by - ay * bx
    cross_second = cx * dy - cy * dx
    return (
        (cross_first * (cx - dx) - (ax - bx) * cross_second) / denominator,
        (cross_first * (cy - dy) - (ay - by) * cross_second) / denominator,
    )


def _project_onto_line(point: Point, line: tuple[Point, Point]) -> Point:
    """Orthogonally project a point onto an infinite line."""
    (ax, ay), (bx, by) = line
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return point
    t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq
    return (ax + t * dx, ay + t * dy)


def _reflect_across_line(point: Point, line: tuple[Point, Point]) -> Point:
    projected = _project_onto_line(point, line)
    return (2 * projected[0] - point[0], 2 * projected[1] - point[1])


def solve_constraints(
    geometry: dict[str, list[Point]], constraints: list[GeometricConstraint], *, passes: int = 4
) -> dict[str, list[Point]]:
    """Apply explicit constraints sequentially; IDs make compaction/reordering safe."""
    solved = {key: list(points) for key, points in geometry.items()}
    for _ in range(max(1, passes)):
        for constraint in constraints:
            if not constraint.enabled:
                continue
            ids = constraint.entity_ids
            if constraint.kind == "fixed":
                stored = constraint.parameters.get("points")
                if ids[0] in solved and isinstance(stored, list):
                    restored: list[Point] = []
                    for point in stored:
                        if isinstance(point, (list, tuple)) and len(point) == 2:
                            restored.append((float(point[0]), float(point[1])))
                    if restored:
                        solved[ids[0]] = restored
                continue
            if constraint.kind == "coincident" and {
                "first_vertex",
                "second_vertex",
            } <= constraint.parameters.keys() and len(ids) >= 2:
                first_points, second_points = solved.get(ids[0]), solved.get(ids[1])
                try:
                    first_vertex = int(constraint.parameters["first_vertex"])
                    second_vertex = int(constraint.parameters["second_vertex"])
                except (TypeError, ValueError):
                    continue
                if (
                    first_points is not None
                    and second_points is not None
                    and 0 <= first_vertex < len(first_points)
                    and 0 <= second_vertex < len(second_points)
                ):
                    updated = list(second_points)
                    updated[second_vertex] = first_points[first_vertex]
                    solved[ids[1]] = updated
                continue
            if constraint.kind == "coincident" and "point_vertex" in constraint.parameters and len(ids) >= 2:
                first_ref = _constraint_line(solved, constraint, 0)
                point_points = solved.get(ids[1])
                try:
                    point_vertex = int(constraint.parameters["point_vertex"])
                except (TypeError, ValueError):
                    continue
                if first_ref is None or point_points is None or not 0 <= point_vertex < len(point_points):
                    continue
                updated = list(point_points)
                updated[point_vertex] = _project_onto_line(updated[point_vertex], first_ref[:2])
                solved[ids[1]] = updated
                continue
            if constraint.kind == "midpoint" and len(ids) >= 2:
                first_ref = _constraint_line(solved, constraint, 0)
                point_points = solved.get(ids[1])
                try:
                    point_vertex = int(constraint.parameters["point_vertex"])
                except (KeyError, TypeError, ValueError):
                    continue
                if first_ref is None or point_points is None or not 0 <= point_vertex < len(point_points):
                    continue
                midpoint = (
                    (first_ref[0][0] + first_ref[1][0]) / 2,
                    (first_ref[0][1] + first_ref[1][1]) / 2,
                )
                updated = list(point_points)
                updated[point_vertex] = midpoint
                solved[ids[1]] = updated
                continue
            if constraint.kind == "intersection" and len(ids) >= 3:
                first_ref = _constraint_line(solved, constraint, 0)
                second_ref = _constraint_line(solved, constraint, 1)
                point_points = solved.get(ids[2])
                try:
                    point_vertex = int(constraint.parameters["point_vertex"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    first_ref is None
                    or second_ref is None
                    or point_points is None
                    or not 0 <= point_vertex < len(point_points)
                ):
                    continue
                crossing = _line_intersection(first_ref[:2], second_ref[:2])
                if crossing is None:
                    continue
                updated = list(point_points)
                updated[point_vertex] = crossing
                solved[ids[2]] = updated
                continue
            if constraint.kind == "symmetric" and len(ids) >= 3:
                axis_ref = _constraint_line(solved, constraint, 0)
                first_points, second_points = solved.get(ids[1]), solved.get(ids[2])
                try:
                    first_vertex = int(constraint.parameters["first_vertex"])
                    second_vertex = int(constraint.parameters["second_vertex"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    axis_ref is None
                    or first_points is None
                    or second_points is None
                    or not 0 <= first_vertex < len(first_points)
                    or not 0 <= second_vertex < len(second_points)
                ):
                    continue
                updated = list(second_points)
                updated[second_vertex] = _reflect_across_line(first_points[first_vertex], axis_ref[:2])
                solved[ids[2]] = updated
                continue
            if constraint.kind == "smooth" and constraint.parameters.get("curve") and len(ids) >= 2:
                first_points, second_points = solved.get(ids[0]), solved.get(ids[1])
                if first_points is None or second_points is None or len(first_points) < 4 or len(second_points) < 3:
                    continue
                # Cubic Bézier G² continuity at the join: match position,
                # first derivative, and second derivative of the second
                # curve to the first curve's terminal control polygon.
                p1, p2, p3 = first_points[-3:]
                q0 = p3
                q1 = (2 * p3[0] - p2[0], 2 * p3[1] - p2[1])
                q2 = (4 * p3[0] - 4 * p2[0] + p1[0], 4 * p3[1] - 4 * p2[1] + p1[1])
                updated = list(second_points)
                updated[:3] = [q0, q1, q2]
                solved[ids[1]] = updated
                continue
            first_ref = _constraint_line(solved, constraint, 0)
            if first_ref is None:
                continue
            first_line: tuple[Point, Point] = (first_ref[0], first_ref[1])
            if not ids:
                continue
            if constraint.kind == "horizontal":
                y = (first_line[0][1] + first_line[1][1]) / 2.0
                solved[ids[0]] = [(first_line[0][0], y), (first_line[1][0], y)]
                continue
            if constraint.kind == "vertical":
                x = (first_line[0][0] + first_line[1][0]) / 2.0
                solved[ids[0]] = [(x, first_line[0][1]), (x, first_line[1][1])]
                continue
            if len(ids) < 2:
                continue
            second_ref = _constraint_line(solved, constraint, 1)
            second = second_ref[:2] if second_ref is not None else None
            if second is None or second_ref is None:
                continue
            first_length = math.dist(*first_line)
            second_length = math.dist(*second)
            first_angle = math.atan2(
                first_line[1][1] - first_line[0][1], first_line[1][0] - first_line[0][0]
            )
            if constraint.kind in {"parallel", "smooth"}:
                solved[ids[1]] = _with_segment_end(
                    solved[ids[1]], second_ref, _directed(second[0], second_length, first_angle)[1]
                )
            elif constraint.kind == "perpendicular":
                solved[ids[1]] = _with_segment_end(
                    solved[ids[1]],
                    second_ref,
                    _directed(second[0], second_length, first_angle + math.pi / 2)[1],
                )
            elif constraint.kind in {"equal", "equal_length"}:
                angle = math.atan2(second[1][1] - second[0][1], second[1][0] - second[0][0])
                solved[ids[1]] = _with_segment_end(
                    solved[ids[1]], second_ref, _directed(second[0], first_length, angle)[1]
                )
            elif constraint.kind == "collinear":
                projected_start = _project_onto_line(second[0], first_line)
                solved[ids[1]] = _with_segment(
                    solved[ids[1]],
                    second_ref,
                    projected_start,
                    _directed(projected_start, second_length, first_angle)[1],
                )
            elif constraint.kind == "coincident":
                first_end = int(constraint.parameters.get("first_endpoint", 1))
                second_end = int(constraint.parameters.get("second_endpoint", 0))
                target = first_line[max(0, min(1, first_end))]
                endpoint_index = second_ref[3] if max(0, min(1, second_end)) else second_ref[2]
                if endpoint_index == second_ref[3]:
                    solved[ids[1]] = _with_segment_end(solved[ids[1]], second_ref, target)
                else:
                    updated = list(solved[ids[1]])
                    updated[endpoint_index] = target
                    solved[ids[1]] = updated
    return solved


def constraint_residuals(
    geometry: dict[str, list[Point]], constraints: list[GeometricConstraint]
) -> dict[str, float]:
    """Return a scale-independent error for each enabled constraint.

    A value near zero is satisfied. Non-finite or structurally invalid
    constraints report infinity so the UI can identify conflicts instead of
    silently drawing every badge as healthy.
    """
    residuals: dict[str, float] = {}
    for constraint in constraints:
        if not constraint.enabled:
            residuals[constraint.id] = 0.0
            continue
        ids = constraint.entity_ids
        if constraint.kind == "projection" or (
            bool(constraint.parameters.get("shape"))
            and constraint.kind in {"equal", "concentric", "tangent"}
        ):
            # Parametric-shape residuals are evaluated against entity metadata
            # by the canvas adapter. The line-only solver has no radius/center
            # data, so reporting infinity here would be a false conflict.
            residuals[constraint.id] = 0.0
            continue
        if constraint.kind == "coincident" and {
            "first_vertex",
            "second_vertex",
        } <= constraint.parameters.keys() and len(ids) >= 2:
            try:
                first_vertex = int(constraint.parameters["first_vertex"])
                second_vertex = int(constraint.parameters["second_vertex"])
                first_points, second_points = geometry.get(ids[0]), geometry.get(ids[1])
                if (
                    first_points is None
                    or second_points is None
                    or not 0 <= first_vertex < len(first_points)
                    or not 0 <= second_vertex < len(second_points)
                ):
                    raise IndexError
                residuals[constraint.id] = math.dist(
                    first_points[first_vertex], second_points[second_vertex]
                )
            except (TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "coincident" and "point_vertex" in constraint.parameters and len(ids) >= 2:
            first_ref = _constraint_line(geometry, constraint, 0)
            point_points = geometry.get(ids[1])
            try:
                point_vertex = int(constraint.parameters["point_vertex"])
                if first_ref is None or point_points is None:
                    raise IndexError
                residuals[constraint.id] = math.dist(
                    point_points[point_vertex], _project_onto_line(point_points[point_vertex], first_ref[:2])
                )
            except (TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "midpoint" and len(ids) >= 2:
            first_ref = _constraint_line(geometry, constraint, 0)
            point_points = geometry.get(ids[1])
            try:
                point_vertex = int(constraint.parameters["point_vertex"])
                if first_ref is None or point_points is None:
                    raise IndexError
                midpoint = (
                    (first_ref[0][0] + first_ref[1][0]) / 2,
                    (first_ref[0][1] + first_ref[1][1]) / 2,
                )
                residuals[constraint.id] = math.dist(point_points[point_vertex], midpoint)
            except (KeyError, TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "intersection" and len(ids) >= 3:
            first_ref = _constraint_line(geometry, constraint, 0)
            second_ref = _constraint_line(geometry, constraint, 1)
            point_points = geometry.get(ids[2])
            try:
                point_vertex = int(constraint.parameters["point_vertex"])
                if first_ref is None or second_ref is None or point_points is None:
                    raise IndexError
                crossing = _line_intersection(first_ref[:2], second_ref[:2])
                if crossing is None:
                    raise ValueError
                residuals[constraint.id] = math.dist(point_points[point_vertex], crossing)
            except (KeyError, TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "symmetric" and len(ids) >= 3:
            axis_ref = _constraint_line(geometry, constraint, 0)
            first_points, second_points = geometry.get(ids[1]), geometry.get(ids[2])
            try:
                first_vertex = int(constraint.parameters["first_vertex"])
                second_vertex = int(constraint.parameters["second_vertex"])
                if axis_ref is None or first_points is None or second_points is None:
                    raise IndexError
                reflected = _reflect_across_line(first_points[first_vertex], axis_ref[:2])
                residuals[constraint.id] = math.dist(second_points[second_vertex], reflected)
            except (KeyError, TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "smooth" and constraint.parameters.get("curve") and len(ids) >= 2:
            first_points, second_points = geometry.get(ids[0]), geometry.get(ids[1])
            if first_points is None or second_points is None or len(first_points) < 4 or len(second_points) < 3:
                residuals[constraint.id] = math.inf
                continue
            p1, p2, p3 = first_points[-3:]
            expected = (
                (p3[0], p3[1]),
                (2 * p3[0] - p2[0], 2 * p3[1] - p2[1]),
                (4 * p3[0] - 4 * p2[0] + p1[0], 4 * p3[1] - 4 * p2[1] + p1[1]),
            )
            residuals[constraint.id] = max(
                math.dist(point, target) for point, target in zip(second_points[:3], expected, strict=True)
            )
            continue
        first_ref = _constraint_line(geometry, constraint, 0) if ids else None
        first = first_ref[:2] if first_ref is not None else None
        if first is None:
            residuals[constraint.id] = math.inf
            continue
        if constraint.kind == "horizontal":
            residuals[constraint.id] = abs(first[1][1] - first[0][1])
            continue
        if constraint.kind == "vertical":
            residuals[constraint.id] = abs(first[1][0] - first[0][0])
            continue
        if constraint.kind == "fixed":
            stored = constraint.parameters.get("points")
            if not isinstance(stored, list):
                residuals[constraint.id] = math.inf
                continue
            try:
                residuals[constraint.id] = max(
                    math.dist(point, (float(target[0]), float(target[1])))
                    for point, target in zip(first, stored, strict=True)
                )
            except (TypeError, ValueError, IndexError):
                residuals[constraint.id] = math.inf
            continue
        second_ref = _constraint_line(geometry, constraint, 1) if len(ids) >= 2 else None
        second = second_ref[:2] if second_ref is not None else None
        if second is None:
            residuals[constraint.id] = math.inf
            continue
        av = (first[1][0] - first[0][0], first[1][1] - first[0][1])
        bv = (second[1][0] - second[0][0], second[1][1] - second[0][1])
        al, bl = math.hypot(*av), math.hypot(*bv)
        if min(al, bl) <= 1e-12:
            residuals[constraint.id] = math.inf
        elif constraint.kind in {"parallel", "smooth", "collinear"}:
            residuals[constraint.id] = abs(av[0] * bv[1] - av[1] * bv[0]) / (al * bl)
        elif constraint.kind == "perpendicular":
            residuals[constraint.id] = abs(av[0] * bv[0] + av[1] * bv[1]) / (al * bl)
        elif constraint.kind in {"equal", "equal_length"}:
            residuals[constraint.id] = abs(al - bl) / max(al, bl)
        elif constraint.kind == "coincident":
            first_end = max(0, min(1, int(constraint.parameters.get("first_endpoint", 1))))
            second_end = max(0, min(1, int(constraint.parameters.get("second_endpoint", 0))))
            residuals[constraint.id] = math.dist(first[first_end], second[second_end])
        else:
            residuals[constraint.id] = math.inf
    return residuals


__all__ = [
    "ConstraintKind",
    "GeometricConstraint",
    "constraint_residuals",
    "solve_constraints",
]
