from src.backend.preflight import analyze_geometry, scale_tolerance


def test_preflight_detects_duplicates_zero_segments_and_open_paths():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
    report = analyze_geometry([square, list(reversed(square)), [(0.0, 0.0), (0.0, 0.0)]])
    assert report.closed == 2
    assert report.open == 1
    assert report.duplicates == 1
    assert report.zero_segments == 1
    assert not report.ready
    kinds = {issue.kind for issue in report.issues}
    assert {"duplicate", "zero_segment", "open_start", "open_end"} <= kinds
    assert all(len(issue.point) == 2 for issue in report.issues)


def test_tolerance_scales_but_remains_bounded():
    assert scale_tolerance([[(0.0, 0.0), (1.0, 1.0)]]) >= 0.001
    assert scale_tolerance([[(0.0, 0.0), (1_000_000.0, 1_000_000.0)]]) == 0.05
