"""Region treatment UI for the Pattern page.

Every closed outline is a region; the Regions list shows all of them and the
editor below it edits whichever one is selected. A region carries at most one
treatment (``simple_stipple.features.pattern.treatments``), and a treated
region subtracts itself from the region containing it — no cutout role, no
zone-membership step.

The engine still consumes zone dicts; ``page._zones`` is a read-only
projection of the treatments, which is what keeps ``engine/patterns``
untouched by this change.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMenu, QMessageBox

from simple_stipple.features.pattern.layout import refresh_pattern_properties_panel
from simple_stipple.features.pattern.params import collect_form_state, restore_form_state
from simple_stipple.features.pattern.treatments import (
    IMAGE_PATTERN,
    TREATMENT_LABELS,
    begin_treatment_change,
    commit_treatment_change,
    prune_treatments,
    region_ids,
    region_row_label,
    region_tree,
    set_treatment,
    treatment_kind,
)
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN

# ── Row ↔ region ↔ engine-zone index ──────────────────────────────────────


def region_id_for_row(page: Any, row: int) -> str | None:
    ids = region_ids(page)
    return ids[row] if 0 <= row < len(ids) else None


def row_for_region_id(page: Any, region_id: str) -> int:
    ids = region_ids(page)
    return ids.index(region_id) if region_id in ids else -1


def selected_region_id(page: Any) -> str | None:
    if not hasattr(page, "_zone_list"):
        return None
    return region_id_for_row(page, page._zone_list.currentRow())


def selected_region_ids(page: Any) -> list[str]:
    """Every region highlighted in the list, current row first.

    The canvas allows a multi-shape selection, so the list has to represent
    one; showing a single row made a two-region selection look like a
    one-region selection.
    """
    if not hasattr(page, "_zone_list"):
        return []
    ids = region_ids(page)
    rows = sorted(index.row() for index in page._zone_list.selectedIndexes())
    current = page._zone_list.currentRow()
    if current not in rows and 0 <= current < len(ids):
        rows.insert(0, current)
    elif current in rows:
        rows.remove(current)
        rows.insert(0, current)
    return [ids[row] for row in rows if 0 <= row < len(ids)]


def highlight_zone_on_canvas(page: Any, row: int) -> None:
    """Highlight a region without changing canvas/layer-tree selection."""
    region_id = region_id_for_row(page, row)
    if region_id is None:
        page._canvas.set_accent_polys({})
        page._canvas.set_region_tint({})
        return
    indices = [
        index for index, outline_id in enumerate(page._outline_ids) if outline_id == region_id
    ]
    entity_ids = page._canvas.get_entity_ids()
    highlighted = {entity_ids[index]: "#f5a623" for index in indices if index < len(entity_ids)}
    page._canvas.set_accent_polys(highlighted)
    page._canvas.set_region_tint(highlighted)


def select_zone_for_canvas_selection(page: Any) -> None:
    """Follow the canvas selection with the Regions list."""
    entity_ids = page._canvas.get_selected_ids()
    if not entity_ids:
        # Clicking empty space deselects on the canvas; the Regions list and
        # its area tint have to follow or the sidebar keeps editing a region
        # the user can no longer see selected.
        page._zone_list.blockSignals(True)
        page._zone_list.clearSelection()
        page._zone_list.setCurrentRow(-1)
        page._zone_list.blockSignals(False)
        page._canvas.set_accent_polys({})
        page._canvas.set_region_tint({})
        page._update_zone_actions()
        refresh_pattern_properties_panel(page)
        return
    # One tree build for the whole selection: a marquee over a dense preview
    # can select thousands of entities, and rebuilding the containment tree
    # per entity made that selection hang.
    ids = region_ids(page)
    rows = [ids.index(eid) for eid in entity_ids if eid in ids]
    if not rows:
        return
    if page._zone_list.currentRow() not in rows:
        page._zone_list.setCurrentRow(rows[0])
    # Mirror the whole canvas selection, not just its first shape.
    page._zone_list.blockSignals(True)
    for row in range(page._zone_list.count()):
        item = page._zone_list.item(row)
        if item is not None:
            item.setSelected(row in rows)
    page._zone_list.blockSignals(False)
    page._zone_list.scrollToItem(page._zone_list.currentItem())


# ── Snapshot for the engine ───────────────────────────────────────────────


def snapshot_zone_jobs(page: Any) -> list[dict]:
    # Pattern-cell cutouts are document-wide motif assignments. Inject the
    # current list at snapshot time so a treatment configured before a cell
    # was removed cannot retain a stale empty list and fill that cell anyway.
    zone_list = page._zones
    for zone in zone_list:
        fill = zone.get("fill")
        if isinstance(fill, dict):
            fill["cell_cutouts"] = [list(poly) for poly in page._pattern_cell_cutouts]
            fill["cell_instance_cutouts"] = [
                list(poly) for poly in page._pattern_cell_instance_cutouts
            ]
    jobs, warnings = page._pattern_service.snapshot_zone_jobs(
        zone_list,
        page._outline_ids,
        page._edit_polys,
    )
    if warnings:
        page._set_status(warnings[-1], STATUS_WARN)
    return jobs


# ── Editor ────────────────────────────────────────────────────────────────


def collect_treatment(page: Any) -> dict:
    """Read the inspector into one treatment dict.

    There is one editor for pattern and fill in this app, and it is the same
    one whether it is editing a selected region or the document defaults —
    which is why this reads the Pattern/Fill sections directly rather than a
    second, partial copy of them.
    """
    label = page._pattern_combo.currentText()
    pattern = page._pattern_key(label)
    if pattern == IMAGE_PATTERN:
        # Choosing Image *is* choosing the Engrave treatment. Checked before
        # the parameter read: Image has no generator and therefore no params.
        return {
            "kind": "engrave",
            "pattern": IMAGE_PATTERN,
            "pattern_label": IMAGE_PATTERN,
            "params": {},
            "scale": page._collect_scale(),
            "fill": None,
            "form_state": collect_form_state(page),
        }
    params = page._collect_pattern_params(pattern) if pattern != "— None —" else {}
    fill = page._collect_fill_options()
    kind = str(page._zone_output_combo.currentData() or "pattern_fill")
    # A kind that needs a treatment the editor does not supply is not
    # meaningful; normalize instead of silently generating nothing.
    if kind in {"pattern", "pattern_fill"} and pattern == "— None —":
        kind = "fill" if fill else "cut"
    elif kind == "fill" and fill is None:
        kind = "pattern" if pattern != "— None —" else "cut"
    return {
        "kind": kind,
        "pattern": pattern,
        "pattern_label": label,
        "params": params,
        "scale": page._collect_scale(),
        "fill": fill,
        "form_state": collect_form_state(page),
    }


def live_update_selected_zone(page: Any, *_args) -> bool:
    """Commit inspector changes to the selected region's treatment.

    Returns whether a region absorbed the edit. When nothing is selected the
    same widgets are editing the document defaults, and the caller schedules
    the solve itself.
    """
    if page._loading_zone or page._suspend_state:
        return False
    # Editing applies to every selected region, not just the current row —
    # selecting three and changing the pattern has to change three.
    targets = selected_region_ids(page)
    if not targets:
        return False
    try:
        treatment = collect_treatment(page)
    except (KeyError, TypeError, ValueError):
        # A line edit can briefly contain an incomplete number while the
        # user types. Keep the last valid treatment until it is complete.
        return False
    before = begin_treatment_change(page)
    for region_id in targets:
        set_treatment(page, region_id, treatment)
    commit_treatment_change(page, before, targets[0])
    sync_engraving_visibility(page)
    # Only the row text changes here. A full rebuild re-enters on_zone_selected,
    # which destroys and recreates every parameter widget — including the field
    # being typed into, so the caret was lost on every keystroke.
    refresh_row_labels(page)
    page._schedule_preview()
    page._emit_state_changed()
    return True


def show_zone_context_menu(page: Any, pos) -> None:
    item = page._zone_list.itemAt(pos)
    if item is None or not region_ids(page):
        return
    page._zone_list.setCurrentItem(item)
    menu = QMenu(page._zone_list)
    clear_action = menu.addAction("Clear treatment")
    clear_action.setShortcut(QKeySequence.StandardKey.Delete)
    chosen = menu.exec(page._zone_list.viewport().mapToGlobal(pos))
    if chosen is clear_action:
        page._remove_selected_zone()


def sync_engraving_visibility(page: Any) -> None:
    """Show the image controls only for the region that carries an image."""
    if not hasattr(page, "_engraving_section"):
        return
    region_id = selected_region_id(page)
    is_image = region_id is not None and treatment_kind(page, region_id) == "engrave"
    page._engraving_section.setVisible(is_image)
    if is_image:
        page._engraving_section.set_expanded(True)


def on_zone_selected(page: Any, row: int) -> None:
    """Load the selected region's treatment into the editor."""
    region_id = region_id_for_row(page, row)
    if region_id is None:
        page._canvas.set_accent_polys({})
        page._canvas.set_region_tint({})
        refresh_pattern_properties_panel(page)
        return
    treatment = page._treatments.get(region_id) or {}
    highlight_zone_on_canvas(page, row)
    page._loading_zone = True
    page._suspend_state = True
    try:
        kind = treatment_kind(page, region_id)
        # An untreated region opens on the default treatment: the combo says
        # what an edit *will* apply, and "None" stays available to clear it.
        page._zone_output_combo.setCurrentIndex(
            max(0, page._zone_output_combo.findData("pattern_fill" if kind == "none" else kind))
        )
        # An untreated region opens on whatever the inspector already shows —
        # the defaults you just set are what clicking a shape applies.
        form_state = treatment.get("form_state")
        if isinstance(form_state, dict) and form_state:
            restore_form_state(page, form_state)
        if kind == "engrave":
            page._populate_pattern_combo(page._pattern_combo, IMAGE_PATTERN)
        page._switch_pattern(page._pattern_combo.currentText())
    finally:
        page._suspend_state = False
        page._loading_zone = False
    page._refresh_section_subtitles()
    # The image controls follow the selection: they edit whichever region owns
    # an image, rather than a single page-global engraving, and only appear
    # when this region is actually an image region.
    page._sync_engraving_widgets_from_region(region_id)
    sync_engraving_visibility(page)
    # Picking a row is a selection change, so Apply's enabled state has to
    # follow it — otherwise the button stays stale until an unrelated refresh.
    page._update_zone_actions()
    refresh_pattern_properties_panel(page)


