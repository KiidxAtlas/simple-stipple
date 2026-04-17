"""Image-to-outline tracing — converts a raster image to polyline lists.

Dependencies: opencv-python-headless, Pillow (display image only).

Pipeline — Region mode
-----------------------
1. _load_image       — load via PIL, composite on white, downscale, convert to BGR
2. _build_mask       — Gaussian blur + Otsu/fixed threshold + morphological close
3. _find_contours    — cv2.findContours with RETR_CCOMP (topology-aware)
4. simplify_contours — cv2.approxPolyDP  (Ramer–Douglas–Peucker)
5. filter_contours   — cv2.contourArea   (area filter)
6. scale_to_mm       — pixel coords → millimetres

Pipeline — Edge mode (line art / sketches)
------------------------------------------
Step 2 is replaced by _build_edge_mask (Canny + morphological dilation).
All other steps are identical.
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Poly = list[tuple[float, float]]


# Shared closure tolerance (mm / pixel space) — must match canvas _is_poly_closed.
_CLOSE_TOL = 0.01


def _close_poly(poly: Poly, tol: float = _CLOSE_TOL) -> Poly:
    """Return *poly* with the starting point appended if needed."""
    if len(poly) < 3:
        return list(poly)
    if abs(poly[0][0] - poly[-1][0]) <= tol and abs(poly[0][1] - poly[-1][1]) <= tol:
        return list(poly)
    return list(poly) + [poly[0]]


# ---------------------------------------------------------------------------
# Step 1 — load image
# ---------------------------------------------------------------------------


def _load_image(path: str, max_px: int = 1200) -> tuple[Image.Image, np.ndarray]:
    """Load image via PIL, composite on white, downscale; return (rgb_img, bgr_array)."""
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    w, h = img.size
    scale = min(max_px / max(w, h, 1), 1.0)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    bgr = cv2.cvtColor(np.array(img, dtype=np.uint8), cv2.COLOR_RGB2BGR)
    return img, bgr


# ---------------------------------------------------------------------------
# Step 2 — threshold + morphological close → binary mask
# ---------------------------------------------------------------------------


def _build_mask(
    bgr: np.ndarray,
    blur: float = 1.5,
    threshold: int | None = None,
    invert: bool = False,
    close_radius: int = 1,
) -> np.ndarray:
    """
    Gaussian blur → Otsu (or fixed) threshold → morphological close.

    Returns a uint8 binary mask (0 / 255).
    ``invert=False`` — dark pixels are foreground (default).
    ``threshold=None`` — auto-select via Otsu.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    ksize = max(1, int(blur * 2) | 1)  # must be odd
    blurred = cv2.GaussianBlur(gray, (ksize, ksize), blur)

    thresh_type = cv2.THRESH_BINARY_INV if not invert else cv2.THRESH_BINARY
    if threshold is None:
        thresh_type |= cv2.THRESH_OTSU
        _, mask = cv2.threshold(blurred, 0, 255, thresh_type)
    else:
        _, mask = cv2.threshold(blurred, int(threshold), 255, thresh_type)

    if close_radius > 0:
        sz = close_radius * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (sz, sz))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    return mask


# ---------------------------------------------------------------------------
# Step 2b — Canny edge detection → binary mask (Edge / line-art mode)
# ---------------------------------------------------------------------------


