"""Pattern zone management — extracted from ``pattern/tab.py``.

Zones let a user assign a different pattern/fill configuration to a subset
of a document's outlines instead of one pattern for the whole document. This
module owns the zone list widget's data flow (create/edit/remove/highlight)
and the per-zone parameter editor. See ``domain/outlines.py`` for the
neighboring cutout/exclusion-role logic these functions call into
(``page._refresh_zone_list()`` etc., via the wrapper methods kept on
``PatternPage`` — see plan.md Section 9.1).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator, QKeySequence
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QMenu, QMessageBox, QWidget

from simple_stipple.features.pattern.defaults import (
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_SPACING,
    DEFAULT_PATTERN_ROTATION,
    FILL_SPACING_FLOOR_MM,
)
from simple_stipple.features.pattern.form_spec import PARAM_SPECS
from simple_stipple.features.pattern.layout import refresh_pattern_properties_panel
from simple_stipple.features.pattern.params import collect_form_state
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_WARN


def preview_outline_indices_for_zone(page: Any, zone_row: int) -> list[int]:
    """Select only a zone's boundary entities, not all generated output."""
    outline_count = len(page._preview_categories.get("outline", []))
    return [
        index
        for index, owner in enumerate(page._preview_zone_owners[:outline_count])
        if owner == zone_row
    ]


def highlight_zone_on_canvas(page: Any, zone_row: int) -> None:
    """Highlight a zone without changing canvas/layer-tree selection."""
    if not 0 <= zone_row < len(page._zones):
        page._canvas.set_accent_polys({})
        return
    if page._showing_preview:
        indices = [
            index for index, owner in enumerate(page._preview_zone_owners) if owner == zone_row
        ]
    else:
        zone_ids = set(page._zones[zone_row].get("outline_ids", []))
        indices = [
            index for index, outline_id in enumerate(page._outline_ids) if outline_id in zone_ids
        ]
    entity_ids = page._canvas.get_entity_ids()
    page._canvas.set_accent_polys(
        {entity_ids[index]: "#f5a623" for index in indices if index < len(entity_ids)}
    )


def select_zone_for_canvas_selection(page: Any, *, preview: bool) -> None:
    entity_ids = page._canvas.get_selected_ids()
    if not entity_ids:
        return
    zone_rows: list[int] = []
    if preview:
        entity_index = {
            entity_id: index for index, entity_id in enumerate(page._canvas.get_entity_ids())
        }
        zone_rows = []
        for entity_id in entity_ids:
            index = entity_index.get(entity_id)
            if index is None or index >= len(page._preview_zone_owners):
                continue
            owner = page._preview_zone_owners[index]
            if owner is not None and owner not in zone_rows:
                zone_rows.append(owner)
    else:
        selected_ids = set(entity_ids)
        zone_rows = [
            row
            for row, zone in enumerate(page._zones)
            if selected_ids.intersection(zone.get("outline_ids", []))
        ]
    if zone_rows:
        # A canvas marquee/Shift-click can span multiple zones. Keep the
        # current editor when it is part of that selection; otherwise choose
        # the zone with the strongest overlap instead of depending on set
        # iteration order.
        current_row = page._zone_list.currentRow()
        if current_row in zone_rows:
            return
        if preview:
            page._zone_list.setCurrentRow(zone_rows[0])
        else:
            selected_ids = set(entity_ids)
            best_row = max(
                zone_rows,
                key=lambda row: len(
                    selected_ids.intersection(page._zones[row].get("outline_ids", []))
                ),
            )
            page._zone_list.setCurrentRow(best_row)


