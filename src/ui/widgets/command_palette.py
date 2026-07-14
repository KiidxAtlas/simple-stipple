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

from src.infra.error_reporting import report_error


class CommandPaletteDialog(QDialog):
    _recent_command_indices: list[int] = []

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

    def keyPressEvent(self, event) -> None:
        """Move the highlighted row with Up/Down.

        The query QLineEdit holds focus throughout (so typing keeps
        filtering) and doesn't consume arrow keys itself, so without this
        override Up/Down did nothing — a non-first match was only pickable
        by mouse, despite the dialog's own hint text implying full keyboard
        operation.
        """
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            count = self._list.count()
            if count:
                row = self._list.currentRow()
                row = (row + 1) % count if key == Qt.Key.Key_Down else (row - 1) % count
                self._list.setCurrentRow(row)
            event.accept()
            return
        super().keyPressEvent(event)

    def _refresh_list(self) -> None:
        text = self._query.text().strip().lower()
        prev_item = self._list.currentItem()
        prev_idx = prev_item.data(Qt.ItemDataRole.UserRole) if prev_item is not None else None
        self._list.clear()
        first_row_for_prev: int | None = None
        matches: list[tuple[int, int, dict]] = []
        for idx, cmd in enumerate(self._commands):
            hay = " ".join(
                [
                    str(cmd.get("title", "")),
                    str(cmd.get("shortcut", "")),
                    str(cmd.get("keywords", "")),
                ]
            ).lower()
            score = self._match_score(text, hay)
            if score is None:
                continue
            recent_rank = (
                self._recent_command_indices.index(idx)
                if idx in self._recent_command_indices
                else 10_000
            )
            matches.append((score * 10_000 + recent_rank, idx, cmd))
        for _score, idx, cmd in sorted(matches, key=lambda item: item[0]):
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

    @staticmethod
    def _match_score(query: str, haystack: str) -> int | None:
        """Token-aware fuzzy subsequence score; lower is a better match."""
        if not query:
            return 0
        total = 0
        for token in query.split():
            direct = haystack.find(token)
            if direct >= 0:
                total += direct
                continue
            position = -1
            gaps = 0
            for char in token:
                next_position = haystack.find(char, position + 1)
                if next_position < 0:
                    return None
                if position >= 0:
                    gaps += next_position - position - 1
                position = next_position
            total += 100 + gaps
        return total

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
        if idx in self._recent_command_indices:
            self._recent_command_indices.remove(idx)
        self._recent_command_indices.insert(0, idx)
        del self._recent_command_indices[12:]
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
