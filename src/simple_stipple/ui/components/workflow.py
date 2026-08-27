"""Workflow progress and status presentation components."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import (
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QWidget,
)

from simple_stipple.ui.components.feedback import (
    announce_accessible,
    record_notification,
    refresh_style,
)
from simple_stipple.ui.components.inputs import ActionButton
from simple_stipple.ui.style import (
    STATUS_ERR,
    STATUS_NEUTRAL,
    STATUS_OK,
    STATUS_WARN,
    icon_path,
)

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class StatusRegion(QFrame):
    """Stable-height semantic operation status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "status-region")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        self._message = QLabel("Ready")
        self._message.setWordWrap(True)
        layout.addWidget(self._icon)
        layout.addWidget(self._message, 1)
        self.setMinimumHeight(30)

    def set_status(self, message: str, tone: str = "neutral") -> None:
        previous = self._message.text()
        icon_name = {
            "success": "check.svg",
            "warn": "warning.svg",
            "danger": "warning.svg",
            "info": "info.svg",
            "neutral": "info.svg",
        }.get(tone, "info.svg")
        self._icon.setPixmap(QIcon(str(icon_path(icon_name))).pixmap(16, 16))
        self._icon.setAccessibleName(f"{tone.title()} status")
        self._message.setText(message or "Ready")
        self.setProperty("tone", tone)
        self.setAccessibleDescription(self._message.text())
        refresh_style(self)
        if self._message.text() != previous:
            announce_accessible(self, urgent=tone == "danger")
            if tone in {"success", "warn", "danger"}:
                record_notification(self._message.text())


class OperationProgress(QFrame):
    """Consistent labelled progress and cancellation surface."""

    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "operation-progress")
        self.setAccessibleName("Operation progress")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel("Working…")
        self._label.setProperty("role", "operation-progress-label")
        self._bar = QProgressBar()
        self._cancel = ActionButton("Cancel")
        self._cancel.setToolTip("Cancel the current operation")
        self._cancel.clicked.connect(self.cancelRequested)
        layout.addWidget(self._label)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._cancel)
        self.setVisible(False)

    def start(self, label: str, *, maximum: int = 0, cancellable: bool = True) -> None:
        self._label.setText(label)
        self._bar.setRange(0, maximum)
        self._bar.setValue(0)
        self._cancel.setVisible(cancellable)
        self.setVisible(True)
        self.setAccessibleDescription(label)
        announce_accessible(self)

    def set_value(self, value: int) -> None:
        self._bar.setValue(value)

    def finish(self) -> None:
        self.setVisible(False)

    def fail(self, message: str) -> None:
        """Surface a recoverable failure without leaving stale progress visible."""
        self._label.setText(message or "Operation failed")
        self.setProperty("tone", "danger")
        self._cancel.setVisible(False)
        self.setVisible(True)
        self.setAccessibleDescription(self._label.text())
        refresh_style(self)
        announce_accessible(self, urgent=True)


# ══════════════════════════════════════════════════════════════════════════
# Status labels
# ══════════════════════════════════════════════════════════════════════════


def set_status_label(
    label: QLabel,
    text: str,
    color: str = STATUS_NEUTRAL,
    *,
    hide_when_empty: bool = True,
    neutral_role: str = "status-neutral",
) -> None:
    """Set a status label's text and color→role styling.

    ``color`` is compared against the standard status colors
    (:data:`~simple_stipple.ui.style.STATUS_OK`/``STATUS_ERR``/``STATUS_WARN``/
    ``STATUS_NEUTRAL``) to pick a ``role`` property for the stylesheet. Pass
    ``hide_when_empty=False`` for labels that should stay visible with a
    blank/neutral role instead of hiding on empty text.
    """
    previous = label.text()
    if not text:
        if hide_when_empty:
            label.setVisible(False)
            return
        label.setText(text)
        label.setProperty("role", "")
        refresh_style(label)
        return
    label.setVisible(True)
    label.setText(text)
    if color == STATUS_OK:
        role = "status-ok"
    elif color == STATUS_ERR:
        role = "status-err"
    elif color == STATUS_WARN:
        role = "status-warn"
    else:
        role = neutral_role
    label.setProperty("role", role)
    label.setAccessibleDescription(text)
    refresh_style(label)
    if text != previous and role in {"status-ok", "status-err", "status-warn"}:
        announce_accessible(label, urgent=role == "status-err")
        record_notification(text)


# ══════════════════════════════════════════════════════════════════════════
# Collapsible sections
# ══════════════════════════════════════════════════════════════════════════
