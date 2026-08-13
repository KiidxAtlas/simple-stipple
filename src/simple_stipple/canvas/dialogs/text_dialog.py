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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from simple_stipple.canvas.operations.text import install_font_file, load_user_fonts, user_fonts_dir
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.units import from_display, to_display
from simple_stipple.ui.components.units import suffix as unit_suffix


class AddTextDialog(QDialog):
    """Collects text, font family, height, and style for canvas text.

    Shows a live preview in the chosen font, and can import .ttf/.otf
    files — imported fonts are copied to the user fonts folder so they
    stay available in future sessions.
    """

    def __init__(self, parent: QWidget | None = None, unit: str = "mm") -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Text")
        self.setMinimumWidth(420)
        self._unit = unit if unit in ("mm", "in") else "mm"
        # Make any fonts previously dropped into the fonts folder available.
        load_user_fonts()

        form = QFormLayout(self)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText("Text to place… (Enter for a new line)")
        self._text_edit.setTabChangesFocus(True)
        self._text_edit.setFixedHeight(70)
        form.addRow("Text", self._text_edit)
        # (see set_values for prefilled editing of existing text)

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
        self._height_spin.setRange(to_display(0.5, self._unit), to_display(1000.0, self._unit))
        self._height_spin.setValue(to_display(10.0, self._unit))
        self._height_spin.setSuffix(f" {unit_suffix(self._unit)}")
        self._height_spin.setDecimals(1 if self._unit == "mm" else 3)
        form.addRow("Height", self._height_spin)

        style_row = QHBoxLayout()
        self._bold_cb = QCheckBox("Bold")
        self._italic_cb = QCheckBox("Italic")
        style_row.addWidget(self._bold_cb)
        style_row.addWidget(self._italic_cb)
        style_row.addStretch()
        form.addRow("Style", style_row)

        # Live preview in the selected font/style. The font must be set via
        # a per-widget stylesheet: the app-wide QSS declares a global
        # font-family, and Qt stylesheets override setFont(), so a plain
        # setFont() here would be silently ignored.
        self._preview = QLabel("Preview")
        self._preview.setProperty("role", "text-preview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(64)
        form.addRow(self._preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)

        for signal in (
            self._text_edit.textChanged,
            self._font_combo.currentFontChanged,
            self._bold_cb.toggled,
            self._italic_cb.toggled,
        ):
            signal.connect(self._update_preview)
        self._text_edit.textChanged.connect(self._update_ok_enabled)
        self._update_preview()
        self._update_ok_enabled()
        install_dialog_focus_lifecycle(self, self._text_edit)

    def _update_ok_enabled(self) -> None:
        # Both add and edit silently no-op on empty text otherwise (edit
        # does so with no message at all) — block it at the source instead.
        self._ok_btn.setEnabled(bool(self._text_edit.toPlainText().strip()))

    def _update_preview(self, *_args) -> None:
        family = self._font_combo.currentFont().family().replace('"', "")
        weight = "bold" if self._bold_cb.isChecked() else "normal"
        style = "italic" if self._italic_cb.isChecked() else "normal"
        # Font selection is document data, not a theme decision. Keep only
        # those dynamic properties local; the preview's surface and type
        # scale are defined by its semantic QSS role.
        self._preview.setStyleSheet(
            f'font-family: "{family}"; font-weight: {weight}; font-style: {style};'
        )
        self._preview.setText(self._text_edit.toPlainText() or "Preview")

    def set_values(self, values: dict) -> None:
        """Prefill the dialog for editing an existing text entity."""
        self.setWindowTitle("Edit Text")
        self._text_edit.setPlainText(str(values.get("text", "")))
        family = str(values.get("family", ""))
        if family:
            from PySide6.QtGui import QFont

            self._font_combo.setCurrentFont(QFont(family))
        try:
            self._height_spin.setValue(to_display(float(values.get("height_mm", 10.0)), self._unit))
        except (TypeError, ValueError):
            pass
        self._bold_cb.setChecked(bool(values.get("bold", False)))
        self._italic_cb.setChecked(bool(values.get("italic", False)))

    def _import_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Font",
            "",
            "Font files (*.ttf *.otf *.ttc)",
        )
        if not path:
            return
        family = install_font_file(path)
        if family is None:
            QMessageBox.warning(self, "Import Font", "Could not load that font file.")
            return
        self._font_combo.setCurrentFont(QFont(family))
        self._update_preview()

    def values(self) -> dict:
        return {
            "text": self._text_edit.toPlainText(),
            "family": self._font_combo.currentFont().family(),
            "height_mm": from_display(float(self._height_spin.value()), self._unit),
            "bold": self._bold_cb.isChecked(),
            "italic": self._italic_cb.isChecked(),
        }
