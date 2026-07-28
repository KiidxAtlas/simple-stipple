"""Reusable layout containers and surface helpers."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


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
    line.setProperty("role", "hairline")
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

    # Wide translated labels and property rows must reflow rather than force
    # the entire application wider than its declared compact breakpoint.
    content.setMinimumWidth(0)
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return frame


class ResponsiveContentSplitter(QSplitter):
    """Horizontal splitter that exposes a compact toggleable secondary drawer."""

    # The splitter itself is narrower than the outer application window after
    # shell gutters. Enter drawer mode early enough that a nominal 1050 px
    # window is never expanded by the combined pane minimum-size hints.
    COMPACT_WIDTH = 1100

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self._responsive_secondary: int | None = None
        self._drawer_label = "Inspector"
        self._drawer_size = 280
        self._compact = False
        self._secondary_size_policy: QSizePolicy | None = None
        self._secondary_minimum_width = 0
        # A QWidget parented directly to QSplitter is automatically inserted as
        # a pane. The old overlay button therefore became pane 0, shifting the
        # canvas/inspector indices and collapsing the canvas itself.
        self._drawer_toggle = QToolButton()
        self._drawer_toggle.setProperty("role", "drawer-toggle")
        self._drawer_toggle.setAccessibleName("Toggle secondary inspector")
        self._drawer_toggle.clicked.connect(self._toggle_drawer)
        self._drawer_toggle.hide()

    def set_responsive_secondary(self, index: int, label: str = "Inspector") -> None:
        self._responsive_secondary = index
        self._drawer_label = label
        if 0 <= index < self.count():
            secondary = self.widget(index)
            if secondary is not None:
                self._secondary_size_policy = secondary.sizePolicy()
                self._secondary_minimum_width = secondary.minimumWidth()
        primary_index = 0 if index != 0 else 1
        if 0 <= primary_index < self.count():
            self._drawer_toggle.setParent(self.widget(primary_index))
        sizes = self.sizes()
        if 0 <= index < len(sizes) and sizes[index] > 0:
            self._drawer_size = sizes[index]
        self._update_responsive_state()

    def _set_drawer_open(self, opened: bool) -> None:
        index = self._responsive_secondary
        if index is None or self.count() < 2:
            return
        sizes = self.sizes()
        total = max(sum(sizes), self.width())
        if opened:
            drawer = min(max(220, self._drawer_size), max(220, total // 2))
            sizes[index] = drawer
            sizes[1 - index] = max(1, total - drawer)
        else:
            if sizes[index] > 0:
                self._drawer_size = sizes[index]
            sizes[1 - index] = max(1, total)
            sizes[index] = 0
        self.setSizes(sizes)
        self._drawer_toggle.setText(
            f"Hide {self._drawer_label}" if opened else f"Show {self._drawer_label}"
        )
        self._drawer_toggle.setAccessibleDescription(self._drawer_toggle.text())

    def _toggle_drawer(self) -> None:
        index = self._responsive_secondary
        if index is not None:
            self._set_drawer_open(self.sizes()[index] == 0)

    def _update_responsive_state(self) -> None:
        if self._responsive_secondary is None:
            return
        compact = self.width() < self.COMPACT_WIDTH
        secondary = self.widget(self._responsive_secondary)
        if secondary is None:
            return
        if compact:
            policy = secondary.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            secondary.setSizePolicy(policy)
            secondary.setMinimumWidth(0)
        elif self._secondary_size_policy is not None:
            secondary.setSizePolicy(self._secondary_size_policy)
            secondary.setMinimumWidth(self._secondary_minimum_width)
        if compact != self._compact:
            self._compact = compact
            self._set_drawer_open(not compact)
        self._drawer_toggle.setVisible(compact)
        self._position_drawer_toggle()

    def _position_drawer_toggle(self) -> None:
        hint = self._drawer_toggle.sizeHint()
        width = max(112, hint.width() + 12)
        parent = self._drawer_toggle.parentWidget()
        parent_width = parent.width() if parent is not None else self.width()
        self._drawer_toggle.setGeometry(max(4, parent_width - width - 6), 76, width, 30)
        self._drawer_toggle.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_responsive_state()


def content_splitter(
    left: QWidget, right: QWidget, *, sizes: tuple[int, int]
) -> ResponsiveContentSplitter:
    """Create a collapsible horizontal splitter with sensible defaults.

    The left pane (canvas) absorbs all extra space on resize/fullscreen;
    the right pane (sidebar) keeps its configured width instead of growing
    to fill the window.
    """
    splitter = ResponsiveContentSplitter()
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
