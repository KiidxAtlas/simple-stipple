"""Small persistent geometric-constraint model and deterministic line solver."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

Point = tuple[float, float]
ConstraintKind = Literal[
    "horizontal", "vertical", "parallel", "perpendicular", "equal_length", "coincident", "fixed"
]


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
        if kind not in {
            "horizontal",
            "vertical",
            "parallel",
            "perpendicular",
            "equal_length",
            "coincident",
            "fixed",
        }:
            return None
        ids = tuple(str(item) for item in value.get("entity_ids", []) if item)
        if not ids:
            return None
        return cls(
            kind=kind,  # type: ignore[arg-type]
            entity_ids=ids,
            parameters=dict(value.get("parameters") or {}),
            id=str(value.get("id") or uuid4().hex),
            enabled=bool(value.get("enabled", True)),
        )


def _line(points: list[Point] | None) -> tuple[Point, Point] | None:
    if points is None or len(points) != 2 or math.dist(points[0], points[1]) <= 1e-12:
        return None
    return points[0], points[1]


def _directed(start: Point, length: float, angle: float) -> list[Point]:
    return [start, (start[0] + length * math.cos(angle), start[1] + length * math.sin(angle))]


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
            first = _line(solved.get(ids[0]))
            if first is None:
                continue
            if constraint.kind == "horizontal":
                y = (first[0][1] + first[1][1]) / 2.0
                solved[ids[0]] = [(first[0][0], y), (first[1][0], y)]
                continue
            if constraint.kind == "vertical":
                x = (first[0][0] + first[1][0]) / 2.0
                solved[ids[0]] = [(x, first[0][1]), (x, first[1][1])]
                continue
            if len(ids) < 2:
                continue
            second = _line(solved.get(ids[1]))
            if second is None:
                continue
            first_length = math.dist(*first)
            second_length = math.dist(*second)
            first_angle = math.atan2(first[1][1] - first[0][1], first[1][0] - first[0][0])
            if constraint.kind == "parallel":
                solved[ids[1]] = _directed(second[0], second_length, first_angle)
            elif constraint.kind == "perpendicular":
                solved[ids[1]] = _directed(second[0], second_length, first_angle + math.pi / 2)
            elif constraint.kind == "equal_length":
                angle = math.atan2(second[1][1] - second[0][1], second[1][0] - second[0][0])
                solved[ids[1]] = _directed(second[0], first_length, angle)
            elif constraint.kind == "coincident":
                first_end = int(constraint.parameters.get("first_endpoint", 1))
                second_end = int(constraint.parameters.get("second_endpoint", 0))
                target = first[max(0, min(1, first_end))]
                updated = list(second)
                updated[max(0, min(1, second_end))] = target
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
        first = _line(geometry.get(ids[0])) if ids else None
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
        second = _line(geometry.get(ids[1])) if len(ids) >= 2 else None
        if second is None:
            residuals[constraint.id] = math.inf
            continue
        av = (first[1][0] - first[0][0], first[1][1] - first[0][1])
        bv = (second[1][0] - second[0][0], second[1][1] - second[0][1])
        al, bl = math.hypot(*av), math.hypot(*bv)
        if min(al, bl) <= 1e-12:
            residuals[constraint.id] = math.inf
        elif constraint.kind == "parallel":
            residuals[constraint.id] = abs(av[0] * bv[1] - av[1] * bv[0]) / (al * bl)
        elif constraint.kind == "perpendicular":
            residuals[constraint.id] = abs(av[0] * bv[0] + av[1] * bv[1]) / (al * bl)
        elif constraint.kind == "equal_length":
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