# ── Applying a treatment ──────────────────────────────────────────────────


def assign_zone(page: Any) -> None:
    """Apply the editor's treatment to every selected region."""
    sel_polys = page._canvas.get_selected()
    if not sel_polys:
        listed = selected_region_ids(page)
        if not listed:
            QMessageBox.information(
                page,
                "No Selection",
                "Select a region on the canvas or in the Regions list, then click 'Apply'.",
            )
            return
        _apply_treatment_to(page, listed)
        return
    sel_ids = [eid for eid in page._canvas.get_selected_ids() if eid in page._outline_ids]
    try:
        treatment = collect_treatment(page)
        page._validate_outline_inputs(sel_polys)
    except ValueError as exc:
        page._set_status(str(exc), STATUS_ERR)
        return
    _apply_treatment_to(page, sel_ids, treatment=treatment)


def _apply_treatment_to(
    page: Any,
    sel_ids: list[str],
    *,
    inherited: list[str] | None = None,
    treatment: dict | None = None,
) -> None:
    """Write the editor's treatment onto every selected region."""
    if treatment is None:
        try:
            treatment = collect_treatment(page)
        except (KeyError, TypeError, ValueError) as exc:
            page._set_status(str(exc), STATUS_ERR)
            return
    ids = region_ids(page)
    targets = [rid for rid in sel_ids if rid in ids]
    if not targets:
        page._set_status(
            "Select a closed shape — an open path is linework and carries no treatment.",
            STATUS_WARN,
        )
        return
    before = begin_treatment_change(page)
    for region_id in (inherited or []) + targets:
        set_treatment(page, region_id, treatment)
    commit_treatment_change(page, before)
    refresh_zone_list(page)
    page._zone_list.setCurrentRow(row_for_region_id(page, targets[-1]))
    sync_engraving_visibility(page)
    page._set_status(
        f"{TREATMENT_LABELS[treatment['kind']]} applied to "
        f"{len(targets)} region{'s' if len(targets) != 1 else ''}.",
        STATUS_OK,
    )
    page._schedule_preview()
    page._emit_state_changed()


