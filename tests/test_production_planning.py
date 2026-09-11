from __future__ import annotations

import pytest

from simple_stipple.core.cad.preflight import analyze_geometry
from simple_stipple.core.cad.production import (
    MachineProfile,
    apply_kerf_compensation,
    estimate_job,
    fits_machine_bed,
    machine_profile_from_settings,
    order_for_cut,
)

OUTER = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
INNER = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0), (5.0, 10.0), (5.0, 5.0)]


def test_estimate_job_reports_lengths_bounds_and_feed_time() -> None:
    estimate = estimate_job([[(0.0, 0.0), (3.0, 4.0)], [(6.0, 4.0), (6.0, 8.0)]], feed_rate_mm_s=2)

    assert estimate.paths == 2
    assert estimate.segments == 2
    assert estimate.cut_length_mm == pytest.approx(9.0)
    assert estimate.travel_length_mm == pytest.approx(3.0)
    assert estimate.bounds_mm == (0.0, 0.0, 6.0, 8.0)
    assert estimate.estimated_cut_seconds == pytest.approx(4.5)


def test_machine_bed_check_distinguishes_unconfigured_and_out_of_bounds() -> None:
    bounds = (0.0, 0.0, 110.0, 80.0)
    assert fits_machine_bed(bounds, MachineProfile()) is None
    assert (
        fits_machine_bed(bounds, MachineProfile(name="Small", bed_width_mm=100, bed_height_mm=100))
        is False
    )
    assert (
        fits_machine_bed(bounds, MachineProfile(name="Large", bed_width_mm=120, bed_height_mm=100))
        is True
    )


def test_machine_profile_reads_safe_zero_defaults_from_settings() -> None:
    profile = machine_profile_from_settings(
        {
            "machine_profile_name": "Shop laser",
            "machine_bed_width_mm": "300",
            "machine_bed_height_mm": 200,
            "machine_feed_rate_mm_s": "25",
            "machine_kerf_mm": "0.15",
        }
    )

    assert profile == MachineProfile("Shop laser", 300.0, 200.0, 25.0, 0.15)


def test_kerf_compensation_expands_or_contracts_closed_contours_only() -> None:
    outside = apply_kerf_compensation([OUTER], kerf_mm=2.0, mode="outside")
    inside = apply_kerf_compensation([OUTER], kerf_mm=2.0, mode="inside")
    open_path = [(0.0, 0.0), (5.0, 0.0)]

    assert estimate_job(outside).width_mm > estimate_job([OUTER]).width_mm
    assert estimate_job(inside).width_mm < estimate_job([OUTER]).width_mm
    assert apply_kerf_compensation([open_path], kerf_mm=2.0, mode="inside") == [open_path]


def test_cut_order_finishes_contained_contours_before_outer_boundary() -> None:
    ordered = order_for_cut([OUTER, INNER])

    assert ordered[0] == INNER
    assert ordered[1] == OUTER


def test_preflight_calls_out_self_intersections_separately() -> None:
    bow_tie = [(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)]

    report = analyze_geometry([bow_tie])

    assert report.self_intersections == 1
    assert any(issue.kind == "self_intersection" for issue in report.issues)


def test_preflight_explains_a_revisited_branch_junction() -> None:
    branched = [(0.0, 0.0), (4.0, 0.0), (2.0, 3.0), (0.0, 0.0), (2.0, 1.0)]

    report = analyze_geometry([branched])

    issue = next(issue for issue in report.issues if issue.kind == "self_intersection")
    assert issue.point == (0.0, 0.0)
    assert issue.message == "Path revisits a junction; split the branch into separate paths"
