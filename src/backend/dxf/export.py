"""DXF exporter for polylines."""

from __future__ import annotations

import io
import math
import textwrap
from typing import Iterable, Sequence

from src.backend.dxf.drawing import DXF, Entities, Layer, Text, LWPolyline, Line


def export_dxf(
    polylines: list[tuple[tuple[float, float], ...]],
    entity_kinds: list[str] | None = None,
    entity_meta: list[dict] | None = None,
    entity_names: list[str] | None = None,
    layer_map: dict[str, str] | None = None,
    closed_overrides: dict[int, bool] | None = None,
    units: str = "millimeters",
) -> bytes:
    """Export *polylines* to a DXF file (binary-safe, ASCII content).

    Parameters
    ----------
    polylines :
        List of vertex sequences.  Each element is ``((x0, y0), (x1, y1), ...)``.
    entity_kinds :
        Optional parallel list of kind strings (``"spline"``, ``"arc"``, etc.).
    entity_meta :
        Optional parallel list of metadata dicts (used for spline segments).
    entity_names :
        Optional parallel list of human-readable names.  When provided, each
        exported entity will carry a ``"NAME"`` XData tag so downstream
        applications (e.g. AutoCAD, QCAD) can read it back.
    layer_map :
        Mapping from entity kind → DXF layer name.  Entities without an
        explicit kind fall back to ``"Default"``.
    closed_overrides :
        Mapping ``{polyline_index: bool}`` that overrides whether a polyline
        should be exported as closed.  ``False`` means the last vertex will
        *not* be duplicated, producing an open chain.
    units :
        DXF units string (passed to the header).

    Returns
    -------
    bytes
        The complete DXF file content.
    """
    dxf = DXF()
    dxf.header.set_variable("INSUNITS", _units_code(units))

    # Build layer set
    kind_to_layer = layer_map or {}
    layers: dict[str, Layer] = {}

    def _ensure_layer(name: str) -> Layer:
        if name not in layers:
            layer = Layer()
            layer.name = name
            dxf.tables.add(layer)
            layers[name] = layer
        return layers[name]

    # Ensure at least "Default"
    _ensure_layer("Default")

    # Export each polyline
    n = len(polylines)
    kinds = entity_kinds if entity_kinds else [None] * n
    metas = entity_meta if entity_meta else [None] * n
    names = entity_names if entity_names else [None] * n

    for idx, poly in enumerate(polylines):
        if len(poly) < 2:
            continue

        kind = kinds[idx] if idx < len(kinds) else None
        meta = metas[idx] if idx < len(metas) else None
        name = names[idx] if idx < len(names) else None

        layer_name = kind_to_layer.get(kind, "Default") if kind else "Default"
        layer = _ensure_layer(layer_name)

        # Determine closed state
        closed = _is_closed(poly)
        if closed_overrides and idx in closed_overrides:
            closed = closed_overrides[idx]

        # Spline entities: export as piecewise polylines / lines
        if kind == "spline":
            segments = int((meta or {}).get("segments", 24)) if meta else 24
            from src.backend.geometry import build_spline_poly

            pts = build_spline_poly(list(poly), segments=segments, closed=closed)
            if len(pts) < 2:
                continue
            _export_polyline_chain(
                dxf, pts, layer, closed=bool(meta.get("closed", closed)), name=name
            )
            continue

        # Arc entities: tessellate into polyline
        if kind == "arc":
            from src.backend.geometry import arc_from_three_points

            # meta should carry center, start_angle, end_angle or 3 points
            if meta and "center" in meta:
                cx, cy = meta["center"]
                r = meta.get("radius", 0)
                start = meta.get("start_angle", 0)
                end = meta.get("end_angle", 180)
                pts = _tessellate_arc(cx, cy, r, start, end, segments=32)
            elif meta and "points" in meta:
                pts = arc_from_three_points(*meta["points"], segments=32)
            else:
                pts = list(poly)  # fallback
            if len(pts) >= 2:
                _export_polyline_chain(dxf, pts, layer, closed=False, name=name)
            continue

        # Regular polylines
        _export_polyline_chain(dxf, list(poly), layer, closed=closed, name=name)

    return dxf.render()


# ── helpers ────────────────────────────────────────────────────────────────


def _is_closed(poly: Sequence[tuple[float, float]]) -> bool:
    """Return ``True`` if the first and last vertices are coincident."""
    if len(poly) < 3:
        return False
    (x0, y0), (x1, y1) = poly[0], poly[-1]
    return math.hypot(x1 - x0, y1 - y0) < 1e-6


def _units_code(units: str) -> int:
    mapping = {
        "unitless": 0,
        "inches": 1,
        "feet": 2,
        "miles": 3,
        "millimeters": 4,
        "centimeters": 5,
        "meters": 6,
    }
    return mapping.get(units.lower(), 4)


def _tessellate_arc(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    end_deg: float,
    *,
    segments: int = 32,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(segments + 1):
        t = i / segments
        angle = math.radians(start_deg + t * (end_deg - start_deg))
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _export_polyline_chain(
    dxf: DXF,
    pts: list[tuple[float, float]],
    layer: Layer,
    *,
    closed: bool = False,
    name: str | None = None,
) -> None:
    """Write *pts* as an LWPolyline (or individual Lines if only 2 points).

    If *name* is provided, attach it as ACAD_XDATA so downstream readers
    can identify the entity.
    """
    if len(pts) == 2:
        # Single segment → export as Line for simplicity
        line = Line()
        line.start = pts[0]
        line.end = pts[1]
        line.layer = layer.name
        if name:
            _attach_name_xdata(line, name)
        dxf.entities.add(line)
        return

    pline = LWPolyline()
    pline.vertices = pts
    pline.closed = closed
    pline.layer = layer.name
    if name:
        _attach_name_xdata(pline, name)
    dxf.entities.add(pline)


def _attach_name_xdata(entity, name: str) -> None:
    """Attach a ``"NAME"`` XData record to *entity*.

    Uses the ``ACAD`` application registry and a simple ``"NAME"`` tag so
    that most DXF readers (AutoCAD, LibreCAD, QCAD, ezdxf) can retrieve it.
    """
    # XData format:
    #   1001  "ACAD"       application name
    #   1000  "<name>"     our custom tag
    entity.xdata = entity.xdata or []
    entity.xdata.extend(["ACAD", name])
