"""Reusable ``Recent ▾`` button.

A small :class:`QPushButton` that pops up a menu listing the most-recently
opened files for a given *kind* and emits :sig:`fileSelected` when one is
chosen.  Designed to sit next to a ``Browse`` button on file rows in pages.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Signal
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from src.ui.util.recent_files import clear_recent, list_recent


class RecentFilesButton(QPushButton):
    """Drop-down button that exposes the recent-files MRU."""

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
                p = Path(path)
                label = f"{p.name}    ‹{p.parent.name or p.parent.anchor}›"
                action = menu.addAction(label)
                action.setToolTip(str(p))
                action.triggered.connect(
                    lambda _checked=False, target=path: self.fileSelected.emit(target)
                )
            menu.addSeparator()
            menu.addAction("Clear history", self._clear)
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def _clear(self) -> None:
        clear_recent(self._settings, self._kind)


__all__ = ["RecentFilesButton"]
