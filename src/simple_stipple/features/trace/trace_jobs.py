"""Trace-owned background and engraving job dependencies."""

from __future__ import annotations

from simple_stipple.engine.imaging.raster import RasterEngravingSpec, export_raster_job
from simple_stipple.engine.imaging.trace import TraceCancelled, image_to_outlines


def trace_image(source_path: str, **kwargs):
    """Run the pure image tracer for the Trace page worker."""
    return image_to_outlines(source_path, **kwargs)


__all__ = [
    "RasterEngravingSpec",
    "TraceCancelled",
    "export_raster_job",
    "trace_image",
]
