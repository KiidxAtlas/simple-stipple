"""Image-to-outline tracing pipeline."""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from PIL import Image
import base64
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

# Trace imaging is intentionally co-located with raster preparation: both are
# pure Pillow/OpenCV transformations that translate between a source image and
# manufacturing geometry.

Poly = list[tuple[float, float]]


class TraceCancelled(Exception):
    """Raised when a newer trace supersedes a background tracing operation."""


# Keep this value aligned with the canvas closure tolerance. It is duplicated
# here deliberately so the imaging engine stays independent of the canvas.
_CLOSE_TOL = 0.01

# Binarizing/edge-detecting at the source image's native pixel grid makes
# every boundary a hard staircase of whole-pixel steps — that's the actual
# root cause of "jagged" traces, not something a downstream smoothing pass
# can fully undo without guessing. Upsampling with cubic interpolation first
# (in _build_mask/_build_edge_mask) turns each edge into a continuous
# intensity/gradient ramp, so Otsu/Canny lands the boundary at sub-pixel
# precision instead of on a whole original pixel. Contour coordinates are
# divided back down by this factor immediately after extraction so every
# other step keeps working in the original image's pixel units.
_MASK_SUPERSAMPLE = 4


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
    # Clamp ``max_px`` to a sane range so a malicious or corrupted call
    # site cannot allocate gigabytes of RGBA pixels.
    max_px = max(64, min(int(max_px), 8192))
    img: Image.Image = Image.open(path)
    # Reject pathologically large source images outright — PIL otherwise
    # decompresses the full file before we get a chance to downscale.
    src_w, src_h = img.size
    if src_w * src_h > 80_000_000:  # ~80 MP — well above any realistic input
        raise ValueError(f"Image too large to trace: {src_w}×{src_h}px (limit ~80 MP)")
    img = img.convert("RGBA")
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


_ILLUM_WORK_DIM = 256


def _correct_illumination(gray: np.ndarray, sigma_frac: float = 0.25) -> np.ndarray:
    """Flatten large-scale lighting gradients (shadow, vignette, glare)
    before Otsu picks a global cutoff.

    Divides the image by a very heavily blurred copy of itself, wide enough
    to capture only slow, whole-frame lighting variation and not any real
    traced feature. This is deliberately NOT the same idea as per-pixel
    adaptive thresholding (cv2.adaptiveThreshold): that uses a small local
    window, which reads the *interior* of any solid shape bigger than the
    window as flat/backgroundlike and drops it — exactly wrong for tracing
    solid fills. A wide-sigma illumination estimate stays flat across a
    solid shape's interior (so Otsu still recovers the whole fill) while
    still correcting a gradient that spans the frame.

    The background estimate is computed on a small, fixed-size downscaled
    copy rather than the input at full resolution. An illumination gradient
    is inherently low-frequency, so a coarse estimate upsampled back is
    indistinguishable from blurring at full size — but doing it this way
    keeps the Gaussian kernel small and bounded regardless of the input's
    resolution. Blurring at full size with a sigma proportional to a large
    (and, after mask supersampling, possibly multi-thousand-pixel) image
    directly scales the kernel size with it, which previously made "Building
    mask..." take an extremely long time (or appear to hang entirely) on
    higher "Max resolution" / supersampled traces.
    """
    h, w = gray.shape[:2]
    scale = _ILLUM_WORK_DIM / max(h, w, 1)
    if scale < 1.0:
        small = cv2.resize(
            gray,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = gray
    small_h, small_w = small.shape[:2]
    sigma = max(15.0, min(small_h, small_w) * sigma_frac)
    background_small = cv2.GaussianBlur(small.astype(np.float32), (0, 0), sigma)
    background = (
        cv2.resize(background_small, (w, h), interpolation=cv2.INTER_LINEAR)
        if scale < 1.0
        else background_small
    )
    gray_f = gray.astype(np.float32)
    normalized = cv2.divide(gray_f, background + 1e-3, scale=255.0)
    return np.clip(normalized, 0, 255).astype(np.uint8)


def _build_mask(
    bgr: np.ndarray,
    blur: float = 1.5,
    threshold: int | None = None,
    invert: bool = False,
    close_radius: int = 1,
    supersample: int = 3,
) -> np.ndarray:
    """
    Gaussian blur → Otsu (or fixed) threshold → morphological close.

    Returns a uint8 binary mask (0 / 255) at ``supersample``x the input
    resolution — see the module-level note above ``SUPERSAMPLE`` for why.
    ``invert=False`` — dark pixels are foreground (default).
    ``threshold=None`` — auto-select via Otsu.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if supersample > 1:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (w * supersample, h * supersample), interpolation=cv2.INTER_CUBIC)

    # blur/close_radius are specified in original-pixel units; scale them up
    # to match so they behave identically regardless of the supersample factor.
    eff_blur = blur * supersample
    eff_close_radius = close_radius * supersample

    ksize = max(1, int(eff_blur * 2) | 1)  # must be odd
    blurred = cv2.GaussianBlur(gray, (ksize, ksize), eff_blur)

    thresh_type = cv2.THRESH_BINARY_INV if not invert else cv2.THRESH_BINARY
    if threshold is None:
        # A single Otsu cutoff assumes even, uniform lighting across the
        # whole frame — on a real photo with any shadow/glare/vignette
        # gradient it swallows entire regions as false foreground (or
        # erases faint strokes) since one brightness split can't separate
        # ink from background differently on each side of the image.
        # _correct_illumination flattens that large-scale gradient first,
        # so Otsu is choosing a cutoff from an already-evened-out image.
        corrected = _correct_illumination(blurred)
        thresh_type |= cv2.THRESH_OTSU
        _, mask = cv2.threshold(corrected, 0, 255, thresh_type)
    else:
        _, mask = cv2.threshold(blurred, int(threshold), 255, thresh_type)

    if eff_close_radius > 0:
        sz = eff_close_radius * 2 + 1
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
    supersample: int = 3,
) -> np.ndarray:
    """
    Canny edge detection → morphological dilation to produce a binary edge mask.

    ``close_radius`` dilates the edges so thin strokes produce closed contours
    that cv2.findContours can trace reliably.  Returns a uint8 mask (0 / 255)
    at ``supersample``x the input resolution.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if supersample > 1:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (w * supersample, h * supersample), interpolation=cv2.INTER_CUBIC)
    eff_blur = blur * supersample
    eff_close_radius = close_radius * supersample
    ksize = max(1, int(eff_blur * 2) | 1)
    blurred = cv2.GaussianBlur(gray, (ksize, ksize), eff_blur)
    edges = cv2.Canny(blurred, int(canny_low), int(canny_high))
    if eff_close_radius > 0:
        sz = eff_close_radius * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (sz, sz))
        edges = cv2.dilate(edges, kernel)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    return edges


