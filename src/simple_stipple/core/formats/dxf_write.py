"""Serialize polylines, shapes, and dimensions out to DXF."""

from __future__ import annotations

import math
import re
from typing import Any

from simple_stipple.core.cad.constants import (
    DXF_CLOSURE_EPS,
    DXF_DEDUP_EPS,
)
from simple_stipple.core.cad.shape_factory import shape_from_meta
from simple_stipple.core.formats.dxf import (
    _LOG,
    _ezdxf_new,
    validate_dxf_document,
)


def _normalize_polyline_for_dxf(
    pts: list[tuple[float, float]],
    *,
    closure_eps: float = DXF_CLOSURE_EPS,
    force_close: bool = False,
) -> tuple[list[tuple[float, float]], bool]:
    """Clean a polyline for DXF emission.

    * Drops consecutive duplicate points (zero-length segments) which break
      some downstream CAD tools and inflate file size.
    * Detects whether the polyline is closed (first \u2248 last within
      ``closure_eps``) and strips the trailing duplicate so callers can
      hand the points to ezdxf with ``close=True`` cleanly.

    Returns ``(coords, is_closed)``.  ``is_closed`` is True when either
    the input was naturally closed or ``force_close`` is requested AND
    the result has \u2265 3 distinct points.
    """
    if not pts:
        return [], False

    # Pass 0: drop NaN / inf coordinates that would corrupt the DXF.
    finite: list[tuple[float, float]] = []
    for p in pts:
        try:
            x = float(p[0])
            y = float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            finite.append((x, y))
    if not finite:
        return [], False

    # Pass 1: drop runs of identical points.
    cleaned: list[tuple[float, float]] = [finite[0]]
    for p in finite[1:]:
        last = cleaned[-1]
        if abs(p[0] - last[0]) > DXF_DEDUP_EPS or abs(p[1] - last[1]) > DXF_DEDUP_EPS:
            cleaned.append(p)

    if len(cleaned) < 2:
        return cleaned, False

    # Pass 2: detect closure (first ~ last) and strip the trailing copy.
    first, last = cleaned[0], cleaned[-1]
    naturally_closed = (
        abs(first[0] - last[0]) <= closure_eps and abs(first[1] - last[1]) <= closure_eps
    )
    if naturally_closed and len(cleaned) >= 3:
        cleaned = cleaned[:-1]
        is_closed = True
    else:
        is_closed = bool(force_close) and len(cleaned) >= 3

    return cleaned, is_closed


_LAYER_COLORS = [5, 4, 6, 1, 2, 8]


def _layer_from_meta_name(name: str | None) -> str | None:
    if not name:
        return None
    label = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name).strip())
    label = re.sub(r"_+", "_", label).strip("_")
    return label[:255] or None


def _entity_attributes(
    doc: Any,
    default_attrs: dict[str, str],
    meta: dict[str, Any] | None,
    entity_names: list[str] | None,
    index: int,
) -> dict[str, str]:
    attrs = dict(default_attrs)
    layer_name = _layer_from_meta_name(meta.get("name")) if isinstance(meta, dict) else None
    if not layer_name and entity_names and index < len(entity_names):
        layer_name = _layer_from_meta_name(entity_names[index])
    if not layer_name:
        return attrs
    if layer_name not in doc.layers:
        doc.layers.add(layer_name, color=2)
    attrs["layer"] = layer_name
    return attrs


def _write_dimension(
    msp: Any,
    points: list[tuple[float, float]],
    meta: dict[str, Any],
    attrs: dict[str, str],
) -> bool:
    try:
        p1 = tuple(meta.get("p1", points[0]))
        p2 = tuple(meta.get("p2", points[1]))
        precision = max(0, min(8, int(meta.get("precision", 2))))
        dim_override = {"dimdec": precision}
        if meta.get("type") == "angle" and "p3" in meta:
            p3 = tuple(meta["p3"])
            override = msp.add_angular_dim_3p(
                base=p3,
                center=p2,
                p1=p1,
                p2=p3,
                override=dim_override,
                dxfattribs=attrs or None,
            )
        elif meta.get("type") == "diameter":
            center = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            override = msp.add_diameter_dim(
                center=center,
                mpoint=p2,
                override=dim_override,
                dxfattribs=attrs or None,
            )
        else:
            offset = float(meta.get("offset", 5.0))
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy)
            if length <= 1e-12:
                return False
            base = (p1[0] - dy * offset / length, p1[1] + dx * offset / length)
            override = msp.add_linear_dim(
                base=base,
                p1=p1,
                p2=p2,
                dimstyle="EZDXF",
                override=dim_override,
                dxfattribs=attrs or None,
            )
        override.render()
        return True
    except (TypeError, ValueError, IndexError):
        _LOG.warning("Invalid dimension metadata; exporting fallback line")
        return False


def _write_native_shape(
    msp: Any,
    kind: str,
    meta: dict[str, Any] | None,
    attrs: dict[str, str],
) -> bool:
    if kind == "polyline" or not isinstance(meta, dict):
        return False
    if kind == "ellipse" and "rotation" not in meta and "angle" in meta:
        meta = {**meta, "rotation": meta["angle"]}
    shape = shape_from_meta(kind, meta)
    return shape is not None and shape.to_dxf(msp, attrs or None)


