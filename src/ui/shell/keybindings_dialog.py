"""Keyboard shortcut editor dialog."""

from __future__ import annotations

import platform

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.settings import DEFAULT_KEYBINDINGS
from src.ui.core.factories import section_label, sep, surface_frame

_KBD_MOD = "Meta" if platform.system() == "Darwin" else "Ctrl"

_KEYBINDING_FIELDS = [
    ("workspace.new", "New workspace"),
    ("workspace.open", "Open workspace"),
    ("workspace.save", "Save workspace"),
    ("workspace.save_as", "Save workspace as"),
    ("app.settings", "Open settings"),
    ("app.command_palette", "Open command palette"),
    ("canvas.select_mode", "Canvas select mode"),
    ("canvas.draw_mode", "Canvas draw mode"),
    ("canvas.edit_mode", "Canvas edit mode"),
    ("canvas.measure", "Canvas measure"),
    ("canvas.fit", "Canvas fit view"),
    ("tab.draft", "Switch to Draft page"),
    ("tab.pattern", "Switch to Pattern page"),
    ("tab.trace", "Switch to Trace page"),
    ("tab.convert", "Switch to Convert page"),
]


class KeybindingsDialog(QDialog):
    """Dialog for editing keyboard shortcuts."""

    def __init__(
        self,
        parent: QWidget | None = None,
        keybindings: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setObjectName("keybindings-dialog")
        self.resize(560, 580)
        self.setMinimumSize(480, 400)
        self.setModal(True)

        self._keybindings: dict = dict(keybindings or {})
        self._entries: dict[str, QLineEdit] = {}
        self._rows: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Keyboard Shortcuts")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            f"Use Qt shortcut syntax (e.g. {_KBD_MOD}+K, Shift+R, F). "
            "Leave blank to use the default."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter shortcuts…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        card = surface_frame("panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(6)
        section_label(card_layout, "Shortcuts")

        actions_row = QHBoxLayout()
        actions_row.addStretch()
        reset_btn = QPushButton("Reset all to defaults")
        reset_btn.setToolTip("Restore all keyboard shortcuts to their defaults")
        reset_btn.clicked.connect(self._reset_all)
        actions_row.addWidget(reset_btn)
        card_layout.addLayout(actions_row)

        _GROUP_TITLES = {
            "workspace": "Workspace",
            "app": "Application",
            "canvas": "Canvas",
            "tab": "Pages",
        }
        current_group = None
        for key, label in _KEYBINDING_FIELDS:
            group = key.split(".", 1)[0]
            if group != current_group:
                current_group = group
                header = QLabel(_GROUP_TITLES.get(group, group.title()))
                header.setProperty("role", "hint")
                card_layout.addWidget(header)
            self._add_row(card_layout, key, label)

        layout.addWidget(card, stretch=1)
        sep(layout)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Apply")
        save_btn.setMinimumWidth(100)
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._apply_filter("")

    def _add_row(self, layout: QVBoxLayout, key: str, label: str) -> None:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel(label)
        lbl.setMinimumWidth(160)
        row.addWidget(lbl)
        entry = QLineEdit()
        default = DEFAULT_KEYBINDINGS.get(key, "")
        entry.setPlaceholderText(default)
        entry.setText(str(self._keybindings.get(key, default)))
        entry.setToolTip(f"{key}  ·  default: {default or '(none)'}")
        row.addWidget(entry, stretch=1)
        self._entries[key] = entry
        self._rows[key] = row_widget
        layout.addWidget(row_widget)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for key, row_widget in self._rows.items():
            if not query:
                row_widget.setVisible(True)
                continue
            label = self._entries[key].placeholderText().lower()
            current = self._entries[key].text().lower()
            haystack = f"{key} {label} {current}"
            row_widget.setVisible(query in haystack)

    def _reset_all(self) -> None:
        for key, entry in self._entries.items():
            entry.setText(DEFAULT_KEYBINDINGS.get(key, ""))

    def _apply(self) -> None:
        result: dict[str, str] = dict(DEFAULT_KEYBINDINGS)
        for key, entry in self._entries.items():
            value = entry.text().strip()
            if value:
                result[key] = value
        self._keybindings = result
        self.accept()

    def get_keybindings(self) -> dict:
        """Return the saved keybindings after the dialog is accepted."""
        return dict(self._keybindings)
