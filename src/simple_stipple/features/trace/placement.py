"""Where the traced image sits on the canvas, and the outlines with it.

The tracer produces outlines in the image's own frame: ``(0, 0)`` to
``(width, height)`` in millimetres. The user can then move, scale, and rotate
the picture; the outlines must stay glued to it, so they are mapped through
the same placement. Rotation turns about the placement's centre, matching how
the canvas draws a rotated background image.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Point = tuple[float, float]
Polyline = list[Point]


@dataclass(frozen=True)
class ImagePlacement:
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0

    def place(self, polys: Sequence[Sequence[Point]], source_size: Point) -> list[Polyline]:
        """Map outlines traced at ``source_size`` into this placement."""
        source_w, source_h = source_size
        sx = self.width_mm / source_w if source_w > 0 else 1.0
        sy = self.height_mm / source_h if source_h > 0 else 1.0
        cx, cy = self._center()
        cos_a, sin_a = self._cos_sin(self.rotation_deg)
        placed: list[Polyline] = []
        for poly in polys:
            out: Polyline = []
            for px, py in poly:
                dx = self.x_mm + px * sx - cx
                dy = self.y_mm + py * sy - cy
                out.append((cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a))
            placed.append(out)
        return placed

    def unplace(self, polys: Sequence[Sequence[Point]]) -> list[Polyline]:
        """Inverse of :meth:`place` at this placement's own size."""
        cx, cy = self._center()
        cos_a, sin_a = self._cos_sin(-self.rotation_deg)
        local: list[Polyline] = []
        for poly in polys:
            out: Polyline = []
            for px, py in poly:
                dx, dy = px - cx, py - cy
                ux = cx + dx * cos_a - dy * sin_a
                uy = cy + dx * sin_a + dy * cos_a
                out.append((ux - self.x_mm, uy - self.y_mm))
            local.append(out)
        return local

    def _center(self) -> Point:
        return self.x_mm + self.width_mm / 2.0, self.y_mm + self.height_mm / 2.0

    @staticmethod
    def _cos_sin(degrees: float) -> Point:
        radians = math.radians(degrees)
        return math.cos(radians), math.sin(radians)
