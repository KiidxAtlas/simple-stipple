"""Layout helpers and widget factories for PySide6 panels."""

from __future__ import annotations

import platform as _platform
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer

# Platform modifier for human-readable shortcut hints
_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


def section_label(parent_layout, text: str) -> QLabel:
    """Compact muted section header with letter-spacing."""
    lb = QLabel(text.upper())
    lb.setProperty("role", "section-label")
    parent_layout.addWidget(lb)
    return lb


def sep(parent_layout) -> QFrame:
    """Hairline horizontal separator."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #21262d;")
    line.setFixedHeight(1)
    parent_layout.addWidget(line)
    return line


def info_chip(text: str, tone: str = "neutral") -> QLabel:
    """Small capsule label used for capabilities, state, and shortcuts."""
    chip = QLabel(text)
    chip.setProperty("role", "chip")
    chip.setProperty("tone", tone)
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return chip


def surface_frame(surface: str = "panel") -> QFrame:
    """Create a styled surface frame for sidebar or content panels."""
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.NoFrame)
    frame.setProperty("surface", surface)
    return frame


def sidebar_panel(
    content: QWidget, *, min_width: int = 340, max_width: int = 430
) -> QFrame:
    """Wrap sidebar content in a styled scrollable panel."""
    frame = surface_frame("sidebar")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    layout.addWidget(scroll)
    frame.setMinimumWidth(min_width)
    frame.setMaximumWidth(max_width)

    # The horizontal scrollbar is off, so the panel must be at least as wide
    # as the content's minimum (plus the vertical scrollbar gutter) or the
    # content gets clipped. Callers populate `content` after wrapping it, so
    # measure on the next event-loop turn, once the layout has settled.
    def _fit_width() -> None:
        gutter = scroll.verticalScrollBar().sizeHint().width() + 2
        needed = content.minimumSizeHint().width() + gutter
        frame.setMinimumWidth(max(min_width, needed))
        frame.setMaximumWidth(max(max_width, needed))

    QTimer.singleShot(0, _fit_width)
    return frame


def content_splitter(
    left: QWidget, right: QWidget, *, sizes: tuple[int, int]
) -> QSplitter:
    """Create a collapsible horizontal splitter with sensible defaults."""
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(True)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes(list(sizes))
    return splitter


def canvas_toolbar(
    on_mode,
    on_fit,
    *,
    modes: tuple[str, ...] = ("Select", "Draw", "Edit"),
    show_fit: bool = True,
    secondary_actions=None,
):
    """Compact canvas toolbar with mode toggles and optional actions."""
    shell = QWidget()
    shell_layout = QHBoxLayout(shell)
    shell_layout.setContentsMargins(0, 0, 0, 0)
    shell_layout.setSpacing(4)

    mode_buttons: dict[str, QPushButton] = {}
    mode_hints = {
        "Select": "Shortcut: S",
        "Draw": "Shortcut: D",
        "Edit": "Shortcut: E",
    }
    for mode in modes:
        btn = QPushButton(mode)
        btn.setMinimumHeight(28)
        btn.setProperty("active", mode == modes[0])
        if mode in mode_hints:
            btn.setToolTip(mode_hints[mode])
        btn.clicked.connect(lambda checked=False, m=mode: on_mode(m))
        shell_layout.addWidget(btn)
        mode_buttons[mode] = btn

    if show_fit:
        sep = QLabel("│")
        sep.setProperty("role", "toolbar-sep")
        shell_layout.addWidget(sep)

        fit_btn = QPushButton("Fit")
        fit_btn.setMinimumHeight(28)
        fit_btn.setToolTip("Fit view to content (Shortcut: F)")
        fit_btn.clicked.connect(on_fit)
        shell_layout.addWidget(fit_btn)

    if secondary_actions:
        sep2 = QLabel("│")
        sep2.setProperty("role", "toolbar-sep")
        shell_layout.addWidget(sep2)
        secondary_hints = {
            "Select All": f"Shortcut: {_KBD_MOD}+A",
            "Deselect": f"Shortcut: {_KBD_MOD}+Shift+A",
            "Delete": "Shortcut: Delete",
            "Undo": f"Shortcut: {_KBD_MOD}+Z",
            "Close": "Shortcut: Shift+C",
            "Open": "Shortcut: Shift+O",
        }
        for spec in secondary_actions:
            label, slot, role = spec if len(spec) == 3 else (*spec, None)
            btn = QPushButton(label)
            btn.setMinimumHeight(28)
            if label in secondary_hints:
                btn.setToolTip(secondary_hints[label])
            if role:
                btn.setProperty("role", role)
            btn.clicked.connect(slot)
            shell_layout.addWidget(btn)

    selection_label = QLabel("")
    selection_label.setStyleSheet("color: #8b949e; font-size: 11px;")
    selection_label.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    shell_layout.addWidget(selection_label, stretch=1)

    return shell, mode_buttons, selection_label


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
        status_callback(message, "#f85149")
        raise ValueError(message) from exc
    clear_line_edit_error(entry)
    return value


def set_line_edit_error(widget, message: str) -> None:
    """Highlight a line edit and attach a validation message."""
    widget.setProperty("error", True)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.setToolTip(message)


def clear_line_edit_error(widget) -> None:
    """Clear validation styling from a line edit."""
    widget.setProperty("error", False)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.setToolTip("")


__all__ = [
    "canvas_toolbar",
    "clear_line_edit_error",
    "content_splitter",
    "info_chip",
    "parse_float_field",
    "parse_float_field_with_feedback",
    "section_label",
    "sep",
    "set_line_edit_error",
    "sidebar_panel",
    "surface_frame",
]
