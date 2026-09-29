"""Declarative field specifications for the Pattern page form."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.core.patterns.fill import NULL_PATTERN
from simple_stipple.core.patterns.geometry import NO_REPEAT, REPEAT_MODES
from simple_stipple.core.patterns.processing import RETIRED_PATTERNS
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
from simple_stipple.ui.components.feedback import clear_line_edit_error, reject_input
from simple_stipple.ui.components.focus import blocked_signals
from simple_stipple.ui.components.inputs import NoWheelSlider, make_resettable_line_edit
from simple_stipple.ui.components.units import parse_numeric_expression

MAX_PATTERN_DIMENSION_MM = 20.0

PATTERN_SUMMARY_FIELDS: dict[str, tuple[str, str]] = {
    "Basketweave": ("_basket_strip_w", "mm"),
    "Brick": ("_brick_w", "mm"),
    "Grip Stipple": ("_grip_spacing", "mm"),
    "Honeycomb": ("_hex_r", "mm"),
    "Knurling": ("_knurl_pitch", "mm"),
    "Mesh": ("_mesh_spacing", "mm"),
    "Seigaiha": ("_seigaiha_r", "mm"),
    "Stipple Dots": ("_stip_spacing", "mm"),
    "Truchet": ("_truchet_tile", "mm"),
    "Voronoi": ("_vor_cells", "cells"),
}


def outline_subtitle(path: str, width: str, height: str) -> tuple[str, bool]:
    """Describe an outline while tolerating dimensions still being edited."""
    if not path.strip():
        return "No file loaded", True
    try:
        w, h = float(width or "0"), float(height or "0")
    except ValueError:
        w = h = 0.0
    dims = f"{w:.1f} × {h:.1f} mm" if w and h else "—"
    return f"{Path(path.strip()).name} · {dims}", False


def pattern_subtitle(name: str, dimension: str, unit: str, fade_text: str) -> tuple[str, bool]:
    """Describe the selected generator without rejecting partial numeric input."""
    if not name or name == "— None —":
        return "None", True
    detail = f" · {dimension.strip()} {unit}".rstrip() if dimension.strip() else ""
    try:
        fade = float(fade_text or DEFAULT_BORDER_FADE)
    except ValueError:
        fade = 0.0
    modifier = f" · Fade {fade:.1f}mm" if fade > 0 else ""
    return f"{name}{detail}{modifier}", False


def fill_subtitle(
    mode: str,
    label: str,
    spacing: str,
    *,
    target_outline: bool,
    target_pattern: bool,
    line_count: int,
) -> tuple[str, bool]:
    """Summarize fill settings and the last generated line count."""
    if mode == "none":
        return "None", True
    targets = []
    if target_outline:
        targets.append("Outline")
    if target_pattern:
        targets.append("Pattern")
    target_text = " + ".join(targets) if targets else "No target"
    count_text = f" · {line_count} lines" if line_count else ""
    return f"{label} · {spacing.strip() or '?'} mm · {target_text}{count_text}", False


def regions_subtitle(count: int) -> tuple[str, bool]:
    """Keep an empty Regions group explanatory and treated regions countable."""
    if count == 0:
        return "Optional · a different treatment per region", True
    return f"{count} region{'s' if count != 1 else ''} with a treatment", False


def retired_pattern_notice(names: Iterable[object]) -> str | None:
    """Name the substitution when a preset or workspace uses a retired generator."""
    swaps = sorted(
        {
            (str(name), RETIRED_PATTERNS[str(name)])
            for name in names
            if str(name) in RETIRED_PATTERNS
        }
    )
    if not swaps:
        return None
    detail = "; ".join(f"{old} → {new}" for old, new in swaps)
    return f"Retired pattern replaced: {detail}"


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


def _align_field(prefix: str) -> ParamField:
    return ParamField(
        f"_{prefix}_align_region",
        "Align to region",
        "false",
        "Anchor the lattice to this shape instead of the document grid — "
        "use it to centre a motif in one region",
        kind="checkbox",
        param_key="align_to_region",
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
            items=list(REPEAT_MODES),
            param_key="repeat_mode",
        ),
        _align_field(prefix),
    ]


def _scatter_repeat_fields(prefix: str) -> list[ParamField]:
    """Repeat controls for random patterns: off scatters over the whole
    region; any lattice mode repeats one random tile, seamlessly."""
    return [
        ParamField(
            f"_{prefix}_repeat",
            "Repeat mode",
            NO_REPEAT,
            "Off scatters freely over the whole region. Straight, Half drop, and "
            "Brick offset repeat one random tile, offsetting each row of tiles",
            kind="combobox",
            items=[NO_REPEAT, *REPEAT_MODES],
            param_key="repeat_mode",
        ),
        ParamField(
            f"_{prefix}_repeat_size",
            "Repeat size (mm)",
            "10",
            "Edge length of the square tile that repeats when Repeat mode is on",
            param_key="repeat_size",
            minimum=1.0,
        ),
        _align_field(prefix),
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
        *_scatter_repeat_fields("stip"),
    ],
    "Grip Stipple": [
        ParamField(
            "_grip_element_size",
            "Element size (mm)",
            "0.28",
            "Approximate radius of each irregular raised grain",
            param_key="element_size",
            minimum=0.001,
        ),
        ParamField(
            "_grip_spacing",
            "Spacing (mm)",
            "0.62",
            "Minimum centre-to-centre distance between grains",
            param_key="spacing",
            minimum=0.001,
        ),
        ParamField(
            "_grip_seed",
            "Seed",
            "42",
            "Deterministic random seed for repeatable grain placement and shapes",
            kind="int",
            param_key="seed",
        ),
        *_scatter_repeat_fields("grip"),
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
        *_lattice_fields("knurl", default_mode="Straight"),
    ],
    "Voronoi": [
        ParamField(
            "_vor_cells",
            "Cell count",
            "60",
            "Number of random Voronoi cells to generate — per repeat tile when Repeat "
            "mode is on (high counts slow previews)",
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
        *_scatter_repeat_fields("vor"),
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


def _plain_number(value: float, *, integer: bool) -> str:
    if integer:
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def numeric_input_error(
    text: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
    length: bool = False,
) -> tuple[float | None, str | None]:
    """Evaluate a committed numeric entry; return ``(value, None)`` or ``(None, reason)``."""
    try:
        value = parse_numeric_expression(text, "mm", is_length=length)
    except ValueError:
        return None, "Enter a number or expression, e.g. 25/2"
    suffix = " mm" if length else ""
    if integer and value != int(value):
        return None, "Enter a whole number"
    if minimum is not None and value < minimum:
        return None, f"Must be at least {minimum:g}{suffix}"
    if maximum is not None and value > maximum:
        return None, f"Must be at most {maximum:g}{suffix}"
    return value, None


def bind_numeric_commit(
    field: QLineEdit,
    commit: Callable[[], None],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
    length: bool = False,
) -> None:
    """Commit a numeric field on ``editingFinished`` (Enter or focus-out) only.

    An invalid entry is rejected in place with a specific reason and never
    reaches *commit*; an expression such as ``25/2`` is evaluated and written
    back as the number it produces. The trailing reset button restores the
    default without focusing the field, so it commits on its own.
    """

    def finish() -> None:
        text = field.text()
        value, reason = numeric_input_error(
            text, minimum=minimum, maximum=maximum, integer=integer, length=length
        )
        if reason is not None:
            reject_input(field, reason)
            return
        clear_line_edit_error(field)
        try:
            float(text)
        except ValueError:
            assert value is not None
            field.setText(_plain_number(value, integer=integer))
        commit()

    field.editingFinished.connect(finish)
    reset = next(iter(field.findChildren(QToolButton)), None)
    if reset is not None:
        # The reset helper restores the default on the next event-loop turn.
        reset.clicked.connect(lambda: QTimer.singleShot(0, finish))


SLIDER_STEPS = 1000
# Ranges wider than this many decades get a logarithmic slider track.
_LOG_SLIDER_DECADES = 2.0


def _decimals(value: float | str | None) -> int:
    if value is None:
        return 0
    return len(f"{float(value):.6f}".rstrip("0").partition(".")[2])


@dataclass(frozen=True)
class SliderScale:
    """Map a numeric field onto an integer slider without inventing precision.

    Ranges spanning more than two decades use a logarithmic track so the fine
    end stays draggable. Slider values round to the coarser of the field's own
    precision and the slider's step, so a drag never produces noise digits.
    Text entry is unaffected and stays exact.
    """

    low: float
    high: float
    decimals: int
    integer: bool = False

    @classmethod
    def for_field(cls, spec: ParamField) -> SliderScale:
        default = float(spec.default)
        low = float(spec.minimum if spec.minimum is not None else min(-360.0, default))
        high = float(spec.maximum if spec.maximum is not None else max(360.0, default))
        decimals = max(2, _decimals(spec.default), _decimals(spec.minimum))
        return cls(low, high, decimals, integer=spec.kind == "int")

    @property
    def logarithmic(self) -> bool:
        return self.low > 0 and math.log10(self.high / self.low) > _LOG_SLIDER_DECADES

    @property
    def steps(self) -> int:
        span = self.high - self.low
        if self.integer and not self.logarithmic and span < SLIDER_STEPS:
            return max(1, round(span))
        return SLIDER_STEPS

    def position(self, value: float) -> int:
        value = min(self.high, max(self.low, value))
        if self.logarithmic:
            fraction = math.log(value / self.low) / math.log(self.high / self.low)
        else:
            fraction = (value - self.low) / max(self.high - self.low, 1e-12)
        return round(fraction * self.steps)

    def _places(self, value: float) -> int:
        if self.logarithmic:
            # Three significant figures, never finer than the field itself.
            magnitude = math.floor(math.log10(value)) if value > 0 else 0
            return max(0, min(self.decimals, 2 - magnitude))
        step = (self.high - self.low) / self.steps
        return max(0, min(self.decimals, math.ceil(-math.log10(step))))

    def value(self, position: int) -> float:
        fraction = min(1.0, max(0.0, position / self.steps))
        if self.logarithmic:
            raw = self.low * (self.high / self.low) ** fraction
        else:
            raw = self.low + (self.high - self.low) * fraction
        if self.integer:
            return float(round(raw))
        return round(raw, self._places(raw))

    def text(self, position: int) -> str:
        return _plain_number(self.value(position), integer=self.integer)


def _range_tooltip(spec: ParamField) -> str:
    unit = " mm" if "(mm)" in spec.label else "°" if "(°)" in spec.label else ""
    if spec.minimum is not None and spec.maximum is not None:
        bounds = f"Range {spec.minimum:g}–{spec.maximum:g}{unit}"
    elif spec.minimum is not None:
        bounds = f"Minimum {spec.minimum:g}{unit}"
    elif spec.maximum is not None:
        bounds = f"Maximum {spec.maximum:g}{unit}"
    else:
        return spec.tooltip
    return f"{spec.tooltip}\n{bounds}" if spec.tooltip else bounds


def build_param_widget(
    page: Any,
    pattern_name: str,
    schedule_preview: Callable[..., None],
) -> QWidget:
    """Build fields from ``PARAM_SPECS`` and bind them to their page attributes.

    Numeric text commits on Enter or focus-out, so typing never re-solves per
    keystroke; sliders, checkboxes and combos stay live (the page debounces).
    """
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    for spec in PARAM_SPECS.get(pattern_name, []):
        field: QWidget
        tooltip = spec.tooltip
        if spec.kind in {"float", "int"}:
            tooltip = _range_tooltip(spec)
            control = QWidget()
            control_layout = QVBoxLayout(control)
            control_layout.setContentsMargins(0, 0, 0, 0)
            control_layout.setSpacing(4)
            label = QLabel(spec.label)
            label.setToolTip(tooltip)
            control_layout.addWidget(label)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            field = _numeric_field(spec.default)
            field.setAccessibleName(spec.label)
            bind_numeric_commit(
                field,
                schedule_preview,
                minimum=spec.minimum,
                maximum=spec.maximum,
                integer=spec.kind == "int",
                length="(mm)" in spec.label,
            )
            row.addWidget(field)
            scale = SliderScale.for_field(spec)
            slider = NoWheelSlider(Qt.Orientation.Horizontal)
            slider.setObjectName(f"{spec.attr.removeprefix('_')}_slider")
            slider.setAccessibleName(f"{spec.label} slider")
            slider.setToolTip(tooltip)
            slider.setRange(0, scale.steps)
            slider.setValue(scale.position(float(spec.default)))

            def from_slider(
                position: int, target: QLineEdit = field, scale: SliderScale = scale
            ) -> None:
                target.setText(scale.text(position))
                clear_line_edit_error(target)
                schedule_preview()

            def from_text(
                text: str, target: NoWheelSlider = slider, scale: SliderScale = scale
            ) -> None:
                try:
                    value = float(text)
                except ValueError:
                    return
                with blocked_signals(target):
                    target.setValue(scale.position(value))

            slider.valueChanged.connect(from_slider)
            # Position sync only; the preview is scheduled by the commit above.
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

        field.setToolTip(tooltip)
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


def restore_form_state(page: Any, payload: dict, *, restore_document_lattice: bool = True) -> None:
    """Write fill-parameter widget values from a plain dict.

    Missing keys fall back to the current widget values, so partial payloads
    (e.g. presets that predate a new field) apply safely.
    Region selection preserves the document lattice even when an older
    region or custom-tile snapshot contains its own origin and seed.
    A retired generator name opens as its replacement (see
    :func:`retired_pattern_notice` for telling the user).
    """
    # Merge: current state supplies defaults for any missing keys
    values = collect_form_state(page)
    values.update(payload or {})

    pattern = str(values.get("pattern", NULL_PATTERN))
    pattern = RETIRED_PATTERNS.get(pattern, pattern)
    page._refresh_pattern_choices(current=pattern)
    page._pattern_combo.setCurrentText(pattern)
    page._pattern_rotation.setText(str(values.get("rotation", DEFAULT_PATTERN_ROTATION)))
    page._pattern_size_percent.setText(str(values.get("size_percent", "100")))
    page._scale_w.setText(str(values.get("scale_w", "")))
    page._scale_h.setText(str(values.get("scale_h", "")))
    page._ar_lock_btn.setChecked(bool(values.get("ar_locked", True)))
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
    if restore_document_lattice:
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
                clear_line_edit_error(w)
    # Restored values replace whatever was rejected before, so drop stale marks.
    for edit in (
        page._pattern_rotation,
        page._pattern_size_percent,
        page._border_fade,
        page._density_strength,
        page._density_angle,
        page._fill_spacing,
        page._fill_angle,
        page._fill_inset,
        page._minimum_segment_edit,
        page._minimum_area_edit,
        page._lattice_origin_x,
        page._lattice_origin_y,
        page._lattice_seed,
    ):
        clear_line_edit_error(edit)
