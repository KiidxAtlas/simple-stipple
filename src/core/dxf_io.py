"""DXF I/O helpers — read and write LWPOLYLINE entities."""

from __future__ import annotations

import logging
import math

import ezdxf  # type: ignore[attr-defined]
from shapely.geometry import Polygon  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

_LOG = logging.getLogger(__name__)


def load_dxf_polylines(path: str) -> list[list[tuple[float, float]]]:
    """Return all LWPOLYLINE and POLYLINE entities as lists of (x, y) tuples.

    For flag-closed polylines (is_closed=True) the closing point is appended
    so that downstream code can treat start≈end as the closed-loop signal.
    Supports both modern LWPOLYLINE (R14+) and legacy POLYLINE (pre-R14) entities.
    """
    doc = ezdxf.readfile(path)
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
                pts = [(float(p[0]), float(p[1])) for p in ent.get_points()]
                _append(pts, ent.is_closed)
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid LWPOLYLINE in %s: %s", path, exc)
        elif dxftype == "POLYLINE":
            try:
                if not ent.is_2d_polyline:
                    continue
                pts = [
                    (float(v.dxf.location.x), float(v.dxf.location.y))
                    for v in ent.vertices
                ]
                _append(pts, ent.is_closed)
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid POLYLINE in %s: %s", path, exc)
                continue

    return result


def polylines_to_outline(polylines: list[list[tuple[float, float]]]):
    """Build a Shapely union polygon from a list of closed polylines.

    Only polylines that form a genuine closed loop (start ≈ end, within 0.5 mm)
    and have meaningful area (> 1 mm²) are used. This prevents open detail lines
    from being auto-closed into sliver polygons that corrupt the union.
    """
    _CLOSE_TOL = 2.0  # mm — how close start and end must be to count as closed
    _AREA_MIN = 1.0  # mm² — ignore degenerate tiny loops

    polys: list[Polygon] = []
    for c in polylines:
        if len(c) < 3:
            continue
        # Accept if the DXF polyline is flagged closed OR start/end are within tolerance
        dx = c[-1][0] - c[0][0]
        dy = c[-1][1] - c[0][1]
        if math.hypot(dx, dy) > _CLOSE_TOL:
            continue  # open path — skip
        try:
            p = Polygon(c)
            if p.is_valid and p.area >= _AREA_MIN:
                polys.append(p)
        except (TypeError, ValueError) as exc:
            _LOG.debug("Skipping invalid closed outline polyline: %s", exc)
    if not polys:
        # Fallback: use all paths so we never return empty.
        # This means open paths become outlines — warn so callers can surface it.
        _LOG.warning(
            "No genuinely closed polylines found (start≈end within %.1f mm, "
            "area ≥ %.1f mm²). Falling back to all paths as outlines — "
            "open paths may be auto-closed.",
            _CLOSE_TOL,
            _AREA_MIN,
        )
        for c in polylines:
            if len(c) < 3:
                continue
            try:
                p = Polygon(c)
                if p.is_valid and p.area > 0:
                    polys.append(p)
            except (TypeError, ValueError) as exc:
                _LOG.debug("Skipping invalid fallback polyline: %s", exc)
    if not polys:
        flat = [pt for c in polylines for pt in c]
        return Polygon(flat).convex_hull
    result = unary_union(polys)
    return (
        result if not result.is_empty else max(polys, key=lambda p: p.area).convex_hull
    )


def write_polylines_dxf(
    polylines: list[list[tuple[float, float]]],
    out_path: str,
    close: bool = False,
    border_polys: list[list[tuple[float, float]]] | None = None,
    pattern_layer: str | None = None,
    border_layer_prefix: str = "BORDER",
) -> None:
    doc = ezdxf.new("R2010")
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
