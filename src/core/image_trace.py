"""Image-to-outline tracing — converts a raster image to polyline lists.

Dependencies: opencv-python-headless, Pillow (display image only).

Pipeline
--------
1. _load_image     — load via PIL, composite on white, downscale, convert to BGR
2. _build_mask     — Gaussian blur + Otsu threshold + morphological close (all cv2)
3. _find_contours  — cv2.findContours (replaces marching squares)
4. _simplify       — cv2.approxPolyDP  (replaces RDP)
5. filter_contours — cv2.contourArea   (replaces shoelace formula)
6. scale_to_mm     — pixel coords → millimetres
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Poly = list[tuple[float, float]]

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
# Step 3 — contour extraction (replaces marching squares)
# ---------------------------------------------------------------------------


def _find_contours(mask: np.ndarray) -> list[np.ndarray]:
    """Extract external contours from binary mask using cv2.findContours."""
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return list(contours)


# ---------------------------------------------------------------------------
# Step 4 — simplification (replaces RDP)
# ---------------------------------------------------------------------------


def simplify_contours(contours: list[Poly], tolerance: float = 1.0) -> list[Poly]:
    """Ramer-Douglas-Peucker simplification via cv2.approxPolyDP."""
    result = []
    for pts in contours:
        arr = np.array(pts, dtype=np.float32).reshape(-1, 1, 2)
        simplified = cv2.approxPolyDP(arr, tolerance, closed=True)
        coords = [(float(p[0][0]), float(p[0][1])) for p in simplified]
        if len(coords) >= 3:
            result.append(coords)
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
        result.append(c)
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
    threshold: int | None = 128,
    invert: bool = False,
    close_radius: int = 1,
    simplify_tol: float = 2.0,
    min_area_px: float = 100.0,
    max_area_px: float | None = None,
    width_mm: float = 50.0,
    max_px: int = 1200,
) -> tuple[Image.Image, list[Poly], int, int]:
    """
    Run the full pipeline.

    Returns ``(display_image, mm_polylines, img_w_px, img_h_px)``.

    ``img_w_px`` / ``img_h_px`` are the *processed* (possibly downscaled)
    image dimensions — useful for computing the actual mm size of the image.
    """
    display_img, bgr = _load_image(path, max_px)
    img_h_px, img_w_px = bgr.shape[:2]

    mask = _build_mask(bgr, blur_radius, threshold, invert, close_radius)
    raw_contours = _find_contours(mask)

    # Convert cv2 contours (N,1,2 int32) to Poly lists for shared helpers
    polys: list[Poly] = [
        [(float(p[0][0]), float(p[0][1])) for p in c]
        for c in raw_contours
        if len(c) >= 3
    ]

    polys = simplify_contours(polys, simplify_tol)
    polys = filter_contours(polys, min_area_px, max_area_px)

    px_per_mm = img_w_px / max(width_mm, 0.001)
    return display_img, scale_to_mm(polys, px_per_mm, img_h_px), img_w_px, img_h_px
