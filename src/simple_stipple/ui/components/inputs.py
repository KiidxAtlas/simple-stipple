"""Reusable action buttons and input factories."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import (
    QTimer,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolButton,
    QWidget,
)

from .feedback import refresh_style

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class ActionButton(QPushButton):
    """One semantic action hierarchy shared across all application surfaces."""

    def __init__(
        self,
        text: str,
        *,
        kind: str = "secondary",
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.set_kind(kind)
        self.setAccessibleName(text.replace("…", ""))
        if tooltip:
            self.setToolTip(tooltip)

    def set_kind(self, kind: str) -> None:
        if kind not in {"primary", "secondary", "danger", "ghost"}:
            raise ValueError(f"Unsupported action kind: {kind}")
        self.setProperty("role", kind)
        refresh_style(self)


class NoWheelSlider(QSlider):
    """Leave wheel scrolling to the inspector containing this slider."""

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Ignoring lets Qt propagate the wheel event to the parent scroll area.
        event.ignore()


def primary_button(text: str, *, height: int = 34, tooltip: str = "") -> QPushButton:
    """A primary call-to-action button (e.g. "Export DXF", "Push")."""
    btn = ActionButton(text, kind="primary", tooltip=tooltip)
    btn.setMinimumHeight(height)
    return btn


# ══════════════════════════════════════════════════════════════════════════
# Inputs
# ══════════════════════════════════════════════════════════════════════════


def browse_row(
    parent_layout,
    *,
    heading: str = "",
    placeholder: str = "",
    tooltip: str = "",
    btn_label: str = "Browse",
    btn_width: int | None = 70,
    btn_tooltip: str = "",
    on_browse,
) -> QLineEdit:
    """Add an optional standard section label, then a line-edit + Browse-button row
    to ``parent_layout``. Returns the line edit."""
    lbl = None
    if heading:
        lbl = QLabel(heading)
        lbl.setProperty("role", "section-label")
        parent_layout.addWidget(lbl)
    row = QHBoxLayout()
    edit = QLineEdit()
    edit.setAccessibleName(heading or placeholder or "File path")
    if lbl is not None:
        lbl.setBuddy(edit)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    if tooltip:
        edit.setToolTip(tooltip)
    btn = QPushButton(btn_label)
    btn.setAccessibleName(f"{btn_label} {heading or 'file'}")
    if btn_width is not None:
        btn.setMinimumWidth(btn_width)
    if btn_tooltip:
        btn.setToolTip(btn_tooltip)
    btn.clicked.connect(on_browse)
    row.addWidget(edit, stretch=1)
    row.addWidget(btn)
    parent_layout.addLayout(row)
    return edit


def make_resettable_line_edit(edit: QLineEdit, default: str) -> QLineEdit:
    """Make the trailing X restore a required field's declared default."""
    edit.setProperty("defaultValue", str(default))
    edit.setClearButtonEnabled(True)
    clear_button = next(iter(edit.findChildren(QToolButton)), None)
    if clear_button is not None:
        clear_button.setToolTip(f"Reset to default ({default})")

        def restore_default() -> None:
            # Qt clears first; restore on the next event-loop turn. Ordinary
            # keyboard editing remains untouched, including transient blanks.
            QTimer.singleShot(0, lambda: edit.setText(str(default)) if not edit.text() else None)

        clear_button.clicked.connect(restore_default)
    return edit
