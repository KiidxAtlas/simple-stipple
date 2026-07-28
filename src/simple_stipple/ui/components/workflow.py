"""Workflow progress and status presentation components."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from simple_stipple.ui.notifications import record_notification
from simple_stipple.ui.style.theme import (
    STATUS_ERR,
    STATUS_NEUTRAL,
    STATUS_OK,
    STATUS_WARN,
    icon_path,
)

from .feedback import announce_accessible, refresh_style
from .inputs import ActionButton

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class WorkflowStepper(QFrame):
    """Responsive workflow progress with explicit current/completed states."""

    stepRequested = Signal(int)

    def __init__(self, steps: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = steps
        self._labels: list[QToolButton] = []
        self.setProperty("surface", "panel")
        self.setProperty("role", "workflow-strip")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        for index, step in enumerate(steps):
            button = QToolButton()
            button.setText(f"{index + 1}  {step}")
            button.setProperty("role", "workflow-step")
            button.setMinimumHeight(32)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setAccessibleName(f"Step {index + 1}: {step}")
            # The strip reports progress; page navigation is handled by each page's
            # real controls. Keeping these out of the focus chain avoids promising
            # an interaction that no consumer implements.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(button)
            self._labels.append(button)
            if index < len(steps) - 1:
                arrow = QLabel()
                arrow.setPixmap(QIcon(str(icon_path("chevron_right.svg"))).pixmap(16, 16))
                arrow.setAccessibleName("Next step")
                arrow.setProperty("role", "workflow-arrow")
                layout.addWidget(arrow)
        layout.addStretch()
        self.set_current_step(0)
        self.setMaximumHeight(self.sizeHint().height())

    def set_current_step(self, index: int) -> None:
        current = max(0, min(index, len(self._labels) - 1)) if self._labels else 0
        self.set_step_states(
            [
                "current"
                if item_index == current
                else "complete"
                if item_index < current
                else "pending"
                for item_index in range(len(self._labels))
            ]
        )

    def set_step_states(
        self, states: list[str] | tuple[str, ...], reasons: dict[int, str] | None = None
    ) -> None:
        """Render independently reduced workflow states.

        Unlike a single chronological index, this can retain completed setup
        while identifying a stale or failed downstream result.
        """
        allowed = {"complete", "current", "pending", "stale", "error"}
        if len(states) != len(self._labels) or any(state not in allowed for state in states):
            raise ValueError("Workflow states must provide one valid state per step")
        reasons = reasons or {}
        for item_index, (label, state) in enumerate(zip(self._labels, states, strict=True)):
            label.setText(
                self._steps[item_index]
                if state in {"complete", "stale", "error"}
                else f"{item_index + 1}  {self._steps[item_index]}"
            )
            icon_name = (
                "check.svg"
                if state == "complete"
                else "warning.svg"
                if state in {"stale", "error"}
                else ""
            )
            label.setIcon(QIcon(str(icon_path(icon_name))) if icon_name else QIcon())
            label.setProperty("state", state)
            label.setEnabled(True)
            reason = reasons.get(item_index, "")
            label.setToolTip(reason)
            label.setAccessibleDescription(reason or f"{self._steps[item_index]} is {state}")
            refresh_style(label)


def workflow_strip(steps: tuple[str, ...]) -> WorkflowStepper:
    """Create the standard interactive page workflow stepper."""
    return WorkflowStepper(steps)


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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("Working…")
        self._bar = QProgressBar()
        self._cancel = ActionButton("Cancel")
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
    (:data:`~simple_stipple.ui.style.theme.STATUS_OK`/``STATUS_ERR``/``STATUS_WARN``/
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
