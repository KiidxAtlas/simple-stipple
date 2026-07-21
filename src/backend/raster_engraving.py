"""Power-aware raster engraving preparation and portable job export."""

from __future__ import annotations

import json
import base64
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps


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
    # Limit tone range to the requested power window. Consumers can recover
    # exact power using the metadata, while ordinary laser software can import
    # the PNG directly as a conventional black-is-strong grayscale image.
    span = spec.max_power_percent - spec.min_power_percent
    result = grayscale.point(
        [
            round(255.0 * (1.0 - (spec.min_power_percent + (1 - v / 255.0) * span) / 100.0))
            for v in range(256)
        ]
    )
    if mask_polys:
        mask = Image.new("1", result.size, 0)
        draw = ImageDraw.Draw(mask)
        for poly in mask_polys:
            pixels = [
                (
                    (x - spec.x_mm) / spec.width_mm * columns,
                    (y - spec.y_mm) / spec.height_mm * rows,
                )
                for x, y in poly
            ]
            if len(pixels) >= 3:
                # XOR gives nested rings even-odd semantics (holes remain clear).
                ring = Image.new("1", result.size, 0)
                ImageDraw.Draw(ring).polygon(pixels, fill=1)
                from PIL import ImageChops

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
    page_x = min(0.0, spec.x_mm)
    page_y = min(0.0, spec.y_mm)
    page_w = max(spec.x_mm + spec.width_mm, 0.0) - page_x
    page_h = max(spec.y_mm + spec.height_mm, 0.0) - page_y
    positioned_svg.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{page_w}mm" height="{page_h}mm" '
        f'viewBox="{page_x} {page_y} {page_w} {page_h}">\n'
        f'  <image x="{spec.x_mm}" y="{spec.y_mm}" width="{spec.width_mm}" '
        f'height="{spec.height_mm}" preserveAspectRatio="none" '
        f'href="data:image/png;base64,{encoded}"/>\n</svg>\n',
        encoding="utf-8",
    )
    return output, metadata, positioned_svg


__all__ = ["RasterEngravingSpec", "export_raster_job", "prepare_engraving_image"]
