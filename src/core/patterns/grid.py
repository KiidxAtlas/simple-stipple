"""Grid pattern generation module."""

from __future__ import annotations

from src.core.generator_api import get_generator

Point = tuple[float, float]
Polyline = list[Point]


def generate_grid(outline_poly: Polyline, spacing: float) -> list[Polyline]:
    """Generate orthogonal grid lines clipped to outline."""
    return get_generator("gen_square_grid")(outline_poly, spacing)


__all__ = ["generate_grid"]
