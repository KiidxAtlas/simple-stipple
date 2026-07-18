"""Shared UI building blocks used across every page: layout helpers, buttons,
inputs, collapsible sections, and status labels.

One standardized place for the small widgets/factories every page needs,
so pages don't each grow their own slightly-different copy. Merged from the
former ``ui/core/factories.py``, ``ui/core/icons.py``, ``ui/core/base_page.py``
(focus-policy helpers only — ``BasePage`` itself lives in ``ui/pages/base.py``),
and ``ui/widgets/collapsible.py``.
"""

from __future__ import annotations

import math
import platform as _platform
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.style.theme import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN
from src.ui.util import clear_recent, list_recent

# Platform modifier for human-readable shortcut hints
_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class RecentFilesButton(QPushButton):
    """Drop-down button exposing the recent-files MRU for one file kind."""

    fileSelected = Signal(str)

    def __init__(
        self,
        settings: dict,
        kind: str,
        *,
        empty_message: str = "No recent files.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Recent ▾", parent)
        self._settings = settings
        self._kind = kind
        self._empty_message = empty_message
        self.setFixedWidth(76)
        self.setToolTip("Pick from recently opened files")
        self.clicked.connect(self._open_menu)

    def _open_menu(self) -> None:
        recent = list_recent(self._settings, self._kind)
        menu = QMenu(self)
        if not recent:
            disabled = menu.addAction(self._empty_message)
            disabled.setEnabled(False)
        else:
            for path in recent:
                item = Path(path)
                label = f"{item.name}    ‹{item.parent.name or item.parent.anchor}›"
                action = menu.addAction(label)
                action.setToolTip(str(item))
                action.triggered.connect(
                    lambda _checked=False, target=path: self.fileSelected.emit(target)
                )
            menu.addSeparator()
            menu.addAction("Clear history", self._clear)
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def _clear(self) -> None:
        clear_recent(self._settings, self._kind)


# ══════════════════════════════════════════════════════════════════════════
# Keyboard-focus policy (generic Qt utility, not page-specific)
# ══════════════════════════════════════════════════════════════════════════


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


def sidebar_panel(content: QWidget, *, min_width: int = 340, max_width: int = 430) -> QFrame:
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


def content_splitter(left: QWidget, right: QWidget, *, sizes: tuple[int, int]) -> QSplitter:
    """Create a collapsible horizontal splitter with sensible defaults.

    The left pane (canvas) absorbs all extra space on resize/fullscreen;
    the right pane (sidebar) keeps its configured width instead of growing
    to fill the window.
    """
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(True)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    splitter.setSizes(list(sizes))
    return splitter


# ══════════════════════════════════════════════════════════════════════════
# Buttons
# ══════════════════════════════════════════════════════════════════════════