def snapshot_zone_jobs(page: Any) -> list[dict]:
    # Pattern-cell cutouts are document-wide motif assignments. Inject the
    # current list at snapshot time so zones created before a cutout was
    # marked cannot retain a stale empty list and fill that cell anyway.
    for zone in page._zones:
        fill = zone.get("fill")
        if isinstance(fill, dict):
            fill["cell_cutouts"] = [list(poly) for poly in page._pattern_cell_cutouts]
            fill["cell_instance_cutouts"] = [
                list(poly) for poly in page._pattern_cell_instance_cutouts
            ]
    jobs, warnings = page._pattern_service.snapshot_zone_jobs(
        page._zones,
        page._outline_ids,
        page._edit_polys,
    )
    if warnings:
        page._set_status(warnings[-1], STATUS_WARN)
    return jobs


def zone_output_label(mode: str) -> str:
    return {
        "pattern_fill": "Pattern + Fill",
        "pattern": "Pattern",
        "fill": "Fill",
        "outline": "Outline",
        "none": "Disabled",
    }.get(mode, "Pattern + Fill")


def zone_label(page: Any, zone: dict, index: int) -> str:
    count = len(zone.get("outline_ids", []))
    mode = str(zone.get("output_mode", "pattern_fill"))
    detail = page._zone_output_label(mode)
    if mode in {"pattern", "pattern_fill"}:
        detail = f"{detail}: {zone.get('pattern', '— None —')}"
    return f"Zone {index + 1} · {detail} · {count} outline{'s' if count != 1 else ''}"


def sync_selected_zone_from_controls(page: Any) -> None:
    if page._loading_zone or not hasattr(page, "_zone_list"):
        return
    row = page._zone_list.currentRow()
    if not (0 <= row < len(page._zones)):
        return
    pattern = page._current_pattern_key()
    try:
        params = page._collect_pattern_params(pattern) if pattern != "— None —" else {}
        scale = page._collect_scale()
    except ValueError:
        # Keep the last valid zone state while a numeric field is midway
        # through an edit; the next valid change will commit it.
        return
    zone = page._zones[row]
    zone.update(
        {
            "pattern": pattern,
            "params": params,
            "scale": scale,
            "fill": page._collect_fill_options(),
            "output_mode": page._zone_output_combo.currentData() or "pattern_fill",
            "form_state": collect_form_state(page),
        }
    )
    zone["label"] = page._zone_label(zone, row)
    item = page._zone_list.item(row)
    if item is not None:
        item.setText(zone["label"])
    refresh_pattern_properties_panel(page)


def rebuild_zone_parameter_editor(
    page: Any, _label: str | None = None, params: dict | None = None
) -> None:
    if not hasattr(page, "_zone_params_grid"):
        return
    while page._zone_params_grid.count():
        item = page._zone_params_grid.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()
    page._zone_param_inputs = {}
    pattern = page._pattern_key(page._zone_pattern_combo.currentText())
    values = params or {}
    row = 0
    for spec in PARAM_SPECS.get(pattern, []):
        field: QWidget
        key = spec.param_key or spec.attr[1:]
        if spec.kind == "checkbox":
            checkbox = QCheckBox(spec.label)
            checkbox.setChecked(bool(values.get(key, spec.default.lower() == "true")))
            page._zone_params_grid.addWidget(checkbox, row, 0, 1, 2)
            field = checkbox
        elif spec.kind == "combobox":
            combo = QComboBox()
            combo.addItems(spec.items)
            combo.setCurrentText(str(values.get(key, spec.default)))
            page._zone_params_grid.addWidget(QLabel(spec.label), row, 0)
            page._zone_params_grid.addWidget(combo, row, 1)
            field = combo
        else:
            line_edit = QLineEdit(str(values.get(key, spec.default)))
            if spec.kind == "int":
                line_edit.setValidator(
                    QIntValidator(
                        int(spec.minimum or -2_147_483_648),
                        int(spec.maximum or 2_147_483_647),
                        line_edit,
                    )
                )
            else:
                line_edit.setValidator(
                    QDoubleValidator(
                        float(spec.minimum or -1e12),
                        float(spec.maximum or 1e12),
                        6,
                        line_edit,
                    )
                )
            page._zone_params_grid.addWidget(QLabel(spec.label), row, 0)
            page._zone_params_grid.addWidget(line_edit, row, 1)
            field = line_edit
        field.setToolTip(spec.tooltip)
        if isinstance(field, QLineEdit):
            field.textChanged.connect(page._live_update_selected_zone)
        elif isinstance(field, QComboBox):
            field.currentIndexChanged.connect(page._live_update_selected_zone)
        elif isinstance(field, QCheckBox):
            field.toggled.connect(page._live_update_selected_zone)
        page._zone_param_inputs[key] = field
        row += 1
    page._zone_rotation = QLineEdit(str(values.get("rotation", DEFAULT_PATTERN_ROTATION)))
    page._zone_rotation.setValidator(QDoubleValidator(-36000, 36000, 4, page._zone_rotation))
    page._zone_rotation.textChanged.connect(page._live_update_selected_zone)
    page._zone_params_grid.addWidget(QLabel("Rotation (°)"), row, 0)
    page._zone_params_grid.addWidget(page._zone_rotation, row, 1)
    row += 1
    page._zone_size_percent = QLineEdit(str(values.get("size_percent", 100)))
    page._zone_size_percent.setValidator(QDoubleValidator(1, 10000, 3, page._zone_size_percent))
    page._zone_size_percent.textChanged.connect(page._live_update_selected_zone)
    page._zone_params_grid.addWidget(QLabel("Pattern size (%)"), row, 0)
    page._zone_params_grid.addWidget(page._zone_size_percent, row, 1)


