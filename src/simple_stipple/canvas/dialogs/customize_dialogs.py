"""Two "check which items show, drag to reorder" customize dialogs:
the canvas radial ("Q") quick menu, and the Draw sidebar's sections/tools.

Merged into one file — ``DrawSidebarCustomizeDialog`` mirrors
``RadialMenuDialog``'s checkbox + drag-to-reorder ``QListWidget`` pattern
(same interaction, smaller fixed pools: the sidebar's own sections/tools
instead of the full command registry, so no filter box is needed).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.platform.config import (
    CONTEXT_MENU_SECTION_LABELS,
    CONTEXT_MENU_TRANSFORM_ITEMS,
    DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS,
    DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS,
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DRAW_SIDEBAR_PATH_TOOL_LABELS,
    DRAW_SIDEBAR_SECTION_LABELS,
    DRAW_SIDEBAR_SHAPE_TOOL_LABELS,
)
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.layout import sep
from simple_stipple.ui.components.tokens import (
    SPACE_LG,
    SPACE_MD,
)

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
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

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
        self._list.itemChanged.connect(self._validate)
        layout.addWidget(self._list, stretch=1)

        self._hint = QLabel("")
        self._hint.setProperty("role", "hint-sm")
        layout.addWidget(self._hint)

        self._populate(current)

        sep(layout)
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setAutoDefault(False)
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        self._apply_btn = QPushButton("Save")
        self._apply_btn.setMinimumWidth(90)
        self._apply_btn.setProperty("role", "primary")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self._validate()
        install_dialog_focus_lifecycle(self, self._filter)

    def _validate(self) -> None:
        """Disable Save and explain why, instead of silently falling back
        to defaults when too few commands stay checked."""
        if not hasattr(self, "_apply_btn"):
            return  # items are still being populated; nothing to validate yet
        count = len(self._checked_tools())
        short = self._MIN_TOOLS - count
        self._apply_btn.setEnabled(count >= self._MIN_TOOLS)
        self._hint.setText(
            f"Check {short} more command{'s' if short != 1 else ''} "
            f"(at least {self._MIN_TOOLS} required)."
            if short > 0
            else f"{count} commands selected."
        )

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
    labels: dict[str, str],
    checked: list[str],
    defaults: tuple[str, ...],
    *,
    default_when_empty: bool = True,
) -> QListWidget:
    widget = QListWidget()
    widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    # A QListWidget normally derives each row's height lazily from its
    # delegate.  That can leave the first paint with overlapping checkbox
    # rows until an item changes state (exactly the behaviour seen in the
    # context-menu customizer).  These lists contain short, single-line
    # commands, so stable uniform rows are both clearer and more reliable.
    widget.setUniformItemSizes(True)
    widget.setSpacing(2)
    widget.setWordWrap(False)
    widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    _fill_list(widget, labels, checked or list(defaults) if default_when_empty else checked)
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
        item.setSizeHint(QSize(0, 36))
        # Long section names remain understandable even when the dialog is
        # deliberately narrowed to keep the canvas visible behind it.
        item.setToolTip(labels[key])
        widget.addItem(item)
    # Populate and lay out synchronously so a just-opened dialog has the
    # same geometry as one whose checkbox has subsequently been toggled.
    widget.doItemsLayout()


def _checked_keys(widget: QListWidget) -> list[str]:
    keys: list[str] = []
    for i in range(widget.count()):
        item = widget.item(i)
        if item.checkState() == Qt.CheckState.Checked:
            keys.append(item.data(Qt.ItemDataRole.UserRole))
    return keys


def _build_ordered_list(labels: dict[str, str], keys: list[str]) -> QListWidget:
    """Build a drag-reorder list containing only explicitly assigned rows."""
    widget = QListWidget()
    widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    widget.setUniformItemSizes(True)
    widget.setSpacing(2)
    widget.setWordWrap(False)
    widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    _fill_ordered_list(widget, labels, keys)
    return widget


def _fill_ordered_list(widget: QListWidget, labels: dict[str, str], keys: list[str]) -> None:
    widget.clear()
    for key in dict.fromkeys(key for key in keys if key in labels):
        item = QListWidgetItem(labels[key])
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(QSize(0, 36))
        item.setToolTip(labels[key])
        widget.addItem(item)
    widget.doItemsLayout()


def _ordered_keys(widget: QListWidget) -> list[str]:
    return [
        widget.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(widget.count())
    ]


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
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

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
        self._list.itemChanged.connect(self._validate)
        layout.addWidget(self._list, stretch=2)
        self._populate(current)

        sep(layout)

        path_label = QLabel("Path tools")
        path_label.setProperty("role", "section-title")
        layout.addWidget(path_label)
        self._path_list = _build_list(
            _PATH_LABELS, self._path_result, DEFAULT_DRAW_SIDEBAR_PATH_TOOLS
        )
        self._path_list.itemChanged.connect(self._validate)
        layout.addWidget(self._path_list, stretch=1)

        shape_label = QLabel("Shape tools")
        shape_label.setProperty("role", "section-title")
        layout.addWidget(shape_label)
        self._shape_list = _build_list(
            _SHAPE_LABELS, self._shape_result, DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS
        )
        self._shape_list.itemChanged.connect(self._validate)
        layout.addWidget(self._shape_list, stretch=1)

        self._hint = QLabel("")
        self._hint.setProperty("role", "hint-sm")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        sep(layout)
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setAutoDefault(False)
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        self._apply_btn = QPushButton("Save")
        self._apply_btn.setMinimumWidth(90)
        self._apply_btn.setProperty("role", "primary")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self._validate()
        install_dialog_focus_lifecycle(self, self._list)

    def _populate(self, checked: list[str]) -> None:
        _fill_list(self._list, _LABELS, checked)

    def _reset(self) -> None:
        self._populate(list(DEFAULT_DRAW_SIDEBAR_SECTIONS))
        _fill_list(self._path_list, _PATH_LABELS, list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS))
        _fill_list(self._shape_list, _SHAPE_LABELS, list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS))

    def _checked_sections(self) -> list[str]:
        return _checked_keys(self._list)

    def _validate(self) -> None:
        """Disable Save and explain why, instead of silently falling back
        to defaults when a required section/tool list is left empty."""
        if not hasattr(self, "_apply_btn"):
            return  # items are still being populated; nothing to validate yet
        missing_required = self._REQUIRED - set(self._checked_sections())
        path_empty = not _checked_keys(self._path_list)
        shape_empty = not _checked_keys(self._shape_list)
        problems = []
        if missing_required:
            names = ", ".join(sorted(_LABELS.get(k, k) for k in missing_required))
            problems.append(f"{names} must stay checked")
        if path_empty:
            problems.append("at least one Path tool must stay checked")
        if shape_empty:
            problems.append("at least one Shape tool must stay checked")
        self._apply_btn.setEnabled(not problems)
        self._hint.setText("; ".join(problems).capitalize() + "." if problems else "")

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
    """Choose visible context-menu controls and their placement."""

    def __init__(
        self,
        parent: QWidget | None = None,
        sections: list[str] | None = None,
        overflow_sections: list[str] | None = None,
        profiles: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Canvas Context Menu")
        # The former single narrow column compressed two independent ordered
        # lists plus a submenu editor into one viewport. Give the lists their
        # own horizontal space so checkbox labels and drag targets remain
        # readable before the user interacts with them.
        self.resize(820, 740)
        self.setMinimumSize(680, 580)
        self.setModal(True)
        labels = dict(CONTEXT_MENU_SECTION_LABELS)
        current = [key for key in (sections or []) if key in labels]
        if not current:
            current = list(DEFAULT_CONTEXT_MENU_SECTIONS)
        self._result = list(current)
        overflow = [key for key in (overflow_sections or []) if key in labels]
        if overflow_sections is None:
            overflow = list(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS)
        self._overflow_result = overflow
        self._profiles: dict[str, dict[str, list[str]]] = {
            name: {
                "sections": list((profiles or {}).get(name, {}).get("sections", current)),
                "overflow": list((profiles or {}).get(name, {}).get("overflow", overflow)),
                "transform": list(
                    (profiles or {}).get(name, {}).get(
                        "transform", [key for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS]
                    )
                ),
            }
            for name in ("draft", "pattern", "trace")
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)
        title = QLabel("Canvas Context Menu")
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Customize for"))
        self._profile_combo = QComboBox()
        self._profile_combo.addItem("Draft", "draft")
        self._profile_combo.addItem("Pattern", "pattern")
        self._profile_combo.addItem("Trace", "trace")
        profile_row.addWidget(self._profile_combo, stretch=1)
        layout.addLayout(profile_row)
        subtitle = QLabel(
            "Choose what appears directly in the canvas menu and what lives under More actions. "
            "Drag rows to set order. View always remains available."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self._list = _build_list(labels, current, DEFAULT_CONTEXT_MENU_SECTIONS)
        self._lock_view_item()
        self._overflow_list = _build_list(
            labels,
            overflow,
            DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS,
        )
        menu_lists = QHBoxLayout()
        menu_lists.setSpacing(SPACE_LG)
        direct_column = QVBoxLayout()
        direct_label = QLabel("Show in menu")
        direct_label.setProperty("role", "section-title")
        direct_column.addWidget(direct_label)
        self._list.setMinimumWidth(370)
        direct_column.addWidget(self._list, stretch=1)
        menu_lists.addLayout(direct_column, stretch=3)
        overflow_column = QVBoxLayout()
        overflow_label = QLabel("Place under More actions")
        overflow_label.setProperty("role", "section-title")
        overflow_column.addWidget(overflow_label)
        self._overflow_list.setMinimumWidth(270)
        overflow_column.addWidget(self._overflow_list, stretch=1)
        menu_lists.addLayout(overflow_column, stretch=2)
        layout.addLayout(menu_lists, stretch=1)
        transform_label = QLabel("Transform submenu")
        transform_label.setProperty("role", "section-title")
        layout.addWidget(transform_label)
        self._transform_list = _build_list(
            dict(CONTEXT_MENU_TRANSFORM_ITEMS),
            self._profiles["draft"]["transform"],
            tuple(key for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS),
        )
        self._transform_list.setMinimumHeight(180)
        self._transform_list.setMaximumHeight(220)
        layout.addWidget(self._transform_list)
        self._profile_combo.currentIndexChanged.connect(self._switch_profile)
        sep(layout)
        buttons = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.setAutoDefault(False)
        reset.clicked.connect(
            lambda: _fill_list(self._list, labels, list(DEFAULT_CONTEXT_MENU_SECTIONS))
        )
        reset.clicked.connect(
            lambda: _fill_list(
                self._transform_list,
                dict(CONTEXT_MENU_TRANSFORM_ITEMS),
                [key for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS],
            )
        )
        reset.clicked.connect(self._lock_view_item)
        reset.clicked.connect(
            lambda: _fill_list(
                self._overflow_list,
                labels,
                list(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS),
            )
        )
        buttons.addWidget(reset)
        buttons.addStretch()
        apply_button = QPushButton("Save")
        apply_button.setProperty("role", "primary")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(apply_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        install_dialog_focus_lifecycle(self, self._list)

    def _lock_view_item(self) -> None:
        """Prevent unchecking "View" instead of silently re-adding it later.

        View provides the only zoom/fit controls in the context menu; the
        dialog used to let it be unchecked and then quietly restore it on
        Save, which looked like the checkbox wasn't working.
        """
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "view":
                item.setCheckState(Qt.CheckState.Checked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setToolTip("View always stays visible")
                break

    def _apply(self) -> None:
        self._save_current_profile()
        active = str(self._profile_combo.currentData())
        self._result = list(self._profiles[active]["sections"])
        self._overflow_result = list(self._profiles[active]["overflow"])
        self.accept()

    def _save_current_profile(self) -> None:
        checked = _checked_keys(self._list)
        if "view" not in checked:
            checked.append("view")
        active = str(self._profile_combo.currentData())
        self._profiles[active] = {"sections": checked, "overflow": [
            key for key in _checked_keys(self._overflow_list) if key in checked
        ], "transform": _checked_keys(self._transform_list)}

    def _switch_profile(self, _index: int) -> None:
        self._save_current_profile()
        active = str(self._profile_combo.currentData())
        labels = dict(CONTEXT_MENU_SECTION_LABELS)
        _fill_list(self._list, labels, self._profiles[active]["sections"])
        _fill_list(self._overflow_list, labels, self._profiles[active]["overflow"])
        _fill_list(
            self._transform_list,
            dict(CONTEXT_MENU_TRANSFORM_ITEMS),
            self._profiles[active]["transform"],
        )
        self._lock_view_item()

    def get_sections(self) -> list[str]:
        return list(self._result)

    def get_overflow_sections(self) -> list[str]:
        return list(self._overflow_result)

    def get_profiles(self) -> dict[str, dict[str, list[str]]]:
        return {
            name: {key: list(value) for key, value in data.items()}
            for name, data in self._profiles.items()
        }


# The section customizer above is retained to read older profiles. New
# profiles use this leaf-action customizer instead: every command is a row,
# rather than a broad section that silently governs several unrelated actions.
_CONTEXT_ACTION_LABELS: dict[str, str] = {
    command.id: command.label for command in canvas_commands.COMMANDS if not command.hidden
}
_CONTEXT_ACTION_LABELS.update(
    {f"transform.{key}": label for key, label in CONTEXT_MENU_TRANSFORM_ITEMS}
)
_CONTEXT_ACTION_LABELS.update(
    {
        "context.create.rectangle": "Rectangle (drag)",
        "context.create.circle": "Circle (drag)",
        "context.create.slot": "Slot (drag)",
        "context.create.hexagon": "Hexagon (drag)",
        "context.create.ring": "Ring",
        "context.create.gear": "Gear / sprocket",
        "context.create.spiral": "Spiral",
        "context.create.teardrop": "Teardrop",
        "context.create.keyhole": "Keyhole",
        "context.create.superellipse": "Superellipse / squircle",
        "context.create.rounded_star": "Rounded star",
        "context.create.chamfered_star": "Chamfered star",
        "context.create.finger_joint_box": "Finger-joint box",
        "context.create.dovetail_box": "Dovetail box",
        "context.create.tabbed_panel": "Tabbed panel",
        "context.entity.select": "Select",
        "context.entity.deselect": "Deselect",
        "context.entity.delete": "Delete",
        "context.entity.edit_text": "Edit text…",
        "context.pattern_cell.instance": "This cell only",
        "context.pattern_cell.repeat": "Every matching tile",
        "context.selection.move": "Move to Coordinate…",
        "context.selection.close_path": "Close path",
        "context.selection.fit": "Frame selection",
        "context.selection.smooth": "Smooth",
        "context.selection.simplify": "Simplify…",
        "context.selection.create_zone": "Apply treatment to selection",
        "context.selection.array_grid": "Grid array…",
        "context.selection.array_radial": "Radial array…",
        "context.bezier_node.corner": "Corner — independent handles",
        "context.bezier_node.smooth": "Smooth — aligned handles",
        "context.bezier_node.symmetric": "Symmetric — linked handles",
        "context.arrange.left": "Align left",
        "context.arrange.center_x": "Align center X",
        "context.arrange.right": "Align right",
        "context.arrange.top": "Align top",
        "context.arrange.center_y": "Align center Y",
        "context.arrange.bottom": "Align bottom",
        "context.arrange.distribute_horizontal_gap": "Distribute horizontal — gap…",
        "context.arrange.distribute_vertical_gap": "Distribute vertical — gap…",
        "context.arrange.distribute_horizontal_centers": "Distribute horizontal — center-to-center…",
        "context.arrange.distribute_vertical_centers": "Distribute vertical — center-to-center…",
        "context.share.outline": "Use as outline",
        "context.share.custom_tile": "Use as Custom Tile",
        "context.share.draft": "Send to Draft",
        "context.share.move_to_layer": "Move selected to layer",
        "context.view.select": "Select [Esc]",
    }
)


class ContextMenuActionCustomizeDialog(QDialog):
    """Configure each context-menu action independently for each workspace."""

    def __init__(self, parent: QWidget | None = None, profiles: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize Canvas Context Menu")
        self.setObjectName("context-menu-action-customize-dialog")
        self.resize(1040, 760)
        self.setMinimumSize(800, 600)
        self.setModal(True)
        default_items = list(_CONTEXT_ACTION_LABELS)
        raw_profiles = profiles if isinstance(profiles, dict) else {}
        self._profiles: dict[str, dict[str, list[str]]] = {}
        for name in ("draft", "pattern", "trace"):
            saved = raw_profiles.get(name, {})
            saved = saved if isinstance(saved, dict) else {}
            action_items_configured = bool(saved.get("action_items_configured", []))
            raw_items = saved.get("items")
            items = (
                [key for key in raw_items if key in _CONTEXT_ACTION_LABELS]
                if action_items_configured and isinstance(raw_items, list)
                else list(default_items)
            )
            overflow = [
                key for key in saved.get("overflow_items", []) if key in _CONTEXT_ACTION_LABELS
            ] if action_items_configured else list(DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS)
            self._profiles[name] = {
                "items": items,
                "overflow_items": overflow,
            }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)
        title = QLabel("Canvas Context Menu")
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Customize for"))
        self._profile_combo = QComboBox()
        for name in ("draft", "pattern", "trace"):
            self._profile_combo.addItem(name.capitalize(), name)
        profile_row.addWidget(self._profile_combo, stretch=1)
        layout.addLayout(profile_row)
        subtitle = QLabel(
            "Every row is one action. Enabled actions stay at the top; drag them to set "
            "the menu order, and choose which enabled actions live under More actions."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Search actions…")
        self._filter.setClearButtonEnabled(True)
        self._filter.setToolTip("Filter actions by name")
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)
        lists = QHBoxLayout()
        lists.setSpacing(SPACE_LG)
        direct = QVBoxLayout()
        direct_label = QLabel("Show in menu")
        direct_label.setProperty("role", "section-title")
        direct.addWidget(direct_label)
        direct_actions = QHBoxLayout()
        select_all = QPushButton("All")
        select_all.setToolTip("Show every action in this context menu")
        select_all.clicked.connect(lambda: self._set_all_visible(True))
        direct_actions.addWidget(select_all)
        select_none = QPushButton("None")
        select_none.setToolTip("Hide every configurable action in this context menu")
        select_none.clicked.connect(lambda: self._set_all_visible(False))
        direct_actions.addWidget(select_none)
        direct_actions.addStretch()
        direct.addLayout(direct_actions)
        # Build the initially visible Draft profile directly.  Rebuilding the
        # just-populated Qt lists in ``_load_profile("draft")`` made their
        # first lifetime needlessly destructive; with PySide this could crash
        # in the native QListWidgetItem cleanup path before the dialog opened.
        initial_profile = self._profiles["draft"]
        self._list = _build_list(
            _CONTEXT_ACTION_LABELS,
            initial_profile["items"],
            tuple(default_items),
            default_when_empty=False,
        )
        self._list.setMinimumWidth(360)
        direct.addWidget(self._list, stretch=1)
        lists.addLayout(direct, stretch=1)
        overflow_column = QVBoxLayout()
        overflow_label = QLabel("Show under More actions")
        overflow_label.setProperty("role", "section-title")
        overflow_column.addWidget(overflow_label)
        more_actions = QHBoxLayout()
        more_all = QPushButton("All")
        more_all.setToolTip("Put every enabled action under More actions")
        more_all.clicked.connect(lambda: self._set_all_more(True))
        more_actions.addWidget(more_all)
        more_none = QPushButton("None")
        more_none.setToolTip("Keep every action in the main context menu")
        more_none.clicked.connect(lambda: self._set_all_more(False))
        more_actions.addWidget(more_none)
        more_actions.addStretch()
        overflow_column.addLayout(more_actions)
        self._overflow_list = _build_list(
            _CONTEXT_ACTION_LABELS,
            initial_profile["overflow_items"],
            (),
            default_when_empty=False,
        )
        self._overflow_list.setMinimumWidth(360)
        overflow_column.addWidget(self._overflow_list, stretch=1)
        lists.addLayout(overflow_column, stretch=1)
        layout.addLayout(lists, stretch=1)
        self._profile_combo.currentIndexChanged.connect(self._switch_profile)
        self._reordering_actions = False
        self._reordering_more_actions = False
        self._list.itemChanged.connect(self._prioritize_enabled_action)
        self._list.model().rowsMoved.connect(self._normalize_action_rows)
        self._overflow_list.itemChanged.connect(self._prioritize_more_action)
        self._overflow_list.model().rowsMoved.connect(self._normalize_more_action_rows)
        sep(layout)
        buttons = QHBoxLayout()
        reset = QPushButton("Reset this workspace")
        reset.setAutoDefault(False)
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch()
        save = QPushButton("Save")
        save.setProperty("role", "primary")
        save.setDefault(True)
        save.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        install_dialog_focus_lifecycle(self, self._filter)

    def _save_current_profile(self) -> None:
        active = str(self._profile_combo.currentData())
        items = _checked_keys(self._list)
        self._profiles[active] = {
            "items": items,
            "overflow_items": [key for key in _checked_keys(self._overflow_list) if key in items],
        }

    def _load_profile(self, name: str) -> None:
        profile = self._profiles[name]
        self._reordering_actions = True
        self._reordering_more_actions = True
        try:
            _fill_list(self._list, _CONTEXT_ACTION_LABELS, profile["items"])
            _fill_list(self._overflow_list, _CONTEXT_ACTION_LABELS, profile["overflow_items"])
        finally:
            self._reordering_actions = False
            self._reordering_more_actions = False
        self._apply_filter(self._filter.text())

    def _prioritize_enabled_action(self, item: QListWidgetItem) -> None:
        """Append a newly enabled action without losing drag-set order."""
        if item.checkState() != Qt.CheckState.Checked:
            self._set_more_action_checked(
                str(item.data(Qt.ItemDataRole.UserRole)), checked=False
            )
        self._prioritize_checked_row(self._list, item, "_reordering_actions")

    def _prioritize_more_action(self, item: QListWidgetItem) -> None:
        """A More action is also enabled in the primary context menu."""
        if item.checkState() == Qt.CheckState.Checked:
            self._set_action_checked(str(item.data(Qt.ItemDataRole.UserRole)), checked=True)
        self._prioritize_checked_row(self._overflow_list, item, "_reordering_more_actions")

    def _prioritize_checked_row(
        self, widget: QListWidget, item: QListWidgetItem, guard_name: str
    ) -> None:
        if getattr(self, guard_name):
            return
        setattr(self, guard_name, True)
        try:
            row = widget.row(item)
            moved = widget.takeItem(row)
            if moved is None:
                return
            if moved.checkState() == Qt.CheckState.Checked:
                enabled_count = sum(
                    widget.item(index).checkState() == Qt.CheckState.Checked
                    for index in range(widget.count())
                )
                widget.insertItem(enabled_count, moved)
            else:
                first_disabled = next(
                    (
                        index
                        for index in range(widget.count())
                        if widget.item(index).checkState() != Qt.CheckState.Checked
                    ),
                    widget.count(),
                )
                widget.insertItem(first_disabled, moved)
        finally:
            setattr(self, guard_name, False)

    def _normalize_action_rows(self, *_args: object) -> None:
        """Keep enabled entries together after a drag, preserving their order."""
        self._normalize_checked_rows(self._list, "_reordering_actions")

    def _normalize_more_action_rows(self, *_args: object) -> None:
        self._normalize_checked_rows(self._overflow_list, "_reordering_more_actions")

    def _normalize_checked_rows(self, widget: QListWidget, guard_name: str) -> None:
        if getattr(self, guard_name):
            return
        setattr(self, guard_name, True)
        try:
            items = [widget.takeItem(0) for _ in range(widget.count())]
            ordered = [
                item for item in items if item is not None and item.checkState() == Qt.CheckState.Checked
            ] + [item for item in items if item is not None and item.checkState() != Qt.CheckState.Checked]
            for item in ordered:
                widget.addItem(item)
        finally:
            setattr(self, guard_name, False)

    def _set_action_checked(self, key: str, *, checked: bool) -> None:
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                return

    def _set_more_action_checked(self, key: str, *, checked: bool) -> None:
        for index in range(self._overflow_list.count()):
            item = self._overflow_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                return

    def _apply_filter(self, text: str) -> None:
        """Keep the action catalogue navigable without changing its order."""
        query = text.casefold().strip()
        for widget in (self._list, self._overflow_list):
            for index in range(widget.count()):
                item = widget.item(index)
                key = str(item.data(Qt.ItemDataRole.UserRole))
                haystack = f"{_CONTEXT_ACTION_LABELS.get(key, '')} {key}".casefold()
                item.setHidden(bool(query) and query not in haystack)

    def _switch_profile(self, _index: int) -> None:
        self._save_current_profile()
        self._load_profile(str(self._profile_combo.currentData()))

    def _reset(self) -> None:
        self._reordering_actions = True
        self._reordering_more_actions = True
        try:
            _fill_list(self._list, _CONTEXT_ACTION_LABELS, list(_CONTEXT_ACTION_LABELS))
            _fill_list(
                self._overflow_list,
                _CONTEXT_ACTION_LABELS,
                list(DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS),
            )
        finally:
            self._reordering_actions = False
            self._reordering_more_actions = False

    def _set_all_visible(self, visible: bool) -> None:
        self._reordering_actions = True
        try:
            for index in range(self._list.count()):
                self._list.item(index).setCheckState(
                    Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
                )
        finally:
            self._reordering_actions = False
        self._normalize_action_rows()
        if not visible:
            self._set_all_more(False)

    def _set_all_more(self, visible: bool) -> None:
        self._reordering_more_actions = True
        try:
            enabled = set(_checked_keys(self._list))
            for index in range(self._overflow_list.count()):
                item = self._overflow_list.item(index)
                should_show = visible and item.data(Qt.ItemDataRole.UserRole) in enabled
                item.setCheckState(
                    Qt.CheckState.Checked if should_show else Qt.CheckState.Unchecked
                )
        finally:
            self._reordering_more_actions = False
        self._normalize_more_action_rows()

    def _apply(self) -> None:
        self._save_current_profile()
        self.accept()

    def get_profiles(self) -> dict[str, dict[str, list[str]]]:
        profiles = {
            name: {key: list(value) for key, value in data.items()}
            for name, data in self._profiles.items()
        }
        for profile in profiles.values():
            profile["action_items_configured"] = ["yes"]
        return profiles


__all__ = [
    "ContextMenuActionCustomizeDialog",
    "ContextMenuCustomizeDialog",
    "DrawSidebarCustomizeDialog",
    "RadialMenuDialog",
]
