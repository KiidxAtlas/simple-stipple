"""Searchable command palette dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.error_reporting import report_error


class CommandPaletteDialog(QDialog):
    def __init__(
        self,
        commands: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setObjectName("command-palette")
        self.setModal(True)
        self.resize(620, 420)
        self.setMinimumSize(500, 320)
        self._commands = commands

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Command Palette")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #f0f6fc;")
        layout.addWidget(title)

        subtitle = QLabel("Search by command, page, or shortcut")
        subtitle.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(subtitle)

        self._query = QLineEdit()
        self._query.setPlaceholderText("Type command, page, or shortcut…")
        self._query.setClearButtonEnabled(True)
        self._query.textChanged.connect(self._refresh_list)
        self._query.returnPressed.connect(self._run_selected)
        layout.addWidget(self._query)

        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.itemDoubleClicked.connect(lambda _item: self._run_selected())
        layout.addWidget(self._list, stretch=1)

        hint = QLabel("Enter = run · Esc = close")
        hint.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(hint)

        self._refresh_list()
        self._query.setFocus()

    def _refresh_list(self) -> None:
        text = self._query.text().strip().lower()
        prev_item = self._list.currentItem()
        prev_idx = (
            prev_item.data(Qt.ItemDataRole.UserRole) if prev_item is not None else None
        )
        self._list.clear()
        first_row_for_prev: int | None = None
        for idx, cmd in enumerate(self._commands):
            hay = " ".join(
                [
                    str(cmd.get("title", "")),
                    str(cmd.get("shortcut", "")),
                    str(cmd.get("keywords", "")),
                ]
            ).lower()
            if text and text not in hay:
                continue
            title = str(cmd.get("title", ""))
            shortcut = str(cmd.get("shortcut", "")).strip()
            label = f"{title}    [{shortcut}]" if shortcut else title
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self._list.addItem(item)
            if prev_idx == idx:
                first_row_for_prev = self._list.count() - 1
        if self._list.count() == 0:
            empty = QListWidgetItem("No matching commands")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
            return
        if first_row_for_prev is not None:
            self._list.setCurrentRow(first_row_for_prev)
        else:
            self._list.setCurrentRow(0)

    def _run_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            self.reject()
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int):
            self.reject()
            return
        callback = self._commands[idx].get("run")
        # Accept first so failures don't leave the modal dialog stuck.
        self.accept()
        if callable(callback):
            try:
                callback()
            except Exception as exc:  # noqa: BLE001
                report_error(
                    f"Command '{self._commands[idx].get('title', '?')}' failed",
                    exc,
                )