def primary_button(text: str, *, height: int = 34, tooltip: str = "") -> QPushButton:
    """A primary call-to-action button (e.g. "Export DXF", "Push")."""
    btn = QPushButton(text)
    btn.setMinimumHeight(height)
    btn.setProperty("role", "primary")
    if tooltip:
        btn.setToolTip(tooltip)
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
    if heading:
        lbl = QLabel(heading)
        lbl.setProperty("role", "section-label")
        parent_layout.addWidget(lbl)
    row = QHBoxLayout()
    edit = QLineEdit()
    if placeholder:
        edit.setPlaceholderText(placeholder)
    if tooltip:
        edit.setToolTip(tooltip)
    btn = QPushButton(btn_label)
    if btn_width is not None:
        btn.setFixedWidth(btn_width)
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
    (:data:`~src.ui.style.theme.STATUS_OK`/``STATUS_ERR``/``STATUS_WARN``/
    ``STATUS_NEUTRAL``) to pick a ``role`` property for the stylesheet. Pass
    ``hide_when_empty=False`` for labels that should stay visible with a
    blank/neutral role instead of hiding on empty text.
    """
    if not text:
        if hide_when_empty:
            label.setVisible(False)
            return
        label.setText(text)
        label.setProperty("role", "")
        label.style().unpolish(label)
        label.style().polish(label)
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
    label.style().unpolish(label)
    label.style().polish(label)


# ══════════════════════════════════════════════════════════════════════════
# Collapsible sections
# ══════════════════════════════════════════════════════════════════════════


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
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(2)

        self._toggle: QToolButton | QLabel
        if collapsible:
            self._toggle = QToolButton()
            self._toggle.setAccessibleName(f"{title} section")
            self._toggle.setAccessibleDescription("Expand or collapse this group of controls")
            self._toggle.setProperty("role", "collapsible-toggle")
            self._toggle.setText(f"{'▾' if expanded else '▸'}  {title}")
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

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setText(f"{'▾' if checked else '▸'}  {self._title}")
        self._content.setVisible(checked)
        self.adjustSize()

    def set_subtitle(self, text: str, *, dim: bool = False) -> None:
        """Update the one-line state summary shown under the title.

        Pass ``dim=True`` to render the subtitle in a more muted color
        (used to indicate the section's feature is currently disabled).
        """
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))
        self._subtitle.setProperty("dim", "true" if dim else "")
        self._subtitle.style().unpolish(self._subtitle)
        self._subtitle.style().polish(self._subtitle)


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


__all__ = [
    "CollapsibleSection",
    "EscapeBlurFilter",
    "RecentFilesButton",
    "blur_focused_line_edit",
    "browse_row",
    "clear_line_edit_error",
    "collapsible_content_widget",
    "content_splitter",
    "info_chip",
    "make_resettable_line_edit",
    "parse_float_field",
    "parse_float_field_with_feedback",
    "primary_button",
    "section_label",
    "sep",
    "set_line_edit_error",
    "set_status_label",
    "sidebar_panel",
    "surface_frame",
]


# Vector icon factories


def icon_from_painter(
    draw_fn: Callable[[QPainter, float, QColor], None],
    *,
    size: int = 18,
    color: str = "#e6edf3",
) -> QIcon:
    """Render ``draw_fn(painter, size, color)`` onto a transparent pixmap
    and wrap it as a QIcon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(painter, float(size), QColor(color))
    painter.end()
    return QIcon(pixmap)


def _draw_gear(painter: QPainter, size: float, color: QColor) -> None:
    cx = cy = size / 2.0
    outer_r = size * 0.44
    inner_r = size * 0.30
    hole_r = size * 0.15
    tooth_w_deg = 20.0
    teeth = 8

    path = QPainterPath()
    for k in range(teeth):
        center_deg = k * (360.0 / teeth)
        a0 = math.radians(center_deg - tooth_w_deg / 2.0)
        a1 = math.radians(center_deg + tooth_w_deg / 2.0)
        gap_deg = 360.0 / teeth - tooth_w_deg
        b0 = math.radians(center_deg + tooth_w_deg / 2.0)
        b1 = math.radians(center_deg + tooth_w_deg / 2.0 + gap_deg)

        p_a0 = QPointF(cx + math.cos(a0) * outer_r, cy + math.sin(a0) * outer_r)
        p_a1 = QPointF(cx + math.cos(a1) * outer_r, cy + math.sin(a1) * outer_r)
        p_b0 = QPointF(cx + math.cos(b0) * inner_r, cy + math.sin(b0) * inner_r)
        p_b1 = QPointF(cx + math.cos(b1) * inner_r, cy + math.sin(b1) * inner_r)

        if k == 0:
            path.moveTo(p_a0)
        else:
            path.lineTo(p_a0)
        path.lineTo(p_a1)
        path.lineTo(p_b0)
        path.lineTo(p_b1)
    path.closeSubpath()

    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, cy), hole_r, hole_r)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path.subtracted(hole))


def _draw_download(painter: QPainter, size: float, color: QColor) -> None:
    cx = size / 2.0
    top = size * 0.22
    shaft_bottom = size * 0.58
    pen = QPen(color, max(1.4, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(cx, top), QPointF(cx, shaft_bottom))

    head = size * 0.18
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            QPointF(cx - head, shaft_bottom - head * 0.6),
            QPointF(cx + head, shaft_bottom - head * 0.6),
            QPointF(cx, shaft_bottom + head * 0.6),
        ]
    )

    tray_y = size * 0.78
    tray_pen = QPen(color, max(1.4, size * 0.09))
    tray_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(tray_pen)
    painter.drawLine(QPointF(size * 0.24, tray_y), QPointF(size * 0.76, tray_y))


def gear_icon(*, size: int = 18, color: str = "#e6edf3") -> QIcon:
    """Settings gear."""
    return icon_from_painter(_draw_gear, size=size, color=color)


def download_icon(*, size: int = 18, color: str = "#e6edf3") -> QIcon:
    """Download/"check for updates" arrow-into-tray."""
    return icon_from_painter(_draw_download, size=size, color=color)


