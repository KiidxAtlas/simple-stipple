"""fit_polyline_to_bezier: reduces a dense/jagged polyline to bezier anchors
+ tangent handles, keeping real corners sharp (zero tangent) and giving
smooth runs a Catmull-Rom-style handle.
"""

from __future__ import annotations

import math

from src.backend.geometry import fit_polyline_to_bezier


def _fit(*args, **kwargs):
    result = fit_polyline_to_bezier(*args, **kwargs)
    assert result is not None
    return result


def _dense_arc(radius: float = 20.0, n: int = 80, start_deg=0.0, end_deg=90.0):
    return [
        (
            radius * math.cos(math.radians(start_deg + (end_deg - start_deg) * i / (n - 1))),
            radius * math.sin(math.radians(start_deg + (end_deg - start_deg) * i / (n - 1))),
        )
        for i in range(n)
    ]


def test_too_few_points_returns_none():
    assert fit_polyline_to_bezier([(0.0, 0.0), (1.0, 1.0)]) is None


def test_dense_smooth_arc_reduces_to_far_fewer_anchors():
    dense = _dense_arc(n=80)
    result = fit_polyline_to_bezier(dense, tolerance=0.5)
    assert result is not None
    anchors, tangents = result
    assert len(anchors) < len(dense) / 2
    assert len(tangents) == len(anchors)


def test_smooth_arc_interior_anchors_get_nonzero_tangents():
    dense = _dense_arc(n=80)
    anchors, tangents = _fit(dense, tolerance=0.5)
    # Interior anchors on a smooth arc should not be flagged as corners.
    interior_nonzero = [t for t in tangents[1:-1] if t != (0.0, 0.0)]
    assert len(interior_nonzero) > 0


def test_right_angle_corner_stays_a_corner():
    # An L-shape: straight along +x, then a hard 90 degree turn along +y.
    poly = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (10.0, 5.0), (10.0, 10.0)]
    anchors, tangents = _fit(poly, tolerance=0.01, corner_angle_deg=55.0)
    # The corner at (10, 0) must survive simplification (tight tolerance)
    # and be flagged as a zero-tangent corner anchor.
    corner_idx = anchors.index((10.0, 0.0))
    assert tangents[corner_idx] == (0.0, 0.0)


def test_gentle_bend_is_not_flagged_as_a_corner():
    # A very shallow bend (~10 degrees) should NOT be treated as a corner.
    poly = [(0.0, 0.0), (10.0, 0.0), (20.0, 1.7)]
    anchors, tangents = _fit(poly, tolerance=0.01, corner_angle_deg=55.0)
    mid_idx = anchors.index((10.0, 0.0))
    assert tangents[mid_idx] != (0.0, 0.0)


def test_noisy_corner_stays_sharp_not_a_small_fillet():
    """Regression: with jitter, Douglas-Peucker can keep two closely-spaced
    vertices straddling one real corner instead of a single vertex — each
    half of that turn then falls under the angle threshold on its own, and
    the corner renders as a small rounded fillet instead of a sharp point."""
    import random

    random.seed(1)
    pts = []
    for i in range(40):
        t = i / 39.0
        if t < 0.5:
            x, y = t / 0.5 * 30.0, 0.0
        else:
            x, y = 30.0, (t - 0.5) / 0.5 * 30.0
        j = random.uniform(-0.1, 0.1)
        pts.append((x + j, y + j))

    anchors, tangents = _fit(pts, tolerance=0.4)
    # The corner survives as a single vertex with an exactly-zero tangent —
    # not two adjacent near-zero-but-not-quite tangents either side of it.
    zero_tangents = [t for t in tangents if t == (0.0, 0.0)]
    assert len(zero_tangents) == 1


def test_closed_polygon_wraps_around_for_tangents():
    # A closed square — every vertex has both a prev and next via wraparound,
    # and each 90-degree corner should still be flagged.
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    result = fit_polyline_to_bezier(square, tolerance=0.01, closed=True)
    assert result is not None
    anchors, tangents = result
    assert len(anchors) == 4  # closing duplicate point dropped
    assert all(t == (0.0, 0.0) for t in tangents)  # every corner is sharp


def test_tangent_handle_length_scales_with_tension():
    dense = _dense_arc(n=80)
    _, tangents_low = _fit(dense, tolerance=0.5, tension=0.1)
    _, tangents_high = _fit(dense, tolerance=0.5, tension=0.5)
    len_low = math.hypot(*tangents_low[1])
    len_high = math.hypot(*tangents_high[1])
    assert len_high > len_low


def test_open_polyline_endpoints_get_one_sided_tangent():
    dense = _dense_arc(n=80)
    anchors, tangents = _fit(dense, tolerance=0.5)
    # Endpoints of an open curve should still get a (one-sided) handle,
    # not necessarily zero — only interior sharp turns are corners.
    assert math.hypot(*tangents[0]) > 0.0
    assert math.hypot(*tangents[-1]) > 0.0
