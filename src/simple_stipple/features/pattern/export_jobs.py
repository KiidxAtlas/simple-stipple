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
    vector_format: str = "fvi",
) -> Path:
    """Write a complete LaserStar package from Pattern export inputs."""
    return export_laserstar_package(
        destination,
        job_name,
        pattern_polys,
        raster_source=engraving_source,
        raster_spec=engraving_job.raster_spec() if engraving_job else None,
        raster_mask=engraving_mask,
        vector_format=vector_format,
    )


# A file format is not a workflow. Every one of these writes the same
# operations; they differ only in what the destination can hold, which is why
# the raster becomes a sidecar for the two that cannot embed one.
EXPORT_FORMATS: tuple[tuple[str, str, str], ...] = (
    # key, menu label, file suffix ("" = writes a folder)
    ("dxf", "DXF — vectors (image as sidecar PNG)", ".dxf"),
    ("svg", "SVG — vectors + embedded image, one file", ".svg"),
    ("fvi", "FVI — LaserStar vectors (image as sidecar PNG)", ".fvi"),
    ("laserstar", "LaserStar package — folder with setup sheet", ""),
)

EXPORT_FORMAT_KEYS = tuple(key for key, _label, _suffix in EXPORT_FORMATS)

# Short label for the primary button.
EXPORT_BUTTON_LABEL = {
    "dxf": "Export DXF",
    "svg": "Export SVG",
    "fvi": "Export FVI",
    "laserstar": "Export package",
}

_SUFFIX = {key: suffix for key, _label, suffix in EXPORT_FORMATS}


def export_format_suffix(export_format: str) -> str:
    return _SUFFIX.get(export_format, ".dxf")


def export_document_file(
    output_path: str,
    export_format: str,
    vector_polys: list[list[tuple[float, float]]],
    *,
    engraving_source: str | None = None,
    engraving_job: EngravingJob | None = None,
    engraving_mask: list[list[tuple[float, float]]] | None = None,
) -> list[Path]:
    """Write one single-file export, plus a raster sidecar when needed.

    Returns every path written, first one first. SVG embeds the image; DXF
    and FVI have nowhere to put a raster, so it lands beside them as the
    positioned PNG + placement JSON the machine setup already expects.
    """
    from simple_stipple.engine.formats.dxf import write_polylines_dxf
    from simple_stipple.engine.formats.fvi import FviExportOptions, write_fvi
    from simple_stipple.engine.formats.svg import SvgImagePlacement, write_document_svg

    target = Path(output_path)
    written: list[Path] = [target]
    spec = engraving_job.raster_spec() if engraving_job else None

    if export_format == "svg":
        placements: list[SvgImagePlacement] = []
        if engraving_source and spec is not None:
            import io

            from PIL import Image

            from simple_stipple.engine.imaging.raster import prepare_engraving_image

            with Image.open(engraving_source) as image:
                prepared = prepare_engraving_image(image, spec.validated(), engraving_mask)
            buffer = io.BytesIO()
            prepared.save(buffer, format="PNG")
            placements.append(
                SvgImagePlacement(
                    png_bytes=buffer.getvalue(),
                    x_mm=spec.x_mm,
                    y_mm=spec.y_mm,
                    width_mm=spec.width_mm,
                    height_mm=spec.height_mm,
                    rotation_deg=spec.rotation_deg,
                )
            )
        write_document_svg(vector_polys, target, images=placements)
        return written

    if export_format == "fvi":
        write_fvi(
            [{"polyline": list(poly), "kind": "polyline", "meta": None} for poly in vector_polys],
            target,
            FviExportOptions(origin="preserve", optimize_travel=True, include_comments=True),
        )
    else:
        write_polylines_dxf(vector_polys, str(target))

    if engraving_source and spec is not None:
        png, metadata, _svg = export_raster_job(
            engraving_source,
            target.with_name(f"{target.stem}-engraving.png"),
            spec,
            engraving_mask,
        )
        written.extend([png, metadata])
    return written


__all__ = [
    "EXPORT_BUTTON_LABEL",
    "EXPORT_FORMATS",
    "EXPORT_FORMAT_KEYS",
    "EngravingJob",
    "export_document_file",
    "export_format_suffix",
    "export_laserstar_job",
    "export_positioned_engraving",
]