# Draw-sidebar icon set — ported from the old ToolPickerDialog.ToolButton
# paintEvent (ellipse/rect/path drawing keyed on a normalized icon rect) plus
# new glyphs for the snapping/mode toggles that sidebar didn't previously
# expose. All follow the same (painter, size, color) -> None contract as
# _draw_gear/_draw_download above.


def _icon_rect(size: float, inset_frac: float = 0.16) -> QRectF:
    inset = size * inset_frac
    return QRectF(inset, inset, size - 2 * inset, size - 2 * inset)


def _line_pen(color: QColor, size: float) -> QPen:
    pen = QPen(color, max(1.2, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_polyline_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(r.center().x() - size * 0.05, r.center().y())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)


def _draw_spline_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.cubicTo(
        r.left() + r.width() * 0.2,
        r.top(),
        r.right() - r.width() * 0.2,
        r.bottom(),
        r.right(),
        r.top(),
    )
    painter.drawPath(path)


def _draw_arc_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.quadTo(r.center().x(), r.top() - size * 0.05, r.right(), r.bottom())
    painter.drawPath(path)


def _draw_bezier_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    p0 = QPointF(r.left(), r.bottom())
    p1 = QPointF(r.right(), r.top())
    # Handles offset only partway toward the corners (not a full-height
    # span) so they read as short control stubs, not a second curve.
    c1 = QPointF(r.left() + r.width() * 0.35, r.bottom() - r.height() * 0.75)
    c2 = QPointF(r.right() - r.width() * 0.35, r.top() + r.height() * 0.75)

    handle_pen = QPen(color.darker(160), max(0.8, size * 0.025))
    handle_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(handle_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(p0, c1)
    painter.drawLine(p1, c2)

    painter.setPen(_line_pen(color, size))
    path = QPainterPath()
    path.moveTo(p0)
    path.cubicTo(c1, c2, p1)
    painter.drawPath(path)

    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    for pt in (p0, p1):
        painter.drawEllipse(pt, size * 0.055, size * 0.055)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(handle_pen)
    for pt in (c1, c2):
        painter.drawEllipse(pt, size * 0.03, size * 0.03)


def _draw_rectangle_icon(painter: QPainter, size: float, color: QColor) -> None:
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(_icon_rect(size))


def _draw_rounded_rectangle_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Rounded rectangle matching the canvas primitive, not its sharp sibling."""
    rect = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = min(rect.width(), rect.height()) * 0.18
    painter.drawRoundedRect(rect, radius, radius)


def _draw_slot_icon(painter: QPainter, size: float, color: QColor) -> None:
    # Deliberately non-square (wide, short) — a stadium/capsule shape reads
    # as "slot" only when it's clearly elongated, not a circle.
    square = _icon_rect(size)
    height = square.height() * 0.5
    r = QRectF(square.left(), square.center().y() - height / 2.0, square.width(), height)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = height / 2.0
    painter.drawRoundedRect(r, radius, radius)


def _draw_circle_icon(painter: QPainter, size: float, color: QColor) -> None:
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(_icon_rect(size))


def _draw_ellipse_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(r.adjusted(0, r.height() * 0.16, 0, -r.height() * 0.16))


def _draw_polygon_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    cx, cy = r.center().x(), r.center().y()
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    n = 6
    for i in range(n):
        ang = math.radians(-90 + i * 360.0 / n)
        pt = QPointF(cx + math.cos(ang) * r.width() / 2.0, cy + math.sin(ang) * r.height() / 2.0)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_star_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Five-point star matching the default Star canvas primitive."""
    r = _icon_rect(size)
    cx, cy = r.center().x(), r.center().y()
    outer = min(r.width(), r.height()) / 2.0
    inner = outer * 0.45
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    for i in range(10):
        angle = math.radians(-90.0 + i * 36.0)
        radius = outer if i % 2 == 0 else inner
        point = QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_text_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
    painter.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()))


def _draw_grid_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    dot_r = size * 0.06
    for gx in (r.left(), r.center().x(), r.right()):
        for gy in (r.top(), r.center().y(), r.bottom()):
            painter.drawEllipse(QPointF(gx, gy), dot_r, dot_r)


def _draw_angle_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    origin = QPointF(r.left(), r.bottom())
    painter.setPen(_line_pen(color, size))
    painter.drawLine(origin, QPointF(r.right(), r.bottom()))
    painter.drawLine(origin, QPointF(r.right(), r.top()))
    arc_r = r.width() * 0.4
    arc_rect = QRectF(origin.x() - arc_r, origin.y() - arc_r, arc_r * 2, arc_r * 2)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(arc_rect, 0, 45 * 16)


