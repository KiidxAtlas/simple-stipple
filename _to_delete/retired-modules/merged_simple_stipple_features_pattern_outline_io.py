"""Non-UI vector-outline loading for the Pattern feature."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_stipple.engine.formats.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    svg_to_dxf,
)
from simple_stipple.engine.formats.svg import read_svg_images


@dataclass(frozen=True)
class VectorOutlineImport:
    """Geometry and optional embedded artwork recovered from one vector file."""

    polylines: list[list[tuple[float, float]]]
    images: list[Any]


def read_outline_vector(
    path: str,
    *,
    read_fvi_file: Callable[[str], Any] = read_fvi,
    convert_svg: Callable[[str, Path], Any] = svg_to_dxf,
    read_dxf: Callable[
        [str], tuple[list[list[tuple[float, float]]], Any]
    ] = load_dxf_polylines_with_report,
    read_svg_artwork: Callable[[str], list[Any]] = read_svg_images,
) -> VectorOutlineImport:
    """Read an FVI or SVG outline without interacting with the page or Qt."""
    suffix = Path(path).suffix.lower()
    if suffix == ".fvi":
        document = read_fvi_file(path)
        return VectorOutlineImport([list(poly) for poly in document.paths], [])
    if suffix == ".svg":
        with tempfile.TemporaryDirectory(prefix="simple-stipple-pattern-svg-") as folder:
            converted = Path(folder) / "outline.dxf"
            convert_svg(path, converted)
            polylines, _report = read_dxf(str(converted))
        return VectorOutlineImport([list(poly) for poly in polylines], list(read_svg_artwork(path)))
    raise ValueError("Choose an FVI or SVG vector file.")


__all__ = ["VectorOutlineImport", "read_outline_vector"]
