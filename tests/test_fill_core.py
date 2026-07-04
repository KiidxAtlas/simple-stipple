"""Fill-region construction and hatching."""

import pytest

from src.ui.pages.pattern.fill import FillSpec, apply_fill, build_fill_region

OUTER = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
INNER = [(30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0), (30.0, 30.0)]


def test_build_fill_region_subtracts_hole():
    region = build_fill_region([OUTER, INNER])
    assert region.area == pytest.approx(100 * 100 - 40 * 40)


def test_apply_fill_lines_skip_hole():
    region = build_fill_region([OUTER, INNER])
    spec = FillSpec(mode="lines", spacing=10, angle_deg=0.0, keep_pattern=True)
    lines = apply_fill(region, spec)
    assert len(lines) == 14
    # Every segment midpoint must lie outside the hole.
    for seg in lines:
        (x0, y0), (x1, y1) = seg[0], seg[-1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        inside_hole = 30 < mx < 70 and 30 < my < 70
        assert not inside_hole, f"fill line crosses hole: {seg}"


def test_fillspec_rejects_unknown_mode():
    with pytest.raises(ValueError):
        FillSpec(mode="nonsense")  # type: ignore[arg-type]  # deliberately invalid