def _draw_constraint_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Axis-lock crosshair — H/V/45 draw-constraint lock (distinct from the
    angle-snap protractor glyph)."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    cx, cy = r.center().x(), r.center().y()
    painter.drawLine(QPointF(r.left(), cy), QPointF(r.right(), cy))
    painter.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))
    dashed = QPen(color.darker(160), max(0.8, size * 0.05))
    dashed.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(dashed)
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))


def _draw_vertex_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(r.right(), r.bottom()), size * 0.09, size * 0.09)


def _draw_edge_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    p0 = QPointF(r.left(), r.bottom())
    p1 = QPointF(r.right(), r.top())
    painter.drawLine(p0, p1)
    mid = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(mid, size * 0.09, size * 0.09)


def _draw_master_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Horseshoe magnet — the "all snapping" master toggle."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(r.left(), r.top(), r.width(), r.height() * 1.3)
    painter.drawArc(rect, 0, 180 * 16)
    leg_bottom = r.top() + r.height() * 0.65
    painter.drawLine(QPointF(r.left(), r.top() + r.height() * 0.35), QPointF(r.left(), leg_bottom))
    painter.drawLine(
        QPointF(r.right(), r.top() + r.height() * 0.35), QPointF(r.right(), leg_bottom)
    )
    tip_pen = QPen(color, max(2.0, size * 0.16))
    tip_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(tip_pen)
    painter.drawLine(QPointF(r.left(), leg_bottom), QPointF(r.left(), leg_bottom + size * 0.08))
    painter.drawLine(QPointF(r.right(), leg_bottom), QPointF(r.right(), leg_bottom + size * 0.08))


def _draw_split_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Scissors — auto-split on draw."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(r.left(), r.top()), size * 0.07, size * 0.07)
    painter.drawEllipse(QPointF(r.left(), r.bottom()), size * 0.07, size * 0.07)


def _draw_construction_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Dashed triangle — construction (reference-only) geometry."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.center().x(), r.top())
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.left(), r.bottom())
    path.closeSubpath()
    painter.drawPath(path)


def _draw_dimension_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    y = r.center().y()
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
    painter.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
    arrow = size * 0.08
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            QPointF(r.left(), y),
            QPointF(r.left() + arrow, y - arrow * 0.6),
            QPointF(r.left() + arrow, y + arrow * 0.6),
        ]
    )
    painter.drawPolygon(
        [
            QPointF(r.right(), y),
            QPointF(r.right() - arrow, y - arrow * 0.6),
            QPointF(r.right() - arrow, y + arrow * 0.6),
        ]
    )


def _draw_measure_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Ruler with tick marks."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(r)
    ticks = 4
    for i in range(1, ticks):
        x = r.left() + r.width() * i / ticks
        painter.drawLine(QPointF(x, r.top()), QPointF(x, r.top() + r.height() * 0.4))


def _draw_finish_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Checkmark — finish open polyline."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    path = QPainterPath()
    path.moveTo(r.left(), r.center().y())
    path.lineTo(r.left() + r.width() * 0.38, r.bottom())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)


