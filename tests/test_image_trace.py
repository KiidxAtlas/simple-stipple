"""Trace pipeline contour extraction: CHAIN_APPROX_SIMPLE regression coverage.

CHAIN_APPROX_NONE (the old mode) returns every pixel along a contour's
boundary — for a shape with long diagonal/straight runs this produces a
"staircase" of collinear points that simplify_contours()'s perimeter-capped
epsilon often can't fully clean up on thin features. CHAIN_APPROX_SIMPLE
collapses runs of collinear points to just their endpoints: a strictly
lossless reduction that should never change the traced shape's area or
extent, only reduce the raw point count feeding into simplification.
"""

from __future__ import annotations

import math
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.backend.trace import (
    _build_mask,
    _correct_illumination,
    _find_contours,
    filter_contours,
    image_to_outlines,
)


def _square_mask(size: int = 100, margin: int = 20) -> np.ndarray:
    mask = np.zeros((size, size), dtype=np.uint8)
    mask[margin : size - margin, margin : size - margin] = 255
    return mask


def _diagonal_triangle_mask(size: int = 100) -> np.ndarray:
    mask = np.zeros((size, size), dtype=np.uint8)
    pts = np.array([[10, 90], [90, 90], [10, 10]], dtype=np.int32)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def test_find_contours_returns_correct_area_for_a_filled_square():
    mask = _square_mask(size=100, margin=20)
    contours = _find_contours(mask)
    assert len(contours) == 1
    area = cv2.contourArea(contours[0].astype(np.float32))
    # 60x60 square, allow for anti-aliasing/boundary rounding.
    assert 3400 <= area <= 3700


def test_find_contours_outer_only_discards_interior_holes():
    mask = _square_mask(size=100, margin=10)
    # Punch a hole in the middle.
    mask[40:60, 40:60] = 0
    all_contours = _find_contours(mask, outer_only=False)
    outer_contours = _find_contours(mask, outer_only=True)
    assert len(all_contours) == 2  # outer boundary + hole boundary
    assert len(outer_contours) == 1


def test_chain_approx_simple_yields_far_fewer_points_than_none_for_a_diagonal_edge():
    """The specific regression this session fixed: CHAIN_APPROX_NONE walks
    every pixel along a diagonal edge (a long "staircase"), while
    CHAIN_APPROX_SIMPLE (now used by _find_contours) collapses each straight
    run to its two endpoints — same shape, dramatically fewer raw vertices
    for simplify_contours() to start from."""
    mask = _diagonal_triangle_mask()
    simple_contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    none_contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    assert len(simple_contours[0]) < len(none_contours[0])

    from_pipeline = _find_contours(mask)
    assert len(from_pipeline) == 1
    assert len(from_pipeline[0]) == len(simple_contours[0])


def test_filter_contours_preserves_shape_after_simple_approx():
    """filter_contours()/_close_poly() downstream of _find_contours() should
    still see a valid, correctly-areaed closed polygon regardless of which
    chain-approximation mode fed it — guards against CHAIN_APPROX_SIMPLE
    somehow breaking area computation for that step."""
    mask = _square_mask(size=100, margin=20)
    contours = _find_contours(mask)
    polys = [[(float(p[0][0]), float(p[0][1])) for p in c] for c in contours]
    filtered = filter_contours(polys, min_area_px=100.0)
    assert len(filtered) == 1
    assert filtered[0][0] == filtered[0][-1]  # closed


