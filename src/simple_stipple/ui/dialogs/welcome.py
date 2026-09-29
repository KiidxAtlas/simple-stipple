"""First-run welcome: pick a starting task and jump to the page that does it."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# (choice id, button text, what the page does). Page choices use page ids.
WELCOME_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("draft", "Draw or import", "Draft — draw shapes, or import DXF/SVG geometry to edit."),
    ("pattern", "Make a pattern", "Pattern — fill an outline with a laser-engraving pattern."),
    ("trace", "Trace an image", "Trace — turn a photo or scan into vector outlines."),
    ("convert", "Convert files", "Convert — convert or repair FVI, DXF, and SVG files."),
)
MANUAL_CHOICE = "manual"


class WelcomeDialog(QDialog):
    """Offer the four workflows plus the manual; ``choice`` holds the pick."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Simple Stipple")
        self.choice: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("What would you like to do first?")
        title.setProperty("role", "dialog-title")
        layout.addWidget(title)
        intro = QLabel(
            "Each task has its own page in the tab bar. You can switch pages at any time; "
            "a saved workspace keeps all of them together."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "dialog-message")
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for row, (choice, text, description) in enumerate(WELCOME_CHOICES):
            button = QPushButton(text)
            button.setAutoDefault(row == 0)
            button.setAccessibleDescription(description)
            button.clicked.connect(lambda _checked=False, value=choice: self._choose(value))
            grid.addWidget(button, row, 0)
            caption = QLabel(description)
            caption.setWordWrap(True)
            caption.setBuddy(button)
            grid.addWidget(caption, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox()
        manual = buttons.addButton("Open the Manual", QDialogButtonBox.ButtonRole.HelpRole)
        manual.setAutoDefault(False)
        manual.clicked.connect(lambda: self._choose(MANUAL_CHOICE))
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.setAutoDefault(False)
        close.setToolTip("Close; reopen any time from Help → Welcome…")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()
