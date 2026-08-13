"""Workspace state serialisation/restoration for PatternPage.

State management uses Pydantic models (``PatternTabState``) for schema
validation at the load/save boundary. The ``get_*`` / ``apply_*``
functions work with raw dicts (for compatibility with existing UI code)
but validate and coerce those dicts through ``PatternTabState`` internally.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from simple_stipple.core.document.model import PatternTabState
from simple_stipple.features.pattern.form import collect_form_state, restore_form_state
from simple_stipple.features.pattern.outline_state import smallest_containing_outline
from simple_stipple.features.pattern.regions.treatments import migrate_workspace_zones
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


def _coerce_to_pattern_state(state: dict | None) -> PatternTabState:
    """Coerce a raw dict (possibly from an old workspace file) into a
    ``PatternTabState``. Returns a minimal valid instance if the data is
    completely malformed."""
    if not isinstance(state, dict):
        return PatternTabState()
    try:
        return cast(PatternTabState, PatternTabState.from_dict(state))
    except (ValidationError, TypeError, ValueError) as exc:
        LOGGER.warning("Discarding invalid Pattern workspace state: %s", exc)
        return PatternTabState()


def get_pattern_workspace_state(page: Any) -> dict:
    polys_to_save = page._canvas.get_polylines_state() or page._edit_polys
    state_dict = {
        "dxf_path": page._dxf_edit.text(),
        "params": collect_form_state(page),
        "orig_polys": page._orig_polys,
        "edit_polys": polys_to_save,
        "outline_ids": list(page._outline_ids),
        "outline_layers": dict(page._outline_layers),
        "pattern_cell_cutouts": [list(poly) for poly in page._pattern_cell_cutouts],
        "pattern_cell_instance_cutouts": [
            list(poly) for poly in page._pattern_cell_instance_cutouts
        ],
        "orig_w": page._orig_w,
        "orig_h": page._orig_h,
        "canvas_view": page._canvas.get_view_state(),
        # Preview cells are derived from the outline, zones, and parameters.
        # Persisting them duplicated the largest geometry in the document and
        # produced 30–40 MB recovery snapshots. Regenerate after restore.
        "preview_polys": [],
        "showing_preview": False,
        "zones": list(page._zones),
        "treatments": {key: dict(value) for key, value in page._treatments.items()},
        "custom_tile_polys": page._custom_tile_polys,
        "engraving_image_path": page._engraving_image_path,
        "engraving_options": {
            "x": page._engrave_x.value(),
            "y": page._engrave_y.value(),
            "width": page._engrave_w.value(),
            "height": page._engrave_h.value(),
            "rotation": page._engrave_rotation.value(),
            "interval": page._engrave_interval.value(),
            "min_power": page._engrave_min_power.value(),
            "max_power": page._engrave_max_power.value(),
            "speed": page._engrave_speed.value(),
            "gamma": page._engrave_gamma.value(),
            "passes": page._engrave_passes.value(),
            "invert": page._engrave_invert.isChecked(),
            "material": page._engrave_material.currentData(),
        },
    }
    return state_dict


def apply_pattern_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    pattern_state = _coerce_to_pattern_state(state)

    show_outline_path = getattr(page, "_show_outline_path", None)
    if callable(show_outline_path):
        show_outline_path(pattern_state.dxf_path)
    else:
        # Keep the state serializer usable by minimal non-Qt harnesses. Live
        # PatternPage instances always take the readable-path presentation
        # route above.
        page._dxf_edit.setText(pattern_state.dxf_path)
    restore_form_state(page, pattern_state.params)
    page._orig_polys = [list(poly) for poly in pattern_state.orig_polys]
    page._edit_polys = [list(poly) for poly in pattern_state.edit_polys]
    outline_ids = pattern_state.outline_ids
    if isinstance(outline_ids, list) and len(outline_ids) == len(page._edit_polys):
        page._outline_ids = [str(v) for v in outline_ids]
    else:
        page._outline_ids = page._fresh_outline_ids(len(page._edit_polys))
    page._outline_layers = {
        str(key): str(value)
        for key, value in pattern_state.outline_layers.items()
        if str(key) in page._outline_ids and str(value).strip()
    }
    for outline_id in page._outline_ids:
        page._outline_layers.setdefault(outline_id, "Outline")
    page._pattern_cell_cutouts = [list(poly) for poly in pattern_state.pattern_cell_cutouts]
    page._pattern_cell_instance_cutouts = [
        list(poly) for poly in pattern_state.pattern_cell_instance_cutouts
    ]
    page._orig_w = float(pattern_state.orig_w)
    page._orig_h = float(pattern_state.orig_h)
    if page._orig_w > 0 and page._orig_h > 0:
        page._orig_dims_label.setText(f"{page._orig_w:.2f} × {page._orig_h:.2f} mm")
    else:
        page._orig_dims_label.setText("—")
    page._preview_polys_cache = [list(poly) for poly in pattern_state.preview_polys]
    # The canvas only ever holds the editable outlines; the solved pattern is
    # an overlay rebuilt after restore.
    page._load_outline_canvas(fit=bool(page._edit_polys))
    page._canvas.set_result_polylines([])
    if pattern_state.canvas_view:
        page._canvas.set_view_state(pattern_state.canvas_view)
    page._suspend_state = False
    page._refresh_canvas_panels()
    # Workspaces written before region treatments carry zones plus explicit
    # cutout ids; both map onto treatments, so a pre-Phase-1 file opens with
    # the same output and nothing to reassign.
    stored = pattern_state.treatments
    if stored:
        page._treatments = {
            str(key): dict(value) for key, value in stored.items() if isinstance(value, dict)
        }
    else:
        page._treatments = migrate_workspace_zones(
            page._outline_ids,
            [dict(zone) for zone in pattern_state.zones],
            [str(v) for v in pattern_state.exclusion_ids],
        )
    page._refresh_zone_list()
    page._custom_tile_polys = [list(poly) for poly in pattern_state.custom_tile_polys]
    page._engraving_image_path = pattern_state.engraving_image_path
    # A pre-region workspace stored one image for the whole document. Give it
    # to whichever region already engraves, so it keeps its mask and becomes
    # editable like any other region property.
    _migrate_page_engraving_to_region(page, pattern_state)
    engraving = pattern_state.engraving_options
    if engraving:
        page._engrave_x.setValue(float(engraving.get("x", 0)))
        page._engrave_y.setValue(float(engraving.get("y", 0)))
        page._engrave_w.setValue(float(engraving.get("width", 100)))
        page._engrave_h.setValue(float(engraving.get("height", 100)))
        page._engrave_rotation.setValue(float(engraving.get("rotation", 0)))
        page._engrave_interval.setValue(float(engraving.get("interval", 0.1)))
        page._engrave_min_power.setValue(float(engraving.get("min_power", 0)))
        page._engrave_max_power.setValue(float(engraving.get("max_power", 80)))
        page._engrave_speed.setValue(float(engraving.get("speed", 100)))
        page._engrave_gamma.setValue(float(engraving.get("gamma", 1)))
        page._engrave_passes.setValue(int(engraving.get("passes", 1)))
        page._engrave_invert.setChecked(bool(engraving.get("invert", False)))
        material_index = page._engrave_material.findData(str(engraving.get("material", "custom")))
        page._engrave_material.blockSignals(True)
        page._engrave_material.setCurrentIndex(max(0, material_index))
        page._engrave_material.blockSignals(False)
    if page._engraving_image_path and Path(page._engraving_image_path).exists():
        page._update_engraving_overlay()
    else:
        page._canvas.clear_background_image()
    page._refresh_engraving_ui()


def _region_under_image(page: Any, options: dict) -> str | None:
    """Innermost region containing the legacy image's placement centre.

    Nothing in an old workspace records which region an image belonged to, so
    the smallest region it sits inside is the best available answer — and it
    matches what the user drew.
    """
    return smallest_containing_outline(
        page._outline_ids,
        page._edit_polys,
        (
            float(options.get("x", 0.0)) + float(options.get("width", 0.0)) / 2.0,
            float(options.get("y", 0.0)) + float(options.get("height", 0.0)) / 2.0,
        ),
    )


def _migrate_page_engraving_to_region(page: Any, pattern_state: Any) -> None:
    from simple_stipple.features.pattern.regions.treatments import engraving_regions, treatment_kind

    path = str(pattern_state.engraving_image_path or "")
    if not path or engraving_regions(page):
        return
    options = pattern_state.engraving_options or {}
    target = next(
        (rid for rid in page._outline_ids if treatment_kind(page, rid) == "engrave"),
        None,
    )
    if target is None:
        target = _region_under_image(page, options)
    if target is None:
        return
    treatment = dict(page._treatments.get(target) or {})
    treatment["kind"] = "engrave"
    treatment["engraving"] = {
        "path": path,
        "x": float(options.get("x", 0.0)),
        "y": float(options.get("y", 0.0)),
        "width": float(options.get("width", 0.0)),
        "height": float(options.get("height", 0.0)),
        "rotation": float(options.get("rotation", 0.0)),
    }
    page._treatments[target] = treatment


def clear_pattern_workspace_state(page: Any) -> None:
    apply_pattern_workspace_state(page, {})
    page._outline_ids = []
    page._treatments = {}
    page._set_status("")
    page._refresh_canvas_panels()


@dataclass(frozen=True)
class PreviewWorkerCall:
    """One fully prepared invocation of a Pattern preview worker."""

    target: Callable[..., None]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


def build_preview_worker_call(
    *,
    zones: list[dict[str, Any]],
    all_polys: list[list[tuple[float, float]]],
    pattern: str,
    params: dict[str, Any],
    scale: tuple[float, float],
    border_polys: list[list[tuple[float, float]]] | None,
    border_fade: float,
    preview_token: int,
    cancel_event: threading.Event,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict[str, Any] | None,
    compute_preview: Callable[..., None],
    compute_preview_zones: Callable[..., None],
) -> PreviewWorkerCall:
    """Choose the existing zone or outline worker and preserve its contract."""
    common_kwargs = {
        "pattern_service": pattern_service,
        "orig_w": orig_w,
        "orig_h": orig_h,
        "on_done": on_done,
        "on_error": on_error,
        "fill_options": fill_options,
    }
    if zones:
        return PreviewWorkerCall(
            target=compute_preview_zones,
            args=(
                zones,
                all_polys,
                False,  # invert_fill
                False,  # mirror_v
                False,  # mirror_h
                border_fade,
                None,  # exclusion_polys
                preview_token,
                cancel_event,
            ),
            kwargs=common_kwargs,
        )
    return PreviewWorkerCall(
        target=compute_preview,
        args=(
            all_polys,
            pattern,
            params,
            scale,
            border_polys,
            False,  # interlace
            False,  # invert_fill
            False,  # mirror_v
            False,  # mirror_h
            border_fade,
            None,  # exclusion_polys
            preview_token,
            cancel_event,
        ),
        kwargs=common_kwargs,
    )
