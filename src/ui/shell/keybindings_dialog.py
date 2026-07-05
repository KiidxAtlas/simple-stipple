"""Keyboard shortcut editor dialog.

Rows come from two sources so there is exactly one definition of each
shortcut's id/label/default:
- app-level ids (workspace, application, canvas-mode switches, page tabs,
  window management) — from ``settings.DEFAULT_KEYBINDINGS``.
- everything else (edit/selection/path/boolean operations, tool modes,
  view/grid controls, draw-primitive quick-selects) — from the canvas
  ``Command`` registry in ``src.ui.canvas.commands``.

Saving writes a flat ``{id: shortcut}`` dict; ``App`` re-applies it to its
own QActions and calls ``canvas_commands.apply_keybindings()`` so canvas
commands pick it up too.
"""

from __future__ import annotations

import platform

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.settings import DEFAULT_KEYBINDINGS
from src.ui.canvas import commands as canvas_commands
from src.ui.core.factories import section_label, sep, surface_frame

_KBD_MOD = "Meta" if platform.system() == "Darwin" else "Ctrl"

# App-level ids aren't canvas Commands, so they need their own labels/groups.
_APP_LABELS: dict[str, str] = {
    "workspace.new": "New Workspace",
    "workspace.new_window": "New Window",
    "workspace.open": "Open Workspace",
    "workspace.save": "Save Workspace",
    "workspace.save_as": "Save Workspace As",
    "app.settings": "Open Settings",
    "app.command_palette": "Open Command Palette",
    "window.fullscreen": "Toggle Fullscreen",
    "canvas.select_mode": "Select Mode",
    "canvas.draw_mode": "Draw Mode",
    "canvas.edit_mode": "Edit Mode",
    "canvas.measure": "Measure Tool",
    "canvas.dimension": "Dimension Tool",
    "canvas.fit": "Fit View",
    "tab.draft": "Switch to Draft Tab",
    "tab.pattern": "Switch to Pattern Tab",
    "tab.trace": "Switch to Trace Tab",
    "tab.convert": "Switch to Convert Tab",
    "tab.repo": "Switch to Repo Tab",
}

_APP_GROUPS: dict[str, str] = {
    "workspace": "Workspace",
    "app": "Application",
    "window": "Window Management",
    "canvas": "Canvas Modes",
    "tab": "Page Tabs",
}


def _app_rows() -> list[tuple[str, str, str, str]]:
    """(key, label, group, default) rows for app-level (non-Command) ids."""
    rows: list[tuple[str, str, str, str]] = []
    for key, default in DEFAULT_KEYBINDINGS.items():
        prefix = key.split(".", 1)[0]
        group = _APP_GROUPS.get(prefix, prefix.title())
        label = _APP_LABELS.get(key, key)
        rows.append((key, label, group, default))
    return rows


def _command_rows() -> list[tuple[str, str, str, str]]:
    """(key, label, group, default) rows sourced from the canvas Command
    registry — one row per distinct settings slot (settings_key collapses
    the app/canvas mode-toggle duplicates onto the app-level row above)."""
    rows: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for cmd in canvas_commands.COMMANDS:
        if cmd.hidden or cmd.keybinding_id in DEFAULT_KEYBINDINGS:
            continue  # already represented by an _app_rows() entry
        if cmd.keybinding_id in seen:
            continue
        seen.add(cmd.keybinding_id)
        group = cmd.category or "Other"
        rows.append((cmd.keybinding_id, cmd.label, group, cmd.shortcut))
    return rows


def _build_fields() -> list[tuple[str, str, str, str]]:
    return _app_rows() + _command_rows()


_KEYBINDING_FIELDS: list[tuple[str, str, str, str]] = _build_fields()


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
        self.resize(640, 720)
        self.setMinimumSize(520, 400)
        self.setModal(True)

        self._keybindings: dict = dict(keybindings or {})
        self._entries: dict[str, QLineEdit] = {}
        self._rows: dict[str, QWidget] = {}
        self._labels: dict[str, str] = {
            key: label for key, label, _group, _default in _KEYBINDING_FIELDS
        }
        self._defaults: dict[str, str] = {
            key: default for key, _label, _group, default in _KEYBINDING_FIELDS
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Keyboard Shortcuts")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            f"Use Qt shortcut syntax (e.g. {_KBD_MOD}+K, Shift+R, "
            f"{_KBD_MOD}+Shift+D). Leave blank to disable."
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

        current_group: str | None = None
        for key, label, group, _default in _KEYBINDING_FIELDS:
            if group != current_group:
                current_group = group
                header = QLabel(group)
                header.setProperty("role", "hint")
                card_layout.addWidget(header)
            self._add_row(card_layout, key, label)
        card_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(card)
        layout.addWidget(scroll, stretch=1)
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
        lbl.setMinimumWidth(180)
        row.addWidget(lbl)

        entry = QLineEdit()
        default = self._defaults.get(key, "")
        entry.setPlaceholderText(default or "(none)")

        current = self._keybindings.get(key, "")
        entry.setText(str(current) if current else default)

        tip_parts = [key]
        if default:
            native = QKeySequence(default).toString(QKeySequence.SequenceFormat.NativeText)
            tip_parts.append(f"default: {native}")
        entry.setToolTip(" · ".join(tip_parts))

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
            label = self._labels.get(key, "").lower()
            current = self._entries[key].text().lower()
            haystack = f"{key} {label} {current}"
            row_widget.setVisible(query in haystack)

    def _reset_all(self) -> None:
        for key, entry in self._entries.items():
            entry.setText(self._defaults.get(key, ""))

    def _apply(self) -> None:
        result: dict[str, str] = dict(self._defaults)
        for key, entry in self._entries.items():
            value = entry.text().strip()
            result[key] = value
        self._keybindings = result
        self.accept()

    def get_keybindings(self) -> dict:
        """Return the saved keybindings after the dialog is accepted."""
        return dict(self._keybindings)
