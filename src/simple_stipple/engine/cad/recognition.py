"""Recover editable parametric primitives from imported closed polylines."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

Point = tuple[float, float]


@dataclass(frozen=True)
class RecognizedShape:
    kind: str
    metadata: dict[str, object]
    confidence: float


def _closed_vertices(points: list[Point]) -> list[Point]:
    if len(points) >= 2 and math.dist(points[0], points[-1]) <= 1e-6:
        vertices = [
            point
            for index, point in enumerate(points[:-1])
            if index == 0 or math.dist(point, points[index - 1]) > 1e-8
        ]
        if len(vertices) <= 4:
            return vertices
        # Imported rectangles commonly contain many collinear tessellation
        # points. Remove only points that are effectively on the segment
        # joining their neighbours so true curved/organic paths stay intact.
        extent = max(
            max(point[0] for point in vertices) - min(point[0] for point in vertices),
            max(point[1] for point in vertices) - min(point[1] for point in vertices),
            1e-9,
        )
        simplified: list[Point] = []
        for index, current in enumerate(vertices):
            previous = vertices[index - 1]
            following = vertices[(index + 1) % len(vertices)]
            dx, dy = following[0] - previous[0], following[1] - previous[1]
            span = math.hypot(dx, dy)
            distance = (
                abs(dx * (previous[1] - current[1]) - (previous[0] - current[0]) * dy) / span
                if span > 1e-12
                else math.inf
            )
            if distance > extent * 0.002:
                simplified.append(current)
        return simplified if len(simplified) >= 3 else vertices
    return []


def _radial_fit(vertices: list[Point]) -> tuple[Point, float, float]:
    center = (
        sum(point[0] for point in vertices) / len(vertices),
        sum(point[1] for point in vertices) / len(vertices),
    )
    radii = [math.dist(center, point) for point in vertices]
    radius = sum(radii) / len(radii)
    deviation = max((abs(value - radius) for value in radii), default=float("inf"))
    return center, radius, deviation / max(radius, 1e-12)


def recognize_polyline(points: list[Point], *, tolerance: float = 0.015) -> RecognizedShape | None:
    """Recognize conservative circle/rectangle/regular-polygon candidates.

    Ambiguous low-sided rings are classified as polygons; dense radial rings
    (16+ vertices) are classified as circles. The conservative tolerance keeps
    organic/imported paths from being destructively relabeled.
    """
    vertices = _closed_vertices(points)
    if len(vertices) < 3 or any(not math.isfinite(v) for point in vertices for v in point):
        return None

    if len(vertices) == 4:
        vectors = [
            (
                vertices[(i + 1) % 4][0] - vertices[i][0],
                vertices[(i + 1) % 4][1] - vertices[i][1],
            )
            for i in range(4)
        ]
        lengths = [math.hypot(*vector) for vector in vectors]
        if min(lengths) > 1e-9:
            perpendicular_error = max(
                abs(
                    vectors[i][0] * vectors[(i + 1) % 4][0]
                    + vectors[i][1] * vectors[(i + 1) % 4][1]
                )
                / (lengths[i] * lengths[(i + 1) % 4])
                for i in range(4)
            )
            opposite_error = max(
                abs(lengths[0] - lengths[2]) / max(lengths[0], lengths[2]),
                abs(lengths[1] - lengths[3]) / max(lengths[1], lengths[3]),
            )
            error = max(perpendicular_error, opposite_error)
            if error <= tolerance:
                center = (
                    sum(point[0] for point in vertices) / 4.0,
                    sum(point[1] for point in vertices) / 4.0,
                )
                return RecognizedShape(
                    "rectangle",
                    {
                        "center": center,
                        "width": (lengths[0] + lengths[2]) / 2.0,
                        "height": (lengths[1] + lengths[3]) / 2.0,
                        "rotation": math.degrees(math.atan2(vectors[0][1], vectors[0][0])),
                    },
                    max(0.0, 1.0 - error / tolerance),
                )

    center, radius, radial_error = _radial_fit(vertices)
    if radius <= 1e-9 or radial_error > tolerance:
        return None
    angles = sorted(
        math.atan2(point[1] - center[1], point[0] - center[0]) % (2 * math.pi) for point in vertices
    )
    gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % (2 * math.pi) for i in range(len(angles))]
    expected_gap = 2 * math.pi / len(vertices)
    angular_error = max(abs(gap - expected_gap) for gap in gaps) / expected_gap
    if angular_error > tolerance * 2:
        return None
    confidence = max(0.0, 1.0 - max(radial_error, angular_error) / (tolerance * 2))
    if len(vertices) >= 16:
        return RecognizedShape("circle", {"center": center, "radius": radius}, confidence)
    first_angle = math.degrees(math.atan2(vertices[0][1] - center[1], vertices[0][0] - center[0]))
    return RecognizedShape(
        "polygon",
        {
            "center": center,
            "radius": radius,
            "sides": len(vertices),
            "rotation": first_angle + 90.0,
        },
        confidence,
    )


def convert_to_parametric(entity: Any, recognized: RecognizedShape | None = None) -> Any:
    """Return an editable parametric copy of a recognized polyline entity."""
    shape = recognized or recognize_polyline(list(entity.points))
    if shape is None:
        raise ValueError("Entity is not a recognized parametric shape")
    converted = deepcopy(entity)
    converted.kind = shape.kind
    meta = deepcopy(getattr(entity, "meta", None)) or {}
    meta.update(shape.metadata)
    meta["parametric"] = True
    meta["recognition_confidence"] = shape.confidence
    converted.meta = meta
    return converted


def recognized_entities(entities: list[Any]) -> list[tuple[int, RecognizedShape]]:
    """Find convertible closed polylines while preserving document indices."""
    found = []
    for index, entity in enumerate(entities):
        if getattr(entity, "kind", "polyline") not in {"polyline", "line"}:
            continue
        if shape := recognize_polyline(list(entity.points)):
            found.append((index, shape))
    return found


__all__ = [
    "RecognizedShape",
    "convert_to_parametric",
    "recognize_polyline",
    "recognized_entities",
]
