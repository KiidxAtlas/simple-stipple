"""Draft workspace-state validation, serialization, and restoration."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from PIL import Image
from pydantic import ValidationError

from simple_stipple.core.document.model import DraftTabState
from simple_stipple.core.formats.svg import read_svg_images

LOGGER = logging.getLogger(__name__)

# ── Page default settings ────────────────────────────────────────────────
DEFAULT_QUICK_SHAPE_MODE = "rectangle"


# ══════════════════════════════════════════════════════════════════════════
# Workspace state serialisation/restoration (merged from former session.py)
# ══════════════════════════════════════════════════════════════════════════
#
# Current format: a flat entity-record list (each record carries its layer,
# flags, and group) plus the ordered layer list and active layer.
#
# State management uses Pydantic models (``DraftTabState``) for schema
# validation at the load/save boundary. The ``get_*`` / ``apply_*``
# functions work with raw dicts (for compatibility with existing UI code)
# but validate and coerce those dicts through ``DraftTabState`` internally.


def _coerce_to_draft_state(state: dict | None) -> DraftTabState:
    """Coerce a raw dict (possibly from an old workspace file) into a
    ``DraftTabState``. Returns a minimal valid instance if the data is
    completely malformed."""
    if not isinstance(state, dict):
        return DraftTabState()
    try:
        return cast(DraftTabState, DraftTabState.from_dict(state))
    except (ValidationError, TypeError, ValueError) as exc:
        LOGGER.warning("Discarding invalid Draft workspace state: %s", exc)
        return DraftTabState()


def get_draft_workspace_state(page: Any) -> dict:
    canvas = page._canvas
    state_dict = {
        "entities": canvas.get_entity_records(),
        "layer_order": canvas.layer_names(),
        "active_layer": canvas.active_layer,
        "canvas_view": canvas.get_view_state(),
        "quick_shape_mode": canvas.quick_shape_mode,
        "quick_shape_enabled": canvas.quick_shape_enabled,
        "last_input_dxf": str(page._last_in_path or ""),
    }
    return state_dict


def apply_draft_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    draft_state = _coerce_to_draft_state(state)

    rt = page._rt()
    canvas = page._canvas

    entities = draft_state.entities
    if isinstance(entities, list) and entities:
        canvas.set_entity_records(entities)
        order = [str(n) for n in draft_state.layer_order if str(n)]
        active = draft_state.active_layer
        if not order:
            order = [rt.default_layer]
        canvas.set_layer_model(order, str(active) if active else order[0])
        if draft_state.canvas_view:
            canvas.set_view_state(draft_state.canvas_view)
    else:
        rt.reset_empty()

    if canvas.poly_count == 0:
        canvas.fit()

    quick_shape_enabled = bool(draft_state.quick_shape_enabled)
    canvas.set_quick_shape_enabled(quick_shape_enabled)
    if quick_shape_enabled and draft_state.quick_shape_mode:
        canvas.set_quick_shape_mode(str(draft_state.quick_shape_mode), flash=False)
    page._last_in_path = str(draft_state.last_input_dxf or "") or None

    page._suspend_state = False
    page._refresh_status()


def clear_draft_workspace_state(page: Any) -> None:
    page._suspend_state = True
    page._rt().reset_empty()
    page._canvas.set_mode("select")
    page._canvas.set_quick_shape_mode(DEFAULT_QUICK_SHAPE_MODE, flash=False)
    page._canvas.set_quick_shape_enabled(False)
    page._last_in_path = None
    page._suspend_state = False
    page._refresh_status()


@dataclass(frozen=True)
class DraftDxfExportPlan:
    """Layer-preserving records ready for ``DxfService.write_polylines_dxf``."""

    records: list[dict[str, Any]]
    first_layer_name: str
    first_layer_records: list[dict[str, Any]]
    extra_layer_records: dict[str, list[dict[str, Any]]] | None


def build_dxf_export_plan(
    records: Iterable[Mapping[str, Any]],
    dimensions: Iterable[Mapping[str, Any]],
    *,
    active_layer_name: str,
    layer_names: Iterable[str],
) -> DraftDxfExportPlan:
    """Add dimension entities and preserve document-layer ordering for export."""
    export_records = [dict(record) for record in records]
    for dimension in dimensions:
        p1 = tuple(dimension["p1"])
        p2 = tuple(dimension["p2"])
        export_records.append(
            {
                "polyline": [p1, p2],
                "kind": "dimension",
                "meta": dict(dimension),
                "layer": str(dimension.get("layer") or active_layer_name),
            }
        )

    by_layer: dict[str, list[dict[str, Any]]] = {}
    for record in export_records:
        by_layer.setdefault(str(record.get("layer") or active_layer_name), []).append(record)
    ordered_names = [name for name in layer_names if name in by_layer]
    if not ordered_names:
        ordered_names = list(by_layer)

    first_layer_name = ordered_names[0] if ordered_names else (active_layer_name or "Layer")
    first_layer_records = by_layer.get(first_layer_name, [])
    extra_layer_records = {name: by_layer[name] for name in ordered_names[1:]} or None
    return DraftDxfExportPlan(
        records=export_records,
        first_layer_name=first_layer_name,
        first_layer_records=first_layer_records,
        extra_layer_records=extra_layer_records,
    )


def show_imported_svg_image(self, path: str) -> int:
    """Place embedded SVG artwork as an editable translucent canvas backdrop."""
    placements = read_svg_images(path)
    if not placements:
        return 0
    first = placements[0]
    try:
        with Image.open(io.BytesIO(first.png_bytes)) as source:
            backdrop = source.convert("RGBA")
            backdrop.putalpha(110)
            self._canvas.set_background_image(
                backdrop.copy(),
                first.width_mm,
                first.height_mm,
                first.x_mm,
                first.y_mm,
                first.rotation_deg,
            )
        self._canvas.set_background_image_editable(True, self._on_backdrop_transform)
        self._canvas.set_background_image_key_callback(self._on_backdrop_key)
    except (OSError, ValueError):
        self._canvas.clear_background_image()
        return 0
    return len(placements)


def on_backdrop_transform(
    self, _x: float, _y: float, _w: float, _h: float, _rotation: float = 0.0
) -> None:
    """Refresh status after the canvas changes reference-artwork placement."""
    self._refresh_status()


def on_backdrop_key(self, action: str, reverse: bool = False) -> None:
    """Clear the imported reference image with the normal remove command."""
    if action != "remove":
        return
    self._canvas.clear_background_image()
    self._set_import_note("")
    self._canvas._show_flash("Reference image removed", 1200)
    self._refresh_status()
