"""CAD-style absolute, relative, and polar coordinate parsing."""

from __future__ import annotations

import math

Point = tuple[float, float]


def parse_coordinate(text: str, *, origin: Point = (0.0, 0.0), scale: float = 1.0) -> Point:
    """Parse ``x,y``, ``@dx,dy``, or ``@distance<angle`` into world coordinates."""
    value = text.strip().replace(" ", "")
    relative = value.startswith("@")
    if relative:
        value = value[1:]
    if "<" in value:
        if not relative:
            raise ValueError("Polar coordinates must start with @")
        distance_text, angle_text = value.split("<", 1)
        distance = float(distance_text) * scale
        angle = math.radians(float(angle_text))
        return (
            origin[0] + distance * math.cos(angle),
            origin[1] + distance * math.sin(angle),
        )
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError("Use x,y, @dx,dy, or @distance<angle")
    x, y = float(parts[0]) * scale, float(parts[1]) * scale
    return (origin[0] + x, origin[1] + y) if relative else (x, y)


__all__ = ["parse_coordinate"]
