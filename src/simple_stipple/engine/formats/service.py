"""DXF/FVI/SVG import-export access for the UI layer.

Wraps ``backend.dxf.service.DxfService`` (itself already a facade over
``backend.dxf.{io,fix,fvi,svg_dxf}``) — a thin re-export, not new logic, so
``ui`` doesn't reach past ``app`` down to ``backend`` directly (see plan.md
Section 8.1 / LP-5). Regressed at some point after being marked complete
2026-07-22 (the file no longer existed); rebuilt 2026-07-25.
"""

from __future__ import annotations

from simple_stipple.engine.formats.dxf import (
    DxfImportReport,
    OutlinePreflight,
    summarize_dxf_import_report,
)
from simple_stipple.engine.formats.dxf_backend import (
    DxfService,
    fix_dxf,
    load_dxf_polylines_with_report,
    read_fvi,
    svg_to_dxf,
    write_polylines_dxf,
)
from simple_stipple.engine.formats.fvi import (
    FVI_UNIT_MM,
    FviDocument,
    FviExportOptions,
    FviExportReport,
    FviImportReport,
    FviNoGeometryError,
)

__all__ = [
    "DxfImportReport",
    "DxfService",
    "FVI_UNIT_MM",
    "FviDocument",
    "FviExportOptions",
    "FviExportReport",
    "FviImportReport",
    "FviNoGeometryError",
    "OutlinePreflight",
    "fix_dxf",
    "load_dxf_polylines_with_report",
    "read_fvi",
    "summarize_dxf_import_report",
    "svg_to_dxf",
    "write_polylines_dxf",
]
