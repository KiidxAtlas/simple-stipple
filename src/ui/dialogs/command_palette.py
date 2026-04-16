"""Searchable command palette dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CommandPaletteDialog(QDialog):
    def __init__(
        self,
        commands: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(620, 420)
        self.setMinimumSize(500, 320)
        self._commands = commands
        self._filtered_indices: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Search actions")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #f0f6fc;")
        layout.addWidget(title)

        self._query = QLineEdit()
        self._query.setPlaceholderText("Type action name or shortcut…")
        self._query.textChanged.connect(self._refresh_list)
        self._query.returnPressed.connect(self._run_selected)
        layout.addWidget(self._query)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _item: self._run_selected())
        layout.addWidget(self._list, stretch=1)

        footer = QHBoxLayout()
        hint = QLabel("Enter = run · Esc = close")
        hint.setStyleSheet("color: #8b949e; font-size: 11px;")
        footer.addWidget(hint)
        footer.addStretch()
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self._run_selected)
        footer.addWidget(run_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._refresh_list()
        self._query.setFocus()

    def _refresh_list(self) -> None:
        text = self._query.text().strip().lower()
        self._filtered_indices = []
        self._list.clear()
        for idx, cmd in enumerate(self._commands):
            hay = " ".join([
                str(cmd.get("title", "")),
                str(cmd.get("shortcut", "")),
                str(cmd.get("keywords", "")),
            ]).lower()
            if text and text not in hay:
                continue
            self._filtered_indices.append(idx)
            title = str(cmd.get("title", ""))
            shortcut = str(cmd.get("shortcut", "")).strip()
            subtitle = str(cmd.get("subtitle", "")).strip()
            label = title
            if shortcut:
                label = f"{title}    [{shortcut}]"
            if subtitle:
                label = f"{label}\n{subtitle}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self._list.addItem(item)
        if self._list.count() > 0:
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
        if callable(callback):
            callback()
        self.accept()
