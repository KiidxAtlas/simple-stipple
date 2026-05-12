"""Workspace state serialisation/restoration for PatternPage."""

from __future__ import annotations

from typing import Any

from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import graph_from_polylines, polylines_from_graph
from src.ui.pages.pattern.params import collect_form_state, restore_form_state


def get_pattern_workspace_state(page: Any) -> dict:
    # If showing preview, the canvas has preview polys — save edit_polys from our snapshot
    polys_to_save = (
        page._edit_polys
        if page._showing_preview
        else page._canvas.get_polylines_state()
    )
    doc_graph = graph_from_polylines(
        polys_to_save,
        layer="pattern_active",
        as_segments=False,
    )
    return {
        "dxf_path": page._dxf_edit.text(),
        "params": collect_form_state(page),
        "orig_polys": page._orig_polys,
        "edit_polys": polys_to_save,
        "outline_ids": list(page._outline_ids),
        "orig_w": page._orig_w,
        "orig_h": page._orig_h,
        "canvas_view": page._canvas.get_view_state(),
        "preview_polys": page._preview_polys_cache,
        "showing_preview": page._showing_preview,
        "document_graph": doc_graph.snapshot(),
        "zones": list(page._zones),
        "exclusion_ids": list(page._exclusion_ids),
    }


def apply_pattern_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    if not isinstance(state, dict):
        state = {}
    page._imported_dxf_layers = []
    page._dxf_edit.setText(str(state.get("dxf_path", "")))
    restore_form_state(page, state.get("params", {}))
    page._orig_polys = [list(poly) for poly in state.get("orig_polys", [])]
    graph_state = state.get("document_graph")
    if isinstance(graph_state, dict):
        doc_graph = DocumentGraph()
        doc_graph.restore(graph_state)
        migrated = polylines_from_graph(doc_graph, layer="pattern_active")
        if not migrated:
            migrated = polylines_from_graph(doc_graph, layer="geometry")
        edit_polys = [list(poly) for poly in migrated]
    else:
        edit_polys = [list(poly) for poly in state.get("edit_polys", page._orig_polys)]
    page._edit_polys = [list(poly) for poly in edit_polys]
    outline_ids = state.get("outline_ids", [])
    if isinstance(outline_ids, list) and len(outline_ids) == len(page._edit_polys):
        page._outline_ids = [str(v) for v in outline_ids]
    else:
        page._outline_ids = page._fresh_outline_ids(len(page._edit_polys))
    page._orig_w = float(state.get("orig_w", 0.0))
    page._orig_h = float(state.get("orig_h", 0.0))
    if page._orig_w > 0 and page._orig_h > 0:
        page._orig_dims_label.setText(f"{page._orig_w:.2f} × {page._orig_h:.2f} mm")
    else:
        page._orig_dims_label.setText("—")
    page._preview_polys_cache = [list(poly) for poly in state.get("preview_polys", [])]
    show_preview = bool(state.get("showing_preview", False)) and bool(
        page._preview_polys_cache
    )
    if show_preview:
        page._canvas.set_polylines_state(page._preview_polys_cache, fit=True)
        page._showing_preview = True
        page._preview_btn.setChecked(True)
        page._preview_btn.setProperty("active", True)
        page._preview_btn.style().unpolish(page._preview_btn)
        page._preview_btn.style().polish(page._preview_btn)
    else:
        page._canvas.set_polylines_state(page._edit_polys, fit=bool(page._edit_polys))
        page._showing_preview = False
        page._preview_btn.setChecked(False)
        page._preview_btn.setProperty("active", False)
        page._preview_btn.style().unpolish(page._preview_btn)
        page._preview_btn.style().polish(page._preview_btn)
    if state.get("canvas_view"):
        page._canvas.set_view_state(state["canvas_view"])
    page._suspend_state = False
    page._refresh_canvas_panels()
    page._zones = list(state.get("zones", []))
    page._refresh_zone_list()
    page._exclusion_ids = [str(v) for v in state.get("exclusion_ids", [])]
    page._sync_canvas_cutout_highlight()
    page._refresh_cutout_status()


def clear_pattern_workspace_state(page: Any) -> None:
    apply_pattern_workspace_state(page, {})
    page._imported_dxf_layers = []
    page._outline_ids = []
    page._set_status("")
    page._refresh_canvas_panels()
