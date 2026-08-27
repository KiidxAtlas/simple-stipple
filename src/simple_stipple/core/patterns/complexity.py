"""Pattern sizing, closure, and complexity-limit helpers."""

from __future__ import annotations

import math
from typing import Any

from simple_stipple.core.patterns.fill import NULL_PATTERN

MAX_ESTIMATED_ELEMENTS = 100_000


def estimate_pattern_elements(outline: Any, pattern: str, params: dict) -> int:
    """Return a conservative pre-generation element estimate."""
    if pattern == NULL_PATTERN or outline is None or outline.is_empty:
        return 0
    minx, miny, maxx, maxy = outline.bounds
    width, height = max(0.0, maxx - minx), max(0.0, maxy - miny)
    area = max(float(getattr(outline, "area", width * height)), 0.0)

    def positive(key: str, default: float = 1.0) -> float:
        value = float(params.get(key, default) or default)
        return max(value if math.isfinite(value) else float(default), 1e-6)

    if pattern == "Voronoi":
        return max(0, int(params.get("n_cells", 0) or 0))
    if pattern == "Knurling":
        return int(math.hypot(width, height) / positive("pitch") * 2) + 2
    if pattern == "Truchet":
        return int(area / positive("tile") ** 2 * 2) + 2
    if pattern == "Seigaiha":
        return int(area / positive("r") ** 2 * 2 * max(1.0, positive("rings", 3.0))) + 2
    if pattern in {"Stipple Dots", "Mesh"}:
        return int(area / positive("spacing") ** 2 * 1.5) + 1
    if pattern == "Honeycomb":
        step = positive("r", positive("r_min", 1.0)) + max(float(params.get("gap", 0) or 0), 0.0)
        return int(area / max(step * step * 2.0, 1e-9)) + 1
    if pattern == "Brick":
        cell = (positive("brick_w") + max(float(params.get("gap", 0) or 0), 0.0)) * (
            positive("brick_h") + max(float(params.get("gap", 0) or 0), 0.0)
        )
        return int(area / max(cell, 1e-9) * 1.5) + 1
    if pattern == "Basketweave":
        return int(area / max(positive("strip_w") * positive("strip_l"), 1e-9) * 2.0) + 1
    if pattern == "Custom Tile":
        points = [point for poly in params.get("tile_polys", []) for point in poly]
        if points:
            tile_w = max(x for x, _y in points) - min(x for x, _y in points)
            tile_h = max(y for _x, y in points) - min(y for _x, y in points)
            gap = max(float(params.get("gap", 0) or 0), 0.0)
            copies = area / max((tile_w + gap) * (tile_h + gap), 1e-9)
            return int(copies * max(len(params.get("tile_polys", [])), 1) * 1.5) + 1
    return 0


def validate_pattern_complexity(outline: Any, pattern: str, params: dict) -> int:
    estimate = estimate_pattern_elements(outline, pattern, params)
    if estimate > MAX_ESTIMATED_ELEMENTS:
        raise ValueError(
            f"Estimated {estimate:,} pattern elements exceeds the {MAX_ESTIMATED_ELEMENTS:,} safety limit. "
            "Increase spacing/size or use a smaller outline."
        )
    return estimate


def apply_scale(polys, sw: float, sh: float, *, orig_w: float, orig_h: float):
    """Scale polygons around their lower-left bounds origin."""
    if orig_w <= 0 or orig_h <= 0 or sw <= 0 or sh <= 0:
        return polys
    sx, sy = sw / orig_w, sh / orig_h
    if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
        return polys
    all_pts = [point for poly in polys for point in poly]
    if not all_pts:
        return polys
    xs, ys = zip(*all_pts)
    ox, oy = min(xs), min(ys)
    return [[(ox + (x - ox) * sx, oy + (y - oy) * sy) for x, y in poly] for poly in polys]
