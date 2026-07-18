from __future__ import annotations

import pytest

from src.backend.pattern.output import clean_output, diagnose_output, order_paths


def test_cleanup_removes_micro_island_and_short_vertex():
    tiny = [(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.0)]
    path = [(0.0, 0.0), (0.01, 0.0), (2.0, 0.0)]
    cleaned = clean_output([tiny, path], minimum_segment=0.05, minimum_area=0.1)
    assert cleaned == [[(0.0, 0.0), (2.0, 0.0)]]


def test_path_order_places_nested_cutout_before_shell():
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    inner = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0), (5.0, 5.0)]
    assert order_paths([outer, inner]) == [inner, outer]


def test_diagnostics_reports_path_and_travel_lengths():
    diagnostics = diagnose_output([[(0.0, 0.0), (3.0, 4.0)], [(6.0, 8.0), (9.0, 12.0)]])
    assert diagnostics.total_length == pytest.approx(10.0)
    assert diagnostics.travel_length == pytest.approx(5.0)
    assert diagnostics.minimum_segment == pytest.approx(5.0)