def _build_edge_mask(
    bgr: np.ndarray,
    blur: float = 1.0,
    canny_low: int = 50,
    canny_high: int = 150,
    close_radius: int = 1,
) -> np.ndarray:
    """
    Canny edge detection → morphological dilation to produce a binary edge mask.

    ``close_radius`` dilates the edges so thin strokes produce closed contours
    that cv2.findContours can trace reliably.  Returns a uint8 mask (0 / 255).
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ksize = max(1, int(blur * 2) | 1)
    blurred = cv2.GaussianBlur(gray, (ksize, ksize), blur)
    edges = cv2.Canny(blurred, int(canny_low), int(canny_high))
    if close_radius > 0:
        sz = close_radius * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (sz, sz))
        edges = cv2.dilate(edges, kernel)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    return edges


# ---------------------------------------------------------------------------
# Step 3 — contour extraction
# ---------------------------------------------------------------------------


def _find_contours(mask: np.ndarray, outer_only: bool = True) -> list[np.ndarray]:
    """Extract contours from binary mask.

    ``outer_only=True`` (default) uses ``cv2.RETR_CCOMP`` and keeps only
    top-level contours (parent == -1).  This prevents inner hole boundaries
    (e.g. inside letters A, B, D, O) from appearing as separate outlines.
    ``outer_only=False`` falls back to ``cv2.RETR_LIST`` and returns all
    contours.
    """
    if outer_only:
        contours, hierarchy = cv2.findContours(
            mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE
        )
        if hierarchy is None or len(contours) == 0:
            return []
        h = hierarchy[0]  # shape (N, 4): [next, prev, child, parent]
        return [c for c, hi in zip(contours, h) if hi[3] == -1]
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    return list(contours)


# ---------------------------------------------------------------------------
# Step 4 — simplification (replaces RDP)
# ---------------------------------------------------------------------------


def simplify_contours(contours: list[Poly], tolerance: float = 1.0) -> list[Poly]:
    """Ramer-Douglas-Peucker simplification via cv2.approxPolyDP.

    Contours are treated as closed loops and returned with an explicit closing
    point so downstream DXF export can preserve them as closed polylines.
    """
    result = []
    for pts in contours:
        arr = np.array(pts, dtype=np.float32).reshape(-1, 1, 2)
        perimeter = cv2.arcLength(arr, closed=True)
        epsilon = max(0.01, float(tolerance))
        if perimeter > 1e-6:
            # Cap at 5% of perimeter — 25% was far too aggressive and erased
            # fine detail (text, thin strokes) even at low tolerance settings.
            epsilon = min(epsilon, perimeter * 0.05)
        simplified = cv2.approxPolyDP(arr, epsilon, closed=True)
        coords = [(float(p[0][0]), float(p[0][1])) for p in simplified]
        if len(coords) >= 3:
            result.append(_close_poly(coords))
    return result


# ---------------------------------------------------------------------------
# Step 5 — area filtering (replaces shoelace formula)
# ---------------------------------------------------------------------------


def filter_contours(
    contours: list[Poly],
    min_area_px: float = 100.0,
    max_area_px: float | None = None,
) -> list[Poly]:
    """Filter contours by pixel-space area using cv2.contourArea."""
    result = []
    for c in contours:
        arr = np.array(c, dtype=np.float32).reshape(-1, 1, 2)
        a = cv2.contourArea(arr)
        if a < min_area_px:
            continue
        if max_area_px is not None and a > max_area_px:
            continue
        result.append(_close_poly(c))
    return result


# ---------------------------------------------------------------------------
# Step 6 — scale to mm
# ---------------------------------------------------------------------------


def scale_to_mm(
    contours: list[Poly],
    px_per_mm: float,
    img_height_px: int,
) -> list[Poly]:
    """Convert pixel-space (x, y) coordinates to millimetres (y-up origin)."""
    if px_per_mm <= 0:
        return contours
    return [
        [(x / px_per_mm, (img_height_px - y) / px_per_mm) for x, y in poly]
        for poly in contours
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def image_to_outlines(
    path: str,
    *,
    blur_radius: float = 1.5,
    threshold: int | None = None,
    invert: bool = False,
    close_radius: int = 1,
    simplify_tol: float = 2.0,
    min_area_px: float = 100.0,
    max_area_px: float | None = None,
    width_mm: float = 50.0,
    max_px: int = 1200,
    edge_mode: bool = False,
    canny_low: int = 50,
    canny_high: int = 150,
    outer_only: bool = True,
    on_progress: Callable[[int, str], None] | None = None,
) -> tuple[Image.Image, list[Poly], int, int]:
    """
    Run the full pipeline.

    Returns ``(display_image, mm_polylines, img_w_px, img_h_px)``.

    ``img_w_px`` / ``img_h_px`` are the *processed* (possibly downscaled)
    image dimensions.

    Parameters
    ----------
    edge_mode:
        Use Canny edge detection instead of threshold masking.  Better for
        line art, sketches, and images with thin strokes.
    canny_low / canny_high:
        Lower / upper hysteresis thresholds for Canny (edge mode only).
    outer_only:
        Discard inner hole contours so shapes like letters A/B/D/O do not
        produce spurious inner outlines.  Defaults to ``True``.
    on_progress:
        Optional ``(percent: int, label: str) -> None`` callback invoked at
        each pipeline step.  Safe to emit a Qt signal from a background thread.
    """

    def _progress(pct: int, label: str) -> None:
        if on_progress is not None:
            on_progress(pct, label)

    _progress(5, "Loading image\u2026")
    display_img, bgr = _load_image(path, max_px)
    img_h_px, img_w_px = bgr.shape[:2]

    _progress(25, "Building mask\u2026")
    if edge_mode:
        mask = _build_edge_mask(bgr, blur_radius, canny_low, canny_high, close_radius)
    else:
        mask = _build_mask(bgr, blur_radius, threshold, invert, close_radius)

    _progress(50, "Finding contours\u2026")
    raw_contours = _find_contours(mask, outer_only=outer_only)

    # Convert cv2 contours (N,1,2 int32) to Poly lists for shared helpers
    polys: list[Poly] = [
        [(float(p[0][0]), float(p[0][1])) for p in c]
        for c in raw_contours
        if len(c) >= 3
    ]

    _progress(70, "Simplifying\u2026")
    polys = simplify_contours(polys, simplify_tol)

    n_before = len(polys)
    _progress(85, f"Filtering {n_before} contour(s)\u2026")
    polys = filter_contours(polys, min_area_px, max_area_px)
    n_kept = len(polys)
    if n_kept < n_before:
        _progress(
            92,
            f"Kept {n_kept} of {n_before} contours (area filter removed {n_before - n_kept}).",
        )

    _progress(95, "Scaling to mm\u2026")
    px_per_mm = img_w_px / max(width_mm, 0.001)
    result = scale_to_mm(polys, px_per_mm, img_h_px)

    _progress(100, "Done.")
    return display_img, result, img_w_px, img_h_px