def _draw_close_path_icon(painter: QPainter, size: float, color: QColor) -> None:
    """An open ring (gap at the top) — distinct from the plain closed
    circle used for the Circle draw tool, reading as "not yet closed"."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    gap_deg = 50
    painter.drawArc(r, (90 + gap_deg // 2) * 16, (360 - gap_deg) * 16)


def _draw_undo_point_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(r.left(), r.top(), r.width(), r.height())
    painter.drawArc(rect, 20 * 16, 300 * 16)
    tip = QPointF(r.left(), r.top() + r.height() * 0.15)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            tip,
            QPointF(tip.x() + size * 0.14, tip.y() - size * 0.03),
            QPointF(tip.x() + size * 0.02, tip.y() + size * 0.14),
        ]
    )


def _draw_cancel_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))


def _draw_select_arrow_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Cursor/selection arrow — back to select mode."""
    r = _icon_rect(size, inset_frac=0.2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(
        [
            QPointF(r.left(), r.top()),
            QPointF(r.left(), r.bottom()),
            QPointF(r.left() + r.width() * 0.55, r.bottom() - r.height() * 0.32),
            QPointF(r.left() + r.width() * 0.78, r.bottom()),
            QPointF(r.left() + r.width() * 0.92, r.bottom() - r.height() * 0.12),
            QPointF(r.left() + r.width() * 0.65, r.bottom() - r.height() * 0.42),
            QPointF(r.right(), r.bottom() - r.height() * 0.42),
        ]
    )


def _draw_smooth_chaikin_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A sharp corner with a small chamfer cut across it — Chaikin's
    corner-cutting, in one glyph."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    apex = QPointF(r.center().x(), r.top())
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(apex)
    path.lineTo(r.right(), r.bottom())
    painter.drawPath(path)
    cut_pen = QPen(color.lighter(140), max(0.8, size * 0.05))
    cut_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(cut_pen)
    span = size * 0.14
    painter.drawLine(
        QPointF(apex.x() - span, apex.y() + span * 0.8),
        QPointF(apex.x() + span, apex.y() + span * 0.8),
    )


def _draw_smooth_gaussian_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A bell curve — Gaussian neighbor-averaging."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
    left = QPointF(r.left(), r.bottom())
    peak = QPointF(r.center().x(), r.top())
    right = QPointF(r.right(), r.bottom())
    # Control points pulled in horizontally *and* up, so the rise curves
    # gently into a rounded peak instead of meeting at a sharp point.
    path = QPainterPath()
    path.moveTo(left)
    path.cubicTo(
        QPointF(left.x() + r.width() * 0.32, left.y()),
        QPointF(peak.x() - r.width() * 0.22, peak.y() + r.height() * 0.12),
        peak,
    )
    path.cubicTo(
        QPointF(peak.x() + r.width() * 0.22, peak.y() + r.height() * 0.12),
        QPointF(right.x() - r.width() * 0.32, right.y()),
        right,
    )
    painter.drawPath(path)


def _draw_smooth_catmull_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A smooth curve threaded exactly through a few dots — distinct from
    the plain "spline" glyph, which has no through-points, matching
    Catmull-Rom's interpolating (not approximating) behavior."""
    r = _icon_rect(size)
    pts = [
        QPointF(r.left(), r.bottom()),
        QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.25),
        QPointF(r.left() + r.width() * 0.65, r.bottom() - r.height() * 0.25),
        QPointF(r.right(), r.top()),
    ]
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(pts[0])
    path.cubicTo(pts[1], pts[1], pts[2])
    path.cubicTo(pts[2], pts[2], pts[3])
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for pt in pts:
        painter.drawEllipse(pt, size * 0.055, size * 0.055)


_ICON_FACTORIES: dict[str, Callable[[QPainter, float, QColor], None]] = {
    "polyline": _draw_polyline_icon,
    "spline": _draw_spline_icon,
    "arc": _draw_arc_icon,
    "bezier": _draw_bezier_icon,
    "rectangle": _draw_rectangle_icon,
    "rounded_rectangle": _draw_rounded_rectangle_icon,
    "slot": _draw_slot_icon,
    "circle": _draw_circle_icon,
    "ellipse": _draw_ellipse_icon,
    "polygon": _draw_polygon_icon,
    "star": _draw_star_icon,
    "text": _draw_text_icon,
    "grid_snap": _draw_grid_snap_icon,
    "angle_snap": _draw_angle_snap_icon,
    "constraint": _draw_constraint_icon,
    "vertex_snap": _draw_vertex_snap_icon,
    "edge_snap": _draw_edge_snap_icon,
    "master_snap": _draw_master_snap_icon,
    "split": _draw_split_icon,
    "construction": _draw_construction_icon,
    "dimension": _draw_dimension_icon,
    "measure": _draw_measure_icon,
    "finish": _draw_finish_icon,
    "close_path": _draw_close_path_icon,
    "undo_point": _draw_undo_point_icon,
    "cancel": _draw_cancel_icon,
    "select_arrow": _draw_select_arrow_icon,
    "smooth_chaikin": _draw_smooth_chaikin_icon,
    "smooth_gaussian": _draw_smooth_gaussian_icon,
    "smooth_catmull": _draw_smooth_catmull_icon,
}


def tool_icon(name: str, *, size: int = 20, color: str = "#c9d1d9") -> QIcon:
    """Look up one of the draw-sidebar icons by name (see `_ICON_FACTORIES`)."""
    draw_fn = _ICON_FACTORIES[name]
    return icon_from_painter(draw_fn, size=size, color=color)


__all__ = ["download_icon", "gear_icon", "icon_from_painter", "tool_icon"]
