"""Input validation and widget-style feedback helpers."""

from __future__ import annotations

import math
import platform as _platform
from collections.abc import Callable

from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QLineEdit,
    QWidget,
)

from simple_stipple.ui.style.theme import (
    STATUS_ERR,
)

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


def announce_accessible(widget: QWidget, *, urgent: bool = False) -> None:
    """Notify assistive technology after a user-visible status change."""
    event_type = (
        QAccessible.Event.Alert if urgent else QAccessible.Event.DescriptionChanged
    )
    QAccessible.updateAccessibility(QAccessibleEvent(widget, event_type))



def parse_float_field(
    text: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_empty: bool = False,
) -> float | None:
    """Parse a float from text with optional range validation.

    Returns *None* when *allow_empty* is True and *text* is blank.
    Raises ``ValueError`` with a human-readable message on failure.
    """
    text = text.strip()
    if not text:
        if allow_empty:
            return None
        raise ValueError("Value is required.")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError("Value must be a number.") from exc
    if not math.isfinite(value):
        raise ValueError("Value must be a finite number.")
    if minimum is not None and value < minimum:
        raise ValueError(f"Value must be at least {minimum:g}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"Value must be at most {maximum:g}.")
    return value


def parse_float_field_with_feedback(
    entry: QLineEdit,
    label: str,
    status_callback: Callable[[str, str], None],
    **kw,
) -> float | None:
    """Parse a float from a line edit and surface validation feedback."""
    try:
        value = parse_float_field(entry.text(), **kw)
    except ValueError as exc:
        message = f"{label} {exc}"
        set_line_edit_error(entry, message)
        status_callback(message, STATUS_ERR)
        raise ValueError(message) from exc
    clear_line_edit_error(entry)
    return value


def refresh_style(widget: QWidget) -> None:
    """Force Qt to re-evaluate the stylesheet for *widget*."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_line_edit_error(widget, message: str) -> None:
    """Highlight a line edit and attach a validation message."""
    widget.setProperty("error", True)
    refresh_style(widget)
    widget.setToolTip(message)


def clear_line_edit_error(widget) -> None:
    """Clear validation styling from a line edit."""
    widget.setProperty("error", False)
    refresh_style(widget)
    widget.setToolTip("")

