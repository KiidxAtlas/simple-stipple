"""Collapsible content section components."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.ui.style.theme import (
    icon_path,
)

from .feedback import refresh_style
from .tokens import MOTION_DURATION_MS

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class CollapsibleSection(QFrame):
    """Expandable/collapsible content section for dense sidebars.

    Pass ``collapsible=False`` for a section that must always stay visible
    (e.g. a primary action shouldn't be hidden behind a collapse toggle) but
    should still match the collapsible sections' chrome (card background,
    bold title) rather than sitting at a different visual level.

    Optional ``subtitle`` displays a one-line state summary under the title
    (e.g. "Honeycomb · 1.2 mm") so users can see the active config without
    expanding the section. Update via :meth:`set_subtitle`.
    """

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        subtitle: str = "",
        collapsible: bool = True,
    ):
        super().__init__()
        self._title = title
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "collapsible")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._toggle: QToolButton | QLabel
        if collapsible:
            self._toggle = QToolButton()
            self._toggle.setAccessibleName(f"{title} section")
            self._toggle.setAccessibleDescription("Expand or collapse this group of controls")
            self._toggle.setProperty("role", "collapsible-toggle")
            self._toggle.setText(title)
            self._toggle.setIcon(
                QIcon(str(icon_path("chevron_down.svg" if expanded else "chevron_right.svg")))
            )
            self._toggle.setCheckable(True)
            self._toggle.setChecked(expanded)
            self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._toggle.clicked.connect(self._on_toggled)
        else:
            self._toggle = QLabel(title)
            self._toggle.setProperty("role", "collapsible-toggle-static")
        layout.addWidget(self._toggle)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setProperty("role", "section-subtitle")
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._subtitle)

        self._content = content
        self._content.setVisible(expanded or not collapsible)
        layout.addWidget(self._content)
        self._motion: QPropertyAnimation | None = None

    def _on_toggled(self, checked: bool) -> None:
        if not isinstance(self._toggle, QToolButton):
            return
        self._toggle.setText(self._title)
        self._toggle.setIcon(
            QIcon(str(icon_path("chevron_down.svg" if checked else "chevron_right.svg")))
        )
        app = QApplication.instance()
        reduced_motion = bool(app and app.property("reducedMotion"))
        if reduced_motion or not self.isVisible():
            self._content.setMaximumHeight(16777215)
            self._content.setVisible(checked)
            self.adjustSize()
            return
        if self._motion is not None:
            self._motion.stop()
        target = max(1, self._content.sizeHint().height())
        self._content.setVisible(True)
        self._motion = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._motion.setDuration(MOTION_DURATION_MS)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.setStartValue(0 if checked else max(1, self._content.height()))
        self._motion.setEndValue(target if checked else 0)

        def finish() -> None:
            self._content.setVisible(checked)
            self._content.setMaximumHeight(16777215 if checked else 0)
            self.adjustSize()

        self._motion.finished.connect(finish)
        self._motion.start()

    def set_expanded(self, expanded: bool) -> None:
        """Set disclosure state without reaching into the header widget."""
        if isinstance(self._toggle, QToolButton):
            self._toggle.setChecked(expanded)
            self._on_toggled(expanded)

    def is_expanded(self) -> bool:
        return not isinstance(self._toggle, QToolButton) or self._toggle.isChecked()

    def set_subtitle(self, text: str, *, dim: bool = False) -> None:
        """Update the one-line state summary shown under the title.

        Pass ``dim=True`` to render the subtitle in a more muted color
        (used to indicate the section's feature is currently disabled).
        """
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))
        self._subtitle.setProperty("dim", "true" if dim else "")
        refresh_style(self._subtitle)


def collapsible_content_widget(*, spacing: int = 8) -> tuple[QWidget, QVBoxLayout]:
    """A bare ``QWidget`` + zero-margin ``QVBoxLayout``, ready to populate
    before wrapping in a :class:`CollapsibleSection`."""
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return content, layout


# ══════════════════════════════════════════════════════════════════════════
# Icons — hand-drawn vector icons for plain QPushButtons (toolbar/header
# glyph buttons), rendered with QPainter instead of Unicode symbol characters.
#
# Unicode glyphs like "⚙" (gear) or "⌘" depend on the platform's installed
# fonts having that exact codepoint; when they don't, Qt falls back to a
# generic/wrong glyph (e.g. the settings gear rendering as a plain circle).
# Drawing the icon ourselves guarantees it looks the same everywhere.
# ══════════════════════════════════════════════════════════════════════════
