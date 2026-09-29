"""Dense, irregular grip-stipple pattern geometry."""

from __future__ import annotations

import math
import random

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import Polygon

from simple_stipple.core.geometry import poisson_disk_points
from simple_stipple.core.patterns.cancellation import cancellation_checkpoint
from simple_stipple.core.patterns.geometry import (
    NO_REPEAT,
    _coords_to_polyline,
    is_repeating,
    periodic_poisson_points,
    stamp_tile_points,
)

_SIDES = 7


def _grain_profile(rng: random.Random, element_size: float) -> list[tuple[float, float]]:
    """One grain's (angle, radius) vertices around its centre."""
    phase = rng.uniform(0.0, math.tau)
    return [
        (phase + math.tau * index / _SIDES, element_size * rng.uniform(0.58, 1.0))
        for index in range(_SIDES)
    ]


def gen_grip_stipple(
    outline_poly,
    element_size: float,
    spacing: float,
    *,
    seed: int = 42,
    repeat_mode: str = NO_REPEAT,
    repeat_size: float = 10.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Generate irregular raised grains with narrow negative-space grooves.

    ``element_size`` is the approximate radius of each grain. Poisson-disk
    spacing keeps neighboring grains from crowding, while seeded radial
    variation gives each one a rough, angular silhouette. With a lattice
    ``repeat_mode`` one square tile of ``repeat_size`` — grain positions and
    shapes — is sampled seamlessly and repeated.
    """
    if element_size <= 0 or spacing <= 0:
        return []

    minx, miny, maxx, maxy = outline_poly.bounds
    if maxx <= minx or maxy <= miny:
        return []

    seed_value = int(seed)
    rng = random.Random(seed_value)
    if is_repeating(repeat_mode):
        size = max(float(repeat_size), spacing)
        tile = periodic_poisson_points(size, size, spacing, seed_value, repeat_mode)
        if not tile:
            return []
        tile_profiles = [_grain_profile(rng, element_size) for _ in tile]
        centers = stamp_tile_points(
            outline_poly,
            tile,
            size,
            size,
            pad=size + element_size,
            repeat_mode=repeat_mode,
            origin_x=origin_x,
            origin_y=origin_y,
        )
        profiles = [tile_profiles[index % len(tile)] for index in range(len(centers))]
    else:
        sampled = poisson_disk_points(minx, miny, maxx, maxy, spacing, seed_value % (2**32))
        centers = [(float(x), float(y)) for x, y in sampled]
        profiles = [_grain_profile(rng, element_size) for _ in centers]

    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    for (cx, cy), profile in zip(centers, profiles, strict=True):
        cancellation_checkpoint()
        grain = Polygon(
            [
                (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                for angle, radius in profile
            ]
        )
        if not grain.is_valid or grain.area <= 1e-9 or not prep.intersects(grain):
            continue
        clipped = grain if prep.contains(grain) else outline_poly.intersection(grain)
        if clipped.is_empty:
            continue
        polygons = [clipped] if clipped.geom_type == "Polygon" else getattr(clipped, "geoms", ())
        for polygon in polygons:
            if polygon.geom_type == "Polygon" and polygon.area >= 0.001:
                result.append(_coords_to_polyline(polygon.exterior.coords))

    return result
