"""Single-source entity storage for the canvas.

``EntityRecord`` bundles what used to live in seven parallel index-keyed
structures on ``PolylineView``. Keeping those aligned by hand was the
dominant bug source in this codebase; geometry, kind, meta, and flags now
travel together, and call sites read ``self._entities[i].points`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Point = tuple[float, float]


@dataclass
class EntityRecord:
    """One drawable entity: geometry, parametric identity, and flags."""

    points: list[Point]
    kind: str = "polyline"
    meta: dict[str, Any] | None = None
    construction: bool = False
    hidden: bool = False
    locked: bool = False
    group: int | None = None
