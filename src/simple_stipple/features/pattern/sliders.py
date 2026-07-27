"""Reusable live slider paired with a precise numeric input."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QWidget


class PatternSlider(QWidget):
    """A logarithm-free floating point slider with an editable spin box."""

    valueChanged = Signal(float)

    def __init__(
        self,
        value: float,
        minimum: float,
        maximum: float,
        *,
        decimals: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        decimals = min(2, max(0, int(decimals)))
        self._factor = float(10**decimals)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.spin_box = QDoubleSpinBox()
        self.spin_box.setDecimals(decimals)
        self.spin_box.setRange(minimum, maximum)
        self.spin_box.setKeyboardTracking(False)
        self.slider.setRange(round(minimum * self._factor), round(maximum * self._factor))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin_box)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin_box.valueChanged.connect(self._from_spin_box)
        self.setValue(value)

    def value(self) -> float:
        return self.spin_box.value()

    def setValue(self, value: float) -> None:
        value = max(self.spin_box.minimum(), min(self.spin_box.maximum(), float(value)))
        self.slider.blockSignals(True)
        self.spin_box.blockSignals(True)
        self.slider.setValue(round(value * self._factor))
        self.spin_box.setValue(value)
        self.spin_box.blockSignals(False)
        self.slider.blockSignals(False)

    def _from_slider(self, value: int) -> None:
        numeric = value / self._factor
        self.spin_box.blockSignals(True)
        self.spin_box.setValue(numeric)
        self.spin_box.blockSignals(False)
        self.valueChanged.emit(numeric)

    def _from_spin_box(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self._factor))
        self.slider.blockSignals(False)
        self.valueChanged.emit(value)


__all__ = ["PatternSlider"]
