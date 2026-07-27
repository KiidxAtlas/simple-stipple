"""Draft workspace-state validation, serialization, and restoration."""

from __future__ import annotations

import logging
from typing import Any, cast

from pydantic import ValidationError

from simple_stipple.document.model import DraftTabState

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
