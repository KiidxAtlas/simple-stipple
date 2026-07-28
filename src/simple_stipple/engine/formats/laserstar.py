"""Operator-oriented handoff package for LaserStar 3602XL / StarFX Premier."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from simple_stipple.engine.formats.fvi import FviExportOptions, write_fvi
from simple_stipple.engine.imaging.raster import RasterEngravingSpec, export_raster_job


@dataclass(frozen=True)
class LaserStarProfile:
    machine: str = "LaserStar 3602XL"
    software: str = "StarFX Premier"
    laser: str = "LM2 60 W"
    lens_mm: int = 163
    frequency_khz: float = 50.0


def _records(polys):
    return [{"polyline": list(poly), "kind": "polyline", "meta": None} for poly in polys]


def export_laserstar_package(
    destination: str | Path,
    job_name: str,
    vector_polys: list[list[tuple[float, float]]],
    *,
    raster_source: str | Path | None = None,
    raster_spec: RasterEngravingSpec | None = None,
    raster_mask: list[list[tuple[float, float]]] | None = None,
    profile: LaserStarProfile | None = None,
) -> Path:
    """Create a single folder that an operator can assemble safely in StarFX."""
    if not vector_polys:
        raise ValueError("Build a Pattern preview before exporting a LaserStar package.")
    profile = profile or LaserStarProfile()
    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", job_name).strip(" .") or "LaserStar Job"
    folder = Path(destination) / safe_name
    folder.mkdir(parents=True, exist_ok=False)
    vector_path = folder / "01_pattern-and-outline.fvi"
    report = write_fvi(
        _records(vector_polys),
        vector_path,
        FviExportOptions(origin="preserve", optimize_travel=True, include_comments=True),
    )

    raster_files: list[str] = []
    if raster_source and raster_spec:
        png, metadata, _svg = export_raster_job(
            raster_source, folder / "02_grayscale-engraving.png", raster_spec, raster_mask
        )
        raster_files = [png.name, metadata.name]
        x, y, w, h = (
            raster_spec.x_mm,
            raster_spec.y_mm,
            raster_spec.width_mm,
            raster_spec.height_mm,
        )
        frame = [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
        write_fvi(
            _records(frame),
            folder / "03_placement-reference.fvi",
            FviExportOptions(origin="preserve", optimize_travel=False),
        )

    all_points = [point for poly in vector_polys for point in poly]
    minx = min(x for x, _ in all_points)
    maxx = max(x for x, _ in all_points)
    miny = min(y for _, y in all_points)
    maxy = max(y for _, y in all_points)
    setup = [
        f"JOB: {safe_name}",
        "",
        "LASERSTAR TARGET",
        f"Machine: {profile.machine}",
        f"Software: {profile.software}",
        f"Laser: {profile.laser}",
        f"Lens: {profile.lens_mm} mm",
        "",
        "VECTOR IMPORT",
        "1. Import 01_pattern-and-outline.fvi into StarFX.",
        "2. Preserve its coordinates/origin; do not center or auto-fit it.",
        f"3. Vector bounds: X {minx:.3f}..{maxx:.3f} mm; Y {miny:.3f}..{maxy:.3f} mm.",
    ]
    if raster_source and raster_spec:
        setup.extend(
            [
                "",
                "GRAYSCALE IMPORT",
                "1. Add 02_grayscale-engraving.png as a StarFX grayscale/image object.",
                "2. Import 03_placement-reference.fvi temporarily for alignment.",
                f"3. Image lower-left: X {raster_spec.x_mm:.3f}, Y {raster_spec.y_mm:.3f} mm.",
                f"4. Image size: {raster_spec.width_mm:.3f} × {raster_spec.height_mm:.3f} mm.",
                "5. Align the image exactly to the placement frame, then disable/delete the frame.",
                f"6. LASERPOWER range: {raster_spec.min_power_percent:.1f}–{raster_spec.max_power_percent:.1f}%.",
                f"7. DRAWSPEED: {raster_spec.speed_mm_s:.1f} mm/s.",
                f"8. LASERFREQ starting value: {profile.frequency_khz:.1f} kHz.",
                f"9. Passes: {raster_spec.passes}; line interval: {raster_spec.line_interval_mm:.3f} mm.",
            ]
        )
    setup.extend(
        [
            "",
            "MANDATORY PREFLIGHT",
            "Use StarFX red trace/profile preview with the laser disabled.",
            "Confirm the 163 mm lens and machine origin. Run a material test coupon.",
            "The values above are starting values, not a guarantee for a material or finish.",
        ]
    )
    (folder / "LaserStar-Setup.txt").write_text("\n".join(setup) + "\n", encoding="utf-8")
    manifest = {
        "schema": "simple-stipple-laserstar-package-v1",
        "job": safe_name,
        "profile": asdict(profile),
        "vector_file": vector_path.name,
        "vector_report": {
            "paths": report.path_count,
            "travel_mm": report.travel_mm,
            "bounds_mm": report.bounds_mm,
            "warnings": list(report.warnings),
        },
        "raster_files": raster_files,
        "raster": asdict(raster_spec) if raster_spec else None,
    }
    (folder / "job-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Lightweight visual inventory; it is a reference, never machine input.
    preview = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(preview)
    sx = 940 / max(maxx - minx, 1e-9)
    sy = 640 / max(maxy - miny, 1e-9)
    scale = min(sx, sy)
    for poly in vector_polys:
        pts = [(30 + (x - minx) * scale, 670 - (y - miny) * scale) for x, y in poly]
        if len(pts) >= 2:
            draw.line(pts, fill="#111827", width=1)
    preview.save(folder / "job-preview.png")
    return folder


__all__ = ["LaserStarProfile", "export_laserstar_package"]
