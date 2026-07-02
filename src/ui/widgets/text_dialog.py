"""Dialog for placing text on the canvas."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.ui.canvas.text_shapes import (
    install_font_file,
    load_user_fonts,
    user_fonts_dir,
)


class AddTextDialog(QDialog):
    """Collects text, font family, height, and style for canvas text.

    Shows a live preview in the chosen font, and can import .ttf/.otf
    files — imported fonts are copied to the user fonts folder so they
    stay available in future sessions.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Text")
        self.setMinimumWidth(420)
        # Make any fonts previously dropped into the fonts folder available.
        load_user_fonts()

        form = QFormLayout(self)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("Text to place…")
        form.addRow("Text", self._text_edit)

        font_row = QHBoxLayout()
        self._font_combo = QFontComboBox()
        font_row.addWidget(self._font_combo, stretch=1)
        add_font_btn = QPushButton("Add font…")
        add_font_btn.setToolTip(
            "Import a .ttf / .otf font file. Imported fonts are copied to\n"
            f"{user_fonts_dir()}\nand stay available in future sessions."
        )
        add_font_btn.clicked.connect(self._import_font)
        font_row.addWidget(add_font_btn)
        form.addRow("Font", font_row)

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

        # Live preview in the selected font/style.
        self._preview = QLabel("Preview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(64)
        self._preview.setStyleSheet(
            "background: #10161d; border: 1px solid #2a3a44; border-radius: 4px;"
        )
        form.addRow(self._preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        for signal in (
            self._text_edit.textChanged,
            self._font_combo.currentFontChanged,
            self._bold_cb.toggled,
            self._italic_cb.toggled,
        ):
            signal.connect(self._update_preview)
        self._update_preview()
        self._text_edit.setFocus()

    def _update_preview(self, *_args) -> None:
        font = QFont(self._font_combo.currentFont().family())
        font.setPointSize(28)
        font.setBold(self._bold_cb.isChecked())
        font.setItalic(self._italic_cb.isChecked())
        self._preview.setFont(font)
        self._preview.setText(self._text_edit.text() or "Preview")

    def _import_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import font",
            "",
            "Font files (*.ttf *.otf *.ttc)",
        )
        if not path:
            return
        family = install_font_file(path)
        if family is None:
            QMessageBox.warning(
                self, "Import font", "Could not load that font file."
            )
            return
        self._font_combo.setCurrentFont(QFont(family))
        self._update_preview()

    def values(self) -> dict:
        return {
            "text": self._text_edit.text(),
            "family": self._font_combo.currentFont().family(),
            "height_mm": float(self._height_spin.value()),
            "bold": self._bold_cb.isChecked(),
            "italic": self._italic_cb.isChecked(),
        }
