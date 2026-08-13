"""Declarative field specifications for the Pattern page form."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from simple_stipple.ui.components.inputs import NoWheelSlider, make_resettable_line_edit
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QLineEdit,
)
from simple_stipple.features.pattern.defaults import (
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
from simple_stipple.ui.components.inputs import make_resettable_line_edit

MAX_PATTERN_DIMENSION_MM = 20.0


@dataclass
class ParamField:
    attr: str  # instance attribute name set on the tab (e.g. "_hex_r")
    label: str  # display label in the grid
    default: str  # default value text
    tooltip: str = ""
    kind: str = "float"  # "float" | "int" | "checkbox" | "combobox"
    items: list[str] = field(default_factory=list)  # choices for "combobox"
    hint: str | None = None  # optional hint label appended after this field
    param_key: str = ""  # key in the generator params dict; defaults to attr[1:]
    minimum: float | None = None  # lower bound for numeric fields
    maximum: float | None = None  # upper bound for numeric fields

    def __post_init__(self) -> None:
        """Cap physical pattern controls for the app's small-format outlines."""
        if self.kind in {"float", "int"} and "(mm)" in self.label:
            self.maximum = (
                min(self.maximum, MAX_PATTERN_DIMENSION_MM)
                if self.maximum is not None
                else MAX_PATTERN_DIMENSION_MM
            )


def _lattice_fields(prefix: str, *, default_mode: str = "Half drop") -> list[ParamField]:
    """Repeat mode and the align-to-region opt-out, shared by every tiling
    pattern.

    The lattice origin is a *document* setting, not a per-pattern one: two
    regions line up because they share one grid, not because the user typed
    the same phase offset into both. All that is left per region is whether to
    leave that grid.
    """
    return [
        ParamField(
            f"_{prefix}_repeat",
            "Repeat mode",
            default_mode,
            "How each row of the lattice is offset from the one below it",
            kind="combobox",
            items=["Straight", "Half drop", "Brick offset"],
            param_key="repeat_mode",
        ),
        ParamField(
            f"_{prefix}_align_region",
            "Align to region",
            "false",
            "Anchor the lattice to this shape instead of the document grid — "
            "use it to centre a motif in one region",
            kind="checkbox",
            param_key="align_to_region",
        ),
    ]


# ── Parameter specs for each named pattern ────────────────────────────────────
# Each list entry maps directly to a row in the param grid widget.
# Fields marked hint="..." render a small muted label below them.

