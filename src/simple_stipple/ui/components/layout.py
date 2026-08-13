"""Reusable layout containers and surface helpers."""

from __future__ import annotations

import platform as _platform
from typing import TypeVar

from PySide6.QtCore import QChildEvent, QEvent, QObject, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from simple_stipple.ui.style import (
    icon_path,
)
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.style import MOTION_DURATION_MS

_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"

L = TypeVar("L", QHBoxLayout, QVBoxLayout, QGridLayout)


def container_with_layout(
    parent: QWidget | None = None,
    layout_class: type[L] = QVBoxLayout,
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
) -> tuple[QWidget, L]:
    """Create a container widget with an attached layout."""
    container = QWidget(parent)
    layout = layout_class(container)

    if margins is not None:
        layout.setContentsMargins(*margins)
    else:
        layout.setContentsMargins(0, 0, 0, 0)

    if spacing is not None:
        layout.setSpacing(spacing)

    return container, layout


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


def empty_state(
    *,
    title: str,
    hint: str = "",
    icon: str = "",
    action: QWidget | None = None,
) -> QWidget:
    """Build the "nothing here yet" panel a page shows before it has content.

    This is the first thing a user reads on an unfamiliar page, so it says
    what the surface is for and what to do next rather than leaving a blank
    rectangle to interpret. Pass *action* to put the single obvious next step
    directly inside it.
    """
    host = QWidget()
    host.setProperty("role", "empty-state")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(8)
    layout.addStretch()
    if icon:
        glyph = QLabel(icon)
        glyph.setProperty("role", "empty-icon")
        glyph.setProperty("icon-only", True)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
    heading = QLabel(title)
    heading.setProperty("role", "empty-title")
    heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    heading.setWordWrap(True)
    layout.addWidget(heading)
    if hint:
        detail = QLabel(hint)
        detail.setProperty("role", "empty-hint")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        layout.addWidget(detail)
    if action is not None:
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(action)
        row.addStretch()
        layout.addLayout(row)
    layout.addStretch()
    # Screen readers otherwise announce an unlabelled container and the user
    # has to tab through to discover why the page looks empty.
    host.setAccessibleName(title)
    host.setAccessibleDescription(hint or title)
    return host


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
    # Prefer reflow, but retain horizontal scrolling as a safety net for
    # controls whose native widgets cannot wrap (long paths, combo entries,
    # translated labels). Disabling it caused silent clipping throughout the
    # app when a child retained a wider size hint than the sidebar.
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(content)
    layout.addWidget(scroll)
    frame.setMinimumWidth(min_width)
    frame.setMaximumWidth(max_width)

    # Wide translated labels and property rows must reflow rather than force
    # the entire application wider than its declared compact breakpoint.
    content.setMinimumWidth(0)
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    _install_sidebar_reflow(content)
    return frame


class _SidebarReflowFilter(QObject):
    """Keep sidebar labels responsive as feature pages add controls."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ChildAdded:
            child_event = event
            assert isinstance(child_event, QChildEvent)
            child = child_event.child()
            if isinstance(child, QWidget):
                self.prepare(child)
        return super().eventFilter(watched, event)

    def prepare(self, widget: QWidget) -> None:
        """Install this filter recursively and configure readable labels."""
        widget.installEventFilter(self)
        labels = [widget] if isinstance(widget, QLabel) else []
        labels.extend(widget.findChildren(QLabel))
        for label in labels:
            if label.text().strip() and not label.property("icon-only"):
                label.setWordWrap(True)
                policy = label.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                label.setSizePolicy(policy)


def _install_sidebar_reflow(content: QWidget) -> None:
    """Apply responsive label policies now and for all subsequently added children."""
    reflow_filter = _SidebarReflowFilter(content)
    reflow_filter.prepare(content)


class ResponsiveContentSplitter(QSplitter):
    """Horizontal splitter that exposes a compact toggleable secondary drawer."""

    # The splitter itself is narrower than the outer application window after
    # shell gutters. Enter drawer mode early enough that a nominal 1050 px
    # window is never expanded by the combined pane minimum-size hints.
    COMPACT_WIDTH = 1100
    COMPACT_HYSTERESIS = 24

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self._responsive_secondary: int | None = None
        self._drawer_label = "Inspector"
        self._drawer_size = 280
        self._compact = False
        self._compact_drawer_open = False
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
            opened = self.sizes()[index] == 0
            self._set_drawer_open(opened)
            if self._compact:
                self._compact_drawer_open = opened

    def _update_responsive_state(self) -> None:
        if self._responsive_secondary is None:
            return
        breakpoint = self.COMPACT_WIDTH + (
            self.COMPACT_HYSTERESIS if self._compact else -self.COMPACT_HYSTERESIS
        )
        compact = self.width() < breakpoint
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
            self._set_drawer_open(self._compact_drawer_open if compact else True)
        self._drawer_toggle.setVisible(compact)
        self._position_drawer_toggle()

    def _position_drawer_toggle(self) -> None:
        hint = self._drawer_toggle.sizeHint()
        width = max(112, hint.width() + 12)
        parent = self._drawer_toggle.parentWidget()
        parent_width = parent.width() if parent is not None else self.width()
        self._drawer_toggle.setGeometry(max(4, parent_width - width - 8), 76, width, 30)
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


_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._toggle: QToolButton | QLabel
        if collapsible:
            self._toggle = QToolButton()
            self._toggle.setAccessibleName(f"{title} section")
            self._toggle.setAccessibleDescription("Expand or collapse this group of controls")
            self._toggle.setProperty("role", "collapsible-toggle")
            self._toggle.setText(title)
            self._toggle.setIcon(
                QIcon(str(icon_path("chevron_down.svg" if expanded else "chevron_right.svg")))
            )
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
        self._motion: QPropertyAnimation | None = None

    def _on_toggled(self, checked: bool) -> None:
        if not isinstance(self._toggle, QToolButton):
            return
        self._toggle.setText(self._title)
        self._toggle.setIcon(
            QIcon(str(icon_path("chevron_down.svg" if checked else "chevron_right.svg")))
        )
        app = QApplication.instance()
        reduced_motion = bool(app and app.property("reducedMotion"))
        if reduced_motion or not self.isVisible():
            self._content.setMaximumHeight(16777215)
            self._content.setVisible(checked)
            self.adjustSize()
            return
        if self._motion is not None:
            self._motion.stop()
        target = max(1, self._content.sizeHint().height())
        self._content.setVisible(True)
        self._motion = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._motion.setDuration(MOTION_DURATION_MS)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.setStartValue(0 if checked else max(1, self._content.height()))
        self._motion.setEndValue(target if checked else 0)

        def finish() -> None:
            self._content.setVisible(checked)
            self._content.setMaximumHeight(16777215 if checked else 0)
            self.adjustSize()

        self._motion.finished.connect(finish)
        self._motion.start()

    def set_expanded(self, expanded: bool) -> None:
        """Set disclosure state without reaching into the header widget."""
        if isinstance(self._toggle, QToolButton):
            self._toggle.setChecked(expanded)
            self._on_toggled(expanded)

    def is_expanded(self) -> bool:
        return not isinstance(self._toggle, QToolButton) or self._toggle.isChecked()

    def set_subtitle(self, text: str, *, dim: bool = False) -> None:
        """Update the one-line state summary shown under the title.

        Pass ``dim=True`` to render the subtitle in a more muted color
        (used to indicate the section's feature is currently disabled).
        """
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))
        self._subtitle.setProperty("dim", "true" if dim else "")
        refresh_style(self._subtitle)


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