def _write_polyline(
    msp: Any,
    points: list[tuple[float, float]],
    attrs: dict[str, str],
    *,
    close: bool,
    open_paths: bool,
) -> None:
    coords, is_closed = _normalize_polyline_for_dxf(
        points,
        force_close=close and not open_paths,
    )
    if len(coords) < 2:
        return
    msp.add_lwpolyline(
        coords,
        close=False if open_paths else is_closed,
        dxfattribs=attrs or None,
    )


def write_polylines_dxf(
    polylines: list[list[tuple[float, float]]],
    out_path: str,
    close: bool = False,
    open_paths: bool = False,
    border_polys: list[list[tuple[float, float]]] | None = None,
    pattern_layer: str | None = None,
    border_layer_prefix: str = "BORDER",
    entity_kinds: list[str] | None = None,
    entity_meta: list[dict[str, Any] | None] | None = None,
    entity_names: list[str] | None = None,
    extra_layers: dict[str, list[list[tuple[float, float]]]] | None = None,
    extra_layer_records: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    doc = _ezdxf_new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Cycling palette used for both the main layer and any extra layers.
    # We deliberately avoid color 7 here: many CAM/laser tools treat DXF
    # color 7 as "BYBLOCK / no color set" and refuse to fill those
    # entities, which would silently break the user's first layer.
    dxfattrs: dict[str, str] = {}
    if pattern_layer:
        if pattern_layer not in doc.layers:
            doc.layers.add(pattern_layer, color=_LAYER_COLORS[0])
        dxfattrs = {"layer": pattern_layer}

    # Pad the kind/meta lists to the polyline count — zip() truncates at the
    # shortest input, which silently dropped shapes when callers passed
    # shorter lists. Never drop geometry.
    kinds = list(entity_kinds) if entity_kinds is not None else []
    metas = list(entity_meta) if entity_meta is not None else []
    if len(kinds) < len(polylines):
        kinds += ["polyline"] * (len(polylines) - len(kinds))
    if len(metas) < len(polylines):
        metas += [None] * (len(polylines) - len(metas))

    for i, (c, kind, meta) in enumerate(zip(polylines, kinds, metas)):
        if len(c) < 2:
            continue
        entity_attrs = _entity_attributes(doc, dxfattrs, meta, entity_names, i)
        if kind == "dimension" and isinstance(meta, dict):
            if _write_dimension(msp, c, meta, entity_attrs):
                continue
        if _write_native_shape(msp, kind, meta, entity_attrs):
            continue
        _write_polyline(msp, c, entity_attrs, close=close, open_paths=open_paths)

    if border_polys:
        # Every outline is its own entity, but they all share one layer —
        # laser/CAM software treats a layer as a single job, and splitting
        # outlines across outline_1/outline_2/… made it run each outline
        # as a separate job instead of cutting them together.
        if border_layer_prefix not in doc.layers:
            doc.layers.add(border_layer_prefix, color=3)
        for c in border_polys:
            coords, is_closed = _normalize_polyline_for_dxf(
                c,
                force_close=not bool(open_paths),
            )
            if len(coords) >= 3 and (open_paths or is_closed):
                msp.add_lwpolyline(
                    coords,
                    close=(False if open_paths else True),
                    dxfattribs={"layer": border_layer_prefix},
                )

    if extra_layer_records:
        extra_layers = {
            name: [list(record["polyline"]) for record in records]
            for name, records in extra_layer_records.items()
        }

    if extra_layers:
        # Each entry produces its own DXF layer. Polylines are written as
        # LWPOLYLINE entities; closure is inferred from coordinate equality.
        # Colors cycle so layers are visually distinguishable in CAD viewers.
        # Offset by 1 when a pattern_layer was emitted so the main layer's
        # color (LAYER_COLORS[0]) isn't reused on the first extra.
        color_offset = 1 if pattern_layer else 0
        for color_idx, (layer_name, layer_polys) in enumerate(extra_layers.items()):
            if not layer_polys:
                continue
            color = _LAYER_COLORS[(color_idx + color_offset) % len(_LAYER_COLORS)]
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=color)
            attrs = {"layer": layer_name}
            records = (extra_layer_records or {}).get(layer_name, [])
            for record_index, c in enumerate(layer_polys):
                if len(c) < 2:
                    continue
                if record_index < len(records):
                    record = records[record_index]
                    kind = str(record.get("kind", "polyline"))
                    meta = record.get("meta")
                    if kind != "polyline" and isinstance(meta, dict):
                        shape = shape_from_meta(kind, meta)
                        if shape is not None and shape.to_dxf(msp, attrs):
                            continue
                coords, is_closed = _normalize_polyline_for_dxf(c, force_close=False)
                if len(coords) < 2:
                    continue
                msp.add_lwpolyline(coords, close=is_closed, dxfattribs=attrs)

    # Audit the document before persisting. Never write a malformed DXF —
    # a file that crashes or silently misbehaves in downstream CAD/CAM tools
    # is worse than a visible export error here.
    try:
        validate_dxf_document(doc)
    except ValueError:
        _LOG.error(
            "write_polylines_dxf: refusing to write invalid document to %s",
            out_path,
        )
        raise

    from simple_stipple.platform.storage import atomic_write_via

    atomic_write_via(out_path, lambda p: doc.saveas(str(p)))
