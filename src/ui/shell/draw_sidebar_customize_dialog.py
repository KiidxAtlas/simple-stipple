"""Customize which sections show in the Draw sidebar, and in what order.

Mirrors RadialMenuDialog's checkbox + drag-to-reorder QListWidget pattern
(src/ui/shell/radial_menu_dialog.py) — same interaction, smaller fixed pool
(the sidebar's own sections instead of the full command registry), so no
filter box is needed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.settings import DEFAULT_DRAW_SIDEBAR_SECTIONS, DRAW_SIDEBAR_SECTION_LABELS
from src.ui.core.factories import sep

_LABELS: dict[str, str] = dict(DRAW_SIDEBAR_SECTION_LABELS)


class DrawSidebarCustomizeDialog(QDialog):
    """Check which Draw-sidebar sections show; drag to reorder. At least
    Path and Shapes must stay checked — a draw sidebar with no way to pick
    a tool isn't useful — or Apply falls back to the defaults."""

    _REQUIRED = {"path", "shapes"}

    def __init__(
        self, parent: QWidget | None = None, sections: list[str] | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Draw Sidebar")
        self.setObjectName("draw-sidebar-customize-dialog")
        self.resize(360, 440)
        self.setMinimumSize(300, 340)
        self.setModal(True)

        current = [s for s in (sections or []) if s in _LABELS]
        if not current:
            current = list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        self._result: list[str] = list(current)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Customize Draw Sidebar")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            "Check which sections show in the Draw sidebar, and drag to set "
            "their order. Path and Shapes must stay checked."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self._list, stretch=1)
        self._populate(current)

        sep(layout)
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.setMinimumWidth(90)
        apply_btn.setProperty("role", "primary")
        apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate(self, checked: list[str]) -> None:
        self._list.clear()
        checked_set = set(checked)
        rest = sorted(s for s in _LABELS if s not in checked_set)
        ordered = list(checked) + rest
        for key in ordered:
            item = QListWidgetItem(_LABELS[key])
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if key in checked_set
                else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)

    def _reset(self) -> None:
        self._populate(list(DEFAULT_DRAW_SIDEBAR_SECTIONS))

    def _checked_sections(self) -> list[str]:
        sections: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                sections.append(item.data(Qt.ItemDataRole.UserRole))
        return sections

    def _apply(self) -> None:
        checked = self._checked_sections()
        self._result = (
            checked
            if self._REQUIRED.issubset(checked)
            else list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        )
        self.accept()

    def get_sections(self) -> list[str]:
        """Return the saved section list after the dialog is accepted."""
        return list(self._result)
