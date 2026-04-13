"""Small layout helper functions for building PySide6 panels."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_ENTRY_ERROR_STYLE = "border: 1px solid #f85149;"


def _section_label(parent_layout, text: str) -> QLabel:
    """Compact muted section header with letter-spacing."""
    lb = QLabel(text.upper())
    lb.setStyleSheet(
        "color: #484f58;"
        "font-size: 10px;"
        "font-weight: 600;"
        "letter-spacing: 0.8px;"
        "padding-bottom: 1px;"
    )
    lb.setContentsMargins(0, 8, 0, 2)
    parent_layout.addWidget(lb)
    return lb


def _sep(parent_layout) -> QFrame:
    """Hairline horizontal separator."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #21262d;")
    line.setFixedHeight(1)
    parent_layout.addWidget(line)
    return line


def _row() -> QHBoxLayout:
    """Create a horizontal row layout."""
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    return h


def _info_chip(text: str, tone: str = "neutral") -> QLabel:
    """Small capsule label used for capabilities, state, and shortcuts."""
    chip = QLabel(text)
    chip.setProperty("role", "chip")
    chip.setProperty("tone", tone)
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return chip


class CollapsibleSection(QFrame):
    """Expandable/collapsible content section for dense sidebars."""

    def __init__(self, title: str, content: QWidget, *, expanded: bool = True):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "collapsible")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._toggle = QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._toggle.clicked.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._content = content
        self._content.setVisible(expanded)
        layout.addWidget(self._content)

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self._content.setVisible(checked)
        self.adjustSize()

    def isExpanded(self) -> bool:
        return self._toggle.isChecked()

    def setExpanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)
        self._on_toggled(expanded)


