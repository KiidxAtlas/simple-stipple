"""Dialog focus lifecycle and keyboard-focus helpers."""

from __future__ import annotations

import platform as _platform
import weakref

from PySide6.QtCore import (
    QEvent,
    QObject,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QWidget,
)

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


def install_dialog_focus_lifecycle(
    dialog: QWidget,
    initial_focus: QWidget | None = None,
    invoker: QWidget | None = None,
) -> None:
    """Set logical initial focus and restore focus after a native modal closes.

    Qt already traps focus and maps Escape to ``reject`` for modal QDialogs;
    this helper supplies the two lifecycle pieces Qt cannot infer. Weak
    references avoid extending QWidget wrapper lifetimes during shutdown.
    """
    initial_ref = weakref.ref(initial_focus) if initial_focus is not None else None
    focused = invoker or QApplication.focusWidget()
    invoker_ref = weakref.ref(focused) if focused is not None else None

    def focus_initial() -> None:
        target = initial_ref() if initial_ref is not None else None
        try:
            if target is not None and target.isEnabled() and not target.isHidden():
                target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except RuntimeError:
            # The dialog can be destroyed before the queued callback runs.
            return

    def restore_invoker(*_args) -> None:
        target = invoker_ref() if invoker_ref is not None else None
        try:
            if target is not None and target.isEnabled() and not target.isHidden():
                target.window().activateWindow()
                target.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            return

    QTimer.singleShot(0, focus_initial)
    finished = getattr(dialog, "finished", None)
    if finished is not None:
        finished.connect(lambda *_args: QTimer.singleShot(0, restore_invoker))


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

    def eventFilter(self, obj, event) -> bool:
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


# ══════════════════════════════════════════════════════════════════════════
# Layout helpers
# ══════════════════════════════════════════════════════════════════════════