def remove_selected_zone(page: Any) -> None:
    region_id = selected_region_id(page)
    if region_id is None or treatment_kind(page, region_id) == "none":
        return
    row = page._zone_list.currentRow()
    before = begin_treatment_change(page)
    page._treatments.pop(region_id, None)
    commit_treatment_change(page, before)
    refresh_zone_list(page)
    page._zone_list.setCurrentRow(row)
    page._schedule_preview()
    page._emit_state_changed()


def clear_zones(page: Any) -> None:
    if not page._treatments:
        return
    reply = QMessageBox.question(
        page,
        "Clear All Treatments?",
        "This removes every region treatment. Continue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    before = begin_treatment_change(page)
    page._treatments = {}
    commit_treatment_change(page, before)
    refresh_zone_list(page)
    page._schedule_preview()
    page._emit_state_changed()


def refresh_row_labels(page: Any) -> None:
    """Restate the Regions rows in place, leaving the editor widgets alone."""
    if not hasattr(page, "_zone_list"):
        return
    tree = region_tree(page)
    ids = [outline_id for outline_id in page._outline_ids if outline_id in tree]
    if page._zone_list.count() != len(ids):
        refresh_zone_list(page)
        return
    for row, region_id in enumerate(ids):
        item = page._zone_list.item(row)
        if item is not None:
            item.setText(region_row_label(page, region_id, row, tree))
    page._refresh_section_subtitles()
    refresh_pattern_properties_panel(page)


def refresh_zone_list(page: Any) -> None:
    if not hasattr(page, "_zone_list"):
        return
    tree = region_tree(page)
    ids = [outline_id for outline_id in page._outline_ids if outline_id in tree]
    current = page._zone_list.currentRow()
    previous = ids[current] if 0 <= current < len(ids) else None
    # Rebuilding must not collapse a multi-region selection down to one row.
    also_selected = {
        ids[index.row()] for index in page._zone_list.selectedIndexes() if index.row() < len(ids)
    }
    page._zone_list.blockSignals(True)
    page._zone_list.clear()
    if ids:
        for index, region_id in enumerate(ids):
            page._zone_list.addItem(region_row_label(page, region_id, index, tree))
    else:
        page._zone_list.addItem("No closed regions yet")
    page._zone_list.blockSignals(False)
    if not ids and page._zone_list.count() > 0:
        item = page._zone_list.item(0)
        if item is not None:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
    elif previous in ids:
        page._zone_list.setCurrentRow(ids.index(previous))
        page._zone_list.blockSignals(True)
        for row, region_id in enumerate(ids):
            item = page._zone_list.item(row)
            if item is not None and region_id in also_selected:
                item.setSelected(True)
        page._zone_list.blockSignals(False)
        page._zone_list.scrollToItem(page._zone_list.currentItem())
    page._update_zone_actions()
    page._refresh_section_subtitles()
    refresh_pattern_properties_panel(page)


def invalidate_zones_for_geometry_change(page: Any, valid_outline_ids: set[str]) -> None:
    dropped = prune_treatments(page, valid_outline_ids)
    refresh_zone_list(page)
    if dropped:
        page._set_status(
            f"Outline changed — dropped {dropped} region treatment"
            f"{'s' if dropped != 1 else ''}; the rest were kept.",
            STATUS_WARN,
        )


def update_zone_actions(page: Any) -> None:
    # A region can be picked on the canvas or in the Regions list. Requiring
    # the canvas made the list a dead end.
    has_selection = bool(getattr(page._canvas, "sel_count", 0)) or selected_region_id(page) is not None
    page._assign_zone_btn.setEnabled(has_selection)
    page._assign_zone_btn.setToolTip(
        "Select a region on the canvas or in the Regions list"
        if not has_selection
        else "Apply these settings to the selected region(s)"
    )