def collect_zone_editor(page: Any) -> tuple[str, dict, dict | None]:
    label = page._zone_pattern_combo.currentText()
    pattern = page._pattern_key(label)
    params: dict = {}
    for spec in PARAM_SPECS.get(pattern, []):
        key = spec.param_key or spec.attr[1:]
        field = page._zone_param_inputs[key]
        if spec.kind == "checkbox":
            params[key] = field.isChecked()
        elif spec.kind == "combobox":
            params[key] = field.currentText()
        elif spec.kind == "int":
            value = page._parse_float_field(
                field,
                spec.label,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
            assert value is not None
            params[key] = int(value)
        else:
            params[key] = page._parse_float_field(
                field,
                spec.label,
                minimum=spec.minimum,
                maximum=spec.maximum,
            )
    params["rotation"] = page._parse_float_field(page._zone_rotation, "Rotation")
    params["size_percent"] = page._parse_float_field(
        page._zone_size_percent,
        "Pattern size",
        minimum=1.0,
        maximum=10000.0,
    )
    params.update(
        {
            "density_mode": "Uniform",
            "density_strength": 0.0,
            "density_angle": 0.0,
            "density_reverse": False,
        }
    )
    custom_name = page._custom_pattern_name(label)
    if pattern == "Custom Tile":
        motif = page._tile_motifs.get(custom_name or "", page._custom_tile_polys)
        if not motif:
            raise ValueError("Choose or save custom pattern geometry first.")
        params["tile_polys"] = [list(poly) for poly in motif]
        params["interlock"] = False
    mode = str(page._zone_fill_mode.currentData() or "none")
    fill = None
    if mode != "none":
        fill = {
            "mode": mode,
            "spacing": max(
                FILL_SPACING_FLOOR_MM,
                page._parse_float_field(
                    page._zone_fill_spacing,
                    "Fill spacing",
                    minimum=FILL_SPACING_FLOOR_MM,
                ),
            ),
            "angle_deg": page._parse_float_field(page._zone_fill_angle, "Fill angle"),
            "inset": page._parse_float_field(page._zone_fill_inset, "Fill inset", minimum=0.0),
            "keep_pattern": True,
            "target_outline": page._zone_fill_target_outline.isChecked(),
            "target_pattern": page._zone_fill_target_pattern.isChecked(),
            "cell_cutouts": [list(poly) for poly in page._pattern_cell_cutouts],
            "cell_instance_cutouts": [list(poly) for poly in page._pattern_cell_instance_cutouts],
        }
    return pattern, params, fill


def apply_selected_zone_edits(page: Any) -> None:
    """Compatibility entry point; zone controls now commit live."""
    page._live_update_selected_zone()


def live_update_selected_zone(page: Any, *_args) -> None:
    if page._loading_zone or page._suspend_state:
        return
    row = page._zone_list.currentRow()
    if not (0 <= row < len(page._zones)):
        return
    try:
        pattern, params, fill = page._collect_zone_editor()
    except (KeyError, TypeError, ValueError):
        # A line edit can briefly contain an incomplete number while the
        # user types. Keep the last valid zone state until it is complete.
        return
    zone = page._zones[row]
    page._preview_user_opt_out = False
    zone.update(
        {
            "pattern": pattern,
            "pattern_label": page._zone_pattern_combo.currentText(),
            "params": params,
            "fill": fill,
            "output_mode": page._zone_output_combo.currentData() or "pattern_fill",
        }
    )
    zone["label"] = page._zone_label(zone, row)
    item = page._zone_list.item(row)
    if item is not None:
        item.setText(zone["label"])
    page._schedule_preview()
    page._emit_state_changed()


def show_zone_context_menu(page: Any, pos) -> None:
    item = page._zone_list.itemAt(pos)
    if item is None or not page._zones:
        return
    page._zone_list.setCurrentItem(item)
    menu = QMenu(page._zone_list)
    delete_action = menu.addAction("Delete Zone")
    delete_action.setShortcut(QKeySequence.StandardKey.Delete)
    chosen = menu.exec(page._zone_list.viewport().mapToGlobal(pos))
    if chosen is delete_action:
        page._remove_selected_zone()


def on_zone_selected(page: Any, row: int) -> None:
    valid = 0 <= row < len(page._zones)
    if not valid:
        page._canvas.set_accent_polys({})
        refresh_pattern_properties_panel(page)
        return
    zone = page._zones[row]
    page._highlight_zone_on_canvas(row)
    page._loading_zone = True
    page._suspend_state = True
    try:
        pattern_label = str(zone.get("pattern_label") or zone.get("pattern", "— None —"))
        page._populate_pattern_combo(page._zone_pattern_combo, pattern_label)
        page._rebuild_zone_parameter_editor(params=dict(zone.get("params", {})))
        fill = zone.get("fill")
        fill_mode = str(fill.get("mode", "none")) if isinstance(fill, dict) else "none"
        page._zone_fill_mode.setCurrentIndex(max(0, page._zone_fill_mode.findData(fill_mode)))
        if isinstance(fill, dict):
            page._zone_fill_spacing.setText(str(fill.get("spacing", DEFAULT_FILL_SPACING)))
            page._zone_fill_angle.setText(str(fill.get("angle_deg", DEFAULT_FILL_ANGLE)))
            page._zone_fill_inset.setText(str(fill.get("inset", DEFAULT_FILL_INSET)))
            page._zone_fill_target_outline.setChecked(bool(fill.get("target_outline", True)))
            page._zone_fill_target_pattern.setChecked(bool(fill.get("target_pattern", False)))
        else:
            page._zone_fill_target_outline.setChecked(True)
            page._zone_fill_target_pattern.setChecked(False)
        mode = str(zone.get("output_mode", "pattern_fill"))
        page._zone_output_combo.setCurrentIndex(max(0, page._zone_output_combo.findData(mode)))
    finally:
        page._suspend_state = False
        page._loading_zone = False
    page._refresh_section_subtitles()
    refresh_pattern_properties_panel(page)


def _resolve_preview_zone_selection(
    page: Any,
    sel_polys: list[list[tuple[float, float]]],
) -> tuple[list[str], list[list[tuple[float, float]]], list[str]]:
    """Map preview selection to durable IDs and promote generated cells only."""
    entity_index = {
        entity_id: index for index, entity_id in enumerate(page._canvas.get_entity_ids())
    }
    outline_count = len(page._preview_categories.get("outline", []))
    source_by_signature: dict[tuple, list[str]] = {}
    for source_id, source_poly in zip(page._outline_ids, page._edit_polys):
        source_by_signature.setdefault(page._pattern_service._poly_signature(source_poly), []).append(
            source_id
        )
    selected_ids: list[str] = []
    promoted: list[list[tuple[float, float]]] = []
    promoted_ids: list[str] = []
    for entity_id, poly in zip(sorted(page._canvas.get_selected_ids()), sel_polys):
        # Generated preview geometry lives in the zone's scaled coordinate
        # space.  Promoted outlines must return to document/source space or
        # later zone containment checks see the child outside its parent and
        # the parent's pattern bleeds through it.
        source_poly = _restore_preview_poly_to_source(page, entity_id, poly, entity_index)
        signature = page._pattern_service._poly_signature(poly)
        reusable = source_by_signature.get(signature, []) if entity_index.get(entity_id, -1) < outline_count else []
        if reusable:
            selected_ids.append(reusable.pop(0))
            continue
        promoted_id = page._fresh_outline_ids(1)[0]
        selected_ids.append(promoted_id)
        promoted_ids.append(promoted_id)
        promoted.append(source_poly)
    return selected_ids, promoted, promoted_ids


def _restore_preview_poly_to_source(
    page: Any,
    entity_id: str,
    poly: list[tuple[float, float]],
    entity_index: dict[str, int],
) -> list[tuple[float, float]]:
    """Undo the owning zone's scale for a cell promoted from Preview.

    ``PatternProcessor.apply_scale`` scales around the bounding box of all
    outlines in that zone.  Reversing that same affine transform preserves
    the cell's position and makes it compatible with ``_edit_polys`` and
    durable zone IDs.
    """
    index = entity_index.get(entity_id, -1)
    outline_count = len(page._preview_categories.get("outline", []))
    if index < outline_count:
        return [(float(x), float(y)) for x, y in poly]
    owners = page._preview_categories.get("zone_owners", [])
    owner = owners[index] if isinstance(owners, list) and index < len(owners) else None
    if not isinstance(owner, int) or not 0 <= owner < len(page._zones):
        return [(float(x), float(y)) for x, y in poly]
    zone = page._zones[owner]
    try:
        sw, sh = (float(zone["scale"][0]), float(zone["scale"][1]))
        ow, oh = float(page._orig_w), float(page._orig_h)
    except (KeyError, TypeError, ValueError, IndexError):
        return [(float(x), float(y)) for x, y in poly]
    if ow <= 0 or oh <= 0 or sw <= 0 or sh <= 0:
        return [(float(x), float(y)) for x, y in poly]
    sx, sy = sw / ow, sh / oh
    if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
        return [(float(x), float(y)) for x, y in poly]
    source_ids = {str(v) for v in zone.get("outline_ids", [])}
    source_polys = [
        source_poly
        for source_id, source_poly in zip(page._outline_ids, page._edit_polys)
        if str(source_id) in source_ids
    ]
    points = [point for source_poly in source_polys for point in source_poly]
    if not points:
        return [(float(x), float(y)) for x, y in poly]
    ox = min(point[0] for point in points)
    oy = min(point[1] for point in points)
    return [
        (ox + (float(x) - ox) / sx, oy + (float(y) - oy) / sy)
        for x, y in poly
    ]


def assign_zone(page: Any) -> None:
    sel_polys = page._canvas.get_selected()
    source_outline_ids = list(page._outline_ids)
    if page._showing_preview:
        # Reuse source IDs for Outline rows; promote generated cells only.
        sel_ids, promoted, promoted_ids = _resolve_preview_zone_selection(page, sel_polys)
    else:
        promoted = []
        promoted_ids = []
        sel_ids = [eid for eid in page._canvas.get_selected_ids() if eid in page._outline_ids]
    if not sel_polys:
        QMessageBox.information(
            page,
            "No Selection",
            "Select one or more outlines on the canvas first, then click 'Assign'.",
        )
        return
    try:
        scale = page._collect_scale()
        pattern, params, fill_snapshot = page._collect_zone_editor()
        page._validate_outline_inputs(sel_polys)
    except ValueError as exc:
        page._set_status(str(exc), STATUS_ERR)
        return
    if promoted:
        page._edit_polys.extend(promoted)
        page._outline_ids.extend(promoted_ids)
    if set(sel_ids).intersection(page._exclusion_ids):
        page._set_status(
            "Remove Cutout from the selected shape before assigning a zone.",
            STATUS_WARN,
        )
        return
    if any(
        set(zone.get("outline_ids", [])) == set(sel_ids)
        and zone["pattern"] == pattern
        and zone["params"] == params
        and zone["scale"] == scale
        and zone.get("fill") == fill_snapshot
        for zone in page._zones
    ):
        page._set_status("Matching zone already exists.", STATUS_WARN)
        return
    requested_mode = page._zone_output_combo.currentData()
    if requested_mode in {"pattern_fill", "pattern", "fill", "outline", "none"}:
        output_mode = str(requested_mode)
    elif pattern == "— None —" and fill_snapshot:
        output_mode = "fill"
    elif pattern == "— None —":
        output_mode = "outline"
    elif fill_snapshot:
        output_mode = "pattern_fill"
    else:
        output_mode = "pattern"
    # A mode that requires a missing treatment is not a meaningful zone. The
    # combo defaults to Pattern + Fill, so normalize the common "fill-only"
    # and "outline-only" cases instead of silently generating an empty result.
    if pattern == "— None —" and output_mode in {"pattern_fill", "pattern"}:
        output_mode = "fill" if fill_snapshot else "outline"
    elif pattern != "— None —" and output_mode == "fill" and fill_snapshot is None:
        output_mode = "pattern"

    _materialize_preview_base_zone(
        page,
        source_outline_ids,
        pattern,
        params,
        fill_snapshot,
        scale,
        output_mode,
        enabled=bool(promoted and not page._zones and source_outline_ids),
    )
    # An outline belongs to at most one zone. Reassignment moves selected
    # outlines out of older zones instead of producing overlapping output.
    selected_ids = set(sel_ids)
    retained_zones: list[dict] = []
    for existing in page._zones:
        remaining = [oid for oid in existing.get("outline_ids", []) if oid not in selected_ids]
        if remaining:
            existing["outline_ids"] = remaining
            retained_zones.append(existing)
    page._zones = retained_zones
    zone = {
        "outline_ids": list(sel_ids),
        "pattern": pattern,
        "pattern_label": page._zone_pattern_combo.currentText(),
        "params": params,
        "scale": scale,
        "fill": fill_snapshot,
        "output_mode": output_mode,
        "form_state": collect_form_state(page),
    }
    zone["label"] = page._zone_label(zone, len(page._zones))
    page._zones.append(zone)
    page._preview_user_opt_out = False
    page._refresh_zone_list()
    page._zone_list.setCurrentRow(len(page._zones) - 1)
    page._schedule_preview()
    page._emit_state_changed()


def _materialize_preview_base_zone(
    page: Any,
    outline_ids: list[str],
    pattern: str,
    params: dict,
    fill: dict | None,
    scale: tuple[float, float],
    output_mode: str,
    *,
    enabled: bool,
) -> None:
    """Materialize the implicit global preview treatment before an exception."""
    if not enabled:
        return
    base_zone = {
        "outline_ids": list(outline_ids),
        "pattern": pattern,
        "pattern_label": page._zone_pattern_combo.currentText(),
        "params": dict(params),
        "scale": scale,
        "fill": fill,
        "output_mode": output_mode,
        "form_state": collect_form_state(page),
    }
    base_zone["label"] = page._zone_label(base_zone, 0)
    page._zones.append(base_zone)


def remove_selected_zone(page: Any) -> None:
    row = page._zone_list.currentRow()
    if 0 <= row < len(page._zones):
        del page._zones[row]
        page._refresh_zone_list()
        if page._zones:
            page._zone_list.setCurrentRow(min(row, len(page._zones) - 1))
        page._schedule_preview()
        page._emit_state_changed()


def clear_zones(page: Any) -> None:
    if not page._zones:
        return
    reply = QMessageBox.question(
        page,
        "Clear All Zones?",
        "This removes every assigned pattern zone. Continue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    page._zones.clear()
    page._refresh_zone_list()
    page._schedule_preview()
    page._emit_state_changed()


def refresh_zone_list(page: Any) -> None:
    if not hasattr(page, "_zone_list"):
        return
    selected_row = page._zone_list.currentRow()
    selected_zone = page._zones[selected_row] if 0 <= selected_row < len(page._zones) else None
    page._zone_list.blockSignals(True)
    page._zone_list.clear()
    if page._zones:
        for index, zone in enumerate(page._zones):
            zone["label"] = page._zone_label(zone, index)
            page._zone_list.addItem(zone["label"])
    else:
        page._zone_list.addItem("No zones assigned yet")
    page._zone_list.blockSignals(False)
    row_height = page._zone_list.sizeHintForRow(0)
    if row_height <= 0:
        row_height = page._zone_list.fontMetrics().height() + 8
    page._zone_list.setFixedHeight(max(44, row_height * max(1, page._zone_list.count()) + 6))
    if not page._zones and page._zone_list.count() > 0:
        item = page._zone_list.item(0)
        if item is not None:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
    elif selected_zone is not None:
        selected_index = next(
            (index for index, zone in enumerate(page._zones) if zone is selected_zone),
            -1,
        )
        if selected_index >= 0:
            page._zone_list.setCurrentRow(selected_index)
    page._update_zone_actions()
    page._refresh_section_subtitles()
    refresh_pattern_properties_panel(page)


def invalidate_zones_for_geometry_change(page: Any, valid_outline_ids: set[str]) -> None:
    if not page._zones:
        return
    retained: list[dict] = []
    removed_assignments = 0
    for zone in page._zones:
        previous_ids = list(zone.get("outline_ids", []))
        remaining_ids = [oid for oid in previous_ids if oid in valid_outline_ids]
        removed_assignments += len(previous_ids) - len(remaining_ids)
        if remaining_ids:
            zone["outline_ids"] = remaining_ids
            retained.append(zone)
    if not removed_assignments:
        return
    page._zones = retained
    page._refresh_zone_list()
    page._set_status(
        f"Outline changed — removed {removed_assignments} affected zone "
        f"assignment{'s' if removed_assignments != 1 else ''}; unaffected zones were kept.",
        STATUS_WARN,
    )


def update_zone_actions(page: Any) -> None:
    has_selection = bool(getattr(page._canvas, "sel_count", 0))
    zone_pattern = (
        page._pattern_key(page._zone_pattern_combo.currentText())
        if hasattr(page, "_zone_pattern_combo")
        else "— None —"
    )
    fill_mode = (
        str(page._zone_fill_mode.currentData() or "none")
        if hasattr(page, "_zone_fill_mode")
        else "none"
    )
    output_mode = (
        str(page._zone_output_combo.currentData() or "pattern_fill")
        if hasattr(page, "_zone_output_combo")
        else "pattern_fill"
    )
    treatment_is_valid = (
        zone_pattern != "— None —" or fill_mode != "none" or output_mode in {"outline", "none"}
    )
    can_assign = has_selection and treatment_is_valid
    page._assign_zone_btn.setEnabled(can_assign)
    page._assign_zone_btn.setToolTip(
        "Select outlines and choose a pattern, fill, outline-only, or disabled output"
        if not can_assign
        else "Create a zone from the selected outlines using these settings"
    )
    if hasattr(page, "_mark_cutout_btn"):
        page._mark_cutout_btn.setEnabled(
            (not page._showing_preview) or bool(getattr(page._canvas, "sel_count", 0))
        )
