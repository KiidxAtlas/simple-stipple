"""Customize which commands appear as wedges in the canvas radial ("Q")
quick menu, and in what order. The pool is every non-hidden canvas Command
(src.ui.canvas.commands.COMMANDS) — draw primitives, edit/selection ops,
booleans, view/grid toggles, and more — so there is one place (commands.py)
that defines what a wedge can be, not a second parallel action list.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from src.settings import DEFAULT_RADIAL_MENU_TOOLS
from src.ui.canvas import commands as canvas_commands
from src.ui.core.factories import sep

# id -> (label, category), built once from the canvas Command registry —
# the actual pool of everything a wedge can be.
_POOL: dict[str, tuple[str, str]] = {
    c.id: (c.label, c.category or "Other")
    for c in canvas_commands.COMMANDS
    if not c.hidden and c.id != "canvas.radial_menu"
}


class RadialMenuDialog(QDialog):
    """Check which commands show up in the "Q" quick menu; drag to reorder.
    At least 3 must stay checked or Apply falls back to the defaults."""

    _MIN_TOOLS = 3

    def __init__(self, parent: QWidget | None = None, tools: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Radial Menu")
        self.setObjectName("radial-menu-dialog")
        self.resize(400, 560)
        self.setMinimumSize(340, 400)
        self.setModal(True)

        current = [t for t in (tools or []) if t in _POOL]
        if not current:
            current = list(DEFAULT_RADIAL_MENU_TOOLS)
        self._result: list[str] = list(current)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Customize Radial Menu")
        title.setProperty("role", "page-title")
        layout.addWidget(title)

        subtitle = QLabel(
            'Check which commands show up as wedges in the "Q" quick menu, and '
            "drag to set their order. At least 3 must stay checked."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(f"Filter {len(_POOL)} commands…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

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
        # Checked items first (in their saved order), then the rest of the
        # pool grouped by category, so unchecking-then-rechecking doesn't
        # lose an item's spot and the long tail is still browsable.
        rest = sorted(
            (t for t in _POOL if t not in checked_set),
            key=lambda t: (_POOL[t][1], _POOL[t][0]),
        )
        ordered = list(checked) + rest
        for tool_id in ordered:
            label, category = _POOL[tool_id]
            item = QListWidgetItem(f"{label}  ·  {category}")
            item.setData(Qt.ItemDataRole.UserRole, tool_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if tool_id in checked_set
                else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        self._apply_filter(self._filter.text() if hasattr(self, "_filter") else "")

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            tool_id = item.data(Qt.ItemDataRole.UserRole)
            label, category = _POOL[tool_id]
            haystack = f"{tool_id} {label} {category}".lower()
            item.setHidden(bool(query) and query not in haystack)

    def _reset(self) -> None:
        self._populate(list(DEFAULT_RADIAL_MENU_TOOLS))

    def _checked_tools(self) -> list[str]:
        tools: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                tools.append(item.data(Qt.ItemDataRole.UserRole))
        return tools

    def _apply(self) -> None:
        checked = self._checked_tools()
        self._result = checked if len(checked) >= self._MIN_TOOLS else list(
            DEFAULT_RADIAL_MENU_TOOLS
        )
        self.accept()

    def get_tools(self) -> list[str]:
        """Return the saved tool list after the dialog is accepted."""
        return list(self._result)
