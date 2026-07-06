"""Workspace state serialisation/restoration for TracePage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import polylines_from_graph
from src.ui.pages.trace.defaults import trace_default


def get_trace_workspace_state(page: Any) -> dict:
    return {
        "image_path": page._img_path or page._img_edit.text(),
        "blur": page._blur.text(),
        "threshold": page._thresh_entry.text(),
        "auto_threshold": page._auto_thresh_cb.isChecked(),
        "invert": page._invert_cb.isChecked(),
        "edge_mode": page._edge_mode_cb.isChecked(),
        "canny_low": page._canny_low.text(),
        "canny_high": page._canny_high.text(),
        "outer_only": page._outer_only_cb.isChecked(),
        "simplify": page._simplify.text(),
        "min_area": page._min_area.text(),
        "max_area": page._max_area.text(),
        "close_r": page._close_r.text(),
        "width_mm": page._width_mm.text(),
        "height_mm": page._height_mm.text(),
        "max_res": page._max_res.text(),
        "aspect_locked": page._lock_cb.isChecked(),
        "bg_visible": page._bg_visible_cb.isChecked(),
        "img_w_px": page._img_w_px,
        "img_h_px": page._img_h_px,
        "img_aspect": page._img_aspect,
        "last_width_mm": page._last_width_mm,
        "last_height_mm": page._last_height_mm,
        "canvas_polys": page._canvas.get_polylines_state(),
        "canvas_view": page._canvas.get_view_state(),
    }


def apply_trace_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    if not isinstance(state, dict):
        state = {}
    page._reset_trace_runtime_state()
    image_path = str(state.get("image_path", "")).strip()
    page._img_path = image_path or None
    page._img_edit.setText(str(state.get("image_path", "")))
    settings = page._settings
    page._blur.setText(str(state.get("blur", trace_default(settings, "blur"))))
    page._thresh_entry.setText(
        str(state.get("threshold", trace_default(settings, "threshold")))
    )
    page._auto_thresh_cb.setChecked(bool(state.get("auto_threshold", True)))
    page._update_thresh_controls()
    page._invert_cb.setChecked(bool(state.get("invert", False)))
    page._edge_mode_cb.setChecked(bool(state.get("edge_mode", False)))
    page._canny_low.setText(
        str(state.get("canny_low", trace_default(settings, "canny_low")))
    )
    page._canny_high.setText(
        str(state.get("canny_high", trace_default(settings, "canny_high")))
    )
    page._outer_only_cb.setChecked(bool(state.get("outer_only", False)))
    page._simplify.setText(str(state.get("simplify", trace_default(settings, "simplify"))))
    page._min_area.setText(str(state.get("min_area", trace_default(settings, "min_area"))))
    page._max_area.setText(str(state.get("max_area", trace_default(settings, "max_area"))))
    page._close_r.setText(str(state.get("close_r", trace_default(settings, "close_r"))))
    page._width_mm.setText(str(state.get("width_mm", trace_default(settings, "width_mm"))))
    page._height_mm.setText(str(state.get("height_mm", "---")))
    page._max_res.setText(str(state.get("max_res", trace_default(settings, "max_res"))))
    page._lock_cb.setChecked(bool(state.get("aspect_locked", True)))
    page._bg_visible_cb.setChecked(bool(state.get("bg_visible", True)))
    try:
        page._img_w_px = int(state.get("img_w_px", 0))
    except (TypeError, ValueError):
        page._img_w_px = 0
    try:
        page._img_h_px = int(state.get("img_h_px", 0))
    except (TypeError, ValueError):
        page._img_h_px = 0
    try:
        page._img_aspect = float(state.get("img_aspect", 1.0))
    except (TypeError, ValueError):
        page._img_aspect = 1.0
    try:
        page._last_width_mm = float(state.get("last_width_mm", 0.0))
    except (TypeError, ValueError):
        page._last_width_mm = 0.0
    try:
        page._last_height_mm = float(state.get("last_height_mm", 0.0))
    except (TypeError, ValueError):
        page._last_height_mm = 0.0
    if image_path and Path(image_path).exists():
        page._load_thumbnail(image_path)
    else:
        page._img_info_lbl.setText("")
    polys: list[list[tuple[float, float]]]
    # Legacy workspaces wrapped the same polylines in a DocumentGraph
    # snapshot; current ones store canvas_polys directly.
    graph_state = state.get("document_graph")
    if isinstance(graph_state, dict) and "canvas_polys" not in state:
        doc_graph = DocumentGraph()
        doc_graph.restore(graph_state)
        polys = polylines_from_graph(doc_graph, layer="trace_preview")
        if not polys:
            polys = polylines_from_graph(doc_graph, layer="geometry")
    else:
        polys = [list(poly) for poly in state.get("canvas_polys", [])]
    page._canvas.set_polylines_state(polys, fit=bool(polys))
    if page._last_width_mm > 0 and page._last_height_mm > 0:
        page._canvas.set_image_bounds(page._last_width_mm, page._last_height_mm)
    if polys and state.get("canvas_view"):
        page._canvas.set_view_state(state["canvas_view"])
    # The view was just established above (restored or fresh-fit) — the next
    # retrace from a settings tweak must preserve it, not re-fit.
    page._needs_view_fit = not polys
    if image_path and page._bg_visible_cb.isChecked() and page._last_width_mm > 0:
        page._restore_background_from_path(image_path)
    elif not page._bg_visible_cb.isChecked():
        page._canvas.clear_background_image()
    page._export_all_btn.setEnabled(bool(polys))
    page._export_sel_action.setEnabled(False)
    page._suspend_state = False
    page._update_trace_action_states()
    page._refresh_canvas_panels()


def clear_trace_workspace_state(page: Any) -> None:
    apply_trace_workspace_state(page, {})
    page._reset_trace_runtime_state()
    page._img_path = None
    page._img_edit.setText("")
    page._canvas.clear_background_image()
    page._canvas.set_image_bounds(0.0, 0.0)
    page._set_status("No image loaded.")
    page._update_trace_action_states()
    page._refresh_canvas_panels()
