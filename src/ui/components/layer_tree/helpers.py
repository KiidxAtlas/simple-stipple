"""Shared helpers for building and managing sidebar layer trees."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

LayerTreeState = dict[str, dict[str, set[int]]]
LayerTreeRow = dict[str, Any]


def hidden_bucket(state: LayerTreeState, layer_name: str) -> set[int]:
    bucket = state.setdefault(layer_name, {"hidden": set()})
    hidden = bucket.get("hidden")
    if hidden is None:
        hidden = set()
        bucket["hidden"] = hidden
    return hidden


def apply_layer_visibility(hidden: set[int], count: int, visible: bool) -> None:
    if visible:
        hidden.clear()
    else:
        hidden.clear()
        hidden.update(range(max(0, count)))


def apply_shape_visibility(hidden: set[int], idx: int, visible: bool) -> None:
    if visible:
        hidden.discard(idx)
    else:
        hidden.add(idx)


def build_shape_rows(
    polylines: list[list[tuple[float, float]]],
    hidden: set[int],
    label_builder: Callable[[int, list[tuple[float, float]]], str],
    *,
    editable: bool,
    draggable: bool,
) -> list[LayerTreeRow]:
    rows: list[LayerTreeRow] = []
    for idx, poly in enumerate(polylines):
        rows.append(
            {
                "key": idx,
                "label": label_builder(idx, poly),
                "visible": idx not in hidden,
                "editable": editable,
                "draggable": draggable,
            }
        )
    return rows


def build_layer_row(
    *,
    name: str,
    display_name: str,
    active: bool,
    visible: bool,
    editable: bool,
    shapes: list[LayerTreeRow],
) -> LayerTreeRow:
    return {
        "name": name,
        "internal_name": name,
        "display_name": display_name,
        "visible": visible,
        "active": active,
        "editable": editable,
        "shapes": shapes,
    }


def describe_polyline(idx: int, poly: list[tuple[float, float]]) -> str:
    if not poly:
        return f"{idx + 1:02d}  Empty"
    xs, ys = zip(*poly)
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    point_count = len(poly)
    if len(poly) > 1 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01:
        point_count -= 1
        kind = "Closed"
    else:
        kind = "Open"
    return (
        f"{idx + 1:02d}  {kind}  ·  {point_count} pts  ·  "
        f"{width:.1f} × {height:.1f} mm"
    )
