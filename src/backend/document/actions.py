"""Deterministic action protocol for DocumentGraph."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.backend.document.graph import DocumentGraph, EntityRef
from src.backend.geometry.shapes import (
    shape_circle,
    shape_polygon,
    shape_rect,
    shape_slot,
)


class ActionType:
    CREATE_SEGMENT = "create_segment"
    CREATE_SHAPE = "create_shape"
    DELETE_ENTITIES = "delete_entities"
    SET_PARAM = "set_param"
    SET_ACTIVE_LAYER = "set_active_layer"
    APPLY_TRANSFORM = "apply_transform"
    REPLACE_LAYER_POLYLINES = "replace_layer_polylines"


@dataclass
class ActionResult:
    action_id: int
    touched: list[EntityRef]
    invalidated_layers: list[str]


def create_segment(
    graph: DocumentGraph,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    layer: str | None = None,
) -> ActionResult:
    layer_name = layer or graph.active_layer
    p1 = graph.add_point(x1, y1)
    p2 = graph.add_point(x2, y2)
    seg = graph.add_segment(p1.id, p2.id, layer=layer_name)

    if layer_name != "geometry":
        existing = graph.get_layer_polylines(layer_name, fallback_geometry=False)
        existing.append([(x1, y1), (x2, y2)])
        graph.set_layer_polylines(layer_name, existing)

    invalidated = sorted(graph.reachable_dependents({layer_name}))
    rec = graph.record_action(
        ActionType.CREATE_SEGMENT,
        {
            "layer": layer_name,
            "p1": p1.id,
            "p2": p2.id,
            "segment": seg.id,
        },
        touched=[("point", p1.id), ("point", p2.id), ("segment", seg.id)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def create_shape(
    graph: DocumentGraph,
    *,
    kind: str,
    bounds: tuple[float, float, float, float],
    layer: str | None = None,
    resolution: int = 64,
) -> ActionResult:
    x0, y0, x1, y1 = bounds
    w = abs(x1 - x0)
    h = abs(y1 - y0)
    if w < 1e-9 or h < 1e-9:
        rec = graph.record_action(
            ActionType.CREATE_SHAPE,
            {"kind": kind, "layer": layer or graph.active_layer, "ignored": True},
            touched=[],
            invalidated_layers=[],
        )
        return ActionResult(rec.id, rec.touched, rec.invalidated_layers)

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    shape = kind.strip().lower()
    if shape == "rectangle":
        local = shape_rect(w, h)
    elif shape == "circle":
        local = shape_circle(min(w, h) / 2.0, max(12, resolution))
    elif shape == "slot":
        local = shape_slot(max(w, h), min(w, h))
    elif shape == "hexagon":
        local = shape_polygon(6, min(w, h) / 2.0)
    else:
        raise ValueError(f"Unsupported shape kind: {kind}")

    poly = [(x + cx, y + cy) for x, y in local]

    layer_name = layer or graph.active_layer
    touched: list[EntityRef] = []
    if layer_name == "geometry":
        seg_ids = graph.add_polyline_as_segments(
            poly,
            layer="geometry",
            merge_points=False,
        )
        touched.extend(("segment", sid) for sid in seg_ids)
    else:
        existing = graph.get_layer_polylines(layer_name, fallback_geometry=False)
        existing.append(poly)
        graph.set_layer_polylines(layer_name, existing)

    invalidated = sorted(graph.reachable_dependents({layer_name}))
    rec = graph.record_action(
        ActionType.CREATE_SHAPE,
        {
            "kind": shape,
            "layer": layer_name,
            "bounds": bounds,
        },
        touched=touched,
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def delete_entities(
    graph: DocumentGraph,
    refs: list[EntityRef],
    *,
    layer: str | None = None,
) -> ActionResult:
    touched: list[EntityRef] = []
    affected_layers: set[str] = set()

    layer_name = layer or graph.active_layer

    for kind, ref in refs:
        if kind == "segment":
            sid = int(ref)
            seg = graph.segments.get(sid)
            if seg is not None:
                affected_layers.add(seg.layer)
            graph.remove_segment(sid)
            touched.append(("segment", sid))
        elif kind == "point":
            pid = int(ref)
            graph.remove_point(pid)
            affected_layers.add("geometry")
            touched.append(("point", pid))
        elif kind == "layer":
            lname = str(ref)
            if lname in graph.layers:
                graph.layers.pop(lname, None)
                affected_layers.add(lname)
                touched.append(("layer", lname))
        elif kind == "param":
            pname = str(ref)
            if pname in graph.params:
                graph.params.pop(pname, None)
                touched.append(("param", pname))
        elif kind == "source":
            sid = int(ref)
            graph.sources.pop(sid, None)
            touched.append(("source", sid))
        elif kind == "layer-polyline":
            idx = int(ref)
            polys = graph.get_layer_polylines(layer_name, fallback_geometry=False)
            if 0 <= idx < len(polys):
                polys.pop(idx)
                graph.set_layer_polylines(layer_name, polys)
                touched.append(("layer-polyline", idx))  # type: ignore[list-item]
                affected_layers.add(layer_name)

    if not affected_layers:
        affected_layers.add(layer_name)

    invalidated = sorted(graph.reachable_dependents(set(affected_layers)))
    rec = graph.record_action(
        ActionType.DELETE_ENTITIES,
        {"refs": refs, "layer": layer_name},
        touched=touched,
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def set_param(graph: DocumentGraph, name: str, value: Any) -> ActionResult:
    node = graph.upsert_param(name, value)
    invalidated = sorted(graph.reachable_dependents({f"param:{name}"}))
    rec = graph.record_action(
        ActionType.SET_PARAM,
        {"name": name, "value": value},
        touched=[("param", node.name)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def set_active_layer(graph: DocumentGraph, layer: str) -> ActionResult:
    graph.set_active_layer(layer)
    rec = graph.record_action(
        ActionType.SET_ACTIVE_LAYER,
        {"layer": layer},
        touched=[("layer", layer)],
        invalidated_layers=[],
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def replace_layer_polylines(
    graph: DocumentGraph,
    layer: str,
    polylines: list[list[tuple[float, float]]],
) -> ActionResult:
    graph.set_layer_polylines(layer, polylines)
    invalidated = sorted(graph.reachable_dependents({layer}))
    rec = graph.record_action(
        ActionType.REPLACE_LAYER_POLYLINES,
        {"layer": layer, "count": len(polylines)},
        touched=[("layer", layer)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def apply_transform(
    graph: DocumentGraph,
    refs: list[EntityRef],
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    scale: float = 1.0,
    rotate_deg: float = 0.0,
    origin: tuple[float, float] | None = None,
    layer: str | None = None,
) -> ActionResult:
    if scale <= 0:
        raise ValueError("scale must be > 0")

    theta = math.radians(rotate_deg)
    ct = math.cos(theta)
    st = math.sin(theta)

    touched: list[EntityRef] = []
    affected_layers: set[str] = set()

    ox, oy = origin if origin is not None else (0.0, 0.0)

    def _tx(x: float, y: float) -> tuple[float, float]:
        x0 = x - ox
        y0 = y - oy
        x1 = x0 * scale
        y1 = y0 * scale
        xr = x1 * ct - y1 * st
        yr = x1 * st + y1 * ct
        return xr + ox + dx, yr + oy + dy

    for kind, ref in refs:
        if kind == "point":
            pid = int(ref)
            point = graph.points.get(pid)
            if point is None:
                continue
            point.x, point.y = _tx(point.x, point.y)
            touched.append(("point", pid))
            affected_layers.add("geometry")
        elif kind == "segment":
            sid = int(ref)
            seg = graph.segments.get(sid)
            if seg is None:
                continue
            for pid in (seg.p0, seg.p1):
                point = graph.points.get(pid)
                if point is None:
                    continue
                point.x, point.y = _tx(point.x, point.y)
                touched.append(("point", pid))
            touched.append(("segment", sid))
            affected_layers.add(seg.layer)
        elif kind == "layer":
            lname = str(ref)
            polys = graph.get_layer_polylines(lname, fallback_geometry=False)
            transformed = [[_tx(x, y) for x, y in poly] for poly in polys]
            graph.set_layer_polylines(lname, transformed)
            touched.append(("layer", lname))
            affected_layers.add(lname)

    if not affected_layers:
        layer_name = layer or graph.active_layer
        affected_layers.add(layer_name)

    invalidated = sorted(graph.reachable_dependents(affected_layers))
    rec = graph.record_action(
        ActionType.APPLY_TRANSFORM,
        {
            "refs": refs,
            "dx": dx,
            "dy": dy,
            "scale": scale,
            "rotate_deg": rotate_deg,
            "origin": (ox, oy),
        },
        touched=touched,
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)
