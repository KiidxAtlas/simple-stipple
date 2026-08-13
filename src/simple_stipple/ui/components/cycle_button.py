"""CycleIconButton — reusable icon button with a direct state picker, used for
multi-state controls in
the Draw sidebar (tool-family pickers, snap toggles, arc mode, constraint
mode, split, construction).

Icons are built via ``simple_stipple.ui.components.icons.tool_icon`` (or any other QIcon) by
the caller and passed in as part of each state tuple — this widget only
owns the interaction behavior, not icon drawing.

Behaviors
---------
* **Click / keyboard activation** — opens a QMenu (checkable actions, one per state,
  current one checked) so any state can be jumped to directly.
* **Pointer and keyboard activation** — use the same native menu, so focus,
  selection, and dismissal follow the platform accessibility conventions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QPushButton, QSizePolicy, QWidget

StateEntry = tuple[str, QIcon, str]  # (id, icon, label)


class CycleIconButton(QPushButton):
    """Icon button supporting a direct state menu.

    Parameters
    ----------
    states : Sequence[StateEntry]
        List of ``(id, icon, label)`` tuples defining the cycle states.
    on_change : Callable[[str], None]
        Invoked with the new state id whenever the state changes, via
        direct menu selection.
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
        self.setAccessibleName(self._states[0][2] if self._states else "Tool option")
        self.setAccessibleDescription(
            f"{len(self._states)} options. Activate to choose an option."
            if len(self._states) > 2
            else "Activate to toggle this option."
        )
        self.setCheckable(True)
        self.setFlat(True)
        # Keep a generous acquisition target without pinning a platform-native
        # bezel to one fixed size. A fixed size clipped at larger accessibility
        # scales on macOS and made this reusable control unusable outside the
        # sidebar grid.
        self.setMinimumHeight(40)
        # The draw sidebar is intentionally a two-column grid. Bound the
        # button's width so a native macOS size hint cannot push its sibling
        # under the vertical scrollbar, but leave height layout-managed.
        self.setMaximumWidth(44)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        # Themed via theme.qss's role="cycle-icon" rules (not a per-widget
        # setStyleSheet) so this follows Light/Dark/high-contrast like every
        # other role-based control.
        self.setProperty("role", "cycle-icon")

        self.clicked.connect(self._on_left_click)

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


    # -- event handlers ----------------------------------------------------

    def _on_left_click(self) -> None:
        if not self._states:
            return
        if len(self._states) <= 2:
            self._select_state((self._current_index + 1) % len(self._states))
            return
        self._show_context_menu(self.mapToGlobal(self.rect().bottomLeft()))

    def _update_visuals(self) -> None:
        entry = self._states[self._current_index]
        self.setIcon(entry[1])
        self.setToolTip(entry[2])
        self.setAccessibleName(entry[2])
        self.setAccessibleDescription(
            f"{len(self._states)} options. Activate to choose an option."
            if len(self._states) > 2
            else "Activate to toggle this option."
        )
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
        # Themed via theme.qss's app-wide QMenu rules, not a per-instance
        # stylesheet — same reasoning as the button above.
        menu = QMenu(self)
        for i, (_sid, _icon, label) in enumerate(self._states):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(i == self._current_index)
            action.triggered.connect(lambda checked, idx=i: self._select_state(idx))
        self._state_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_state_menu", None))
        menu.popup(pos)

    def _select_state(self, index: int) -> None:
        if 0 <= index < len(self._states):
            self._current_index = index
            self._update_visuals()
            self._on_change(self._states[index][0])