PARAM_SPECS: dict[str, list[ParamField]] = {
    "Custom Tile": [
        ParamField(
            "_custom_tile_gap",
            "Tile gap (mm)",
            "0.5",
            "Spacing between repetitions of the selected custom geometry",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
        ParamField(
            "_custom_tile_repeat",
            "Repeat mode",
            "Straight",
            "How neighboring motif copies are offset or transformed",
            kind="combobox",
            items=[
                "Straight",
                "Half drop",
                "Brick offset",
                "Mirror rows",
                "Mirror columns",
                "Alternate 180°",
            ],
            param_key="repeat_mode",
        ),
        ParamField(
            "_custom_tile_align_region",
            "Align to region",
            "false",
            "Anchor the repeat to this shape instead of the document grid",
            kind="checkbox",
            param_key="align_to_region",
        ),
    ],
    "Honeycomb": [
        ParamField(
            "_hex_r",
            "Hex size (mm)",
            "1.75",
            "Radius of each hexagonal cell",
            param_key="r",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_hex_gap",
            "Gap (mm)",
            "0.5",
            "Spacing between adjacent hexagons",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
        *_lattice_fields("hex", default_mode="Half drop"),
    ],
    "Basketweave": [
        ParamField(
            "_basket_strip_w",
            "Strip width (mm)",
            "2.0",
            "Width of each woven strip",
            param_key="strip_w",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_basket_strip_l",
            "Strip length (mm)",
            "8.0",
            "Length of each woven strip",
            param_key="strip_l",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_basket_gap",
            "Gap (mm)",
            "0.2",
            "Gap between woven strips",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
        *_lattice_fields("weave", default_mode="Straight"),
    ],
    "Stipple Dots": [
        ParamField(
            "_stip_r",
            "Dot radius (mm)",
            "0.4",
            "Radius of each stipple dot",
            param_key="r",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_stip_spacing",
            "Spacing (mm)",
            "1.2",
            "Centre-to-centre distance between dots",
            param_key="spacing",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_stip_seed",
            "Seed",
            "42",
            "Deterministic random seed for repeatable stipple placement",
            kind="int",
            param_key="seed",
        ),
    ],
    "Brick": [
        ParamField(
            "_brick_w",
            "Brick width (mm)",
            "4.0",
            "Width of each brick",
            param_key="brick_w",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_brick_h",
            "Brick height (mm)",
            "2.0",
            "Height of each brick",
            param_key="brick_h",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_brick_gap",
            "Gap (mm)",
            "0.5",
            "Mortar gap between bricks",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
        *_lattice_fields("brick", default_mode="Half drop"),
    ],
    "Mesh": [
        ParamField(
            "_mesh_r",
            "Circle radius (mm)",
            "0.35",
            "Radius of each mesh circle",
            param_key="r",
            minimum=0.001,
            maximum=100,
        ),
        ParamField(
            "_mesh_spacing",
            "Grid spacing (mm)",
            "1.2",
            "Centre-to-centre distance between mesh circles",
            param_key="spacing",
            minimum=0.001,
            maximum=1000,
        ),
        *_lattice_fields("mesh", default_mode="Straight"),
    ],
    "Truchet": [
        ParamField(
            "_truchet_tile",
            "Tile size (mm)",
            "6",
            "Edge length of each square tile; arcs meet exactly at tile edges",
            param_key="tile",
            minimum=0.2,
            maximum=20,
        ),
        ParamField(
            "_truchet_gap",
            "Cell gap (mm)",
            "0.3",
            "Space between cells; 0 tiles the surface with no room to fill around them",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
        ParamField(
            "_truchet_seed",
            "Seed",
            "1",
            "Fixes the random tile rotations so a re-solve is reproducible",
            kind="int",
            param_key="seed",
            minimum=0,
            maximum=999999,
        ),
        *_lattice_fields("truchet", default_mode="Straight"),
    ],
    "Seigaiha": [
        ParamField(
            "_seigaiha_r",
            "Scale radius (mm)",
            "6",
            "Radius of each wave scale; rows overlap by half this value",
            param_key="r",
            minimum=0.2,
            maximum=20,
        ),
        ParamField(
            "_seigaiha_rings",
            "Rings per scale",
            "3",
            "Concentric arcs drawn inside each scale",
            kind="int",
            param_key="rings",
            minimum=1,
            maximum=12,
        ),
        ParamField(
            "_seigaiha_ring_gap",
            "Ring spacing (mm)",
            "1.2",
            "Distance between concentric arcs within one scale",
            param_key="ring_gap",
            minimum=0.05,
            maximum=20,
        ),
        ParamField(
            "_seigaiha_gap",
            "Scale spacing (mm)",
            "0.3",
            "Gap between neighbouring scales; 0 tiles the surface completely",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
        *_lattice_fields("seigaiha"),
    ],
    "Knurling": [
        ParamField(
            "_knurl_pitch",
            "Groove pitch (mm)",
            "1.5",
            "Spacing between grooves — the diamond size follows from pitch and angle",
            param_key="pitch",
            minimum=0.1,
            maximum=20,
        ),
        ParamField(
            "_knurl_angle",
            "Groove angle (°)",
            "30",
            "Angle of each groove family from horizontal",
            param_key="angle",
            minimum=-89.0,
            maximum=89.0,
        ),
        ParamField(
            "_knurl_groove",
            "Groove width (mm)",
            "0.3",
            "Gap between raised pads — this is the cut groove itself",
            param_key="groove",
            minimum=0.0,
            maximum=20,
        ),
        ParamField(
            "_knurl_cross",
            "Cross-hatch (diamond)",
            "true",
            "Off gives a single straight-knurl family instead of diamonds",
            kind="checkbox",
            param_key="cross",
        ),
        ParamField(
            "_knurl_align_region",
            "Align to region",
            "false",
            "Anchor the grooves to this shape instead of the document grid",
            kind="checkbox",
            param_key="align_to_region",
        ),
    ],
    "Voronoi": [
        ParamField(
            "_vor_cells",
            "Cell count",
            "60",
            "Number of random Voronoi cells to generate (high counts slow previews)",
            kind="int",
            param_key="n_cells",
            minimum=2,
            maximum=2000,
        ),
        ParamField(
            "_vor_gap",
            "Gap (mm)",
            "0.15",
            "Inset distance between Voronoi cells",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
        ParamField(
            "_vor_seed",
            "Seed",
            "42",
            "Random seed for reproducible cell placement",
            kind="int",
            param_key="seed",
        ),
    ],
}


def _numeric_field(default: str, width: int = 88) -> QLineEdit:
    field = QLineEdit(default)
    make_resettable_line_edit(field, default)
    field.setFixedWidth(width)
    return field


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "hint-sm")
    return label


def build_param_widget(
    page: Any,
    pattern_name: str,
    schedule_preview: Callable[..., None],
) -> QWidget:
    """Build fields from ``PARAM_SPECS`` and bind them to their page attributes."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    for spec in PARAM_SPECS.get(pattern_name, []):
        field: QWidget
        if spec.kind in {"float", "int"}:
            control = QWidget()
            control_layout = QVBoxLayout(control)
            control_layout.setContentsMargins(0, 0, 0, 0)
            control_layout.setSpacing(4)
            label = QLabel(spec.label)
            label.setToolTip(spec.tooltip)
            control_layout.addWidget(label)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            field = _numeric_field(spec.default)
            field.setAccessibleName(spec.label)
            if spec.kind == "int":
                field.setValidator(
                    QIntValidator(
                        int(spec.minimum if spec.minimum is not None else -2_147_483_648),
                        int(spec.maximum if spec.maximum is not None else 2_147_483_647),
                        field,
                    )
                )
            else:
                validator = QDoubleValidator(
                    float(spec.minimum if spec.minimum is not None else -1e12),
                    float(spec.maximum if spec.maximum is not None else 1e12),
                    6,
                    field,
                )
                validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                field.setValidator(validator)
            field.textChanged.connect(schedule_preview)
            row.addWidget(field)
            # Keep the precise field and a drag-friendly live control in sync.
            # The page's existing 100 ms preview timer performs the debounce.
            slider = NoWheelSlider(Qt.Orientation.Horizontal)
            slider.setObjectName(f"{spec.attr.removeprefix('_')}_slider")
            slider.setAccessibleName(f"{spec.label} slider")
            slider.setRange(0, 1000)
            default = float(spec.default)
            low = float(spec.minimum if spec.minimum is not None else min(-360.0, default))
            high = float(spec.maximum if spec.maximum is not None else max(360.0, default))

            def slider_value(value: float, lo: float = low, hi: float = high) -> int:
                return round(1000.0 * (max(lo, min(hi, value)) - lo) / max(hi - lo, 1e-12))

            slider.setValue(slider_value(default))

            def from_slider(
                value: int,
                target: QLineEdit = field,
                lo: float = low,
                hi: float = high,
                integer: bool = spec.kind == "int",
            ) -> None:
                number = lo + (hi - lo) * value / 1000.0
                # Sliders are for quick, predictable adjustment. Keep float
                # values at the same two-decimal precision shown throughout
                # the Pattern workspace instead of exposing interpolation
                # artifacts such as ``1.0494274624``.
                target.setText(str(round(number)) if integer else f"{number:.2f}")

            def from_text(
                text: str,
                target: NoWheelSlider = slider,
                lo: float = low,
                hi: float = high,
            ) -> None:
                try:
                    position = round(1000.0 * (float(text) - lo) / max(hi - lo, 1e-12))
                except ValueError:
                    return
                target.blockSignals(True)
                target.setValue(max(0, min(1000, position)))
                target.blockSignals(False)

            slider.valueChanged.connect(from_slider)
            field.textChanged.connect(from_text)
            slider.setMinimumWidth(90)
            row.addWidget(slider, stretch=1)
            control_layout.addLayout(row)
            setattr(page, f"{spec.attr}_slider", slider)
            layout.addWidget(control)
        elif spec.kind == "checkbox":
            checkbox = QCheckBox(spec.label)
            checkbox.stateChanged.connect(schedule_preview)
            layout.addWidget(checkbox)
            field = checkbox
        else:
            control = QWidget()
            control_layout = QVBoxLayout(control)
            control_layout.setContentsMargins(0, 0, 0, 0)
            control_layout.setSpacing(4)
            control_layout.addWidget(QLabel(spec.label))
            combo = QComboBox()
            combo.setAccessibleName(spec.label)
            combo.addItems(spec.items)
            combo.setCurrentText(spec.default)
            combo.currentTextChanged.connect(schedule_preview)
            control_layout.addWidget(combo)
            layout.addWidget(control)
            field = combo

        field.setToolTip(spec.tooltip)
        setattr(page, spec.attr, field)
        if spec.hint is not None:
            layout.addWidget(_hint(spec.hint))

    layout.addStretch()

    return widget


# ── Internal widget helpers ───────────────────────────────────────────────────


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
        # Document scope, not region scope: every treatment carries a copy so
        # a workspace round-trips it, but they all hold the same grid.
        "lattice_origin_x": page._lattice_origin_x.text(),
        "lattice_origin_y": page._lattice_origin_y.text(),
        "lattice_seed": page._lattice_seed.text(),
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
    page._lattice_origin_x.setText(str(values.get("lattice_origin_x", "0")))
    page._lattice_origin_y.setText(str(values.get("lattice_origin_y", "0")))
    page._lattice_seed.setText(str(values.get("lattice_seed", "1")))
    page._push_document_lattice()
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
