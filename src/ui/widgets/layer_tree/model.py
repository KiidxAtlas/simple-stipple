"""Layer tree shared type aliases and helper functions."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

# ── Type aliases ──────────────────────────────────────────────────────────────

LayerTreeState = dict[str, dict[str, set[int]]]
LayerTreeRow = dict[str, Any]
LayerRowsBuilder = Callable[[LayerTreeState], list[dict[str, Any]]]

# ── Layer tree helper functions ───────────────────────────────────────────────


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
    groups: dict[int, int] | None = None,
    group_labels: dict[int, str] | None = None,
) -> list[LayerTreeRow]:
    """Build one tree row per shape — except grouped shapes, which collapse
    into a single row per group (like an SVG ``<g>``). A group row's key is
    the tuple of member indices; use :func:`flatten_shape_keys` to resolve
    keys back to indices."""
    rows: list[LayerTreeRow] = []
    groups = groups or {}
    members_by_gid: dict[int, list[int]] = {}
    for idx in range(len(polylines)):
        gid = groups.get(idx)
        if gid is not None:
            members_by_gid.setdefault(gid, []).append(idx)

    emitted: set[int] = set()
    for idx, poly in enumerate(polylines):
        gid = groups.get(idx)
        if gid is None:
            rows.append(
                {
                    "key": idx,
                    "label": label_builder(idx, poly),
                    "visible": idx not in hidden,
                    "editable": editable,
                    "draggable": draggable,
                }
            )
            continue
        if gid in emitted:
            continue
        emitted.add(gid)
        members = members_by_gid[gid]
        custom = (group_labels or {}).get(gid)
        title = custom or "Group"
        rows.append(
            {
                "key": tuple(members),
                "label": f"{idx + 1:02d}  {title}  ·  {len(members)} shapes",
                "visible": any(i not in hidden for i in members),
                "editable": editable,
                "draggable": draggable,
            }
        )
    return rows


def flatten_shape_keys(keys: object) -> list[int]:
    """Resolve tree shape keys (ints or group tuples) to shape indices."""
    if not isinstance(keys, (list, tuple, set)):
        keys = [keys]
    out: list[int] = []
    for k in keys:
        if isinstance(k, int):
            out.append(k)
        elif isinstance(k, (tuple, list)):
            out.extend(int(i) for i in k if isinstance(i, int))
    return out


def build_layer_row(
    *,
    name: str,
    display_name: str,
    active: bool,
    visible: bool,
    editable: bool,
    shapes: list[LayerTreeRow],
    color: str | None = None,
) -> LayerTreeRow:
    return {
        "name": name,
        "internal_name": name,
        "display_name": display_name,
        "visible": visible,
        "active": active,
        "editable": editable,
        "shapes": shapes,
        "color": color,
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
