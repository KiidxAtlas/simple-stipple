"""Pattern-owned export job dependencies.

The page owns dialogs and user feedback; this module gives its file-producing
jobs a feature-local home instead of routing them through a mixed-purpose facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simple_stipple.engine.formats.laserstar import export_laserstar_package
from simple_stipple.engine.imaging.raster import RasterEngravingSpec, export_raster_job


@dataclass(frozen=True)
class EngravingJob:
    """Feature-neutral placement and laser settings for a raster export."""

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    line_interval_mm: float
    min_power_percent: float
    max_power_percent: float
    speed_mm_s: float
    gamma: float
    invert: bool
    passes: int
    rotation_deg: float

    def raster_spec(self) -> RasterEngravingSpec:
        return RasterEngravingSpec(
            x_mm=self.x_mm,
            y_mm=self.y_mm,
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            line_interval_mm=self.line_interval_mm,
            min_power_percent=self.min_power_percent,
            max_power_percent=self.max_power_percent,
            speed_mm_s=self.speed_mm_s,
            gamma=self.gamma,
            invert=self.invert,
            passes=self.passes,
            rotation_deg=self.rotation_deg,
        )


def export_positioned_engraving(
    source_path: str,
    output_path: str,
    job: EngravingJob,
    mask_polys: list[list[tuple[float, float]]],
) -> Path:
    """Write the Pattern page's positioned raster engraving and return its PNG."""
    png, _metadata, _svg = export_raster_job(source_path, output_path, job.raster_spec(), mask_polys)
    return png


def export_laserstar_job(
    destination: str,
    job_name: str,
    pattern_polys: list[list[tuple[float, float]]],
    *,
    engraving_source: str | None = None,
    engraving_job: EngravingJob | None = None,
    engraving_mask: list[list[tuple[float, float]]] | None = None,
) -> Path:
    """Write a complete LaserStar package from Pattern export inputs."""
    return export_laserstar_package(
        destination,
        job_name,
        pattern_polys,
        raster_source=engraving_source,
        raster_spec=engraving_job.raster_spec() if engraving_job else None,
        raster_mask=engraving_mask,
    )


__all__ = ["EngravingJob", "export_laserstar_job", "export_positioned_engraving"]
