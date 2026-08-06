"""Pure polyline translation, rotation, scaling, and mirroring."""

from __future__ import annotations

import math
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

Point = tuple[float, float]
Polyline = list[Point]


def translate(points: Polyline, dx: float, dy: float) -> Polyline:
    return [(x + dx, y + dy) for x, y in points]


def translate_entities(entities: Iterable[Any], dx: float, dy: float) -> list[Any]:
    """Return independent translated copies of entity-like records.

    Stable source IDs are deliberately cleared when possible so inserting the
    results into a document always allocates fresh identities.
    """
    translated = []
    for source in entities:
        entity = deepcopy(source)
        entity.points = translate(list(source.points), dx, dy)
        if hasattr(entity, "id"):
            from simple_stipple.document.identity import new_entity_id

            entity.id = new_entity_id()
        translated.append(entity)
    return translated


def rotate(points: Polyline, center: Point, angle_deg: float) -> Polyline:
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    cx, cy = center
    return [
        (cx + (x - cx) * cosine - (y - cy) * sine, cy + (x - cx) * sine + (y - cy) * cosine)
        for x, y in points
    ]


def scale(points: Polyline, center: Point, factor: float) -> Polyline:
    if factor <= 0:
        raise ValueError("Scale factor must be positive")
    cx, cy = center
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points]


def mirror(points: Polyline, center: Point, axis: str) -> Polyline:
    cx, cy = center
    if axis == "horizontal":
        return [(2.0 * cx - x, y) for x, y in points]
    if axis == "vertical":
        return [(x, 2.0 * cy - y) for x, y in points]
    raise ValueError("Mirror axis must be 'horizontal' or 'vertical'")
