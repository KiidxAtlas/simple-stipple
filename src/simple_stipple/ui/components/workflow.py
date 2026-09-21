"""Workflow progress and status presentation components."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from simple_stipple.ui.components.feedback import (
    announce_accessible,
    record_notification,
    refresh_style,
)
from simple_stipple.ui.style import (
    STATUS_ERR,
    STATUS_NEUTRAL,
    STATUS_OK,
    STATUS_WARN,
)

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
