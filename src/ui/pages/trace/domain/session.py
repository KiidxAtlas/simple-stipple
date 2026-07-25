"""Workspace state serialisation/restoration for TracePage.

State management uses Pydantic models (``TraceTabState``) for schema
validation at the load/save boundary. The ``get_*`` / ``apply_*``
functions work with raw dicts (for compatibility with existing UI code)
but validate and coerce those dicts through ``TraceTabState`` internally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from src.backend.model.document import TraceTabState
from src.ui.pages.trace.ui.form import trace_default


def _coerce_to_trace_state(state: dict | None) -> TraceTabState:
    """Coerce a raw dict (possibly from an old workspace file) into a
    ``TraceTabState``. Returns a minimal valid instance if the data is
    completely malformed."""
    if not isinstance(state, dict):
        return TraceTabState()
    try:
        return cast(TraceTabState, TraceTabState.from_dict(state))
    except Exception:
        return TraceTabState()


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
    trace_state = _coerce_to_trace_state(state)

    page._reset_trace_runtime_state()
    image_path = str(trace_state.image_path).strip()
    page._img_path = image_path or None
    page._img_edit.setText(trace_state.image_path)
    settings = page._settings
    page._blur.setText(str(trace_state.blur or trace_default(settings, "blur")))
    page._thresh_entry.setText(str(trace_state.threshold or trace_default(settings, "threshold")))
    page._auto_thresh_cb.setChecked(trace_state.auto_threshold)
    page._update_thresh_controls()
    page._invert_cb.setChecked(trace_state.invert)
    page._edge_mode_cb.setChecked(trace_state.edge_mode)
    page._canny_low.setText(str(trace_state.canny_low or trace_default(settings, "canny_low")))
    page._canny_high.setText(str(trace_state.canny_high or trace_default(settings, "canny_high")))
    page._outer_only_cb.setChecked(trace_state.outer_only)
    page._simplify.setText(str(trace_state.simplify or trace_default(settings, "simplify")))
    page._min_area.setText(str(trace_state.min_area or trace_default(settings, "min_area")))
    page._max_area.setText(str(trace_state.max_area or trace_default(settings, "max_area")))
    page._close_r.setText(str(trace_state.close_r or trace_default(settings, "close_r")))
    page._width_mm.setText(str(trace_state.width_mm or trace_default(settings, "width_mm")))
    page._height_mm.setText(str(trace_state.height_mm or trace_default(settings, "height_mm")))
    page._max_res.setText(str(trace_state.max_res or trace_default(settings, "max_res")))
    page._lock_cb.setChecked(trace_state.aspect_locked)
    page._bg_visible_cb.setChecked(trace_state.bg_visible)
    page._img_w_px = trace_state.img_w_px
    page._img_h_px = trace_state.img_h_px
    page._img_aspect = trace_state.img_aspect
    page._last_width_mm = trace_state.last_width_mm
    page._last_height_mm = trace_state.last_height_mm
    if image_path and Path(image_path).exists():
        page._load_thumbnail(image_path)
    else:
        page._img_info_lbl.setText("")
    polys: list[list[tuple[float, float]]] = [list(poly) for poly in trace_state.canvas_polys]
    page._canvas.set_polylines_state(polys, fit=bool(polys))
    if trace_state.last_width_mm > 0 and trace_state.last_height_mm > 0:
        page._canvas.set_image_bounds(trace_state.last_width_mm, trace_state.last_height_mm)
    if polys and trace_state.canvas_view:
        page._canvas.set_view_state(trace_state.canvas_view)
    # The view was just established above (restored or fresh-fit) — the next
    # retrace from a settings tweak must preserve it, not re-fit.
    page._needs_view_fit = not polys
    if image_path and trace_state.bg_visible and trace_state.last_width_mm > 0:
        page._restore_background_from_path(image_path)
    elif not trace_state.bg_visible:
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
