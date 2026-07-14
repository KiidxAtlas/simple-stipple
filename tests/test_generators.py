"""Pattern generators produce geometry for a simple square outline.

Param dicts use the same keys the UI sends (see src/ui/pages/pattern/_spec.py).
"""

import pytest
from shapely.geometry import Polygon

from src.backend.pattern.cancellation import (
    PatternGenerationCancelled,
    cancellation_scope,
)
from src.ui.pages.pattern.services import PatternProcessingService

OUTLINE = Polygon([(0, 0), (200, 0), (200, 200), (0, 200)])

CASES = [
    ("Flow Lines", {"spacing": 8, "amplitude": 5, "wavelength": 30, "angle": 25}, None),
    ("Honeycomb", {"r": 5, "gap": 1}, 537),
    ("Gradient Honeycomb", {"r_min": 3, "r_max": 8, "gap": 1, "angle": 45}, 456),
    ("Stipple Dots", {"r": 0.5, "spacing": 3}, None),
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


def test_density_field_is_deterministic_and_reduces_elements():
    pps = PatternProcessingService()
    params = {
        "r": 3,
        "gap": 1,
        "density_mode": "Horizontal",
        "density_strength": 0.9,
    }
    first = pps._gen_pattern(OUTLINE, "Honeycomb", params)
    second = pps._gen_pattern(OUTLINE, "Honeycomb", params)
    uniform = pps._gen_pattern(OUTLINE, "Honeycomb", {"r": 3, "gap": 1})
    assert first == second
    assert 0 < len(first) < len(uniform)


def test_density_field_supports_direction_and_reversal():
    pps = PatternProcessingService()
    base = {
        "r": 3,
        "gap": 1,
        "density_mode": "Horizontal",
        "density_strength": 0.9,
    }
    horizontal = pps._gen_pattern(OUTLINE, "Honeycomb", {**base, "density_angle": 0})
    vertical = pps._gen_pattern(OUTLINE, "Honeycomb", {**base, "density_angle": 90})
    reversed_field = pps._gen_pattern(
        OUTLINE, "Honeycomb", {**base, "density_angle": 0, "density_reverse": True}
    )
    assert horizontal != vertical
    assert horizontal != reversed_field


def test_preview_quality_changes_curve_resolution_not_element_count():
    pps = PatternProcessingService()
    base = {"r": 1, "spacing": 20}
    fast = pps._gen_pattern(OUTLINE, "Mesh", {**base, "quality": "fast"})
    high = pps._gen_pattern(OUTLINE, "Mesh", {**base, "quality": "high"})
    assert len(fast) == len(high)
    assert sum(map(len, fast)) < sum(map(len, high))


def test_complexity_guard_rejects_accidental_runaway_before_generation():
    pps = PatternProcessingService()
    with pytest.raises(ValueError, match="safety limit"):
        pps._gen_pattern(
            OUTLINE,
            "Flow Lines",
            {"spacing": 0.001, "amplitude": 2, "wavelength": 10, "angle": 0},
        )


def test_complexity_estimator_reports_normal_jobs_without_rejecting():
    estimate = PatternProcessingService.validate_pattern_complexity(
        OUTLINE, "Mesh", {"spacing": 10, "r": 1}
    )
    assert 0 < estimate < PatternProcessingService.MAX_ESTIMATED_ELEMENTS


def test_pattern_generation_can_cancel_during_outer_loop():
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with (
        cancellation_scope(cancelled),
        pytest.raises(PatternGenerationCancelled, match="cancelled"),
    ):
        PatternProcessingService()._gen_pattern(OUTLINE, "Mesh", {"r": 1, "spacing": 2})

    assert checks == 3
