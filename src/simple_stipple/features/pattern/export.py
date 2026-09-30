"""Pattern-owned export job dependencies.

The page owns dialogs and user feedback; this module gives its file-producing
jobs a feature-local home instead of routing them through a mixed-purpose facade.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from simple_stipple.core.cad.preflight import GeometryIssue
from simple_stipple.core.formats.laserstar import export_laserstar_package
from simple_stipple.core.imaging import RasterEngravingSpec, export_raster_job
from simple_stipple.features.pattern.regions.treatments import (
    IMAGE_PATTERN,
    region_engraving,
    treatment_kind,
)


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
    dither: str = "continuous"

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
            dither=self.dither,
        )


def build_engraving_job(
    *,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    line_interval_mm: float,
    min_power_percent: float,
    max_power_percent: float,
    speed_mm_s: float,
    gamma: float,
    passes: int,
    invert: bool,
    rotation_deg: float,
    dither: str = "continuous",
) -> EngravingJob:
    """Create a feature-neutral engraving payload from a UI control snapshot."""
    return EngravingJob(
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        line_interval_mm=line_interval_mm,
        min_power_percent=min_power_percent,
        max_power_percent=max_power_percent,
        speed_mm_s=speed_mm_s,
        gamma=gamma,
        passes=passes,
        invert=invert,
        rotation_deg=rotation_deg,
        dither=dither,
    )


def export_positioned_engraving(
    source_path: str,
    output_path: str,
    job: EngravingJob,
    mask_polys: list[list[tuple[float, float]]],
) -> Path:
    """Write the Pattern page's positioned raster engraving and return its PNG."""
    png, _metadata, _svg = export_raster_job(
        source_path, output_path, job.raster_spec(), mask_polys
    )
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
    ("png", "PNG — engraving image only (lossless, placed + clipped)", ".png"),
    ("dxf", "DXF — vectors (image as sidecar PNG)", ".dxf"),
    ("svg", "SVG — vectors + embedded image, one file", ".svg"),
    ("fvi", "FVI — LaserStar vectors (image as sidecar PNG)", ".fvi"),
    ("laserstar", "LaserStar package — folder with setup sheet", ""),
)

EXPORT_FORMAT_KEYS = tuple(key for key, _label, _suffix in EXPORT_FORMATS)

# Short label for the primary button; it names the format it will write.
EXPORT_BUTTON_LABEL = {
    "png": "Export PNG…",
    "dxf": "Export DXF…",
    "svg": "Export SVG…",
    "fvi": "Export FVI…",
    "laserstar": "Export Package…",
}

_FORMAT_NAME = {
    "png": "PNG",
    "dxf": "DXF",
    "svg": "SVG",
    "fvi": "FVI",
    "laserstar": "LaserStar package",
}

_SUFFIX = {key: suffix for key, _label, suffix in EXPORT_FORMATS}

