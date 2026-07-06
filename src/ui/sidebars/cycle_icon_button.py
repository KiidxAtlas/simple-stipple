"""CycleIconButton — reusable icon button with click-to-cycle, right-click
modal, and hover flyout, used consistently for every multi-state control in
the Draw sidebar (tool-family pickers, snap toggles, arc mode, constraint
mode, split, construction).

Icons are built via ``src.ui.core.icons.tool_icon`` (or any other QIcon) by
the caller and passed in as part of each state tuple — this widget only
owns the interaction behavior, not icon drawing.

Behaviors
---------
* **Left-click** — advances to the next state, invokes ``on_change(id)``.
* **Right-click** — pops a small QMenu (checkable actions, one per state,
  current one checked) so any state can be jumped to directly.
* **Hover** (after ~350ms) — shows a lightweight frameless flyout listing
  all states with the current one highlighted, auto-hidden on mouse-leave.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

StateEntry = tuple[str, QIcon, str]  # (id, icon, label)


class _FlyoutFrame(QFrame):
    """The hover flyout's top-level window. Reports its own leave (not just
    the triggering button's) so moving the cursor from the button down into
    the flyout doesn't destroy it before a click can land."""

    def __init__(self, owner: "CycleIconButton", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._owner._schedule_hide_flyout()


class CycleIconButton(QPushButton):
    """Icon button supporting click-to-cycle, right-click modal, and hover
    flyout across a list of named states.

    Parameters
    ----------
    states : Sequence[StateEntry]
        List of ``(id, icon, label)`` tuples defining the cycle states.
    on_change : Callable[[str], None]
        Invoked with the new state id whenever the state changes, via
        either left-click cycling or a direct right-click/menu selection.
    parent : QWidget | None
        Parent widget.
    """

    def __init__(
        self,
        states: Sequence[StateEntry],
        on_change: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._states = list(states)
        self._on_change = on_change
        self._current_index: int = 0

        self.setIcon(self._states[0][1] if self._states else QIcon())
        self.setToolTip(self._states[0][2] if self._states else "")
        self.setCheckable(True)
        self.setFlat(True)
        # Native styles (macOS in particular) can ignore CSS min/max-width
        # on a QPushButton's bezel and render wider than the stylesheet
        # asks for — a hard pixel size is the only constraint every style
        # actually honors, which is what kept the old sidebar's buttons
        # from overflowing its fixed width.
        self.setFixedSize(36, 32)

        self.setObjectName("cycle-icon-button")
        self.setStyleSheet(
            """
            QPushButton#cycle-icon-button {
                border-radius: 6px;
                background: #161b22;
                border: 1px solid #30363d;
            }
            QPushButton#cycle-icon-button:hover {
                background: #1c2128;
                border-color: #58a6ff;
            }
            QPushButton#cycle-icon-button:checked {
                background: #1f3a6e;
                border-color: #2f81f7;
            }
            QPushButton#cycle-icon-button:pressed {
                background: #21262d;
                border-color: #79c0ff;
            }
            QPushButton#cycle-icon-button:disabled {
                background: #0d1117;
                border-color: #21262d;
            }
            """
        )

        self.clicked.connect(self._on_left_click)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(350)
        self._hover_timer.timeout.connect(self._show_flyout)
        self._flyout: QFrame | None = None

        # Belt-and-suspenders auto-hide: leaveEvent-triggered scheduling
        # covers the common case, but the flyout is a separate frameless
        # top-level window, and Qt doesn't always deliver a clean leaveEvent
        # when the cursor crosses between two disjoint top-level surfaces
        # quickly. Poll while visible so it can never get stuck open.
        self._flyout_watchdog = QTimer(self)
        self._flyout_watchdog.setInterval(150)
        self._flyout_watchdog.timeout.connect(self._hide_flyout_unless_still_hovered)

    # -- public API --------------------------------------------------------

    @property
    def current_state_id(self) -> str:
        if self._states:
            return self._states[self._current_index][0]
        return ""

    def set_current_state(self, state_id: str) -> None:
        """Programmatically set the current state by id (no callback fired —
        for syncing the button's display to already-applied view state)."""
        for i, (sid, _icon, _label) in enumerate(self._states):
            if sid == state_id:
                self._current_index = i
                self._update_visuals()
                return

    def state_count(self) -> int:
        return len(self._states)

    # -- event handlers ----------------------------------------------------

    def _on_left_click(self) -> None:
        if not self._states:
            return
        self._hide_flyout()
        self._current_index = (self._current_index + 1) % len(self._states)
        self._update_visuals()
        self._on_change(self._states[self._current_index][0])

    def _update_visuals(self) -> None:
        entry = self._states[self._current_index]
        self.setIcon(entry[1])
        self.setToolTip(entry[2])
        # First state reads as the "neutral" one (Off / Free / etc.) for
        # boolean-style toggles; tool-family pickers can override the
        # checked look explicitly via setChecked() after syncing state.
        self.setChecked(self._current_index != 0)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        else:
            super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        self._hide_flyout()
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                color: #c9d1d9;
                font-size: 11px;
            }
            QMenu::item:selected {
                background: #1c2128;
                color: #79c0ff;
            }
            QMenu::item:checked {
                color: #79c0ff;
            }
            """
        )
        for i, (_sid, _icon, label) in enumerate(self._states):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(i == self._current_index)
            action.triggered.connect(lambda checked, idx=i: self._select_state(idx))
        menu.exec(pos)

    def _select_state(self, index: int) -> None:
        if 0 <= index < len(self._states):
            self._current_index = index
            self._update_visuals()
            self._on_change(self._states[index][0])

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hover_timer.start()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover_timer.stop()
        self._schedule_hide_flyout()

    def _schedule_hide_flyout(self) -> None:
        """Don't tear the flyout down the instant the cursor leaves —
        real mouse movement from the button to the flyout crosses empty
        space (the flyout sits beside the panel, not flush against the
        button) that belongs to neither widget, so an immediate check
        here would hide it before the cursor ever arrives. Re-check after
        a short grace period instead, by which point the cursor has
        either reached the button/flyout or genuinely moved away."""
        if self._flyout is None:
            return
        QTimer.singleShot(220, self._hide_flyout_unless_still_hovered)

    def _hide_flyout_unless_still_hovered(self) -> None:
        if self._flyout is None:
            return
        widget_under_cursor = QApplication.widgetAt(QCursor.pos())
        if (
            widget_under_cursor is self
            or widget_under_cursor is self._flyout
            or (
                widget_under_cursor is not None
                and self._flyout.isAncestorOf(widget_under_cursor)
            )
        ):
            return
        self._hide_flyout()

    def _hide_flyout(self) -> None:
        self._flyout_watchdog.stop()
        if self._flyout is not None:
            self._flyout.hide()
            self._flyout.deleteLater()
            self._flyout = None

    def _on_flyout_pick(self, index: int) -> None:
        self._hide_flyout()
        self._select_state(index)

    def _show_flyout(self) -> None:
        if len(self._states) < 2:
            return  # nothing to preview for a single-state/direct-action button

        flyout = _FlyoutFrame(self, self.window())
        flyout.setObjectName("cycle-flyout")
        flyout.setStyleSheet(
            """
            QFrame#cycle-flyout {
                background: #21262d;
                border: 2px solid #58a6ff;
                border-radius: 8px;
            }
            QFrame#cycle-flyout QPushButton {
                background: transparent;
                border: none;
                color: #e6edf3;
                font-size: 12px;
                padding: 7px 18px;
                text-align: center;
            }
            QFrame#cycle-flyout QPushButton:hover {
                background: #30363d;
                color: #79c0ff;
            }
            QFrame#cycle-flyout QPushButton[role="flyout-current"] {
                color: #79c0ff;
                font-weight: 700;
            }
            """
        )

        shadow = QGraphicsDropShadowEffect(flyout)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 180))
        flyout.setGraphicsEffect(shadow)

        layout = QVBoxLayout(flyout)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        for i, (_sid, _icon, label) in enumerate(self._states):
            item = QPushButton(label, flyout)
            item.setFlat(True)
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == self._current_index:
                item.setProperty("role", "flyout-current")
            item.clicked.connect(lambda checked=False, idx=i: self._on_flyout_pick(idx))
            layout.addWidget(item)

        flyout.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        flyout.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        flyout.adjustSize()

        # Anchor beside the whole sidebar panel, not centered under the
        # button — the popup's width (to fit labels like "Bezier Pen" or
        # "Center→Start→End") is usually wider than the narrow sidebar
        # itself, so centering it under the button made it spill over and
        # cover neighboring section text instead of sitting cleanly beside
        # the panel.
        panel_rect = self._panel_global_rect()
        button_top_global = self.mapToGlobal(self.rect().topLeft())
        flyout.move(panel_rect.right() + 6, button_top_global.y())

        flyout.show()
        self._flyout = flyout
        self._flyout_watchdog.start()

    def _panel_global_rect(self) -> QRect:
        """Global geometry of the enclosing sidebar panel (the ancestor
        named "draw-side-panel"), falling back to this button's own
        top-level window if no such ancestor is found."""
        widget = self.parentWidget()
        while widget is not None:
            if widget.objectName() == "draw-side-panel":
                top_left = widget.mapToGlobal(widget.rect().topLeft())
                return QRect(top_left, widget.size())
            widget = widget.parentWidget()
        top_left = self.mapToGlobal(self.rect().topLeft())
        return QRect(top_left, self.size())