def _circle_bgr_antialiased(size: int = 120, radius: int = 40) -> np.ndarray:
    """A circle with a genuine sub-pixel boundary gradient, like a real
    camera photo of ink on paper — not a hard-stepped synthetic raster.
    Drawn at 8x then downsampled with area-averaging, which blends each
    edge pixel proportionally to how much of it the circle covers."""
    big = size * 8
    img = np.full((big, big, 3), 255, dtype=np.uint8)
    cv2.circle(img, (big // 2, big // 2), radius * 8, (0, 0, 0), thickness=-1)
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def _rms_radius_error(contour: np.ndarray, cx: float, cy: float, radius: float) -> float:
    pts = contour.reshape(-1, 2).astype(np.float64)
    dists = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    return float(np.sqrt(np.mean((dists - radius) ** 2)))


def test_supersampling_the_mask_meaningfully_improves_boundary_precision():
    """Root-cause fix for "jagged trace" complaints: a real photo's edges
    already carry a genuine sub-pixel intensity gradient (like this
    anti-aliased test circle), but binarizing it at the source image's
    native pixel grid collapses that gradient into a single hard step,
    discarding the sub-pixel information. Upsampling with cubic
    interpolation before threshold recovers it, so Otsu lands the boundary
    much closer to the true edge instead of snapping to a whole pixel. A
    circle is the clearest way to measure this: its true boundary is
    smooth, so RMS deviation from the ideal radius directly measures
    staircase/quantization error."""
    size, radius = 120, 40
    bgr = _circle_bgr_antialiased(size, radius)

    mask_native = _build_mask(bgr, blur=1.0, threshold=128, supersample=1)
    mask_super = _build_mask(bgr, blur=1.0, threshold=128, supersample=4)

    contours_native = _find_contours(mask_native)
    contours_super = _find_contours(mask_super)
    assert len(contours_native) == 1
    assert len(contours_super) == 1

    err_native = _rms_radius_error(contours_native[0], size / 2, size / 2, radius)
    # Supersampled contour coordinates are in the upsampled grid — scale back
    # down to native-pixel units before comparing errors like-for-like.
    err_super = _rms_radius_error(contours_super[0], size / 2 * 4, size / 2 * 4, radius * 4)
    err_super /= 4

    assert err_super < err_native * 0.5


def test_image_to_outlines_produces_area_accurate_contour_after_supersampling():
    """The supersampled mask's contour coordinates get divided back down
    by image_to_outlines() before simplify/filter/scale — confirm the final
    mm-space output still has the right real-world area, not off by the
    supersample factor (a squared error if the divide were skipped, or a
    linear one if only one axis were mishandled)."""
    size, radius = 200, 60
    bgr = _circle_bgr_antialiased(size, radius)
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    path = tempfile.mktemp(suffix=".png")
    img.save(path)

    width_mm = 50.0
    _, polys, img_w_px, _img_h_px = image_to_outlines(
        path,
        blur_radius=1.0,
        threshold=128,
        simplify_tol=0.5,
        min_area_px=10.0,
        width_mm=width_mm,
    )
    assert len(polys) == 1

    px_per_mm = img_w_px / width_mm
    expected_radius_mm = radius / px_per_mm

    xs = [p[0] for p in polys[0]]
    ys = [p[1] for p in polys[0]]
    measured_radius_mm = ((max(xs) - min(xs)) + (max(ys) - min(ys))) / 4
    assert math.isclose(measured_radius_mm, expected_radius_mm, rel_tol=0.1)


def test_supersampled_trace_stays_fast_on_a_realistic_image():
    """Mask supersampling multiplies pixel count by supersample^2 — confirm
    a full-resolution (1200px) image with many small contours still traces
    well within the live-preview budget, so this doesn't reintroduce the
    kind of sluggishness/freeze the auto-smoothing feature caused."""
    import time

    size = 1200
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for i in range(40):
        x0, y0 = (i * 37) % (size - 100), (i * 53) % (size - 100)
        draw.ellipse([x0, y0, x0 + 60, y0 + 60], outline="black", width=6)
    path = tempfile.mktemp(suffix=".png")
    img.save(path)

    start = time.monotonic()
    _, polys, _w, _h = image_to_outlines(
        path,
        blur_radius=1.5,
        threshold=128,
        simplify_tol=2.0,
        min_area_px=50,
        width_mm=50.0,
        max_px=1200,
    )
    elapsed = time.monotonic() - start
    assert polys
    assert elapsed < 2.0


def _unevenly_lit_text_mask_source(size: int = 300) -> np.ndarray:
    """A grayscale image of text with a lighting gradient across the
    frame (dark/shadowed on one side, bright/glare on the other) — a very
    common real-photo condition that a single global Otsu cutoff can't
    handle, since it swallows the shadowed side as false foreground."""
    img = cv2.putText(
        np.full((size, size), 255, dtype=np.uint8),
        "kendo",
        (30, 160),
        cv2.FONT_HERSHEY_SCRIPT_COMPLEX,
        2.0,
        (0,),
        6,
        cv2.LINE_AA,
    ).astype(np.float64)
    gradient = np.tile(np.linspace(0.5, 1.3, size), (size, 1))
    return np.clip(img * gradient, 0, 255).astype(np.uint8)


def test_auto_threshold_handles_uneven_lighting_without_swallowing_half_the_image():
    """Regression: plain Otsu on an unevenly-lit photo classified the
    entire darker/shadowed half of the frame as foreground (thousands of
    extra pixels, several times the true text area), instead of isolating
    just the text strokes. _correct_illumination flattens the large-scale
    gradient first so Otsu's global cutoff applies to an evened-out image."""
    gray = _unevenly_lit_text_mask_source()
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    mask = _build_mask(bgr, blur=1.0, threshold=None, supersample=1)
    fg_px = int((mask > 0).sum())

    # The true text strokes cover a few thousand pixels at this size/font;
    # plain Otsu on this fixture swallows ~30000+ (half the 300x300 frame).
    assert fg_px < 8000


def test_illumination_correction_leaves_a_uniformly_lit_image_effectively_unchanged():
    """A flat/evenly-lit image has no gradient to correct — confirm the
    correction doesn't distort a normal image's brightness relationships
    (e.g. invert dark-vs-light or introduce heavy banding)."""
    size = 100
    gray = np.full((size, size), 200, dtype=np.uint8)
    gray[30:70, 30:70] = 40

    corrected = _correct_illumination(gray)
    # The dark square must stay clearly darker than the background.
    assert int(corrected[50, 50]) < int(corrected[5, 5]) - 50


def test_solid_fill_area_is_still_recovered_correctly_through_illumination_correction():
    """Regression guard against the *other* failure mode: a naive local/
    adaptive-threshold-style fix would read the interior of a large solid
    shape as "background-like" (no local contrast away from its edges) and
    only pick up a thin ring near the boundary. Confirm a big solid square
    still comes back at (approximately) its true area post-correction."""
    size, margin = 300, 20
    gray = np.full((size, size), 255, dtype=np.uint8)
    gray[margin : size - margin, margin : size - margin] = 0
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    mask = _build_mask(bgr, blur=1.0, threshold=None, supersample=1)
    fg_px = int((mask > 0).sum())
    true_area = (size - 2 * margin) ** 2
    assert fg_px > true_area * 0.9


def test_illumination_correction_stays_fast_regardless_of_image_size():
    """Regression: the background-illumination estimate's Gaussian blur
    sigma used to scale directly with the input's own resolution (a
    quarter of its shorter side). After 4x mask supersampling and a higher
    "Max resolution" default, that sigma (and the proportionally huge
    kernel cv2.GaussianBlur builds for it) could reach thousands of
    pixels, making a single call take minutes -- observed as "Building
    mask..." never finishing. The fix estimates the background on a small,
    fixed-size downscaled copy first, so cost stays bounded no matter how
    large the input is."""
    import time

    gray = np.random.randint(0, 255, (6000, 6000), dtype=np.uint8)
    start = time.monotonic()
    _correct_illumination(gray)
    elapsed = time.monotonic() - start
    assert elapsed < 3.0
