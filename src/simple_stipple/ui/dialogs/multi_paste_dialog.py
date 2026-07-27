"""Dialog for creating evenly spaced clipboard copies."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QVBoxLayout

from simple_stipple.ui.dialogs.base import BaseDialog


class MultiPasteDialog(BaseDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, title="Paste Multiple")

    def create_content(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        self.distance_input = QDoubleSpinBox()
        self.distance_input.setRange(0.001, 1_000_000.0)
        self.distance_input.setDecimals(3)
        self.distance_input.setSuffix(" mm")
        self.distance_input.setValue(5.0)
        self.count_input = QSpinBox()
        self.count_input.setRange(1, 10_000)
        self.count_input.setValue(2)
        self.direction_input = QComboBox()
        self.direction_input.addItems(["Right", "Left", "Up", "Down"])
        form.addRow("Offset", self.distance_input)
        form.addRow("Copies", self.count_input)
        form.addRow("Direction", self.direction_input)
        layout.addLayout(form)

    def values(self) -> tuple[float, int, tuple[float, float]]:
        vectors = {"Right": (1.0, 0.0), "Left": (-1.0, 0.0), "Up": (0.0, 1.0), "Down": (0.0, -1.0)}
        return (
            self.distance_input.value(),
            self.count_input.value(),
            vectors[self.direction_input.currentText()],
        )


__all__ = ["MultiPasteDialog"]
