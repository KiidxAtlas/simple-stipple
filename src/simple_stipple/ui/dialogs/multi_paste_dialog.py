"""Dialog for creating evenly spaced clipboard copies."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QVBoxLayout

from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.dialogs.base import BaseDialog
from simple_stipple.ui.units import from_display, suffix, to_display


class MultiPasteDialog(BaseDialog):
    def __init__(self, parent=None, *, unit: str = "mm") -> None:
        self._unit = unit
        super().__init__(parent, title="Paste Multiple")

    def create_content(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        self.distance_input = QDoubleSpinBox()
        self.distance_input.setRange(0.001, 1_000_000.0)
        self.distance_input.setDecimals(3)
        self.distance_input.setSuffix(f" {suffix(self._unit)}")
        self.distance_input.setValue(to_display(5.0, self._unit))
        self.count_input = QSpinBox()
        self.count_input.setRange(1, 10_000)
        self.count_input.setValue(2)
        self.direction_input = QComboBox()
        self.direction_input.addItems(["Right", "Left", "Up", "Down"])
        form.addRow("Offset", self.distance_input)
        form.addRow("Copies", self.count_input)
        form.addRow("Direction", self.direction_input)
        layout.addLayout(form)
        install_dialog_focus_lifecycle(self, self.distance_input)

    def values(self) -> tuple[float, int, tuple[float, float]]:
        """Return (offset_mm, copies, direction) — offset converted from display units."""
        vectors = {"Right": (1.0, 0.0), "Left": (-1.0, 0.0), "Up": (0.0, 1.0), "Down": (0.0, -1.0)}
        return (
            from_display(self.distance_input.value(), self._unit),
            self.count_input.value(),
            vectors[self.direction_input.currentText()],
        )


__all__ = ["MultiPasteDialog"]
