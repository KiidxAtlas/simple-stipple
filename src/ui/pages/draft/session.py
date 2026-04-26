"""Workspace state serialisation/restoration for DraftPage."""

from __future__ import annotations

from typing import Any


def get_draft_workspace_state(page: Any) -> dict:
    rt = page._rt()
    rt.graph_adapter.capture_from_canvas(page._canvas)
    return {
        "canvas_polys": page._canvas.get_polylines_state(),
        "canvas_view": page._canvas.get_view_state(),
        "layer_view_state": {
            name: {
                "hidden_indices": sorted(bucket.get("hidden", set())),
                "locked_indices": sorted(bucket.get("locked", set())),
            }
            for name, bucket in rt.layer_view_state.items()
        },
        "quick_shape_mode": page._canvas.quick_shape_mode,
        "quick_shape_enabled": page._canvas.quick_shape_enabled,
        "last_input_dxf": page._last_in_path,
        "document_graph": rt.doc_graph.snapshot(),
    }


def apply_draft_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    if not isinstance(state, dict):
        state = {}
    rt = page._rt()
    page._imported_dxf_layers = []
    rt.layer_view_state = {}

    graph_state = state.get("document_graph")
    if isinstance(graph_state, dict):
        rt.restore_graph_state(graph_state)

    polys = state.get("canvas_polys", [])
    if polys and not isinstance(graph_state, dict):
        rt.load_polys(polys, fit=True)
    else:
        if not isinstance(graph_state, dict):
            rt.reset_empty()

    layer_view_state = state.get("layer_view_state")
    if isinstance(layer_view_state, dict):
        for layer_name, payload in layer_view_state.items():
            if not isinstance(payload, dict):
                continue
            bucket = rt.layer_view_bucket(str(layer_name))
            bucket["hidden"] = {
                int(i) for i in payload.get("hidden_indices", []) if isinstance(i, int)
            }
            bucket["locked"] = {
                int(i) for i in payload.get("locked_indices", []) if isinstance(i, int)
            }

    rt.set_current_layer_view(rt.doc_graph.active_layer)

    if state.get("canvas_view"):
        page._canvas.set_view_state(state["canvas_view"])
        view_state = state["canvas_view"]
        if isinstance(view_state, dict):
            loaded_hidden = {
                int(i)
                for i in view_state.get("hidden_indices", [])
                if isinstance(i, int)
            }
            loaded_locked = {
                int(i)
                for i in view_state.get("locked_indices", [])
                if isinstance(i, int)
            }
            if not layer_view_state:
                bucket = rt.layer_view_bucket(rt.doc_graph.active_layer)
                bucket["hidden"] = loaded_hidden
                bucket["locked"] = loaded_locked
    page._reload_active_layer(fit=bool(page._canvas.poly_count == 0))

    quick_shape_enabled = bool(state.get("quick_shape_enabled", False))
    page._canvas.set_quick_shape_enabled(quick_shape_enabled)
    if quick_shape_enabled and state.get("quick_shape_mode"):
        page._canvas.set_quick_shape_mode(str(state["quick_shape_mode"]), flash=False)
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