# ---------------------------------------------------------------------------
# Step 3 — contour extraction
# ---------------------------------------------------------------------------


def _find_contours(mask: np.ndarray, outer_only: bool = False) -> list[np.ndarray]:
    """Extract contours from binary mask.

    ``outer_only=False`` (default) uses ``cv2.RETR_LIST`` and returns all
    contours — outer boundaries *and* interior holes, so lettering with
    counters (o, a, p, d…) traces faithfully.
    ``outer_only=True`` uses ``cv2.RETR_CCOMP`` and keeps only top-level
    contours (parent == -1), discarding hole boundaries for a silhouette-only
    trace.
    """
    # CHAIN_APPROX_SIMPLE drops points that lie on a straight run between two
    # pixel-grid corners — a strictly free win over CHAIN_APPROX_NONE (every
    # boundary pixel): it can only remove *redundant* collinear points, never
    # change the traced shape, yet it means simplify_contours() below starts
    # from far fewer staircase vertices per diagonal edge.
    if outer_only:
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None or len(contours) == 0:
            return []
        h = hierarchy[0]  # shape (N, 4): [next, prev, child, parent]
        return [c for c, hi in zip(contours, h) if hi[3] == -1]
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
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
        [(x / px_per_mm, (img_height_px - y) / px_per_mm) for x, y in poly] for poly in contours
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
    simplify_tol: float = 1.0,
    min_area_px: float = 100.0,
    max_area_px: float | None = None,
    width_mm: float = 50.0,
    max_px: int = 2500,
    edge_mode: bool = False,
    canny_low: int = 50,
    canny_high: int = 150,
    outer_only: bool = False,
    on_progress: Callable[[int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
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
        Discard inner hole contours for a silhouette-only trace.  Defaults to
        ``False`` so letter counters (o, a, p, d…) are included.
    on_progress:
        Optional ``(percent: int, label: str) -> None`` callback invoked at
        each pipeline step.  Safe to emit a Qt signal from a background thread.
    cancel_check:
        Optional ``() -> bool`` polled between pipeline stages; when it
        returns True, raises ``TraceCancelled`` instead of continuing. Lets
        a live-preview caller abort a stale, superseded trace after its
        current stage instead of always burning through the full pipeline
        (important now that mask-building runs at ``_MASK_SUPERSAMPLE``x the
        pixel count) — otherwise every rapid settings tweak queues up
        another full-cost trace competing for CPU, which is what made the
        UI feel like it "eventually freezes" under fast repeated edits.
    """

    def _progress(pct: int, label: str) -> None:
        if on_progress is not None:
            on_progress(pct, label)

    def _check_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            raise TraceCancelled()

    _progress(5, "Loading image\u2026")
    display_img, bgr = _load_image(path, max_px)
    img_h_px, img_w_px = bgr.shape[:2]
    _check_cancelled()

    _progress(25, "Building mask\u2026")
    if edge_mode:
        mask = _build_edge_mask(
            bgr, blur_radius, canny_low, canny_high, close_radius, _MASK_SUPERSAMPLE
        )
    else:
        mask = _build_mask(bgr, blur_radius, threshold, invert, close_radius, _MASK_SUPERSAMPLE)
    _check_cancelled()

    _progress(50, "Finding contours\u2026")
    raw_contours = _find_contours(mask, outer_only=outer_only)
    _check_cancelled()

    # Convert cv2 contours (N,1,2 int32) to Poly lists for shared helpers, and
    # divide back down out of the supersampled mask's pixel grid so every
    # downstream step (simplify/filter/scale) keeps operating in the same
    # original-image pixel units its tolerances/areas are tuned for.
    polys: list[Poly] = [
        [(float(p[0][0]) / _MASK_SUPERSAMPLE, float(p[0][1]) / _MASK_SUPERSAMPLE) for p in c]
        for c in raw_contours
        if len(c) >= 3
    ]

    _progress(70, "Simplifying\u2026")
    polys = simplify_contours(polys, simplify_tol)
    _check_cancelled()

    n_before = len(polys)
    _progress(85, f"Filtering {n_before} contour(s)\u2026")
    polys = filter_contours(polys, min_area_px, max_area_px)
    n_kept = len(polys)
    if n_kept < n_before:
        _progress(
            92,
            f"Kept {n_kept} of {n_before} contours (area filter removed {n_before - n_kept}).",
        )
    _check_cancelled()

    _progress(95, "Scaling to mm\u2026")
    px_per_mm = img_w_px / max(width_mm, 0.001)
    result = scale_to_mm(polys, px_per_mm, img_h_px)

    _progress(100, "Done.")
    return display_img, result, img_w_px, img_h_px


__all__ = [
    "RasterEngravingSpec",
    "TraceCancelled",
    "export_raster_job",
    "filter_contours",
    "image_to_outlines",
    "prepare_engraving_image",
    "scale_to_mm",
    "simplify_contours",
]


@dataclass(frozen=True)
class RasterEngravingSpec:
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 100.0
    height_mm: float = 100.0
    line_interval_mm: float = 0.10
    min_power_percent: float = 0.0
    max_power_percent: float = 80.0
    speed_mm_s: float = 100.0
    gamma: float = 1.0
    contrast: float = 1.0
    brightness: float = 1.0
    passes: int = 1
    invert: bool = False
    rotation_deg: float = 0.0

    def validated(self) -> RasterEngravingSpec:
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError("Engraving width and height must be greater than zero.")
        if not 0.025 <= self.line_interval_mm <= 2.0:
            raise ValueError("Line interval must be between 0.025 and 2 mm.")
        if not 0 <= self.min_power_percent <= self.max_power_percent <= 100:
            raise ValueError("Power must satisfy 0 ≤ minimum ≤ maximum ≤ 100%.")
        if self.speed_mm_s <= 0:
            raise ValueError("Engraving speed must be greater than zero.")
        if not 0.1 <= self.gamma <= 5.0:
            raise ValueError("Gamma must be between 0.1 and 5.")
        if not 0.1 <= self.contrast <= 5.0 or not 0.1 <= self.brightness <= 5.0:
            raise ValueError("Brightness and contrast must be between 0.1 and 5.")
        if not 1 <= self.passes <= 100:
            raise ValueError("Passes must be between 1 and 100.")
        if not math.isfinite(self.rotation_deg):
            raise ValueError("Rotation must be a finite angle.")
        return self


def prepare_engraving_image(
    image: Image.Image,
    spec: RasterEngravingSpec,
    mask_polys: list[list[tuple[float, float]]] | None = None,
) -> Image.Image:
    """Return an 8-bit power map: black=max power, white=min power."""
    spec = spec.validated()
    rows = max(1, round(spec.height_mm / spec.line_interval_mm))
    columns = max(1, round(rows * spec.width_mm / spec.height_mm))
    grayscale = ImageOps.grayscale(image).resize((columns, rows), Image.Resampling.LANCZOS)
    grayscale = ImageEnhance.Brightness(grayscale).enhance(spec.brightness)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(spec.contrast)
    if spec.invert:
        grayscale = ImageOps.invert(grayscale)
    gamma_inverse = 1.0 / spec.gamma
    grayscale = grayscale.point(
        [round(255.0 * ((value / 255.0) ** gamma_inverse)) for value in range(256)]
    )
    span = spec.max_power_percent - spec.min_power_percent
    result = grayscale.point(
        [
            round(255.0 * (1.0 - (spec.min_power_percent + (1 - value / 255.0) * span) / 100.0))
            for value in range(256)
        ]
    )
    if mask_polys:
        mask = Image.new("1", result.size, 0)
        for poly in mask_polys:
            angle = math.radians(-spec.rotation_deg)
            center_x = spec.x_mm + spec.width_mm / 2.0
            center_y = spec.y_mm + spec.height_mm / 2.0
            pixels = [
                (
                    (
                        center_x
                        + (x - center_x) * math.cos(angle)
                        - (y - center_y) * math.sin(angle)
                        - spec.x_mm
                    )
                    / spec.width_mm
                    * columns,
                    (
                        center_y
                        + (x - center_x) * math.sin(angle)
                        + (y - center_y) * math.cos(angle)
                        - spec.y_mm
                    )
                    / spec.height_mm
                    * rows,
                )
                for x, y in poly
            ]
            if len(pixels) >= 3:
                from PIL import ImageChops

                ring = Image.new("1", result.size, 0)
                ImageDraw.Draw(ring).polygon(pixels, fill=1)
                mask = ImageChops.logical_xor(mask, ring)
        result = Image.composite(result, Image.new("L", result.size, 255), mask)
    return result


def export_raster_job(
    source_path: str | Path,
    output_png: str | Path,
    spec: RasterEngravingSpec,
    mask_polys: list[list[tuple[float, float]]] | None = None,
) -> tuple[Path, Path, Path]:
    """Write PNG, placement/power JSON, and a position-preserving SVG."""
    spec = spec.validated()
    source = Path(source_path)
    output = Path(output_png).with_suffix(".png")
    metadata = output.with_suffix(".engrave.json")
    positioned_svg = output.with_suffix(".positioned.svg")
    with Image.open(source) as image:
        prepared = prepare_engraving_image(image, spec, mask_polys)
        prepared.save(output, format="PNG", dpi=(25.4 / spec.line_interval_mm,) * 2)
    payload = {
        "schema": "simple-stipple-raster-engraving-v1",
        "image": output.name,
        "source": str(source),
        "placement_mm": {
            "x": spec.x_mm,
            "y": spec.y_mm,
            "width": spec.width_mm,
            "height": spec.height_mm,
        },
        "settings": asdict(spec),
        "clipped_to_target": bool(mask_polys),
        "note": "Power and physical depth require material-specific calibration.",
    }
    metadata.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    encoded = base64.b64encode(output.read_bytes()).decode("ascii")
    center_x = spec.x_mm + spec.width_mm / 2.0
    center_y = spec.y_mm + spec.height_mm / 2.0
    angle = math.radians(spec.rotation_deg)
    corners = (
        (spec.x_mm, spec.y_mm),
        (spec.x_mm + spec.width_mm, spec.y_mm),
        (spec.x_mm + spec.width_mm, spec.y_mm + spec.height_mm),
        (spec.x_mm, spec.y_mm + spec.height_mm),
    )
    rotated = [
        (
            center_x + (x - center_x) * math.cos(angle) - (y - center_y) * math.sin(angle),
            center_y + (x - center_x) * math.sin(angle) + (y - center_y) * math.cos(angle),
        )
        for x, y in corners
    ]
    page_x = min(0.0, *(point[0] for point in rotated))
    page_y = min(0.0, *(point[1] for point in rotated))
    page_w = max(0.0, *(point[0] for point in rotated)) - page_x
    page_h = max(0.0, *(point[1] for point in rotated)) - page_y
    positioned_svg.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{page_w}mm" height="{page_h}mm" viewBox="{page_x} {page_y} {page_w} {page_h}">\n'
        f'  <image x="{spec.x_mm}" y="{spec.y_mm}" width="{spec.width_mm}" '
        f'height="{spec.height_mm}" preserveAspectRatio="none" '
        f'transform="rotate({spec.rotation_deg} {center_x} {center_y})" '
        f'href="data:image/png;base64,{encoded}"/>\n</svg>\n',
        encoding="utf-8",
    )
    return output, metadata, positioned_svg
