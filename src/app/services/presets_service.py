"""Pattern presets service.

Wraps backend.pattern.presets.
"""

from __future__ import annotations

# LaserStar package
from src.backend.laserstar_package import export_laserstar_package

# Pattern cancellation and output
from src.backend.pattern.cancellation import cancellation_scope
from src.backend.pattern.fill import NULL_PATTERN
from src.backend.pattern.output import diagnose_output, prepare_output
from src.backend.pattern.presets import (
    SETTINGS_KEY,
    deserialize_presets,
    ensure_builtins_seeded,
    export_to_file,
    import_from_file,
    merge_presets,
    reset_to_builtins,
    serialize_presets,
)

# Pattern processing
from src.backend.pattern.processing import PATTERNS, PatternProcessor

# Raster engraving
from src.backend.raster_engraving import RasterEngravingSpec, export_raster_job

# Trace
from src.backend.trace import TraceCancelled, image_to_outlines

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
