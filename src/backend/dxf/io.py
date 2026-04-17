"""DXF I/O helpers — read and write LWPOLYLINE entities."""

from __future__ import annotations

import logging
import math
from typing import Any, NamedTuple, cast

import ezdxf  # type: ignore[attr-defined]
from shapely.geometry import Polygon  # type: ignore[import-untyped]
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

_LOG = logging.getLogger(__name__)
OUTLINE_CLOSE_TOLERANCE_MM = 2.0
OUTLINE_MIN_AREA_MM2 = 1.0


class OutlinePreflight(NamedTuple):
    usable_polygons: list[Polygon]
    usable_closed_count: int
    open_count: int
    too_small_count: int


def _ezdxf_readfile(path: str):
    return cast(Any, ezdxf).readfile(path)


def _ezdxf_new(version: str = "R2010"):
    return cast(Any, ezdxf).new(version)


def load_dxf_polylines(path: str) -> list[list[tuple[float, float]]]:
    """Return all LWPOLYLINE and POLYLINE entities as lists of (x, y) tuples.

    For flag-closed polylines (is_closed=True) the closing point is appended
    so that downstream code can treat start≈end as the closed-loop signal.
    Supports both modern LWPOLYLINE (R14+) and legacy POLYLINE (pre-R14) entities.
    """
    doc = _ezdxf_readfile(path)
    msp = doc.modelspace()
    result: list[list[tuple[float, float]]] = []

    def _append(pts: list[tuple[float, float]], closed: bool) -> None:
        if len(pts) < 2:
            return
        if closed and (
            abs(pts[-1][0] - pts[0][0]) > 1e-6 or abs(pts[-1][1] - pts[0][1]) > 1e-6
        ):
            pts.append(pts[0])
        result.append(pts)

    for ent in msp:
        dxftype = ent.dxftype()
        if dxftype == "LWPOLYLINE":
            try:
                lw = cast(Any, ent)
                pts = [(float(p[0]), float(p[1])) for p in lw.get_points()]
                _append(pts, bool(lw.is_closed))
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid LWPOLYLINE in %s: %s", path, exc)
        elif dxftype == "POLYLINE":
            try:
                poly = cast(Any, ent)
                if not poly.is_2d_polyline:
                    continue
                pts = [
                    (float(v.dxf.location.x), float(v.dxf.location.y))
                    for v in poly.vertices
                ]
                _append(pts, bool(poly.is_closed))
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid POLYLINE in %s: %s", path, exc)
                continue

    return result


def analyze_outline_polylines(
    polylines: list[list[tuple[float, float]]],
) -> OutlinePreflight:
    """Analyze candidate outline polylines before pattern generation."""
    usable: list[Polygon] = []
    open_count = 0
    too_small_count = 0
    for c in polylines:
        if len(c) < 3:
            if c:
                open_count += 1
            continue
        dx = c[-1][0] - c[0][0]
        dy = c[-1][1] - c[0][1]
        if math.hypot(dx, dy) > OUTLINE_CLOSE_TOLERANCE_MM:
            open_count += 1
            continue
        try:
            p = Polygon(c)
        except (TypeError, ValueError):
            continue
        if not p.is_valid:
            continue
        if p.area < OUTLINE_MIN_AREA_MM2:
            too_small_count += 1
            continue
        usable.append(p)
    return OutlinePreflight(
        usable_polygons=usable,
        usable_closed_count=len(usable),
        open_count=open_count,
        too_small_count=too_small_count,
    )


def polylines_to_outline(polylines: list[list[tuple[float, float]]]) -> BaseGeometry:
    """Build a Shapely union polygon from a list of closed polylines.

    Only polylines that form a genuine closed loop (start ≈ end, within 0.5 mm)
    and have meaningful area (> 1 mm²) are used. This prevents open detail lines
    from being auto-closed into sliver polygons that corrupt the union.
    """
    analysis = analyze_outline_polylines(polylines)
    if not analysis.usable_polygons:
        raise ValueError(
            "No valid closed outline was found. Close or repair the outline before generating a pattern."
        )
    result = unary_union(analysis.usable_polygons)
    if result.is_empty:
        raise ValueError(
            "The validated outline produced an empty region. Repair or simplify the outline and try again."
        )
    return (
        result
        if not result.is_empty
        else max(analysis.usable_polygons, key=lambda p: p.area).convex_hull
    )


def write_polylines_dxf(
    polylines: list[list[tuple[float, float]]],
    out_path: str,
    close: bool = False,
    border_polys: list[list[tuple[float, float]]] | None = None,
    pattern_layer: str | None = None,
    border_layer_prefix: str = "BORDER",
) -> None:
    doc = _ezdxf_new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    dxfattrs: dict[str, str] = {}
    if pattern_layer:
        if pattern_layer not in doc.layers:
            doc.layers.add(pattern_layer, color=7)
        dxfattrs = {"layer": pattern_layer}

    for c in polylines:
        if len(c) >= 2:
            if (
                close
                and len(c) >= 3
                and math.hypot(c[-1][0] - c[0][0], c[-1][1] - c[0][1]) < 0.5
            ):
                poly_close = True
                coords = c[:-1]
            else:
                poly_close = False
                coords = c
            msp.add_lwpolyline(coords, close=poly_close, dxfattribs=dxfattrs or None)

    if border_polys:
        count = len(border_polys)
        for idx, c in enumerate(border_polys):
            if len(c) < 2:
                continue
            layer_name = border_layer_prefix
            if count > 1:
                layer_name = f"{border_layer_prefix}_{idx + 1}"
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=3)
            msp.add_lwpolyline(c, close=True, dxfattribs={"layer": layer_name})

    doc.saveas(out_path)