class CanvasStatusStrip(QFrame):
    """Compact status bar for canvas — mode, selection, zoom, coordinates, and readiness."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "status-strip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self._mode_label = QLabel("Select")
        self._mode_label.setStyleSheet("color: #79c0ff; font-size: 11px; font-weight: 600;")
        layout.addWidget(self._mode_label)

        layout.addWidget(self._dot())

        self._objects_label = QLabel("0 obj")
        self._objects_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._objects_label)

        layout.addWidget(self._dot())

        self._selection_label = QLabel("0 sel")
        self._selection_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._selection_label)

        layout.addStretch()

        # Cursor position
        self._cursor_label = QLabel("")
        self._cursor_label.setStyleSheet("color: #6e7681; font-size: 10px; font-family: 'Menlo', 'Courier New';")
        layout.addWidget(self._cursor_label)

        layout.addWidget(self._dot())

        # Zoom level
        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        self._zoom_label.setToolTip("Zoom level (scroll to zoom)")
        layout.addWidget(self._zoom_label)

        layout.addWidget(self._dot())

        self._readiness_chip = _info_chip("No geometry", "warn")
        layout.addWidget(self._readiness_chip)

    @staticmethod
    def _dot() -> QLabel:
        d = QLabel("·")
        d.setStyleSheet("color: #30363d; font-size: 11px;")
        return d

    def set_snapshot(
        self,
        *,
        mode: str,
        selected_count: int,
        object_count: int,
        precision_text: str,
        readiness_text: str,
        readiness_tone: str = "neutral",
        zoom_percent: int = 100,
        cursor_pos: tuple[float, float] | None = None,
    ) -> None:
        self._mode_label.setText(mode.title())
        self._objects_label.setText(f"{object_count} obj")
        self._selection_label.setText(f"{selected_count} sel")
        self._selection_label.setStyleSheet(
            f"color: {'#79c0ff' if selected_count else '#8b949e'}; font-size: 11px;"
        )
        self._zoom_label.setText(f"{zoom_percent}%")
        if cursor_pos:
            self._cursor_label.setText(f"X {cursor_pos[0]:.2f}  Y {cursor_pos[1]:.2f}")
        else:
            self._cursor_label.setText("")
        self._readiness_chip.setText(readiness_text)
        self._readiness_chip.setProperty("tone", readiness_tone)
        self._readiness_chip.style().unpolish(self._readiness_chip)
        self._readiness_chip.style().polish(self._readiness_chip)


class CanvasObjectBrowser(QFrame):
    """Lightweight object list for editable canvas geometry."""

    selectionRequested = Signal(object)
    fitRequested = Signal()

    def __init__(self, title: str = "Objects") -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "object-browser")
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setProperty("role", "callout-title")
        header.addWidget(title_label)
        self._summary = QLabel("0 objects")
        self._summary.setProperty("role", "callout-body")
        header.addWidget(self._summary)
        header.addStretch()
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemSelectionChanged.connect(self._emit_selection_request)
        self._list.itemDoubleClicked.connect(lambda _item: self.fitRequested.emit())
        layout.addWidget(self._list, stretch=1)

    def set_objects(
        self,
        polys: list[list[tuple[float, float]]],
        selected_indices: list[int],
    ) -> None:
        self._syncing = True
        self._list.clear()
        for idx, poly in enumerate(polys):
            item = QListWidgetItem(self._describe_poly(idx, poly))
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self._list.addItem(item)
            if idx in selected_indices:
                item.setSelected(True)
        self._summary.setText(
            f"{len(polys)} objects  ·  {len(selected_indices)} selected"
        )
        self._syncing = False

    def _emit_selection_request(self) -> None:
        if self._syncing:
            return
        indices = []
        for item in self._list.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(idx, int):
                indices.append(idx)
        self.selectionRequested.emit(indices)

    @staticmethod
    def _describe_poly(idx: int, poly: list[tuple[float, float]]) -> str:
        if not poly:
            return f"{idx + 1:02d}  Empty"
        xs, ys = zip(*poly)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        point_count = len(poly)
        if len(poly) > 1 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01:
            point_count -= 1
            kind = "Closed"
        else:
            kind = "Open"
        return (
            f"{idx + 1:02d}  {kind}  ·  {point_count} pts  ·  "
            f"{width:.1f} × {height:.1f} mm"
        )


def _surface_frame(surface: str = "panel") -> QFrame:
    """Create a styled surface frame for sidebar or content panels."""
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.NoFrame)
    frame.setProperty("surface", surface)
    return frame


def _sidebar_panel(
    content: QWidget, *, min_width: int = 340, max_width: int = 430
) -> QFrame:
    """Wrap sidebar content in a styled scrollable panel."""
    frame = _surface_frame("sidebar")
    frame.setMinimumWidth(min_width)
    frame.setMaximumWidth(max_width)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    layout.addWidget(scroll)
    return frame


def _content_splitter(left: QWidget, right: QWidget, *, sizes: tuple[int, int]) -> QSplitter:
    """Create a collapsible horizontal splitter with sensible defaults."""
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(True)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes(list(sizes))
    return splitter


def _canvas_toolbar(
    on_mode,
    on_fit,
    *,
    modes: tuple[str, ...] = ("Select", "Draw", "Edit"),
    secondary_actions=None,
):
    """Compact canvas toolbar with mode toggles and optional actions."""
    shell = QWidget()
    shell_layout = QHBoxLayout(shell)
    shell_layout.setContentsMargins(0, 0, 0, 0)
    shell_layout.setSpacing(4)

    # Mode buttons — tight group
    mode_buttons: dict[str, QPushButton] = {}
    for mode in modes:
        btn = QPushButton(mode)
        btn.setMinimumHeight(28)
        btn.setProperty("active", mode == modes[0])
        btn.clicked.connect(lambda checked=False, m=mode: on_mode(m))
        shell_layout.addWidget(btn)
        mode_buttons[mode] = btn

    # Separator
    sep = QLabel("│")
    sep.setStyleSheet("color: #21262d; font-size: 12px;")
    shell_layout.addWidget(sep)

    fit_btn = QPushButton("Fit")
    fit_btn.setMinimumHeight(28)
    fit_btn.clicked.connect(on_fit)
    shell_layout.addWidget(fit_btn)

    if secondary_actions:
        sep2 = QLabel("│")
        sep2.setStyleSheet("color: #21262d; font-size: 12px;")
        shell_layout.addWidget(sep2)
        for spec in secondary_actions:
            label, slot, role = spec if len(spec) == 3 else (*spec, None)
            btn = QPushButton(label)
            btn.setMinimumHeight(28)
            if role:
                btn.setProperty("role", role)
            btn.clicked.connect(slot)
            shell_layout.addWidget(btn)

    selection_label = QLabel("")
    selection_label.setStyleSheet("color: #8b949e; font-size: 11px;")
    selection_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    shell_layout.addWidget(selection_label, stretch=1)

    return shell, mode_buttons, selection_label


def parse_float_field(
    text: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_empty: bool = False,
) -> float | None:
    """Parse a float value from text with optional range validation.

    Returns *None* when *allow_empty* is True and *text* is blank.
    Raises ``ValueError`` with a human-readable message on any failure.
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


def set_line_edit_error(widget, message: str) -> None:
    """Highlight a line edit and attach a validation message."""
    widget.setStyleSheet(_ENTRY_ERROR_STYLE)
    widget.setToolTip(message)


def clear_line_edit_error(widget) -> None:
    """Clear validation styling from a line edit."""
    widget.setStyleSheet("")
    widget.setToolTip("")
