"""Dialog for placing text on the canvas."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QWidget,
)


class AddTextDialog(QDialog):
    """Collects text, font family, height, and style for canvas text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Text")
        form = QFormLayout(self)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("Text to place…")
        form.addRow("Text", self._text_edit)

        self._font_combo = QFontComboBox()
        form.addRow("Font", self._font_combo)

        self._height_spin = QDoubleSpinBox()
        self._height_spin.setRange(0.5, 1000.0)
        self._height_spin.setValue(10.0)
        self._height_spin.setSuffix(" mm")
        self._height_spin.setDecimals(1)
        form.addRow("Height", self._height_spin)

        style_row = QHBoxLayout()
        self._bold_cb = QCheckBox("Bold")
        self._italic_cb = QCheckBox("Italic")
        style_row.addWidget(self._bold_cb)
        style_row.addWidget(self._italic_cb)
        style_row.addStretch()
        form.addRow("Style", style_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._text_edit.setFocus()

    def values(self) -> dict:
        return {
            "text": self._text_edit.text(),
            "family": self._font_combo.currentFont().family(),
            "height_mm": float(self._height_spin.value()),
            "bold": self._bold_cb.isChecked(),
            "italic": self._italic_cb.isChecked(),
        }
