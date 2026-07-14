"""Opt-in performance baselines; run with `pytest benchmarks --benchmark-only`."""

from __future__ import annotations

from shapely.geometry import box

from src.backend.pattern.tiling import gen_square_grid


def test_square_grid_10k_extent(benchmark):
    outline = box(0, 0, 10_000, 10_000)
    result = benchmark(gen_square_grid, outline, 10)
    assert result
