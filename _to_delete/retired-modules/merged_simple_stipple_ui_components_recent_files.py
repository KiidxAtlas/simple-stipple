"""Recent-file menu button."""

from __future__ import annotations

import platform as _platform
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from simple_stipple.ui.components.recent import clear_recent, list_recent
from simple_stipple.ui.style.theme import (
    icon_path,
)

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
        super().__init__("Recent", parent)
        self.setIcon(QIcon(str(icon_path("chevron_down.svg"))))
        self._settings = settings
        self._kind = kind
        self._empty_message = empty_message
        # Reserve room for both the word and the disclosure icon. At 76 px
        # Fusion elides "Recent" to "Recen", a particularly unhelpful label
        # in the already-dense file source row.
        self.setMinimumWidth(94)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Open recent files")
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