#: Default format when the document is only an image. PNG is lossless, so the
#: power map survives exactly; JPEG would smear dithered and halftone pixels.
IMAGE_EXPORT_FORMAT = "png"


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
    """Prepare every output before publishing; restore originals on failure."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.stem}-export-", dir=target.parent))
    cleanup = True
    try:
        prepared = _write_document_files(
            str(staging / target.name),
            export_format,
            vector_polys,
            engraving_source=engraving_source,
            engraving_job=engraving_job,
            engraving_mask=engraving_mask,
        )
        backups = staging / "originals"
        backups.mkdir()
        destinations = [target.parent / path.name for path in prepared]
        originals = {}
        for destination in destinations:
            if destination.exists() or destination.is_symlink():
                backup = backups / destination.name
                shutil.copy2(destination, backup, follow_symlinks=False)
                originals[destination] = backup
        installed = []
        try:
            for source, destination in zip(prepared, destinations, strict=True):
                os.replace(source, destination)
                installed.append(destination)
        except OSError as failure:
            recovery_errors = []
            for destination in reversed(installed):
                try:
                    if destination in originals:
                        os.replace(originals[destination], destination)
                    else:
                        destination.unlink()
                except OSError as recovery_error:
                    recovery_errors.append(str(recovery_error))
            if recovery_errors:
                cleanup = False
                raise OSError(
                    f"Export failed: {failure}. Some files could not be restored: "
                    f"{'; '.join(recovery_errors)}. Original files are preserved in {backups}."
                ) from failure
            raise
        return destinations
    finally:
        if cleanup:
            shutil.rmtree(staging)


def _write_document_files(
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
    from simple_stipple.core.formats.dxf import write_polylines_dxf
    from simple_stipple.core.formats.fvi import FviExportOptions, write_fvi
    from simple_stipple.core.formats.svg import SvgImagePlacement, write_document_svg

    target = Path(output_path)
    written: list[Path] = [target]
    spec = engraving_job.raster_spec() if engraving_job else None

    if export_format == "svg":
        placements: list[SvgImagePlacement] = []
        if engraving_source and spec is not None:
            import io

            from PIL import Image

            from simple_stipple.core.imaging import prepare_engraving_image

            with Image.open(engraving_source) as image:
                prepared = prepare_engraving_image(image, spec.validated(), engraving_mask)
            buffer = io.BytesIO()
            prepared.convert("RGB").save(buffer, format="PNG")
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

    if export_format == "png":
        if not engraving_source or spec is None:
            raise ValueError("PNG export writes the engraving image — add an image first.")
        png, metadata, positioned_svg = export_raster_job(
            engraving_source, target, spec, engraving_mask
        )
        positioned_svg.unlink()
        return [png, metadata]

    if export_format == "fvi":
        write_fvi(
            [{"polyline": list(poly), "kind": "polyline", "meta": None} for poly in vector_polys],
            target,
            FviExportOptions(origin="preserve", optimize_travel=True, include_comments=True),
        )
    else:
        write_polylines_dxf(vector_polys, str(target))

    if engraving_source and spec is not None:
        png, metadata, positioned_svg = export_raster_job(
            engraving_source,
            target.with_name(f"{target.stem}-engraving.png"),
            spec,
            engraving_mask,
        )
        written.extend([png, metadata, positioned_svg])
    return written


__all__ = [
    "EXPORT_BUTTON_LABEL",
    "EXPORT_FORMATS",
    "EXPORT_FORMAT_KEYS",
    "IMAGE_EXPORT_FORMAT",
    "EngravingJob",
    "build_engraving_job",
    "export_document_file",
    "export_format_suffix",
    "export_laserstar_job",
    "export_positioned_engraving",
]


# Lower runs first.
_ORDER = {"engrave": 0, "mark": 1, "cut": 2}

_KIND_LABEL = {"engrave": "Engrave", "mark": "Mark", "cut": "Cut"}


@dataclass(frozen=True)
class Operation:
    """One row of the Output panel: one thing the machine will do."""

    key: str
    kind: str  # engrave | mark | cut
    subject: str  # what is produced
    target: str  # where it lands
    detail: str = ""

    @property
    def label(self) -> str:
        parts = [f"{_KIND_LABEL[self.kind]}  {self.subject}  →  {self.target}"]
        if self.detail:
            parts.append(self.detail)
        return "      ".join(parts)


def export_summary(
    export_format: str, operations: list[Operation], enabled: list[Operation], findings: int
) -> str:
    """One line saying what Export will write: format, operations in run order,
    and how many preflight findings are outstanding."""
    name = _FORMAT_NAME.get(export_format, export_format.upper())
    if not operations:
        return f"{name} · nothing to export yet"
    runs: list[list] = []
    for operation in enabled:
        if runs and runs[-1][0] == operation.kind:
            runs[-1][1] += 1
        else:
            runs.append([operation.kind, 1])
    order = " → ".join(
        f"{_KIND_LABEL[kind]}{f' ×{count}' if count > 1 else ''}" for kind, count in runs
    )
    count = (
        f"{len(enabled)} operation{'s' if len(enabled) != 1 else ''}"
        if len(enabled) == len(operations)
        else f"{len(enabled)} of {len(operations)} operations"
    )
    parts = [name, f"{count}: {order}" if order else count]
    parts.append(f"{findings} warning{'s' if findings != 1 else ''}" if findings else "No warnings")
    return " · ".join(parts)


def _region_name(page, region_id: str) -> str:
    ids = [rid for rid in page._outline_ids if rid in page._region_tree()]
    return f"Region {ids.index(region_id) + 1}" if region_id in ids else "Region"


def document_operations(page) -> list[Operation]:
    """Every operation this document produces, in run order."""
    operations: list[Operation] = []
    for region_id in page._outline_ids:
        kind = treatment_kind(page, region_id)
        name = _region_name(page, region_id)
        if kind == "engrave":
            engraving = region_engraving(page, region_id)
            image = Path(engraving["path"]).name if engraving else "no image"
            operations.append(
                Operation(
                    key=f"engrave:{region_id}",
                    kind="engrave",
                    subject=image,
                    target=f"inside {name}",
                    detail=_engraving_detail(page),
                )
            )
        elif kind in {"pattern", "fill", "pattern_fill"}:
            treatment = page._treatments.get(region_id) or {}
            subject = str(treatment.get("pattern_label") or treatment.get("pattern") or "Fill")
            if subject in {"— None —", IMAGE_PATTERN}:
                subject = "Fill"
            operations.append(
                Operation(
                    key=f"mark:{region_id}",
                    kind="mark",
                    subject=subject,
                    target=name,
                    detail="",
                )
            )
        elif kind == "cut":
            operations.append(
                Operation(key=f"cut:{region_id}", kind="cut", subject=name, target="outline")
            )
    # An image added without a region selected belongs to no region, so the
    # loop above never sees it — and it was then dropped from the export in
    # silence, while sitting visibly on the canvas. It is on the part, so it
    # is an operation; the whole outline is its mask.
    if getattr(page, "_engraving_image_path", "") and not any(
        op.kind == "engrave" for op in operations
    ):
        operations.append(
            Operation(
                key="engrave:document",
                kind="engrave",
                subject=Path(page._engraving_image_path).name,
                target="whole outline",
                detail=_engraving_detail(page),
            )
        )

    # The part still has to come off the sheet. An outermost region is cut
    # whatever is done inside it, which is why the reference scenario's ring
    # is both marked and cut — the boundary is the same shape either way.
    tree = page._region_tree()
    for region_id in page._outline_ids:
        region = tree.get(region_id)
        if region is None or region.depth != 0:
            continue
        if treatment_kind(page, region_id) == "cut":
            continue  # already listed as its own Cut
        operations.append(
            Operation(
                key=f"cut:boundary:{region_id}",
                kind="cut",
                subject=f"{_region_name(page, region_id)} boundary",
                target="outline",
            )
        )
    untreated = sum(
        1
        for rid in page._outline_ids
        if treatment_kind(page, rid) == "none" and (tree.get(rid) is None or tree[rid].depth > 0)
    )
    if untreated:
        operations.append(
            Operation(
                key="cut:remaining",
                kind="cut",
                subject=f"{untreated} untreated outline{'s' if untreated != 1 else ''}",
                target="outline",
            )
        )
    operations.sort(key=lambda op: _ORDER[op.kind])
    return operations


def _engraving_detail(page) -> str:
    try:
        return (
            f"{page._engrave_max_power.value():g}% · "
            f"{page._engrave_speed.value():g} mm/s · "
            f"{page._engrave_passes.value():g} pass"
        )
    except AttributeError:
        return ""


# ── Density validation ────────────────────────────────────────────────────


def density_issues(
    jobs: list[dict],
    minimum_spacing_mm: float,
) -> tuple[GeometryIssue, ...]:
    """Flag regions whose solved fill spacing is below the machine minimum.

    This is the salvageable part of the "digital twin" idea: no simulation,
    just a threshold check on a number the solver already produced, surfaced
    while the design is being made instead of at export.
    """
    if minimum_spacing_mm <= 0:
        return ()
    issues: list[GeometryIssue] = []
    for index, job in enumerate(jobs):
        fill = job.get("fill")
        if not isinstance(fill, dict):
            continue
        spacing = float(fill.get("spacing") or 0.0)
        if spacing <= 0 or spacing >= minimum_spacing_mm:
            continue
        polys = job.get("polys") or []
        raw_point = next((poly[0] for poly in polys if poly), None)
        if raw_point is None or len(raw_point) < 2:
            point = (0.0, 0.0)
        else:
            point = (float(raw_point[0]), float(raw_point[1]))
        issues.append(
            GeometryIssue(
                "density",
                index,
                point,
                f"Fill spacing {spacing:g} mm is below the {minimum_spacing_mm:g} mm "
                "machine minimum",
                "warning",
            )
        )
    return tuple(issues)
