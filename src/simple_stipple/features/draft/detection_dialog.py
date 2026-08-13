"""Non-destructive confirmation for imported parametric-shape detection."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from simple_stipple.core.cad.detection import DetectedShape


class ShapeDetectionDialog(QDialog):
    def __init__(self, shapes: list[DetectedShape], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert Imported Shapes")
        self.resize(640, 420)
        self.setMinimumSize(520, 320)
        counts = Counter(shape.kind for shape in shapes)
        summary = ", ".join(
            f"{count} {kind}{'' if count == 1 else 's'}" for kind, count in counts.items()
        )
        layout = QVBoxLayout(self)
        self._shapes = list(shapes)
        heading = QLabel(
            f"Found {len(shapes)} editable parametric shapes ({summary}).\n"
            "Converting makes their dimensions editable while preserving the imported geometry."
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        results = QListWidget()
        for index, shape in enumerate(shapes, start=1):
            item = QListWidgetItem(
                f"{index}. {shape.kind.title()} · {shape.confidence * 100:.0f}% confidence"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            results.addItem(item)
        self._results = results
        layout.addWidget(results, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Convert")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_indices(self) -> list[int]:
        """Return only candidates explicitly approved by the user."""
        return [
            index
            for index in range(self._results.count())
            if self._results.item(index).checkState() == Qt.CheckState.Checked
        ]


__all__ = ["ShapeDetectionDialog"]
