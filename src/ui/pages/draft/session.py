"""Workspace state serialisation/restoration for DraftPage.

Current format: a flat entity-record list (each record carries its layer,
flags, and group) plus the ordered layer list and active layer. The legacy
DocumentGraph format ("document_graph" + per-layer hidden/locked buckets)
is still read and migrated on load.
"""

from __future__ import annotations

from typing import Any


def get_draft_workspace_state(page: Any) -> dict:
    canvas = page._canvas
    return {
        "entities": canvas.get_entity_records(),
        "layer_order": canvas.layer_names(),
        "active_layer": canvas.active_layer,
        "canvas_view": canvas.get_view_state(),
        "quick_shape_mode": canvas.quick_shape_mode,
        "quick_shape_enabled": canvas.quick_shape_enabled,
        "last_input_dxf": page._last_in_path,
    }


def _apply_legacy_buckets(canvas: Any, layer_view_state: dict) -> None:
    """Legacy sessions stored hidden/locked as per-layer *local* index
    buckets (the canvas only ever held one layer at a time). Map them onto
    the per-entity flags using each layer's entity order."""
    by_layer: dict[str, list[int]] = {}
    for i, e in enumerate(canvas._entities):
        by_layer.setdefault(e.layer or "", []).append(i)
    for layer_name, payload in layer_view_state.items():
        if not isinstance(payload, dict):
            continue
        globals_ = by_layer.get(str(layer_name), [])
        for local in payload.get("hidden_indices", []) or []:
            if isinstance(local, int) and 0 <= local < len(globals_):
                canvas._entities[globals_[local]].hidden = True
        for local in payload.get("locked_indices", []) or []:
            if isinstance(local, int) and 0 <= local < len(globals_):
                canvas._entities[globals_[local]].locked = True


def apply_draft_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    if not isinstance(state, dict):
        state = {}
    rt = page._rt()
    canvas = page._canvas

    entities = state.get("entities")
    graph_state = state.get("document_graph")
    if isinstance(entities, list):
        canvas.set_entity_records(entities)
        order = [str(n) for n in state.get("layer_order", []) or [] if str(n)]
        active = state.get("active_layer")
        if not order:
            order = [rt.default_layer]
        canvas.set_layer_model(order, str(active) if active else order[0])
        if state.get("canvas_view"):
            canvas.set_view_state(state["canvas_view"])
    elif isinstance(graph_state, dict):
        # Legacy DocumentGraph workspace.
        rt.restore_graph_state(graph_state)
        layer_view_state = state.get("layer_view_state")
        if isinstance(layer_view_state, dict):
            _apply_legacy_buckets(canvas, layer_view_state)
        view_state = state.get("canvas_view")
        if isinstance(view_state, dict):
            # hidden/locked/groups in the legacy view state are indexed by
            # the active layer's *local* order — records already carry all
            # of that, so only the camera/grid parts are safe to apply.
            safe = {
                k: v
                for k, v in view_state.items()
                if k not in {"hidden_indices", "locked_indices", "groups"}
            }
            canvas.set_view_state(safe)
    else:
        polys = state.get("canvas_polys", [])
        if polys:
            rt.load_polys(polys, fit=True)
        else:
            rt.reset_empty()
        if state.get("canvas_view"):
            canvas.set_view_state(state["canvas_view"])

    if canvas.poly_count == 0:
        canvas.fit()

    quick_shape_enabled = bool(state.get("quick_shape_enabled", False))
    canvas.set_quick_shape_enabled(quick_shape_enabled)
    if quick_shape_enabled and state.get("quick_shape_mode"):
        canvas.set_quick_shape_mode(str(state["quick_shape_mode"]), flash=False)
    page._last_in_path = str(state.get("last_input_dxf", "") or "") or None

    page._suspend_state = False
    page._refresh_status()


def clear_draft_workspace_state(page: Any) -> None:
    page._suspend_state = True
    page._rt().reset_empty()
    page._canvas.set_mode("select")
    page._canvas.set_quick_shape_mode("rectangle", flash=False)
    page._canvas.set_quick_shape_enabled(False)
    page._last_in_path = None
    page._suspend_state = False
    page._refresh_status()
