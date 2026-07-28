"""Outline cutout/exclusion-role logic for the Pattern page — assigning a
role (boundary/cutout/open_path/ignore) to an outline via right-click, and
the cutout-status callout in the sidebar. Operates on the page's own
``_outline_ids``/``_edit_polys``/``_outline_roles``/``_exclusion_ids``
arrays, kept as page attributes (not moved) since tests assert on them
directly — see plan.md Section 9.1's LP-1 update for why this subsystem is
extracted as logic-only rather than moving the data too. Follows the same
``page: Any``-first free-function convention as ``domain/session.py``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QIcon

from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.style.theme import STATUS_OK, STATUS_WARN, icon_path


def on_canvas_cutout_toggle(page: Any, idx: int) -> None:
    if page._showing_preview:
        page._canvas._show_flash("Exit preview mode to assign cutouts", 1200)
        return
    if not (0 <= idx < len(page._outline_ids)):
        return
    oid = page._outline_ids[idx]
    current = page._outline_roles.get(oid, "boundary")
    fallback = (
        "open_path"
        if page._pattern_service._is_open_polyline(page._edit_polys[idx])
        else "boundary"
    )
    on_canvas_outline_role_change(page, idx, fallback if current == "cutout" else "cutout")


def on_canvas_outline_role_change(page: Any, idx: int, role: str) -> None:
    if page._showing_preview:
        page._canvas._show_flash("Exit preview mode to assign outline roles", 1200)
        return
    if not (0 <= idx < len(page._outline_ids)) or role not in {
        "boundary",
        "cutout",
        "open_path",
        "ignore",
    }:
        return
    oid = page._outline_ids[idx]
    if role == "boundary" and page._pattern_service._is_open_polyline(page._edit_polys[idx]):
        page._set_status("Close this path before assigning it as a fill boundary.", STATUS_WARN)
        return
    if role == "cutout":
        closed, _open_paths = page._pattern_service._merge_and_classify_outlines(
            page._generation_polys()
        )
        target_is_closed = not page._pattern_service._is_open_polyline(page._edit_polys[idx])
        if target_is_closed and len(closed) <= 1:
            page._set_status("The only closed outline cannot be a cutout.", STATUS_WARN)
            return
        # A cutout cannot also own a zone; that combination subtracts the
        # zone from itself and silently generates no result.
        retained_zones: list[dict] = []
        for zone in page._zones:
            remaining = [zid for zid in zone.get("outline_ids", []) if zid != oid]
            if remaining:
                retained_zones.append({**zone, "outline_ids": remaining})
        page._zones = retained_zones
        page._refresh_zone_list()
    page._outline_roles[oid] = role
    page._ensure_outline_roles()
    sync_canvas_cutout_highlight(page)
    refresh_cutout_status(page)
    page._set_status(
        f"Outline role: {role.replace('_', ' ').title()}",
        STATUS_OK,
    )
    page._schedule_preview()
    page._emit_state_changed()


def explain_outline_role(page: Any, idx: int) -> None:
    if not (0 <= idx < len(page._outline_ids)):
        return
    page._ensure_outline_roles()
    role = page._outline_roles.get(page._outline_ids[idx], "boundary")
    closed = not page._pattern_service._is_open_polyline(page._edit_polys[idx])
    explanations = {
        "boundary": "Boundary: closed region contributes to the fillable pattern area.",
        "cutout": "Cutout: this shape is subtracted from every overlapping fill region.",
        "open_path": "Open path: exported as linework but never auto-closed, filled, or subtracted.",
        "ignore": "Ignore: excluded from preview and generated output.",
    }
    detail = explanations[role]
    if role == "boundary" and not closed:
        detail += " Close the path before it can be filled."
    page._set_status(detail, "#79c0ff")
    page._canvas._show_flash(detail, 3500)


def mark_selection_as_cutout(page: Any) -> None:
    entity_ids = page._canvas.get_selected_ids()
    if not entity_ids:
        page._set_status("Select one or more shapes on canvas first.", STATUS_WARN)
        return
    if page._showing_preview:
        outline_count = len(page._preview_categories.get("outline", []))
        pattern_polys = page._preview_categories.get("pattern", [])
        # Preview geometry receives normal runtime entity IDs. Resolve those
        # IDs back to display indices instead of assuming synthetic names.
        entity_indices = {
            entity_id: index for index, entity_id in enumerate(page._canvas.get_entity_ids())
        }
        cell_indices = {entity_indices[eid] for eid in entity_ids if eid in entity_indices}
        selected_cells = [
            list(pattern_polys[idx - outline_count])
            for idx in cell_indices
            if idx in page._canvas._pattern_cell_indices
            and 0 <= idx - outline_count < len(pattern_polys)
        ]
        unique_cells: dict[tuple, list[tuple[float, float]]] = {}
        for poly in selected_cells:
            unique_cells.setdefault(page._pattern_service._poly_repeat_signature(poly), poly)
        for poly in unique_cells.values():
            page._toggle_pattern_cell_cutout_poly(poly)
        if selected_cells:
            page._configure_pattern_cell_context()
            refresh_cutout_status(page)
            page._schedule_preview()
        return
    for eid in entity_ids:
        if eid in page._outline_ids:
            on_canvas_cutout_toggle(page, page._outline_ids.index(eid))


def clear_exclusions(page: Any) -> None:
    if (
        not page._exclusion_ids
        and not page._pattern_cell_cutouts
        and not page._pattern_cell_instance_cutouts
    ):
        return
    for outline_id in page._exclusion_ids:
        index = page._outline_ids.index(outline_id) if outline_id in page._outline_ids else -1
        if index >= 0:
            page._outline_roles[outline_id] = (
                "open_path"
                if page._pattern_service._is_open_polyline(page._edit_polys[index])
                else "boundary"
            )
    page._exclusion_ids.clear()
    page._pattern_cell_cutouts.clear()
    page._pattern_cell_instance_cutouts.clear()
    sync_canvas_cutout_highlight(page)
    refresh_cutout_status(page)
    page._schedule_preview()
    page._emit_state_changed()


def sync_canvas_cutout_highlight(page: Any) -> None:
    if not hasattr(page, "_canvas"):
        return
    id_to_idx = {oid: i for i, oid in enumerate(page._outline_ids)}
    page._ensure_outline_roles()
    roles = {
        page._canvas.get_entity_ids()[id_to_idx[outline_id]]: role
        for outline_id, role in page._outline_roles.items()
        if outline_id in id_to_idx and id_to_idx[outline_id] < len(page._canvas.get_entity_ids())
    }
    page._canvas.set_outline_roles(roles)


def apply_cutout_callout_style(page: Any, *, active: bool) -> None:
    active_val = "true" if active else ""
    page._cutout_callout.setProperty("active", active_val)
    refresh_style(page._cutout_callout)
    page._cutout_icon.setProperty("active", active_val)
    refresh_style(page._cutout_icon)
    page._cutout_status_label.setProperty("active", active_val)
    refresh_style(page._cutout_status_label)


def refresh_cutout_status(page: Any) -> None:
    if not hasattr(page, "_cutout_status_label"):
        return
    outline_count = len(page._exclusion_ids)
    cell_count = len(page._pattern_cell_cutouts) + len(page._pattern_cell_instance_cutouts)
    n = outline_count + cell_count
    if n == 0:
        page._cutout_icon.setPixmap(QIcon(str(icon_path("info.svg"))).pixmap(16, 16))
        page._cutout_status_label.setText("Right-click a shape on canvas to mark as cutout")
        page._cutout_clear_btn.setVisible(False)
        apply_cutout_callout_style(page, active=False)
    else:
        page._cutout_icon.setPixmap(QIcon(str(icon_path("check.svg"))).pixmap(16, 16))
        parts = []
        if outline_count:
            parts.append(f"{outline_count} outline")
        if cell_count:
            parts.append(f"{cell_count} pattern cell")
        page._cutout_status_label.setText(
            f"{' + '.join(parts)} cutout{'s' if n != 1 else ''} active — shown orange"
        )
        page._cutout_clear_btn.setVisible(True)
        apply_cutout_callout_style(page, active=True)


def ensure_outline_roles(page: Any) -> None:
    """Drop roles for outlines that no longer exist, default-assign new ones,
    and rebuild ``_exclusion_ids`` from the result — the invariant every
    mutation of ``_outline_ids``/``_edit_polys`` must restore."""
    valid_ids = set(page._outline_ids)
    page._outline_roles = {
        key: value
        for key, value in page._outline_roles.items()
        if key in valid_ids and value in {"boundary", "cutout", "open_path", "ignore"}
    }
    for index, outline_id in enumerate(page._outline_ids):
        if outline_id not in page._outline_roles:
            is_open = page._pattern_service._is_open_polyline(page._edit_polys[index])
            page._outline_roles[outline_id] = "open_path" if is_open else "boundary"
    page._exclusion_ids = [
        outline_id
        for outline_id in page._outline_ids
        if page._outline_roles.get(outline_id) == "cutout"
    ]


def generation_polys(page: Any) -> list[list[tuple[float, float]]]:
    """Outlines that pattern generation should actually consume — everything
    marked boundary or open_path, excluding cutouts and ignored shapes."""
    ensure_outline_roles(page)
    return [
        list(poly)
        for outline_id, poly in zip(page._outline_ids, page._edit_polys)
        if page._outline_roles.get(outline_id) in {"boundary", "open_path"}
    ]
