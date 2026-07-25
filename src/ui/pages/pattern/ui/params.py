"""Pattern parameter collection and restoration.

Provides:
  collect_pattern_params(tab, pattern) -> dict
  collect_form_state(page) -> dict
  restore_form_state(page, payload)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QLineEdit,
)

from src.ui.components import make_resettable_line_edit
from src.ui.pages.pattern.domain.defaults import (
    DEFAULT_BORDER_FADE,
    DEFAULT_DENSITY_ANGLE,
    DEFAULT_DENSITY_MODE,
    DEFAULT_DENSITY_STRENGTH,
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_MODE,
    DEFAULT_FILL_SPACING,
    DEFAULT_MIN_ISLAND_AREA,
    DEFAULT_MIN_SEGMENT,
    DEFAULT_PATTERN_ROTATION,
    DEFAULT_PREVIEW_QUALITY,
)
from src.ui.pages.pattern.ui.form_spec import PARAM_SPECS

# ── Internal widget helpers ───────────────────────────────────────────────────


def _param_entry(
    grid: QGridLayout, row: int, label: str, default: str, width: int = 80
) -> QLineEdit:
    grid.addWidget(QLabel(label), row, 0)
    e = QLineEdit(default)
    e.setAccessibleName(label)
    make_resettable_line_edit(e, default)
    e.setFixedWidth(width)
    grid.addWidget(e, row, 1)
    return e


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "hint-sm")
    return lbl


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
    params["size_percent"] = tab._parse_float_field(
        tab._pattern_size_percent, "Pattern size", minimum=1.0, maximum=10000.0
    )
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
        "size_percent": page._pattern_size_percent.text(),
        "scale_w": page._scale_w.text(),
        "scale_h": page._scale_h.text(),
        "ar_locked": page._ar_lock_btn.isChecked(),
        "include_border": page._include_border_cb.isChecked(),
        "border_fade": page._border_fade.text(),
        "density_mode": page._density_mode_combo.currentText(),
        "density_strength": page._density_strength.text(),
        "density_angle": page._density_angle.text(),
        "density_reverse": page._density_reverse.isChecked(),
        "fill_mode": page._fill_mode_combo.currentData() or DEFAULT_FILL_MODE,
        "fill_spacing": page._fill_spacing.text(),
        "fill_angle": page._fill_angle.text(),
        "fill_inset": page._fill_inset.text(),
        "fill_keep_outline": page._fill_keep_outline_cb.isChecked(),
        "fill_target_outline": page._fill_target_outline_cb.isChecked(),
        "fill_target_pattern": page._fill_target_pattern_cb.isChecked(),
        "minimum_segment": page._minimum_segment_edit.text(),
        "minimum_area": page._minimum_area_edit.text(),
        "optimize_paths": page._optimize_paths_cb.isChecked(),
        "preview_quality": page._preview_quality_combo.currentData() or DEFAULT_PREVIEW_QUALITY,
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
    page._pattern_rotation.setText(str(values.get("rotation", DEFAULT_PATTERN_ROTATION)))
    page._pattern_size_percent.setText(str(values.get("size_percent", "100")))
    page._scale_w.setText(str(values.get("scale_w", "")))
    page._scale_h.setText(str(values.get("scale_h", "")))
    page._ar_lock_btn.setChecked(bool(values.get("ar_locked", True)))
    page._include_border_cb.setChecked(bool(values.get("include_border", True)))
    page._border_fade.setText(str(values.get("border_fade", DEFAULT_BORDER_FADE)))
    page._density_mode_combo.setCurrentText(str(values.get("density_mode", DEFAULT_DENSITY_MODE)))
    page._density_strength.setText(str(values.get("density_strength", DEFAULT_DENSITY_STRENGTH)))
    page._density_angle.setText(str(values.get("density_angle", DEFAULT_DENSITY_ANGLE)))
    page._density_reverse.setChecked(bool(values.get("density_reverse", False)))
    fill_mode_value = str(values.get("fill_mode", DEFAULT_FILL_MODE) or DEFAULT_FILL_MODE)
    fill_idx = page._fill_mode_combo.findData(fill_mode_value)
    page._fill_mode_combo.setCurrentIndex(max(fill_idx, 0))
    page._fill_spacing.setText(str(values.get("fill_spacing", DEFAULT_FILL_SPACING)))
    page._fill_angle.setText(str(values.get("fill_angle", DEFAULT_FILL_ANGLE)))
    page._fill_inset.setText(str(values.get("fill_inset", DEFAULT_FILL_INSET)))
    page._fill_keep_outline_cb.setChecked(bool(values.get("fill_keep_outline", True)))
    target_outline = bool(values.get("fill_target_outline", True))
    target_pattern = bool(values.get("fill_target_pattern", False))
    page._fill_target_outline_cb.setChecked(target_outline)
    page._fill_target_pattern_cb.setChecked(target_pattern)
    page._minimum_segment_edit.setText(str(values.get("minimum_segment", DEFAULT_MIN_SEGMENT)))
    page._minimum_area_edit.setText(str(values.get("minimum_area", DEFAULT_MIN_ISLAND_AREA)))
    page._optimize_paths_cb.setChecked(bool(values.get("optimize_paths", True)))
    quality = str(values.get("preview_quality", DEFAULT_PREVIEW_QUALITY))
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
