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

from src.app.services.presets_service import NULL_PATTERN
from src.backend.model.document import PatternTabState
from src.ui.pages.pattern.ui.params import collect_form_state, restore_form_state

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
    # If showing preview, the canvas has preview polys — save edit_polys from our snapshot
    polys_to_save = (
        page._edit_polys if page._showing_preview else page._canvas.get_polylines_state()
    )
    state_dict = {
        "dxf_path": page._dxf_edit.text(),
        "params": collect_form_state(page),
        "orig_polys": page._orig_polys,
        "edit_polys": polys_to_save,
        "outline_ids": list(page._outline_ids),
        "outline_roles": dict(page._outline_roles),
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
        "exclusion_ids": list(page._exclusion_ids),
        "custom_tile_polys": page._custom_tile_polys,
        "engraving_image_path": page._engraving_image_path,
        "engraving_options": {
            "x": page._engrave_x.value(),
            "y": page._engrave_y.value(),
            "width": page._engrave_w.value(),
            "height": page._engrave_h.value(),
            "interval": page._engrave_interval.value(),
            "min_power": page._engrave_min_power.value(),
            "max_power": page._engrave_max_power.value(),
            "speed": page._engrave_speed.value(),
            "gamma": page._engrave_gamma.value(),
            "passes": page._engrave_passes.value(),
            "invert": page._engrave_invert.isChecked(),
            "target": page._engrave_target.currentData(),
            "material": page._engrave_material.currentData(),
        },
    }
    return state_dict


def apply_pattern_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    pattern_state = _coerce_to_pattern_state(state)

    page._dxf_edit.setText(pattern_state.dxf_path)
    restore_form_state(page, pattern_state.params)
    page._orig_polys = [list(poly) for poly in pattern_state.orig_polys]
    page._edit_polys = [list(poly) for poly in pattern_state.edit_polys]
    outline_ids = pattern_state.outline_ids
    if isinstance(outline_ids, list) and len(outline_ids) == len(page._edit_polys):
        page._outline_ids = [str(v) for v in outline_ids]
    else:
        page._outline_ids = page._fresh_outline_ids(len(page._edit_polys))
    raw_roles = pattern_state.outline_roles
    page._outline_roles = {
        str(key): str(value)
        for key, value in raw_roles.items()
        if str(value) in {"boundary", "cutout", "open_path", "ignore"}
    }
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
    show_preview = bool(pattern_state.showing_preview) and bool(page._preview_polys_cache)
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
    if pattern_state.canvas_view:
        page._canvas.set_view_state(pattern_state.canvas_view)
    page._suspend_state = False
    page._refresh_canvas_panels()
    page._zones = []
    for raw_zone in pattern_state.zones:
        zone = dict(raw_zone)
        zone["pattern"] = str(zone.get("pattern") or NULL_PATTERN).strip() or NULL_PATTERN
        zone["pattern_label"] = (
            str(zone.get("pattern_label") or zone["pattern"]).strip() or zone["pattern"]
        )
        zone.setdefault("params", {})
        page._zones.append(zone)
    page._refresh_zone_list()
    page._exclusion_ids = [str(v) for v in pattern_state.exclusion_ids]
    # Migrate legacy workspaces whose explicit cutouts predate outline roles.
    for outline_id in page._exclusion_ids:
        page._outline_roles[outline_id] = "cutout"
    page._custom_tile_polys = [list(poly) for poly in pattern_state.custom_tile_polys]
    page._engraving_image_path = pattern_state.engraving_image_path
    engraving = pattern_state.engraving_options
    if engraving:
        page._engrave_x.setValue(float(engraving.get("x", 0)))
        page._engrave_y.setValue(float(engraving.get("y", 0)))
        page._engrave_w.setValue(float(engraving.get("width", 100)))
        page._engrave_h.setValue(float(engraving.get("height", 100)))
        page._engrave_interval.setValue(float(engraving.get("interval", 0.1)))
        page._engrave_min_power.setValue(float(engraving.get("min_power", 0)))
        page._engrave_max_power.setValue(float(engraving.get("max_power", 80)))
        page._engrave_speed.setValue(float(engraving.get("speed", 100)))
        page._engrave_gamma.setValue(float(engraving.get("gamma", 1)))
        page._engrave_passes.setValue(int(engraving.get("passes", 1)))
        page._engrave_invert.setChecked(bool(engraving.get("invert", False)))
        target_index = page._engrave_target.findData(str(engraving.get("target", "outline")))
        page._engrave_target.setCurrentIndex(max(0, target_index))
        material_index = page._engrave_material.findData(str(engraving.get("material", "custom")))
        page._engrave_material.blockSignals(True)
        page._engrave_material.setCurrentIndex(max(0, material_index))
        page._engrave_material.blockSignals(False)
    if page._engraving_image_path and Path(page._engraving_image_path).exists():
        page._engraving_image_label.setText(Path(page._engraving_image_path).name)
        page._engraving_section.set_subtitle(Path(page._engraving_image_path).name)
        page._update_engraving_overlay()
    page._sync_canvas_cutout_highlight()
    page._refresh_cutout_status()


def clear_pattern_workspace_state(page: Any) -> None:
    apply_pattern_workspace_state(page, {})
    page._outline_ids = []
    page._outline_roles = {}
    page._set_status("")
    page._refresh_canvas_panels()
