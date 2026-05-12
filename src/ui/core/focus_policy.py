"""Reusable keyboard focus policies for line-edit heavy UIs."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget


def blur_focused_line_edit(
    focus_target: QWidget,
    *,
    within: QWidget | None = None,
) -> bool:
    """Blur the active line edit and move focus to ``focus_target``.

    When ``within`` is provided, only line edits inside that container are handled.
    """
    fw = QApplication.focusWidget()
    if not isinstance(fw, QLineEdit):
        return False
    if within is not None and fw is not within and not within.isAncestorOf(fw):
        return False
    fw.clearFocus()
    focus_target.setFocus()
    return True


class EscapeBlurFilter(QObject):
    """Event filter that maps Esc to blur-focused-line-edit behavior."""

    def __init__(
        self,
        focus_target: QWidget,
        *,
        within: QWidget | None = None,
    ) -> None:
        super().__init__(focus_target)
        self._focus_target = focus_target
        self._within = within

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and isinstance(obj, QLineEdit)
        ):
            return blur_focused_line_edit(
                self._focus_target,
                within=self._within,
            )
        return False
