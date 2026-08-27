"""Pure outline-state normalization and canvas-record preparation for Pattern."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon

from simple_stipple.core.formats.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    svg_to_dxf,
)
from simple_stipple.core.formats.svg import read_svg_images

Point = tuple[float, float]
Polyline = list[Point]


@dataclass(frozen=True)
class NormalizedOutlines:
    """Valid incoming paths and their optional source-layer labels."""

    polylines: list[Polyline]
    layers: list[str | None]


def normalize_outline_items(items: Sequence[Any]) -> NormalizedOutlines:
    """Coerce transferred/imported outline items without touching UI state."""
    polylines: list[Polyline] = []
    layers: list[str | None] = []
    for item in items:
        if isinstance(item, dict):
            points = item.get("points", [])
            layer = item.get("layer")
        else:
            points, layer = item, None
        try:
            poly = [(float(x), float(y)) for x, y in points]
        except (TypeError, ValueError):
            continue
        if len(poly) >= 2:
            polylines.append(poly)
            layers.append(str(layer) if layer else None)
    return NormalizedOutlines(polylines, layers)


def reconcile_outline_ids(
    ids: Sequence[str], paths: Sequence[Polyline], fresh_ids: Callable[[int], list[str]]
) -> list[str]:
    """Return one stable outline identity per path, preserving available ids."""
    return list(ids[: len(paths)]) + fresh_ids(max(0, len(paths) - len(ids)))


def canvas_records(
    paths: Sequence[Polyline], ids: Sequence[str], layers: dict[str, str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build editor records and their stable first-seen layer order."""
    records = [
        {"id": entity_id, "points": poly, "layer": layers.get(entity_id, "Outline")}
        for entity_id, poly in zip(ids, paths, strict=True)
    ]
    return records, list(dict.fromkeys(str(record["layer"]) for record in records))


def outline_bounds(paths: Sequence[Polyline]) -> tuple[float, float] | None:
    """Return the width and height of all outline points, when present."""
    points = [point for path in paths for point in path]
    if not points:
        return None
    xs, ys = zip(*points)
    return max(xs) - min(xs), max(ys) - min(ys)


def smallest_containing_outline(
    ids: Sequence[str], paths: Sequence[Polyline], center: Point
) -> str | None:
    """Return the smallest valid outline containing ``center``."""
    point = ShapelyPoint(center)
    best: tuple[float, str] | None = None
    for outline_id, poly in zip(ids, paths):
        if len(poly) < 3:
            continue
        try:
            shape = ShapelyPolygon(poly)
        except (TypeError, ValueError):
            continue
        if not shape.is_valid:
            shape = shape.buffer(0)
        if shape.is_empty or shape.area <= 0 or not shape.covers(point):
            continue
        if best is None or shape.area < best[0]:
            best = (float(shape.area), outline_id)
    return best[1] if best else None


__all__ = [
    "NormalizedOutlines",
    "VectorOutlineImport",
    "canvas_records",
    "normalize_outline_items",
    "outline_bounds",
    "read_outline_vector",
    "reconcile_outline_ids",
    "smallest_containing_outline",
]


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
