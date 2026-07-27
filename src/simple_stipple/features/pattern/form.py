"""Declarative construction of pattern parameter forms."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QSlider, QWidget

from simple_stipple.features.pattern.form_spec import PARAM_SPECS
from simple_stipple.ui.components.inputs import make_resettable_line_edit


def _numeric_field(default: str, width: int = 80) -> QLineEdit:
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
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    row = 0

    for spec in PARAM_SPECS.get(pattern_name, []):
        field: QWidget
        if spec.kind in {"float", "int"}:
            grid.addWidget(QLabel(spec.label), row, 0)
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
            grid.addWidget(field, row, 1)
            # Keep the precise field and a drag-friendly live control in sync.
            # The page's existing 100 ms preview timer performs the debounce.
            slider = QSlider(Qt.Orientation.Horizontal)
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
                target.setText(str(round(number)) if integer else f"{number:.6g}")

            def from_text(
                text: str,
                target: QSlider = slider,
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
            grid.addWidget(slider, row, 2)
            setattr(page, f"{spec.attr}_slider", slider)
        elif spec.kind == "checkbox":
            checkbox = QCheckBox(spec.label)
            checkbox.stateChanged.connect(schedule_preview)
            grid.addWidget(checkbox, row, 0, 1, 2)
            field = checkbox
        else:
            grid.addWidget(QLabel(spec.label), row, 0)
            combo = QComboBox()
            combo.setAccessibleName(spec.label)
            combo.setFixedWidth(120)
            combo.addItems(spec.items)
            combo.setCurrentText(spec.default)
            combo.currentTextChanged.connect(schedule_preview)
            grid.addWidget(combo, row, 1)
            field = combo

        field.setToolTip(spec.tooltip)
        setattr(page, spec.attr, field)
        row += 1
        if spec.hint is not None:
            grid.addWidget(_hint(spec.hint), row, 0, 1, 2)
            row += 1

    return widget
