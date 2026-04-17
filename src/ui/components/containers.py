"""Composite panel and browser widgets for PySide6 UIs."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.components.factories import _info_chip


class CollapsibleSection(QFrame):
    """Expandable/collapsible content section for dense sidebars."""

    def __init__(self, title: str, content: QWidget, *, expanded: bool = True):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "collapsible")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._toggle = QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._toggle.clicked.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._content = content
        self._content.setVisible(expanded)
        layout.addWidget(self._content)

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self._content.setVisible(checked)
        self.adjustSize()

    def isExpanded(self) -> bool:
        return self._toggle.isChecked()

    def setExpanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)
        self._on_toggled(expanded)


class CanvasPrecisionBar(QFrame):
    """Persistent precision controls for canvas-heavy workflows."""

    def __init__(self, canvas: Any | None, *, on_changed=None) -> None:
        super().__init__()
        self._canvas = canvas
        self._on_changed = on_changed

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "precision-bar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        label = QLabel("Precision")
        label.setStyleSheet("color: #8b949e; font-size: 10px; font-weight: 700;")
        layout.addWidget(label)

        self._grid_btn = QPushButton()
        self._grid_btn.setMinimumHeight(24)
        self._grid_btn.clicked.connect(self._toggle_grid)
        layout.addWidget(self._grid_btn)

        self._snap_btn = QPushButton()
        self._snap_btn.setMinimumHeight(24)
        self._snap_btn.clicked.connect(self._toggle_snap)
        layout.addWidget(self._snap_btn)

        self._construction_btn = QPushButton()
        self._construction_btn.setMinimumHeight(24)
        self._construction_btn.clicked.connect(self._toggle_construction)
        layout.addWidget(self._construction_btn)

        self._measure_btn = QPushButton()
        self._measure_btn.setMinimumHeight(24)
        self._measure_btn.clicked.connect(self._toggle_measure)
        layout.addWidget(self._measure_btn)

        layout.addWidget(QLabel("Grid mm"))
        self._spacing = QLineEdit()
        self._spacing.setFixedWidth(64)
        self._spacing.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._spacing.returnPressed.connect(self._apply_spacing)
        layout.addWidget(self._spacing)

        self._spacing_dec = QPushButton("−")
        self._spacing_dec.setFixedSize(24, 24)
        self._spacing_dec.clicked.connect(lambda: self._scale_spacing(0.5))
        layout.addWidget(self._spacing_dec)

        self._spacing_inc = QPushButton("+")
        self._spacing_inc.setFixedSize(24, 24)
        self._spacing_inc.clicked.connect(lambda: self._scale_spacing(2.0))
        layout.addWidget(self._spacing_inc)

        layout.addStretch()
        self.refresh()

    def bind_canvas(self, canvas: Any | None) -> None:
        self._canvas = canvas
        self.refresh()

    def refresh(self) -> None:
        if self._canvas is None:
            self.setVisible(False)
            return
        if not hasattr(self._canvas, "get_precision_state"):
            self.setVisible(False)
            return

        self.setVisible(True)
        state = self._canvas.get_precision_state()
        grid_on = bool(state.get("grid_visible", False))
        snap_on = bool(state.get("grid_snap", False))
        construction_on = bool(state.get("construction_mode", False))
        measure_on = bool(state.get("measure_mode", False))
        spacing = float(state.get("grid_spacing", 1.0))

        self._grid_btn.setText("Grid: On" if grid_on else "Grid: Off")
        self._grid_btn.setProperty("role", "primary" if grid_on else None)

        self._snap_btn.setText("Snap: On" if snap_on else "Snap: Off")
        self._snap_btn.setProperty("role", "primary" if snap_on else None)

        self._construction_btn.setText(
            "Construction: On" if construction_on else "Construction: Off"
        )
        self._construction_btn.setProperty(
            "role", "primary" if construction_on else None
        )

        self._measure_btn.setText("Measure: On" if measure_on else "Measure: Off")
        self._measure_btn.setProperty("role", "primary" if measure_on else None)

        self._spacing.setText(f"{spacing:g}")

        for button in (
            self._grid_btn,
            self._snap_btn,
            self._construction_btn,
            self._measure_btn,
        ):
            button.style().unpolish(button)
            button.style().polish(button)

    def _after_change(self) -> None:
        self.refresh()
        if callable(self._on_changed):
            self._on_changed()

    def _toggle_grid(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "set_grid_visible") and hasattr(canvas, "get_precision_state"):
            state = canvas.get_precision_state()
            canvas.set_grid_visible(not bool(state.get("grid_visible", False)))
            self._after_change()

    def _toggle_snap(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "set_grid_snap") and hasattr(canvas, "get_precision_state"):
            state = canvas.get_precision_state()
            canvas.set_grid_snap(not bool(state.get("grid_snap", False)))
            self._after_change()

    def _toggle_construction(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "set_construction_mode") and hasattr(canvas, "get_precision_state"):
            state = canvas.get_precision_state()
            canvas.set_construction_mode(not bool(state.get("construction_mode", False)))
            self._after_change()

    def _toggle_measure(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "toggle_measure"):
            canvas.toggle_measure()
            self._after_change()

    def _scale_spacing(self, factor: float) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if not hasattr(canvas, "get_precision_state") or not hasattr(canvas, "set_grid_spacing"):
            return
        current = float(canvas.get_precision_state().get("grid_spacing", 1.0))
        canvas.set_grid_spacing(max(0.1, min(100.0, current * factor)))
        self._after_change()

    def _apply_spacing(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if not hasattr(canvas, "set_grid_spacing"):
            return
        try:
            value = float(self._spacing.text().strip())
        except ValueError:
            self.refresh()
            return
        canvas.set_grid_spacing(max(0.1, min(100.0, value)))
        self._after_change()


class ContextCoachStrip(QFrame):
    """Short, progressive workflow hints that avoid dense keyboard dumps."""

    def __init__(self, *, title: str = "Workflow coach") -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "coach-strip")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self._title = QLabel(title)
        self._title.setStyleSheet("color: #79c0ff; font-size: 11px; font-weight: 700;")
        layout.addWidget(self._title)

        self._primary = QLabel("")
        self._primary.setStyleSheet("color: #c9d1d9; font-size: 11px;")
        self._primary.setWordWrap(True)
        layout.addWidget(self._primary)

        self._secondary = QLabel("")
        self._secondary.setStyleSheet("color: #8b949e; font-size: 10px;")
        self._secondary.setWordWrap(True)
        layout.addWidget(self._secondary)

    def set_message(self, primary: str, secondary: str = "") -> None:
        self._primary.setText(primary)
        self._secondary.setText(secondary)


class CanvasStatusStrip(QFrame):
    """Compact status bar — mode, selection, zoom, coordinates, and readiness."""

    def __init__(self, *, show_readiness: bool = True) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "status-strip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self._mode_label = QLabel("Select")
        self._mode_label.setStyleSheet(
            "color: #79c0ff; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self._mode_label)

        self._readiness_dot = self._dot()
        layout.addWidget(self._readiness_dot)

        self._objects_label = QLabel("0 obj")
        self._objects_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._objects_label)

        layout.addWidget(self._dot())

        self._selection_label = QLabel("0 sel")
        self._selection_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._selection_label)

        layout.addWidget(self._dot())

        self._precision_label = QLabel("Free move")
        self._precision_label.setStyleSheet("color: #6e7681; font-size: 10px;")
        layout.addWidget(self._precision_label)

        layout.addStretch()

        self._cursor_label = QLabel("")
        self._cursor_label.setStyleSheet(
            "color: #6e7681; font-size: 10px; font-family: 'Menlo', 'Courier New';"
        )
        layout.addWidget(self._cursor_label)

        layout.addWidget(self._dot())

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        self._zoom_label.setToolTip("Zoom level (scroll to zoom)")
        layout.addWidget(self._zoom_label)

        layout.addWidget(self._dot())

        self._readiness_chip = _info_chip("No geometry", "warn")
        layout.addWidget(self._readiness_chip)
        self.set_readiness_visible(show_readiness)

    def set_readiness_visible(self, visible: bool) -> None:
        self._readiness_dot.setVisible(visible)
        self._readiness_chip.setVisible(visible)

    @staticmethod
    def _dot() -> QLabel:
        d = QLabel("·")
        d.setStyleSheet("color: #30363d; font-size: 11px;")
        return d

    def set_snapshot(
        self,
        *,
        mode: str,
        selected_count: int,
        object_count: int,
        precision_text: str,
        topology_text: str = "",
        readiness_text: str,
        readiness_tone: str = "neutral",
        zoom_percent: int = 100,
        cursor_pos: tuple[float, float] | None = None,
    ) -> None:
        self._mode_label.setText(mode.title())
        self._objects_label.setText(f"{object_count} obj")
        self._selection_label.setText(f"{selected_count} sel")
        self._selection_label.setStyleSheet(
            f"color: {'#79c0ff' if selected_count else '#8b949e'}; font-size: 11px;"
        )
        combined_precision = precision_text
        if topology_text:
            combined_precision = f"{precision_text} · {topology_text}"
        self._precision_label.setText(combined_precision)
        self._zoom_label.setText(f"{zoom_percent}%")
        if cursor_pos:
            self._cursor_label.setText(f"X {cursor_pos[0]:.2f}  Y {cursor_pos[1]:.2f}")
        else:
            self._cursor_label.setText("")
        self._readiness_chip.setText(readiness_text)
        self._readiness_chip.setProperty("tone", readiness_tone)
        self._readiness_chip.style().unpolish(self._readiness_chip)
        self._readiness_chip.style().polish(self._readiness_chip)


class CanvasObjectBrowser(QFrame):
    """Hierarchical object browser for editable canvas geometry."""

    selectionRequested = Signal(object)
    fitRequested = Signal()
    visibilityChanged = Signal(int, bool)
    lockChanged = Signal(int, bool)

    def __init__(self, title: str = "Objects") -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "object-browser")
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setProperty("role", "callout-title")
        header.addWidget(title_label)
        self._summary = QLabel("0 objects")
        self._summary.setProperty("role", "callout-body")
        header.addWidget(self._summary)
        header.addStretch()
        layout.addLayout(header)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.itemSelectionChanged.connect(self._emit_selection_request)
        self._tree.itemChanged.connect(self._emit_visibility_change)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemDoubleClicked.connect(
            lambda _item, _col=0: self.fitRequested.emit()
        )
        layout.addWidget(self._tree, stretch=1)

        self._hidden_indices: set[int] = set()
        self._locked_indices: set[int] = set()

    def set_objects(
        self,
        polys: list[list[tuple[float, float]]],
        selected_indices: list[int],
        hidden_indices: list[int] | None = None,
        locked_indices: list[int] | None = None,
    ) -> None:
        self._syncing = True
        self._tree.clear()
        self._hidden_indices = set(hidden_indices or [])
        self._locked_indices = set(locked_indices or [])

        if not polys:
            empty = QTreeWidgetItem([
                "No objects yet — load or create geometry to browse it here."
            ])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(empty)
            self._summary.setText("0 objects")
            self._syncing = False
            return

        open_root = QTreeWidgetItem(["Open shapes"])
        closed_root = QTreeWidgetItem(["Closed shapes"])
        open_root.setExpanded(True)
        closed_root.setExpanded(True)
        self._tree.addTopLevelItem(closed_root)
        self._tree.addTopLevelItem(open_root)

        for idx, poly in enumerate(polys):
            locked = idx in self._locked_indices
            label = self._describe_poly(idx, poly)
            if locked:
                label = f"🔒 {label}"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, idx)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, locked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.CheckState.Unchecked
                if idx in self._hidden_indices
                else Qt.CheckState.Checked,
            )
            is_closed = False
            if len(poly) > 1:
                is_closed = (
                    math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                    < 0.01
                )
            (closed_root if is_closed else open_root).addChild(item)
            if idx in selected_indices:
                item.setSelected(True)
        self._summary.setText(
            f"{len(polys)} objects  ·  {len(selected_indices)} selected"
        )
        self._tree.expandAll()
        self._syncing = False

    def _selected_object_indices(self) -> list[int]:
        indices: list[int] = []
        for item in self._tree.selectedItems():
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(idx, int):
                indices.append(idx)
        return indices

    def _emit_selection_request(self) -> None:
        if self._syncing:
            return
        self.selectionRequested.emit(self._selected_object_indices())

    def _emit_visibility_change(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._syncing:
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int):
            return
        visible = item.checkState(0) == Qt.CheckState.Checked
        if visible:
            self._hidden_indices.discard(idx)
        else:
            self._hidden_indices.add(idx)
        self.visibilityChanged.emit(idx, visible)

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int):
            return

        selected = self._selected_object_indices()
        target_indices = selected or [idx]

        menu = QMenu(self)

        all_locked = bool(target_indices) and all(
            i in self._locked_indices for i in target_indices
        )
        lock_label = "Unlock selected" if all_locked else "Lock selected"

        def _toggle_lock() -> None:
            should_lock = not all_locked
            for i in target_indices:
                if should_lock:
                    self._locked_indices.add(i)
                else:
                    self._locked_indices.discard(i)
                self.lockChanged.emit(i, should_lock)

        def _set_visible(indices: list[int], visible: bool) -> None:
            for i in indices:
                if visible:
                    self._hidden_indices.discard(i)
                else:
                    self._hidden_indices.add(i)
                self.visibilityChanged.emit(i, visible)

        menu.addAction(lock_label, _toggle_lock)
        menu.addSeparator()
        menu.addAction("Hide selected", lambda: _set_visible(target_indices, False))
        menu.addAction("Show selected", lambda: _set_visible(target_indices, True))
        menu.addAction("Show all", lambda: _set_visible(list(self._hidden_indices), True))
        menu.popup(self._tree.viewport().mapToGlobal(pos))

    @staticmethod
    def _describe_poly(idx: int, poly: list[tuple[float, float]]) -> str:
        if not poly:
            return f"{idx + 1:02d}  Empty"
        xs, ys = zip(*poly)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        point_count = len(poly)
        if (
            len(poly) > 1
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        ):
            point_count -= 1
            kind = "Closed"
        else:
            kind = "Open"
        return (
            f"{idx + 1:02d}  {kind}  ·  {point_count} pts  ·  "
            f"{width:.1f} × {height:.1f} mm"
        )


class DxfLayersTree(QFrame):
    """Read-only DXF/logical layer tree for canvas sidebars."""

    def __init__(self, title: str = "DXF Layers") -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "object-browser")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setProperty("role", "callout-title")
        header.addWidget(title_label)
        self._summary = QLabel("0 layers")
        self._summary.setProperty("role", "callout-body")
        header.addWidget(self._summary)
        header.addStretch()
        layout.addLayout(header)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self._tree, stretch=1)

    def set_layers(self, layers: list[tuple[str, int, bool, bool]]) -> None:
        """Set layer rows as (name, entity_count, dirty, is_active)."""
        self._tree.clear()
        if not layers:
            empty = QTreeWidgetItem(["No layers to show yet."])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(empty)
            self._summary.setText("0 layers")
            return
        for name, _entity_count, _dirty, _is_active in layers:
            item = QTreeWidgetItem([name])
            self._tree.addTopLevelItem(item)
        self._summary.setText(f"{len(layers)} layers")
        self._tree.expandAll()


__all__ = [
    "CanvasObjectBrowser",
    "CanvasPrecisionBar",
    "CanvasStatusStrip",
    "CollapsibleSection",
    "ContextCoachStrip",
    "DxfLayersTree",
]
