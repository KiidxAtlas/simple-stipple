"""Pattern parameter widget builder and parameter collector.

Provides:
  build_param_widget(tab, pattern_name, schedule_fn) -> QWidget
  collect_pattern_params(tab, pattern) -> dict
  collect_form_state(page) -> dict
  restore_form_state(page, payload)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from src.ui.components import make_resettable_line_edit
from src.ui.pages.pattern._spec import PARAM_SPECS

# ── Internal widget helpers ───────────────────────────────────────────────────


def _param_entry(
    grid: QGridLayout, row: int, label: str, default: str, width: int = 80
) -> QLineEdit:
    grid.addWidget(QLabel(label), row, 0)
    e = QLineEdit(default)
    make_resettable_line_edit(e, default)
    e.setFixedWidth(width)
    grid.addWidget(e, row, 1)
    return e


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "hint-sm")
    return lbl


# ── Generic declarative param builder ────────────────────────────────────────


def build_param_widget(tab: Any, pattern_name: str, schedule_fn) -> QWidget:
    """Build a param widget from the declarative PARAM_SPECS and attach fields to tab."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)

    specs = PARAM_SPECS.get(pattern_name, [])
    grid_row = 0

    for spec in specs:
        if spec.kind in ("float", "int"):
            entry = _param_entry(g, grid_row, spec.label, spec.default)
            if spec.kind == "int":
                entry.setValidator(
                    QIntValidator(
                        int(spec.minimum if spec.minimum is not None else -2_147_483_648),
                        int(spec.maximum if spec.maximum is not None else 2_147_483_647),
                        entry,
                    )
                )
            else:
                validator = QDoubleValidator(
                    float(spec.minimum if spec.minimum is not None else -1e12),
                    float(spec.maximum if spec.maximum is not None else 1e12),
                    6,
                    entry,
                )
                validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                entry.setValidator(validator)
            entry.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, entry)
            entry.textChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

        elif spec.kind == "checkbox":
            cb = QCheckBox(spec.label)
            cb.setChecked(False)
            cb.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, cb)
            g.addWidget(cb, grid_row, 0, 1, 2)
            cb.stateChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

        elif spec.kind == "combobox":
            g.addWidget(QLabel(spec.label), grid_row, 0)
            combo = QComboBox()
            combo.setFixedWidth(120)
            combo.addItems(spec.items)
            idx = combo.findText(spec.default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setToolTip(spec.tooltip)
            setattr(tab, spec.attr, combo)
            g.addWidget(combo, grid_row, 1)
            combo.currentTextChanged.connect(schedule_fn)
            grid_row += 1
            if spec.hint is not None:
                g.addWidget(_hint_label(spec.hint), grid_row, 0, 1, 2)
                grid_row += 1

    return w


# ── Parameter collection ──────────────────────────────────────────────────────


def collect_pattern_params(tab: Any, pattern: str) -> dict:
    """Collect validated generator parameters for the selected pattern."""
    specs = PARAM_SPECS.get(pattern)
    if specs is None:
        raise ValueError(f"Pattern '{pattern}' is no longer available.")
    params = {}
    for spec in specs:
        key = spec.param_key or spec.attr[1:]
        widget = getattr(tab, spec.attr)
        bounds = {
            k: v for k, v in (("minimum", spec.minimum), ("maximum", spec.maximum)) if v is not None
        }
        if spec.kind == "checkbox":
            params[key] = widget.isChecked()
        elif spec.kind == "combobox":
            params[key] = widget.currentText()
        elif spec.kind == "int":
            params[key] = tab._parse_int_field(widget, spec.label, **bounds)
        else:
            params[key] = tab._parse_float_field(widget, spec.label, **bounds)

    params["rotation"] = tab._parse_float_field(tab._pattern_rotation, "Pattern rotation")
    params["density_mode"] = tab._density_mode_combo.currentText()
    params["density_strength"] = tab._parse_float_field(
        tab._density_strength, "Density strength", minimum=0.0, maximum=1.0
    )
    params["density_angle"] = tab._parse_float_field(tab._density_angle, "Density angle")
    params["density_reverse"] = tab._density_reverse.isChecked()
    if pattern == "Custom Tile":
        if not tab._custom_tile_polys:
            raise ValueError("Custom Tile has no motif. Send or choose tile geometry first.")
        params["tile_polys"] = [list(poly) for poly in tab._custom_tile_polys]
        params["interlock"] = False
    return params


# ── Form-state serialization ──────────────────────────────────────────────────
# These functions own the complete read/write of all fill-parameter widget
# values for workspace saves, preset load/save, and workspace restore.
# They live here (not on the page) so the page class only needs to do layout.


def collect_form_state(page: Any) -> dict:
    """Read all fill-parameter widget values into a plain dict (for presets / workspace)."""
    data: dict = {
        "pattern": page._pattern_combo.currentText(),
        "rotation": page._pattern_rotation.text(),
        "scale_w": page._scale_w.text(),
        "scale_h": page._scale_h.text(),
        "ar_locked": page._ar_lock_btn.isChecked(),
        "include_border": page._include_border_cb.isChecked(),
        "border_fade": page._border_fade.text(),
        "density_mode": page._density_mode_combo.currentText(),
        "density_strength": page._density_strength.text(),
        "density_angle": page._density_angle.text(),
        "density_reverse": page._density_reverse.isChecked(),
        "fill_mode": page._fill_mode_combo.currentData() or "none",
        "fill_spacing": page._fill_spacing.text(),
        "fill_angle": page._fill_angle.text(),
        "fill_inset": page._fill_inset.text(),
        "fill_keep_outline": page._fill_keep_outline_cb.isChecked(),
        "fill_target_outline": page._fill_target_outline_cb.isChecked(),
        "fill_target_pattern": page._fill_target_pattern_cb.isChecked(),
        "minimum_segment": page._minimum_segment_edit.text(),
        "minimum_area": page._minimum_area_edit.text(),
        "optimize_paths": page._optimize_paths_cb.isChecked(),
        "preview_quality": page._preview_quality_combo.currentData() or "balanced",
    }
    # All PARAM_SPECS pattern fields — key derived from attr name (strip leading _)
    for specs in PARAM_SPECS.values():
        for spec in specs:
            w = getattr(page, spec.attr, None)
            if w is None:
                continue
            key = spec.attr[1:]  # e.g. "_hex_r" -> "hex_r"
            if spec.kind == "checkbox":
                data[key] = w.isChecked()
            elif spec.kind == "combobox":
                data[key] = w.currentText()
            else:
                data[key] = w.text()
    if page._pattern_combo.currentText() == "Custom Tile":
        data["custom_tile_polys"] = [list(poly) for poly in page._custom_tile_polys]
    return data


def restore_form_state(page: Any, payload: dict) -> None:
    """Write fill-parameter widget values from a plain dict.

    Missing keys fall back to the current widget values, so partial payloads
    (e.g. presets that predate a new field) apply safely.
    """
    # Merge: current state supplies defaults for any missing keys
    values = collect_form_state(page)
    values.update(payload or {})

    page._refresh_pattern_choices(current=str(values.get("pattern", "— None —")))
    pattern = str(values.get("pattern", "— None —"))
    page._pattern_combo.setCurrentText(pattern)
    page._pattern_rotation.setText(str(values.get("rotation", "0")))
    page._scale_w.setText(str(values.get("scale_w", "")))
    page._scale_h.setText(str(values.get("scale_h", "")))
    page._ar_lock_btn.setChecked(bool(values.get("ar_locked", True)))
    page._include_border_cb.setChecked(bool(values.get("include_border", True)))
    page._border_fade.setText(str(values.get("border_fade", "0")))
    page._density_mode_combo.setCurrentText(str(values.get("density_mode", "Uniform")))
    page._density_strength.setText(str(values.get("density_strength", "0.75")))
    page._density_angle.setText(str(values.get("density_angle", "0")))
    page._density_reverse.setChecked(bool(values.get("density_reverse", False)))
    fill_mode_value = str(values.get("fill_mode", "none") or "none")
    fill_idx = page._fill_mode_combo.findData(fill_mode_value)
    page._fill_mode_combo.setCurrentIndex(max(fill_idx, 0))
    page._fill_spacing.setText(str(values.get("fill_spacing", "0.5")))
    page._fill_angle.setText(str(values.get("fill_angle", "0")))
    page._fill_inset.setText(str(values.get("fill_inset", "0")))
    page._fill_keep_outline_cb.setChecked(bool(values.get("fill_keep_outline", True)))
    target_outline = bool(values.get("fill_target_outline", True))
    target_pattern = bool(values.get("fill_target_pattern", False))
    page._fill_target_outline_cb.setChecked(target_outline)
    page._fill_target_pattern_cb.setChecked(target_pattern)
    page._minimum_segment_edit.setText(str(values.get("minimum_segment", "0")))
    page._minimum_area_edit.setText(str(values.get("minimum_area", "0")))
    page._optimize_paths_cb.setChecked(bool(values.get("optimize_paths", True)))
    quality = str(values.get("preview_quality", "balanced"))
    page._preview_quality_combo.setCurrentIndex(
        max(0, page._preview_quality_combo.findData(quality))
    )
    page._on_fill_mode_changed()
    if "custom_tile_polys" in values:
        raw_tile = values.get("custom_tile_polys") or []
        page._custom_tile_polys = [
            [(float(x), float(y)) for x, y in poly]
            for poly in raw_tile
            if isinstance(poly, (list, tuple)) and len(poly) >= 2
        ]

    # All PARAM_SPECS pattern fields
    for specs in PARAM_SPECS.values():
        for spec in specs:
            w = getattr(page, spec.attr, None)
            if w is None:
                continue
            key = spec.attr[1:]
            if spec.kind == "checkbox":
                w.setChecked(bool(values.get(key, False)))
            elif spec.kind == "combobox":
                w.setCurrentText(str(values.get(key, spec.default)))
            else:
                w.setText(str(values.get(key, spec.default)))
