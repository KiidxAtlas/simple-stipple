"""DXF import/export service.

Wraps backend.dxf.io, backend.dxf.fix, backend.dxf.fvi,
backend.dxf.svg_dxf, backend.dxf.schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from src.backend.dxf.fix import FixMode
from src.backend.dxf.fix import fix_dxf as _fix_dxf
from src.backend.dxf.fvi import (
    FviDocument,
    FviExportOptions,
    FviExportReport,
    FviImportReport,
)
from src.backend.dxf.fvi import (
    convert_fvi_to_dxf as _convert_fvi_to_dxf,
)
from src.backend.dxf.fvi import (
    parse_fvi as _parse_fvi,
)
from src.backend.dxf.fvi import (
    read_fvi as _read_fvi,
)
from src.backend.dxf.fvi import (
    render_fvi as _render_fvi,
)
from src.backend.dxf.fvi import (
    summarize_fvi_import as _summarize_fvi_import,
)
from src.backend.dxf.fvi import (
    write_fvi as _write_fvi,
)
from src.backend.dxf.io import (
    DxfImportReport,
    OutlinePreflight,
    analyze_outline_polylines,
    load_dxf_polylines,
    load_dxf_polylines_by_layer_with_report,
    polylines_to_outline,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.backend.dxf.io import (
    load_dxf_polylines_with_report as _load_dxf_polylines_with_report,
)
from src.backend.dxf.svg_dxf import (
    dxf_to_svg as _dxf_to_svg,
)
from src.backend.dxf.svg_dxf import (
    svg_to_dxf as _svg_to_dxf,
)
from src.backend.dxf.svg_dxf import (
    write_polylines_svg as _write_polylines_svg,
)


class DxfService:
    """DXF import and export operations.

    All methods accept plain data (paths, bytes, dicts) and return plain data.
    No CanvasDocument, no EntityRecord, no command objects.
    """

    # -- Import --

    @staticmethod
    def load_dxf_polylines(filepath: str) -> list[list[tuple[float, float]]]:
        """Load polylines from a DXF file. Returns list of (x, y) tuple lists."""
        return load_dxf_polylines(filepath)

    @staticmethod
    def load_dxf_polylines_with_report(
        filepath: str,
    ) -> tuple[list[list[tuple[float, float]]], DxfImportReport]:
        """Load polylines with an import report."""
        return load_dxf_polylines_with_report(filepath)

    @staticmethod
    def load_dxf_polylines_by_layer_with_report(
        filepath: str,
    ) -> tuple[dict[str, list[list[tuple[float, float]]]], DxfImportReport]:
        """Load polylines grouped by layer with an import report."""
        return load_dxf_polylines_by_layer_with_report(filepath)

    @staticmethod
    def load_fvi(filepath: str) -> FviDocument:
        """Load FVI (FluxVision Interface) file."""
        return _read_fvi(filepath)

    @staticmethod
    def parse_fvi(data: str) -> FviDocument:
        """Parse FVI data from a string."""
        return _parse_fvi(data)

    @staticmethod
    def convert_fvi_to_dxf(src: str | Path, dst: str | Path) -> FviImportReport:
        """Convert FVI file to DXF file. Returns import report."""
        return _convert_fvi_to_dxf(Path(src), Path(dst))

    # -- Export --

    @staticmethod
    def write_polylines_dxf(
        polylines: Sequence[Sequence[tuple[float, float]]],
        filepath: str,
        *,
        close: bool = False,
        open_paths: bool = False,
        border_polys: Sequence[Sequence[tuple[float, float]]] | None = None,
        pattern_layer: str | None = None,
        border_layer_prefix: str = "BORDER",
        entity_kinds: Sequence[str] | None = None,
        entity_meta: Sequence[dict[str, Any] | None] | None = None,
        entity_names: Sequence[str] | None = None,
        extra_layers: Mapping[str, Sequence[Sequence[tuple[float, float]]]] | None = None,
        extra_layer_records: Mapping[str, Sequence[dict[str, Any]]] | None = None,
    ) -> None:
        """Write polylines to a DXF file. Returns None."""
        write_polylines_dxf(
            [list(p) for p in polylines],
            filepath,
            close=close,
            open_paths=open_paths,
            border_polys=[list(p) for p in border_polys] if border_polys else None,
            pattern_layer=pattern_layer,
            border_layer_prefix=border_layer_prefix,
            entity_kinds=list(entity_kinds) if entity_kinds else None,
            entity_meta=list(entity_meta) if entity_meta else None,
            entity_names=list(entity_names) if entity_names else None,
            extra_layers={k: [list(p) for p in v] for k, v in extra_layers.items()}
            if extra_layers
            else None,
            extra_layer_records={k: list(v) for k, v in extra_layer_records.items()}
            if extra_layer_records
            else None,
        )

    @staticmethod
    def write(
        polylines: Sequence[Sequence[tuple[float, float]]],
        filepath: str,
    ) -> None:
        """Write polylines to a DXF file (alias for write_polylines_dxf)."""
        write_polylines_dxf([list(p) for p in polylines], filepath)

    # -- Fix / validation --

    @staticmethod
    def fix_dxf(
        input_path: str,
        output_path: str,
        *,
        mode: FixMode = "safe",
    ) -> dict:
        """Fix a DXF file. Returns stats dict."""
        return _fix_dxf(input_path, output_path, mode=mode)

    @staticmethod
    def copy_unchanged(filepath: str) -> str:
        """Copy a DXF file without changes. Returns the filepath."""
        import shutil

        dst = filepath + ".copy"
        shutil.copy2(filepath, dst)
        return dst

    # -- Analysis --

    @staticmethod
    def analyze_outline_polylines(
        polylines: Sequence[Sequence[tuple[float, float]]],
    ) -> OutlinePreflight:
        """Analyze polylines for outline properties."""
        return analyze_outline_polylines([list(p) for p in polylines])

    @staticmethod
    def polylines_to_outline(
        polylines: Sequence[Sequence[tuple[float, float]]],
    ) -> BaseGeometry:
        """Extract the outermost outline from a set of polylines."""
        return polylines_to_outline([list(p) for p in polylines])

    @staticmethod
    def summarize_dxf_import_report(report: DxfImportReport) -> str | None:
        """Summarize a DXF import report."""
        return summarize_dxf_import_report(report)

    @staticmethod
    def summarize_fvi_import(report: FviImportReport) -> str | None:
        """Summarize an FVI import."""
        return _summarize_fvi_import(report)

    @staticmethod
    def svg_to_dxf(input_path: str | Path, output_path: str | Path) -> dict:
        """Convert SVG file to DXF file. Returns conversion stats."""
        return _svg_to_dxf(input_path, output_path)

    @staticmethod
    def dxf_to_svg(input_path: str | Path, output_path: str | Path) -> dict:
        """Convert DXF file to SVG file. Returns conversion stats."""
        return _dxf_to_svg(input_path, output_path)

    @staticmethod
    def write_polylines_svg(
        polylines: Sequence[Sequence[tuple[float, float]]],
        filepath: str,
    ) -> None:
        """Write polylines to an SVG file."""
        _write_polylines_svg([list(p) for p in polylines], filepath)

    @staticmethod
    def write_fvi(
        data: Sequence[dict],
        filepath: str,
    ) -> FviExportReport:
        """Write data to an FVI file."""
        return _write_fvi(data, filepath)

    @staticmethod
    def read_fvi(filepath: str) -> FviDocument:
        """Read an FVI file."""
        return _read_fvi(filepath)

    @staticmethod
    def render_fvi(
        data: Sequence[dict],
        options: FviExportOptions | None = None,
    ) -> tuple[str, FviExportReport]:
        """Render FVI text from data with options."""
        return _render_fvi(data, options)


# Module-level re-exports for direct function access
load_dxf_polylines_with_report = _load_dxf_polylines_with_report
read_fvi = _read_fvi
svg_to_dxf = _svg_to_dxf
fix_dxf = _fix_dxf
write_polylines_dxf = write_polylines_dxf
