"""DxfLayersTree widget — hierarchical layer and shape tree for canvas sidebars."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from simple_stipple.ui.components.focus import blur_focused_line_edit

_SWATCH_PALETTE = [
    "#f85149",  # red
    "#f0883e",  # orange
    "#d29922",  # amber
    "#3fb950",  # green
    "#39c5cf",  # teal
    "#58a6ff",  # blue
    "#a371f7",  # purple
    "#db61a2",  # pink
]


def _swatch_icon(color: str | None, *, size: int = 12) -> QIcon:
    """Small filled circle for a layer's color, or an empty/outline circle
    when no color is assigned (keeps row heights identical either way)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if color:
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor("#30363d"))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pm)


class DxfLayersTree(QFrame):
    """Hierarchical layer and shape tree for canvas sidebars."""

    selectionRequested = Signal(object)
    shapesDeleteRequested = Signal(str, list)  # layer, shape keys
    fitRequested = Signal()
    layerActivated = Signal(str)
    layerVisibilityChanged = Signal(str, bool)
    layerAdded = Signal(str)
    layerRenamed = Signal(str, str)
    layerDeleted = Signal(str)
    layersDeleteRequested = Signal(list)  # batch layer delete (multi-select)
    layersConsolidateRequested = Signal(list, str)  # source layers, target layer
    layerMoved = Signal(str, int)
    layerSoloRequested = Signal(str)
    bulkVisibilityRequested = Signal(bool)
    shapeVisibilityChanged = Signal(str, object, bool)
    shapeMoveRequested = Signal(str, object, str)
    shapesMoveRequested = Signal(str, list, str)
    moveSelectedRequested = Signal(str)
    shapeRenamed = Signal(str, object, str)  # layer, key, new_label
    layerColorChangeRequested = Signal(str, object)  # layer, hex str | None

    # New signals for shape operations from layer tree context menu.
    shapesGroupRequested = Signal(str, list)  # layer, shape keys → group
    shapesUngroupRequested = Signal(str, list)  # layer, shape keys (group tuples) → ungroup
    shapesMergeRequested = Signal(str, list)  # layer, shape keys → merge (union)
    shapesCopyRequested = Signal(str, list)  # layer, shape keys → copy to clipboard

    _ROLE_KIND = int(Qt.ItemDataRole.UserRole)
    _ROLE_INTERNAL_NAME = int(Qt.ItemDataRole.UserRole + 1)
    _ROLE_DISPLAY_NAME = int(Qt.ItemDataRole.UserRole + 2)
    _ROLE_VISIBLE = int(Qt.ItemDataRole.UserRole + 3)
    _ROLE_EDITABLE = int(Qt.ItemDataRole.UserRole + 4)
    _ROLE_SHAPE_KEY = int(Qt.ItemDataRole.UserRole + 5)
    _ROLE_SOURCE_LAYER = int(Qt.ItemDataRole.UserRole + 6)
    _ROLE_COLOR = int(Qt.ItemDataRole.UserRole + 7)

    class _LayerTreeWidget(QTreeWidget):
        def __init__(self, owner: DxfLayersTree) -> None:
            super().__init__()
            self._owner = owner

        def mousePressEvent(self, event) -> None:  # type: ignore[override]
            # Track whether this click is an additive multi-select (Ctrl/
            # Shift/Meta) so currentItemChanged can avoid treating it as
            # "activate this layer" — activating rebuilds the whole tree,
            # which wiped out the multi-selection the user was building.
            mods = event.modifiers()
            self._owner._multi_select_click = bool(
                mods
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )
            super().mousePressEvent(event)

        def keyPressEvent(self, event) -> None:  # type: ignore[override]
            if self._owner._editable and event.key() in (
                Qt.Key.Key_Delete,
                Qt.Key.Key_Backspace,
            ):
                self._owner._delete_current_layer()
                event.accept()
                return
            super().keyPressEvent(event)

        def startDrag(self, supportedActions) -> None:  # type: ignore[override]
            if not self._owner._editable:
                return
            current = self.currentItem()
            # Layer-reorder drag: when the user grabs a single layer row we
            # emit a "layer" payload so dropEvent can reposition it.
            selected = self.selectedItems()
            if (
                current is not None
                and current.data(0, DxfLayersTree._ROLE_KIND) == "layer"
                and (not selected or selected == [current])
            ):
                layer_name = str(current.data(0, DxfLayersTree._ROLE_INTERNAL_NAME) or "")
                if layer_name and layer_name != "geometry":
                    payload = {"kind": "layer", "name": layer_name}
                    drag = QDrag(self)
                    mime = self.mimeData([current])
                    mime.setData(
                        "application/x-simple-stipple-layer-tree",
                        json.dumps(payload).encode("utf-8"),
                    )
                    drag.setMimeData(mime)
                    drag.exec(supportedActions)
                    return

            shape_items = [
                item
                for item in self.selectedItems()
                if item.data(0, DxfLayersTree._ROLE_KIND) == "shape"
                and bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled)
            ]
            if not shape_items:
                item = self.currentItem()
                if item is None:
                    return
                if item.data(0, DxfLayersTree._ROLE_KIND) != "shape":
                    return
                shape_items = [item]

            source_layer = str(shape_items[0].data(0, DxfLayersTree._ROLE_SOURCE_LAYER) or "")
            if not source_layer:
                return

            shape_keys: list[Any] = []
            for item in shape_items:
                item_layer = str(item.data(0, DxfLayersTree._ROLE_SOURCE_LAYER) or "")
                if item_layer != source_layer:
                    continue
                shape_keys.append(item.data(0, DxfLayersTree._ROLE_SHAPE_KEY))
            if not shape_keys:
                return

            drag_payload: dict[str, Any] = {
                "kind": "shape",
                "source_layer": source_layer,
                "shape_keys": shape_keys,
            }
            drag = QDrag(self)
            mime = self.mimeData(shape_items)
            mime.setData(
                "application/x-simple-stipple-layer-tree",
                json.dumps(drag_payload).encode("utf-8"),
            )
            drag.setMimeData(mime)
            drag.exec(supportedActions)

        def dropEvent(self, event) -> None:  # type: ignore[override]
            if not self._owner._editable:
                return
            target = self.itemAt(event.position().toPoint())
            raw = event.mimeData().data("application/x-simple-stipple-layer-tree")
            if raw.isEmpty():
                return
            try:
                payload = json.loads(bytes(raw).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            kind = payload.get("kind")

            if kind == "layer":
                source_layer = str(payload.get("name", ""))
                if not source_layer or source_layer not in self._owner._layer_order:
                    return
                # Determine the destination index from the drop target.
                if target is None:
                    new_index = len(self._owner._layer_order) - 1
                else:
                    target_layer_item = (
                        target
                        if target.data(0, DxfLayersTree._ROLE_KIND) == "layer"
                        else target.parent()
                    )
                    if target_layer_item is None:
                        return
                    target_name = str(
                        target_layer_item.data(0, DxfLayersTree._ROLE_INTERNAL_NAME) or ""
                    )
                    if not target_name or target_name not in self._owner._layer_order:
                        return
                    if target_name == source_layer:
                        return
                    new_index = self._owner._layer_order.index(target_name)
                self._owner.layerMoved.emit(source_layer, new_index)
                event.acceptProposedAction()
                return

            if kind != "shape":
                return
            if target is None:
                return
            # Allow dropping onto a shape item — resolve to its parent layer.
            if target.data(0, DxfLayersTree._ROLE_KIND) == "shape":
                target = target.parent()
            if target is None or target.data(0, DxfLayersTree._ROLE_KIND) != "layer":
                return

            source_layer = str(payload.get("source_layer", ""))
            target_layer = str(target.data(0, DxfLayersTree._ROLE_INTERNAL_NAME))
            shape_keys = payload.get("shape_keys", [])
            if not isinstance(shape_keys, list):
                shape_keys = []
            if not source_layer or not target_layer or source_layer == target_layer:
                return

            # Emit a single batched signal so listeners can move every
            # dropped shape in one graph operation.
            self._owner.shapesMoveRequested.emit(source_layer, list(shape_keys), target_layer)
            event.acceptProposedAction()

    def __init__(self, title: str = "DXF Layers", *, editable: bool = False) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "layer-tree")
        self._editable = editable
        self._syncing = False
        self._multi_select_click = False
        self._layer_order: list[str] = []
        self._shape_keys_by_layer: dict[str, list[Any]] = {}
        self._filter_text: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        title_label = QLabel(title)
        title_label.setProperty("role", "callout-title")
        header.addWidget(title_label)
        self._summary = QLabel("0 layers")
        self._summary.setProperty("role", "callout-body")
        header.addWidget(self._summary)
        header.addStretch()
        if self._editable:
            self._add_button = self._make_tool_button(
                "+",
                "Add a new layer.\n"
                "Right-click any layer to rename / delete / reorder.\n"
                "Drag shapes onto a layer row to move them between layers.",
                self._prompt_add_layer,
            )
            header.addWidget(self._add_button)
            self._show_all_button = self._make_tool_button(
                "◉",
                "Show all layers",
                lambda: self.bulkVisibilityRequested.emit(True),
            )
            header.addWidget(self._show_all_button)
            self._hide_all_button = self._make_tool_button(
                "○",
                "Hide all layers",
                lambda: self.bulkVisibilityRequested.emit(False),
            )
            header.addWidget(self._hide_all_button)
        layout.addLayout(header)

        # Search / filter row — narrows the visible layer set as the user types.
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter layers and shapes…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._search)

        self._tree = self._LayerTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.setIndentation(14)
        self._tree.setIconSize(QSize(12, 12))
        self._tree.setDragEnabled(self._editable)
        self._tree.setAcceptDrops(self._editable)
        self._tree.setDropIndicatorShown(self._editable)
        self._tree.setDragDropMode(
            QAbstractItemView.DragDropMode.DragDrop
            if self._editable
            else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self._tree.currentItemChanged.connect(self._emit_current_item_change)
        self._tree.itemChanged.connect(self._handle_item_changed)
        self._tree.itemSelectionChanged.connect(self._emit_selection_request)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        if self._editable:
            self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._tree.customContextMenuRequested.connect(self._show_context_menu)
            self._tree.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)

        # Esc should always leave focused layer-tree inputs (search/rename editors).
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self._escape_focused_input)
        layout.addWidget(self._tree, stretch=1)

    def _escape_focused_input(self) -> None:
        blur_focused_line_edit(self._tree, within=self)

    @staticmethod
    def _make_tool_button(text: str, tooltip: str, slot) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setAccessibleName(tooltip)
        btn.setAccessibleDescription(tooltip)
        btn.setMinimumSize(32, 32)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _delete_current_layer(self) -> None:
        if not self._editable:
            return
        # Del on selected shape/group rows deletes those shapes; the layer
        # itself is only deleted when a layer row is what's selected.
        shape_keys = [
            self._item_shape_key(it)
            for it in self._tree.selectedItems()
            if self._item_kind(it) == "shape"
        ]
        if shape_keys:
            item = self._tree.currentItem()
            parent = item.parent() if item is not None else None
            layer = self._item_internal_name(parent) if parent is not None else ""
            self.shapesDeleteRequested.emit(layer, shape_keys)
            return
        item = self._tree.currentItem()
        if item is None:
            return
        target = item if self._item_kind(item) == "layer" else item.parent()
        if target is None:
            return
        name = self._item_internal_name(target)
        if not name or name == "geometry":
            # The context menu already disables Delete for the default
            # layer; the Delete/Backspace key path had no equivalent
            # explanation and just silently did nothing.
            QToolTip.showText(QCursor.pos(), "The default layer can't be deleted", self._tree)
            return
        self.layerDeleted.emit(name)

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter_text
        for row in range(self._tree.topLevelItemCount()):
            layer_item = self._tree.topLevelItem(row)
            if layer_item is None:
                continue
            layer_text = layer_item.text(0).lower()
            any_child_match = False
            for child_idx in range(layer_item.childCount()):
                child = layer_item.child(child_idx)
                child_visible = not needle or needle in child.text(0).lower()
                child.setHidden(not child_visible)
                if child_visible:
                    any_child_match = True
            if not needle:
                layer_item.setHidden(False)
                continue
            layer_item.setHidden(needle not in layer_text and not any_child_match)

    def _patch_existing_layers(self, layers: Sequence[tuple[Any, ...] | dict[str, Any]]) -> bool:
        """Update labels/visibility in place when row identity is unchanged."""
        if self._tree.topLevelItemCount() != len(layers):
            return False
        for index, row in enumerate(layers):
            if not isinstance(row, dict):
                return False
            name = str(row.get("internal_name", row.get("name", "")))
            item = self._tree.topLevelItem(index)
            if item is None or self._item_internal_name(item) != name:
                return False
            shapes = list(row.get("shapes", []))
            if item.childCount() != len(shapes):
                return False
            for child_index, shape in enumerate(shapes):
                key = shape.get("key") if isinstance(shape, dict) else shape[0]
                child = item.child(child_index)
                if child is None or child.data(0, self._ROLE_SHAPE_KEY) != key:
                    return False

        self._syncing = True
        try:
            for index, row in enumerate(layers):
                item = self._tree.topLevelItem(index)
                assert item is not None
                display = str(row.get("display_name", row.get("name", "")))
                shapes = list(row.get("shapes", []))
                item.setText(0, f"{display}{f'  ·  {len(shapes)}' if shapes else ''}")
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if bool(row.get("visible", True)) else Qt.CheckState.Unchecked,
                )
                for child, shape in zip(
                    (item.child(i) for i in range(item.childCount())), shapes
                ):
                    child.setText(0, str(shape.get("label", "Shape")))
                    child.setCheckState(
                        0,
                        Qt.CheckState.Checked
                        if bool(shape.get("visible", True))
                        else Qt.CheckState.Unchecked,
                    )
            self._apply_filter()
        finally:
            self._syncing = False
        return True

    def set_layers(self, layers: Sequence[tuple[Any, ...] | dict[str, Any]]) -> None:
        if self._patch_existing_layers(layers):
            return
        self._syncing = True
        # Remember which layers are currently collapsed BEFORE the rebuild —
        # `_tree.clear()` destroys all QTreeWidgetItems (and their expand
        # state) below, and this method runs on nearly every canvas
        # interaction (refresh_tree()), so without this a collapsed layer
        # would silently re-expand the next time anything else changed.
        collapsed_layers: set[str] = set()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is not None and not item.isExpanded():
                name = str(item.data(0, self._ROLE_INTERNAL_NAME) or "")
                if name:
                    collapsed_layers.add(name)
        self._tree.clear()
        self._layer_order = []
        self._shape_keys_by_layer = {}

        if not layers:
            empty = QTreeWidgetItem(["No layers to show yet."])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(empty)
            self._summary.setText("0 layers")
            self._syncing = False
            return

        active_item: QTreeWidgetItem | None = None
        for row in layers:
            if isinstance(row, dict):
                internal_name = str(row.get("internal_name", row.get("name", "")))
                display_name = str(
                    row.get(
                        "display_name",
                        "Layer 1" if internal_name == "geometry" else row.get("name", ""),
                    )
                )
                visible = bool(row.get("visible", True))
                is_active = bool(row.get("active", False))
                editable = bool(row.get("editable", self._editable))
                shapes = list(row.get("shapes", []))
                layer_color = row.get("color")
            else:
                internal_name = str(row[0])
                display_name = "Layer 1" if internal_name == "geometry" else str(row[0])
                visible = True
                is_active = bool(row[3]) if len(row) > 3 else False
                editable = bool(row[4]) if len(row) > 4 else self._editable
                shapes = []
                layer_color = None

            self._layer_order.append(internal_name)
            self._shape_keys_by_layer[internal_name] = []

            # Compose a label with an inline shape-count badge.
            shape_count = len(shapes)
            badge = f"  ·  {shape_count}" if shape_count else ""
            layer_item = QTreeWidgetItem([f"{display_name}{badge}"])
            layer_item.setData(0, self._ROLE_KIND, "layer")
            layer_item.setData(0, self._ROLE_INTERNAL_NAME, internal_name)
            layer_item.setData(0, self._ROLE_DISPLAY_NAME, display_name)
            layer_item.setData(0, self._ROLE_VISIBLE, visible)
            layer_item.setData(0, self._ROLE_EDITABLE, editable)
            layer_item.setData(0, self._ROLE_COLOR, layer_color)
            layer_item.setIcon(0, _swatch_icon(layer_color))
            tip = f"{internal_name} ({len(shapes)} shapes)"
            if layer_color:
                tip += " — right-click to change color"
            layer_item.setToolTip(0, tip)
            layer_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            layer_flags |= Qt.ItemFlag.ItemIsUserCheckable
            if self._editable:
                layer_flags |= Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsDragEnabled
            if self._editable and editable:
                layer_flags |= Qt.ItemFlag.ItemIsEditable
            layer_item.setFlags(layer_flags)
            layer_item.setCheckState(
                0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
            )
            if is_active:
                font = layer_item.font(0)
                font.setBold(True)
                layer_item.setFont(0, font)
                active_item = layer_item

            for shape in shapes:
                if isinstance(shape, dict):
                    shape_key = shape.get("key")
                    shape_label = str(shape.get("label", "Shape"))
                    shape_visible = bool(shape.get("visible", True))
                    shape_editable = bool(shape.get("editable", editable))
                    draggable = bool(shape.get("draggable", self._editable and shape_editable))
                else:
                    shape_key = shape[0]
                    shape_label = str(shape[1]) if len(shape) > 1 else f"Shape {shape_key}"
                    shape_visible = bool(shape[2]) if len(shape) > 2 else True
                    shape_editable = bool(shape[3]) if len(shape) > 3 else editable
                    draggable = self._editable and shape_editable

                self._shape_keys_by_layer[internal_name].append(shape_key)
                shape_item = QTreeWidgetItem([shape_label])
                shape_item.setData(0, self._ROLE_KIND, "shape")
                shape_item.setData(0, self._ROLE_INTERNAL_NAME, internal_name)
                shape_item.setData(0, self._ROLE_DISPLAY_NAME, shape_label)
                shape_item.setData(0, self._ROLE_SHAPE_KEY, shape_key)
                shape_item.setData(0, self._ROLE_VISIBLE, shape_visible)
                shape_item.setData(0, self._ROLE_EDITABLE, shape_editable)
                shape_item.setData(0, self._ROLE_SOURCE_LAYER, internal_name)
                shape_item.setToolTip(0, f"{display_name} shape")
                shape_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                shape_flags |= Qt.ItemFlag.ItemIsUserCheckable
                if self._editable and draggable:
                    shape_flags |= Qt.ItemFlag.ItemIsDragEnabled
                if self._editable and shape_editable:
                    shape_flags |= Qt.ItemFlag.ItemIsEditable
                shape_item.setFlags(shape_flags)
                shape_item.setCheckState(
                    0,
                    Qt.CheckState.Checked if shape_visible else Qt.CheckState.Unchecked,
                )
                layer_item.addChild(shape_item)

            self._tree.addTopLevelItem(layer_item)

        self._summary.setText(f"{len(self._layer_order)} layers")
        if active_item is not None:
            self._tree.setCurrentItem(active_item)
        # Restore each layer's previous expand state instead of blanket
        # expandAll() — only layers that were explicitly collapsed before
        # this rebuild stay collapsed; any layer new to this rebuild (never
        # seen collapsed) defaults to expanded, matching prior behavior.
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is None:
                continue
            name = str(item.data(0, self._ROLE_INTERNAL_NAME) or "")
            item.setExpanded(name not in collapsed_layers)
        # Re-apply any active filter so newly rebuilt rows obey it.
        self._apply_filter()
        self._syncing = False

    def _item_kind(self, item: QTreeWidgetItem | None) -> str:
        if item is None:
            return ""
        return str(item.data(0, self._ROLE_KIND) or "")

    def _item_internal_name(self, item: QTreeWidgetItem | None) -> str:
        if item is None:
            return ""
        return str(item.data(0, self._ROLE_INTERNAL_NAME) or "")

    def _item_shape_key(self, item: QTreeWidgetItem | None) -> Any:
        if item is None:
            return None
        return item.data(0, self._ROLE_SHAPE_KEY)

    def _item_source_layer(self, item: QTreeWidgetItem | None) -> str:
        """Return the layer name a shape item belongs to."""
        if item is None:
            return ""
        return str(item.data(0, self._ROLE_SOURCE_LAYER) or "")

    def _unique_layer_name(self, base_name: str) -> str:
        """Next unused, always-numbered name ("Layer 1", "Layer 2", …).

        A bare candidate (no number) used to be returned whenever it wasn't
        an EXACT string match for an existing name — but "Layer" != "Layer 1",
        so right after the default "Layer 1" existed, the next new layer was
        named plain "Layer" (no number), and only the one after that finally
        picked up numbering again. Always keeping the numeric suffix avoids
        that inconsistent, confusing sequence.
        """
        candidate = base_name.strip() or "Layer"
        if candidate == "geometry":
            candidate = "Layer"
        pattern = re.compile(rf"^{re.escape(candidate)}\s+(\d+)$")
        used_numbers: set[int] = set()
        for name in self._layer_order:
            if name == candidate:
                used_numbers.add(1)
                continue
            m = pattern.match(name)
            if m:
                used_numbers.add(int(m.group(1)))
        n = 1
        while n in used_numbers or f"{candidate} {n}" in self._layer_order:
            n += 1
        return f"{candidate} {n}"

    def _prompt_add_layer(self) -> None:
        """Add a new layer with an auto-incremented name and a distinct
        default color — no naming popup. Right-click (or double-click) the
        new layer row to rename it or change its color later."""
        if not self._editable:
            return
        # Cycle the swatch palette by current layer count so each new layer
        # starts visually distinct instead of colorless/default.
        color = _SWATCH_PALETTE[len(self._layer_order) % len(_SWATCH_PALETTE)]
        name = self._unique_layer_name("Layer")
        self.layerAdded.emit(name)
        self.layerColorChangeRequested.emit(name, color)

    def _prompt_custom_layer_color(self, layer_name: str) -> None:
        color = QColorDialog.getColor(QColor("#2f81f7"), self, "Layer Color")
        if color.isValid():
            self.layerColorChangeRequested.emit(layer_name, color.name())

    def _emit_current_item_change(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if self._syncing or current is None:
            return
        if self._multi_select_click:
            # Ctrl/Shift/Meta multi-select shouldn't activate (and thereby
            # rebuild + wipe) the tree's just-made multi-selection.
            return
        if self._item_kind(current) == "layer":
            self.layerActivated.emit(self._item_internal_name(current))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        """Double-click: rename shape items inline; fit view for layer items."""
        if item is None:
            return
        kind = self._item_kind(item)
        if kind == "shape" and self._editable and bool(item.data(0, self._ROLE_EDITABLE)):
            self._tree.editItem(item, 0)
        else:
            self.fitRequested.emit()

    def select_shape_keys(self, entity_ids: Sequence[str]) -> None:
        """Highlight tree rows matching the given entity IDs (canvas
        selection -> tree sync). A group row is highlighted when any of its
        members is in *entity_ids*. Does not re-emit ``selectionRequested``."""
        target = set(entity_ids)
        self._syncing = True
        try:
            self._tree.clearSelection()
            first: QTreeWidgetItem | None = None
            for i in range(self._tree.topLevelItemCount()):
                layer_item = self._tree.topLevelItem(i)
                if layer_item is None:
                    continue
                for c in range(layer_item.childCount()):
                    child = layer_item.child(c)
                    key = self._item_shape_key(child)
                    members = key if isinstance(key, (tuple, list)) else (key,)
                    if any(m in target for m in members if isinstance(m, str)):
                        child.setSelected(True)
                        if first is None:
                            first = child
            if first is not None:
                # Expand the parent layer first — a selected shape inside a
                # collapsed layer was highlighted but stayed invisible.
                parent = first.parent()
                if parent is not None and not parent.isExpanded():
                    self._tree.expandItem(parent)
                self._tree.scrollToItem(first)
        finally:
            self._syncing = False

    def _emit_selection_request(self) -> None:
        if self._syncing:
            return
        selected: list[Any] = []
        seen: set[Any] = set()
        for item in self._tree.selectedItems():
            kind = self._item_kind(item)
            if kind == "shape":
                key = self._item_shape_key(item)
                if key not in seen:
                    selected.append(key)
                    seen.add(key)
            elif kind == "layer":
                for key in self._shape_keys_by_layer.get(self._item_internal_name(item), []):
                    if key in seen:
                        continue
                    selected.append(key)
                    seen.add(key)
        self.selectionRequested.emit(selected)

    def _handle_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._syncing:
            return
        kind = self._item_kind(item)
        if kind == "layer":
            editable = bool(item.data(0, self._ROLE_EDITABLE))
            old_display = str(item.data(0, self._ROLE_DISPLAY_NAME) or "")
            # Strip the inline " · N" badge that set_layers appends.
            raw_text = item.text(0).strip()
            if "  ·  " in raw_text:
                raw_text = raw_text.split("  ·  ", 1)[0].strip()
            new_display = raw_text
            if editable and new_display != old_display:
                old_internal = self._item_internal_name(item)
                if not new_display or new_display in self._layer_order:
                    # Blank name or a collision with another layer — revert
                    # instead of leaving the row's text empty/invalid until
                    # the next full set_layers() rebuild.
                    self._syncing = True
                    item.setText(0, old_display)
                    self._syncing = False
                    # Explain the revert — a silent snap-back gave the user no
                    # clue why their new name didn't take.
                    reason = (
                        "Layer name cannot be blank"
                        if not new_display
                        else f'A layer named "{new_display}" already exists'
                    )
                    QToolTip.showText(QCursor.pos(), reason, self._tree)
                else:
                    self._syncing = True
                    item.setData(0, self._ROLE_INTERNAL_NAME, new_display)
                    item.setData(0, self._ROLE_DISPLAY_NAME, new_display)
                    for idx in range(item.childCount()):
                        child = item.child(idx)
                        child.setData(0, self._ROLE_INTERNAL_NAME, new_display)
                        child.setData(0, self._ROLE_SOURCE_LAYER, new_display)
                    self._syncing = False
                    self._layer_order = [
                        new_display if name == old_internal else name for name in self._layer_order
                    ]
                    if old_internal in self._shape_keys_by_layer:
                        self._shape_keys_by_layer[new_display] = self._shape_keys_by_layer.pop(
                            old_internal
                        )
                    # A connected slot commonly reacts to a rename by
                    # rebuilding the whole tree (set_layers()), which
                    # deletes the underlying C++ QTreeWidgetItem — `item`
                    # is unsafe to touch after this emit. Mirrors the
                    # shape-rename branch below, which already returns
                    # right after its own rename emit for the same reason.
                    self.layerRenamed.emit(old_internal, new_display)
                    return

        visible = item.checkState(0) == Qt.CheckState.Checked
        if kind == "layer":
            self._syncing = True
            for idx in range(item.childCount()):
                item.child(idx).setCheckState(0, item.checkState(0))
            self._syncing = False
            self.layerVisibilityChanged.emit(self._item_internal_name(item), visible)
            return
        if kind == "shape":
            old_display = str(item.data(0, self._ROLE_DISPLAY_NAME) or "")
            new_text = item.text(0).strip()
            if bool(item.data(0, self._ROLE_EDITABLE)) and new_text != old_display:
                if not new_text:
                    # Cleared to blank — revert instead of leaving the row's
                    # displayed text empty until the next full rebuild.
                    self._syncing = True
                    item.setText(0, old_display)
                    self._syncing = False
                    # Explain the revert, matching the layer-rename branch
                    # above — a silent snap-back gave no clue why.
                    QToolTip.showText(QCursor.pos(), "Shape name cannot be blank", self._tree)
                else:
                    self._syncing = True
                    item.setData(0, self._ROLE_DISPLAY_NAME, new_text)
                    self._syncing = False
                    self.shapeRenamed.emit(
                        self._item_internal_name(item),
                        self._item_shape_key(item),
                        new_text,
                    )
                    return
            self.shapeVisibilityChanged.emit(
                self._item_internal_name(item), self._item_shape_key(item), visible
            )

    def _move_layer(self, name: str, delta: int) -> None:
        if not self._editable or name not in self._layer_order:
            return
        current_index = self._layer_order.index(name)
        new_index = current_index + delta
        if new_index < 0 or new_index >= len(self._layer_order):
            return
        self.layerMoved.emit(name, new_index)

    def _show_context_menu(self, pos) -> None:
        if not self._editable:
            return
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        # QAction tooltips are hidden by default in Qt — Consolidate/Group/
        # Merge/Delete/Copy all set explanatory tooltips below that never
        # rendered without this.
        menu.setToolTipsVisible(True)

        if item is None:
            menu.addAction("New layer", self._prompt_add_layer)
            menu.popup(self._tree.viewport().mapToGlobal(pos))
            return

        kind = self._item_kind(item)
        layer_name = self._item_internal_name(item if kind == "layer" else item.parent())
        menu.addAction("New layer", self._prompt_add_layer)

        if kind == "layer":
            menu.addSeparator()
            menu.addAction("Activate layer", lambda: self.layerActivated.emit(layer_name))
            menu.addAction("Rename layer\tF2", lambda: self._tree.editItem(item, 0))
            color_menu = menu.addMenu("Set color")
            for hex_color in _SWATCH_PALETTE:
                swatch_action = color_menu.addAction(
                    "   " + hex_color,
                    lambda _c=hex_color: self.layerColorChangeRequested.emit(layer_name, _c),
                )
                swatch_action.setIcon(_swatch_icon(hex_color, size=14))
            color_menu.addSeparator()
            color_menu.addAction("Custom…", lambda: self._prompt_custom_layer_color(layer_name))
            current_color = item.data(0, self._ROLE_COLOR)
            clear_action = color_menu.addAction(
                "No color",
                lambda: self.layerColorChangeRequested.emit(layer_name, None),
            )
            clear_action.setEnabled(bool(current_color))

            # Collect all selected layer names for batch delete — falls back
            # to just the right-clicked layer when nothing else is selected.
            selected_layer_names = [
                self._item_internal_name(it)
                for it in self._tree.selectedItems()
                if self._item_kind(it) == "layer"
            ]
            if layer_name not in selected_layer_names:
                selected_layer_names = [layer_name]
            deletable_layers = [n for n in selected_layer_names if n != "geometry"]

            if len(selected_layer_names) > 1:
                del_action = menu.addAction(
                    f"Delete {len(deletable_layers)} layers\tDel",
                    lambda: self.layersDeleteRequested.emit(deletable_layers),
                )
                del_action.setEnabled(bool(deletable_layers))
                other_selected = [n for n in selected_layer_names if n != layer_name]
                menu.addAction(
                    f"Consolidate {len(selected_layer_names)} layers into '{layer_name}'",
                    lambda: self.layersConsolidateRequested.emit(other_selected, layer_name),
                ).setToolTip(
                    "Move every shape from the other selected layers onto\n"
                    f"'{layer_name}' and remove the now-empty layers."
                )
            else:
                del_action = menu.addAction(
                    "Delete layer\tDel", lambda: self.layerDeleted.emit(layer_name)
                )
                if layer_name == "geometry":
                    del_action.setEnabled(False)
            menu.addSeparator()
            menu.addAction(
                "Solo (isolate)",
                lambda: self.layerSoloRequested.emit(layer_name),
            )
            menu.addAction(
                "Show all",
                lambda: self.bulkVisibilityRequested.emit(True),
            )
            menu.addAction(
                "Hide all",
                lambda: self.bulkVisibilityRequested.emit(False),
            )
            menu.addSeparator()
            idx = self._layer_order.index(layer_name) if layer_name in self._layer_order else -1
            up_action = menu.addAction("Move up", lambda: self._move_layer(layer_name, -1))
            down_action = menu.addAction("Move down", lambda: self._move_layer(layer_name, 1))
            if idx <= 0:
                up_action.setEnabled(False)
            if idx < 0 or idx >= len(self._layer_order) - 1:
                down_action.setEnabled(False)
            menu.addAction(
                "Move selected here",
                lambda: self.moveSelectedRequested.emit(layer_name),
            )
        elif kind == "shape":
            shape_key = self._item_shape_key(item)
            menu.addAction("Rename shape\tF2", lambda: self._tree.editItem(item, 0))

            # Shape operations section.
            menu.addSeparator()
            # Collect all selected shape keys on this layer for batch ops that
            # require same-layer shapes (Group/Ungroup/Merge).
            selected_shape_keys = [
                self._item_shape_key(it)
                for it in self._tree.selectedItems()
                if self._item_kind(it) == "shape" and str(self._item_source_layer(it)) == layer_name
            ]
            if not selected_shape_keys:
                selected_shape_keys = [shape_key]

            # Delete/Copy have no same-layer requirement — use the FULL
            # cross-layer selection so multi-selecting shapes across
            # different layers and choosing Delete/Copy actually acts on
            # all of them, not just the ones on the right-clicked row's
            # layer (the downstream handlers already work off global entity
            # indices, so there's no reason to filter by layer here).
            all_selected_shape_keys = [
                self._item_shape_key(it)
                for it in self._tree.selectedItems()
                if self._item_kind(it) == "shape"
            ]
            if not all_selected_shape_keys:
                all_selected_shape_keys = [shape_key]

            has_groups = any(isinstance(k, (tuple, list)) for k in selected_shape_keys)

            group_action = menu.addAction(
                "Group shapes\tCtrl+G",
                lambda: self.shapesGroupRequested.emit(layer_name, selected_shape_keys),
            )
            if has_groups:
                group_action.setEnabled(False)
                group_action.setToolTip("Group shapes\tCtrl+G\nAlready in a group — ungroup first.")

            ungroup_action = menu.addAction(
                "Ungroup shapes\tShift+Ctrl+G",
                lambda: self.shapesUngroupRequested.emit(layer_name, selected_shape_keys),
            )
            if not has_groups:
                ungroup_action.setEnabled(False)

            menu.addAction(
                "Merge selected\tCtrl+M",
                lambda: self.shapesMergeRequested.emit(layer_name, selected_shape_keys),
            ).setToolTip(
                "Merge selected shapes into a single object using boolean union.\n"
                "Shapes must overlap or touch to produce visible results."
            )

            menu.addSeparator()

            # Delete and copy actions.
            delete_action = menu.addAction(
                "Delete shapes\tDel",
                lambda: self.shapesDeleteRequested.emit(layer_name, all_selected_shape_keys),
            )
            delete_action.setToolTip(
                "Delete shapes\tDel\nRemoves selected shapes. Locked shapes are skipped."
            )

            copy_action = menu.addAction(
                "Copy shapes\tCtrl+C",
                lambda: self.shapesCopyRequested.emit(layer_name, all_selected_shape_keys),
            )
            copy_action.setToolTip(
                "Copy shapes\tCtrl+C\n"
                "Copies selected shapes to the clipboard for pasting on the canvas."
            )

            menu.addSeparator()
            menu.addAction(
                "Hide shape",
                lambda: self.shapeVisibilityChanged.emit(
                    layer_name, self._item_shape_key(item), False
                ),
            )
            menu.addAction(
                "Show shape",
                lambda: self.shapeVisibilityChanged.emit(
                    layer_name, self._item_shape_key(item), True
                ),
            )
            # "Move to Layer" submenu — collects the FULL selection across
            # every layer (not just shapes on the right-clicked row's
            # layer), grouped by each shape's real source layer, so a
            # cross-layer multi-selection still moves everything at once.
            keys_by_source_layer: dict[str, list[Any]] = {}
            for it in self._tree.selectedItems():
                if self._item_kind(it) != "shape":
                    continue
                src = self._item_source_layer(it) or layer_name
                keys_by_source_layer.setdefault(src, []).append(self._item_shape_key(it))
            if not keys_by_source_layer:
                keys_by_source_layer = {layer_name: [shape_key]}
            total_selected = sum(len(v) for v in keys_by_source_layer.values())

            other_layers = [n for n in self._layer_order if n != layer_name]
            if other_layers:
                move_menu = menu.addMenu(
                    f"Move {total_selected} shape(s) to Layer"
                    if total_selected > 1
                    else "Move to Layer"
                )
                for target in other_layers:

                    def _move_all(_by_layer=keys_by_source_layer, _t=target) -> None:
                        for src_layer, keys in _by_layer.items():
                            if src_layer != _t and keys:
                                self.shapesMoveRequested.emit(src_layer, keys, _t)

                    move_menu.addAction(target, _move_all)
            menu.addAction(
                "Move selected here",
                lambda: self.moveSelectedRequested.emit(layer_name),
            )

        menu.popup(self._tree.viewport().mapToGlobal(pos))
