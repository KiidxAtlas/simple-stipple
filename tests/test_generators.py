"""Pattern generators produce geometry for a simple square outline.

Param dicts use the same keys the UI sends (see src/ui/pages/pattern/_spec.py).
"""

import pytest
from shapely.geometry import Polygon

from src.ui.pages.pattern.services import PatternProcessingService

OUTLINE = Polygon([(0, 0), (200, 0), (200, 200), (0, 200)])

CASES = [
    ("Honeycomb", {"r": 5, "gap": 1}, 537),
    ("Gradient Honeycomb", {"r_min": 3, "r_max": 8, "gap": 1, "angle": 45}, 456),
    ("Stipple Dots", {"r": 0.5, "spacing": 3}, 2696),
    ("Penrose Tiling", {"scale": 20, "gap": 0.1}, 226),
    ("Hilbert Curve", {"order": 3, "margin": 1}, 1),
    (
        "Reaction Diffuse",
        {
            "cell": 2,
            "iters": 50,
            "threshold": 0.22,
            "seed": 42,
            "pattern": "labyrinth",
        },
        None,  # stochastic count; just require non-empty
    ),
    ("Square Grid", {"spacing": 10}, 41),
    ("Mesh", {"r": 1, "spacing": 20}, 100),
    ("Brick", {"brick_w": 10, "brick_h": 5, "gap": 1}, 665),
]


@pytest.mark.parametrize("name,params,expected", CASES, ids=[c[0] for c in CASES])
def test_generator_produces_polylines(name, params, expected):
    pps = PatternProcessingService()
    polys = pps._gen_pattern(OUTLINE, name, params)
    assert polys, f"{name} produced no geometry"
    assert all(len(p) >= 2 for p in polys)
    if expected is not None:
        assert len(polys) == expected
