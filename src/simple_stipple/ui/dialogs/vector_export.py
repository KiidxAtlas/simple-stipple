"""Choose vector artwork and format options before the native save dialog."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.core.formats.service import FviExportOptions
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.dialogs.fvi_dialog import FviExportPanel

_FORMATS = [
    ("DXF", "dxf"),
    ("SVG", "svg"),
    ("StarFX FVI", "fvi"),
]


class VectorExportDialog(QDialog):
    """One options dialog; artwork selection never changes the canvas selection."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        parent: QWidget | None = None,
        *,
        format_name: str = "dxf",
        group_labels: dict[int, str] | None = None,
        selected_ids: set[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export vectors")
        self._records = records
        self._fvi_panel: FviExportPanel | None = None
        self.resize(580, 580)
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Format"))
        self._format = QComboBox()
        for label, value in _FORMATS:
            self._format.addItem(label, value)
        self._format.setCurrentIndex(max(0, self._format.findData(format_name)))
        row.addWidget(self._format, 1)
        root.addLayout(row)
        self._tabs = QTabWidget(self)
        artwork = QWidget()
        layout = QVBoxLayout(artwork)
        hint = QLabel(
            "Select layers, groups, or shapes. Select nothing to export all visible artwork. "
            "Use Cmd/Ctrl-click or Shift-click for multiple rows."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("Layers and shapes")
        self._tree.setAccessibleName("Export layers and shapes")
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layers: dict[str, QTreeWidgetItem] = {}
        groups: dict[tuple[str, int], QTreeWidgetItem] = {}
        for index, record in enumerate(records):
            layer = str(record.get("layer") or "Layer 1")
            if layer not in layers:
                layers[layer] = QTreeWidgetItem(self._tree, [layer])
            parent_item = layers[layer]
            group = record.get("group")
            if group is not None:
                key = (layer, group)
                if key not in groups:
                    group_name = (group_labels or {}).get(group, f"Group {group}")
                    groups[key] = QTreeWidgetItem(parent_item, [group_name])
                parent_item = groups[key]
            meta = record.get("meta") or {}
            name = meta.get("label") or meta.get("name")
            label = str(name or f"{str(record.get('kind', 'Path')).capitalize()} {index + 1}")
            item = QTreeWidgetItem(parent_item, [label])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            if selected_ids and record.get("entity_id") in selected_ids:
                item.setSelected(True)
        self._tree.expandAll()
        layout.addWidget(self._tree, 1)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        clear = QPushButton("Clear selection (export all)")
        clear.clicked.connect(self._tree.clearSelection)
        layout.addWidget(clear)
        self._tabs.addTab(artwork, "Artwork")
        root.addWidget(self._tabs, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Choose file…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._format.currentIndexChanged.connect(self._format_changed)
        self._format_changed()
        self._tree.itemSelectionChanged.connect(self._refresh_summary)
        self._refresh_summary()
        install_dialog_focus_lifecycle(self, self._tree)

    def format_name(self) -> str:
        return str(self._format.currentData())

    def fvi_options(self) -> FviExportOptions:
        return self._fvi_panel.options() if self._fvi_panel is not None else FviExportOptions()

    def has_selection(self) -> bool:
        return bool(self._tree.selectedItems())

    def selected_records(self) -> list[dict[str, Any]]:
        items = self._tree.selectedItems()
        if not items:
            return list(self._records)
        indices: set[int] = set()
        pending = list(items)
        while pending:
            item = pending.pop()
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(index, int):
                indices.add(index)
            pending.extend(item.child(i) for i in range(item.childCount()))
        return [record for i, record in enumerate(self._records) if i in indices]

    def _format_changed(self, *_args) -> None:
        format_name = self.format_name()
        if format_name == "fvi" and self._fvi_panel is None:
            self._fvi_panel = FviExportPanel(self.selected_records())
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self._fvi_panel)
            self._tabs.addTab(scroll, "FVI options")
        if self._fvi_panel is not None:
            self._tabs.setTabVisible(1, format_name == "fvi")
            if format_name == "fvi":
                self._fvi_panel.set_records(self.selected_records())

    def _refresh_summary(self) -> None:
        count = len(self.selected_records())
        scope = "Selected artwork" if self.has_selection() else "All visible artwork"
        self._summary.setText(f"{scope}: {count} path(s) will be exported")
        if self._fvi_panel is not None and self.format_name() == "fvi":
            self._fvi_panel.set_records(self.selected_records())
