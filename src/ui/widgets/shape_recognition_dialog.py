"""Non-destructive confirmation for imported parametric-shape recognition."""

from __future__ import annotations

from collections import Counter

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from src.backend.cad.recognition import RecognizedShape


class ShapeRecognitionDialog(QDialog):
    def __init__(self, shapes: list[RecognizedShape], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert Imported Shapes")
        counts = Counter(shape.kind for shape in shapes)
        summary = ", ".join(f"{count} {kind}{'' if count == 1 else 's'}" for kind, count in counts.items())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Found {len(shapes)} editable parametric shapes ({summary}). Convert them?"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Convert")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


__all__ = ["ShapeRecognitionDialog"]
