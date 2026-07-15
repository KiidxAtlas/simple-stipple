"""Two "check which items show, drag to reorder" customize dialogs:
the canvas radial ("Q") quick menu, and the Draw sidebar's sections/tools.

Merged into one file — ``DrawSidebarCustomizeDialog`` mirrors
``RadialMenuDialog``'s checkbox + drag-to-reorder ``QListWidget`` pattern
(same interaction, smaller fixed pools: the sidebar's own sections/tools
instead of the full command registry, so no filter box is needed).
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

from src.infra.settings import (
    CONTEXT_MENU_SECTION_LABELS,
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DRAW_SIDEBAR_PATH_TOOL_LABELS,
    DRAW_SIDEBAR_SECTION_LABELS,
    DRAW_SIDEBAR_SHAPE_TOOL_LABELS,
)
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.components import sep

# ══════════════════════════════════════════════════════════════════════════
# Radial menu ("Q" quick menu)
# ══════════════════════════════════════════════════════════════════════════

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
        apply_btn = QPushButton("Save")
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
                Qt.CheckState.Checked if tool_id in checked_set else Qt.CheckState.Unchecked
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
        self._result = (
            checked if len(checked) >= self._MIN_TOOLS else list(DEFAULT_RADIAL_MENU_TOOLS)
        )
        self.accept()

    def get_tools(self) -> list[str]:
        """Return the saved tool list after the dialog is accepted."""
        return list(self._result)


# ══════════════════════════════════════════════════════════════════════════
# Draw sidebar sections/tools
# ══════════════════════════════════════════════════════════════════════════

_LABELS: dict[str, str] = dict(DRAW_SIDEBAR_SECTION_LABELS)
_PATH_LABELS: dict[str, str] = dict(DRAW_SIDEBAR_PATH_TOOL_LABELS)
_SHAPE_LABELS: dict[str, str] = dict(DRAW_SIDEBAR_SHAPE_TOOL_LABELS)


def _build_list(
    labels: dict[str, str], checked: list[str], defaults: tuple[str, ...]
) -> QListWidget:
    widget = QListWidget()
    widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    _fill_list(widget, labels, checked or list(defaults))
    return widget


def _fill_list(widget: QListWidget, labels: dict[str, str], checked: list[str]) -> None:
    widget.clear()
    checked_set = set(checked)
    rest = sorted(s for s in labels if s not in checked_set)
    for key in list(checked) + rest:
        item = QListWidgetItem(labels[key])
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if key in checked_set else Qt.CheckState.Unchecked)
        widget.addItem(item)


def _checked_keys(widget: QListWidget) -> list[str]:
    keys: list[str] = []
    for i in range(widget.count()):
        item = widget.item(i)
        if item.checkState() == Qt.CheckState.Checked:
            keys.append(item.data(Qt.ItemDataRole.UserRole))
    return keys


class DrawSidebarCustomizeDialog(QDialog):
    """Check which Draw-sidebar sections show; drag to reorder. At least
    Path and Shapes must stay checked — a draw sidebar with no way to pick
    a tool isn't useful — or Apply falls back to the defaults.

    Also lets individual Path/Shapes tool icons be hidden/reordered within
    those two sections, independent of the section-level toggle above; each
    of those two lists requires at least one tool to stay checked."""

    _REQUIRED = {"path", "shapes"}

    def __init__(
        self,
        parent: QWidget | None = None,
        sections: list[str] | None = None,
        path_tools: list[str] | None = None,
        shape_tools: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Draw Sidebar")
        self.setObjectName("draw-sidebar-customize-dialog")
        self.resize(380, 640)
        self.setMinimumSize(320, 480)
        self.setModal(True)

        current = [s for s in (sections or []) if s in _LABELS]
        if not current:
            current = list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        self._result: list[str] = list(current)
        self._path_result: list[str] = [t for t in (path_tools or []) if t in _PATH_LABELS] or list(
            DEFAULT_DRAW_SIDEBAR_PATH_TOOLS
        )
        self._shape_result: list[str] = [
            t for t in (shape_tools or []) if t in _SHAPE_LABELS
        ] or list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)

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
        layout.addWidget(self._list, stretch=2)
        self._populate(current)

        sep(layout)

        path_label = QLabel("Path tools")
        path_label.setProperty("role", "section-title")
        layout.addWidget(path_label)
        self._path_list = _build_list(
            _PATH_LABELS, self._path_result, DEFAULT_DRAW_SIDEBAR_PATH_TOOLS
        )
        layout.addWidget(self._path_list, stretch=1)

        shape_label = QLabel("Shape tools")
        shape_label.setProperty("role", "section-title")
        layout.addWidget(shape_label)
        self._shape_list = _build_list(
            _SHAPE_LABELS, self._shape_result, DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS
        )
        layout.addWidget(self._shape_list, stretch=1)

        sep(layout)
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        apply_btn = QPushButton("Save")
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
        _fill_list(self._list, _LABELS, checked)

    def _reset(self) -> None:
        self._populate(list(DEFAULT_DRAW_SIDEBAR_SECTIONS))
        _fill_list(self._path_list, _PATH_LABELS, list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS))
        _fill_list(self._shape_list, _SHAPE_LABELS, list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS))

    def _checked_sections(self) -> list[str]:
        return _checked_keys(self._list)

    def _apply(self) -> None:
        checked = self._checked_sections()
        self._result = (
            checked if self._REQUIRED.issubset(checked) else list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        )
        path_checked = _checked_keys(self._path_list)
        self._path_result = path_checked or list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
        shape_checked = _checked_keys(self._shape_list)
        self._shape_result = shape_checked or list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
        self.accept()

    def get_sections(self) -> list[str]:
        """Return the saved section list after the dialog is accepted."""
        return list(self._result)

    def get_path_tools(self) -> list[str]:
        """Return the saved Path tool list (which icons, in what order)."""
        return list(self._path_result)

    def get_shape_tools(self) -> list[str]:
        """Return the saved Shapes tool list (which icons, in what order)."""
        return list(self._shape_result)


class ContextMenuCustomizeDialog(QDialog):
    """Choose which optional top-level canvas context-menu sections appear."""

    def __init__(self, parent: QWidget | None = None, sections: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Canvas Context Menu")
        self.resize(460, 540)
        self.setMinimumSize(380, 420)
        self.setModal(True)
        labels = dict(CONTEXT_MENU_SECTION_LABELS)
        current = [key for key in (sections or []) if key in labels]
        if not current:
            current = list(DEFAULT_CONTEXT_MENU_SECTIONS)
        self._result = list(current)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("Canvas Context Menu")
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Uncheck optional sections you do not want on right-click. Direct Select, "
            "Delete, and Cutout actions remain available when clicking a shape. View "
            "remains enabled so an empty-canvas menu is never blank."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self._list = _build_list(labels, current, DEFAULT_CONTEXT_MENU_SECTIONS)
        layout.addWidget(self._list, stretch=1)
        sep(layout)
        buttons = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(
            lambda: _fill_list(self._list, labels, list(DEFAULT_CONTEXT_MENU_SECTIONS))
        )
        buttons.addWidget(reset)
        buttons.addStretch()
        apply_button = QPushButton("Save")
        apply_button.setProperty("role", "primary")
        apply_button.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(apply_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _apply(self) -> None:
        checked = _checked_keys(self._list)
        if "view" not in checked:
            checked.append("view")
        self._result = checked
        self.accept()

    def get_sections(self) -> list[str]:
        return list(self._result)


__all__ = ["ContextMenuCustomizeDialog", "DrawSidebarCustomizeDialog", "RadialMenuDialog"]
