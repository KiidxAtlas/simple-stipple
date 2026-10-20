"""Explicit state for multi-event pointer gestures.

Each gesture is a single object that exists exactly while the gesture is in
progress. It replaces groups of loosely related view flags (``_shift_drag`` +
``_band_start`` + ``_band_additive``, ``_lasso_active`` + ``_lasso_points`` …)
whose valid combinations were only implied by the code that set them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF

if TYPE_CHECKING:
    from simple_stipple.canvas.objects import DocumentSnapshot

# Lasso trail samples closer than this (in pixels) add no visible detail.
LASSO_MIN_STEP_PX = 3.0


class MarqueeShape(Enum):
    BOX = "box"
    LASSO = "lasso"


@dataclass
class Marquee:
    """A drag-to-select region, started on empty canvas."""

    shape: MarqueeShape
    start: QPointF
    additive: bool = False
    trail: list[QPointF] = field(default_factory=list)

    @classmethod
    def box(cls, start: QPointF, *, additive: bool) -> Marquee:
        return cls(MarqueeShape.BOX, QPointF(start), additive)

    @classmethod
    def lasso(cls, start: QPointF, *, additive: bool) -> Marquee:
        return cls(MarqueeShape.LASSO, QPointF(start), additive, [QPointF(start)])

    @property
    def is_box(self) -> bool:
        return self.shape is MarqueeShape.BOX

    @property
    def is_lasso(self) -> bool:
        return self.shape is MarqueeShape.LASSO

    def extend_trail(self, pos: QPointF) -> None:
        """Record a lasso sample when it has moved far enough from the last."""
        last = self.trail[-1] if self.trail else self.start
        if math.hypot(pos.x() - last.x(), pos.y() - last.y()) >= LASSO_MIN_STEP_PX:
            self.trail.append(QPointF(pos))


@dataclass
class MoveGesture:
    """Select-mode drag-to-move of the current selection.

    The gesture is *armed* on press and becomes *dragging* once the pointer
    passes the drag threshold; the pre-drag document snapshot is captured at
    that moment so release can commit and Escape can restore it.
    """

    origin_w: tuple[float, float]
    snapshot: DocumentSnapshot | None = None
    applied_w: tuple[float, float] = (0.0, 0.0)

    @property
    def dragging(self) -> bool:
        return self.snapshot is not None

    def begin_drag(self, snapshot: DocumentSnapshot) -> None:
        self.snapshot = snapshot
        self.applied_w = (0.0, 0.0)


__all__ = ["LASSO_MIN_STEP_PX", "Marquee", "MarqueeShape", "MoveGesture"]
