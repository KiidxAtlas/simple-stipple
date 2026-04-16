"""Stipple pattern generation module."""

from __future__ import annotations

from src.core.generator_api import get_generator

Point = tuple[float, float]
Polyline = list[Point]


def generate_stipple(
    outline_poly: Polyline,
    radius: float,
    spacing: float,
    *,
    interlaced: bool = False,
) -> list[Polyline]:
    """Generate stipple geometry using selected stipple mode."""
    fn = "gen_stipple_interlaced" if interlaced else "gen_stipple_dots"
    return get_generator(fn)(outline_poly, radius, spacing)


__all__ = ["generate_stipple"]
