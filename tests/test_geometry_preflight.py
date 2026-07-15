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


def test_closed_duplicate_detection_ignores_start_vertex_rotation():
    first = [(0, 0), (10, 0), (10, 10), (0, 0)]
    rotated = [(10, 0), (10, 10), (0, 0), (10, 0)]
    report = analyze_geometry([first, rotated])
    assert report.duplicates == 1
    assert not report.ready


def test_nearly_closed_path_is_distinguished_from_exactly_closed():
    report = analyze_geometry([[(0, 0), (10, 0), (10, 10), (0.0005, 0)]])
    assert report.closed == 1
    assert report.near_closed == 1
    assert not report.ready
    assert any(issue.kind == "near_closed" for issue in report.issues)
