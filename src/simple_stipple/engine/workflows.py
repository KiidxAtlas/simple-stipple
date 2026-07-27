"""Pattern presets service.

Wraps backend.pattern.presets.
"""

from __future__ import annotations

from simple_stipple.engine.formats.laserstar import export_laserstar_package
from simple_stipple.engine.imaging.raster import RasterEngravingSpec, export_raster_job
from simple_stipple.engine.imaging.trace import TraceCancelled, image_to_outlines
from simple_stipple.engine.patterns.cancellation import cancellation_scope
from simple_stipple.engine.patterns.fill import NULL_PATTERN
from simple_stipple.engine.patterns.output import diagnose_output, prepare_output
from simple_stipple.engine.patterns.presets import (
    SETTINGS_KEY,
    deserialize_presets,
    ensure_builtins_seeded,
    export_to_file,
    import_from_file,
    merge_presets,
    reset_to_builtins,
    serialize_presets,
)
from simple_stipple.engine.patterns.processing import PATTERNS, PatternProcessor

__all__ = [
    "NULL_PATTERN",
    "PATTERNS",
    "PatternProcessor",
    "SETTINGS_KEY",
    "cancellation_scope",
    "prepare_output",
    "diagnose_output",
    "RasterEngravingSpec",
    "export_raster_job",
    "TraceCancelled",
    "image_to_outlines",
    "export_laserstar_package",
    "export_to_file",
    "import_from_file",
    "merge_presets",
    "reset_to_builtins",
    "serialize_presets",
    "deserialize_presets",
    "ensure_builtins_seeded",
]
