"""Compact grid and object-snap controls for canvas pages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
)

from src.ui.canvas.constants import GRID_SPACING_MAX_MM, GRID_SPACING_MIN_MM
from src.ui.components import clear_line_edit_error, set_line_edit_error
from src.ui.util import parse_numeric_expression


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

        self._pan_btn = QPushButton("Pan")
        self._pan_btn.setMinimumHeight(24)
        self._pan_btn.setCheckable(True)
        self._pan_btn.setToolTip("Pan the canvas by dragging (Shortcut: P)")
        self._pan_btn.setAccessibleName("Pan tool")
        self._pan_btn.clicked.connect(self._toggle_pan)
        layout.addWidget(self._pan_btn)
        layout.addSpacing(6)

        self._grid_btn = QPushButton("Grid")
        self._grid_btn.setMinimumHeight(24)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setToolTip("Toggle canvas grid overlay")
        self._grid_btn.clicked.connect(self._toggle_grid)
        layout.addWidget(self._grid_btn)

        # Keep the grid's spacing controls physically attached to Grid. They
        # appear when Grid (or grid-point snapping) is enabled, without
        # unrelated snapping/constraint controls opening a visual gap.
        self._spacing_label = QLabel("Spacing")
        layout.addWidget(self._spacing_label)
        self._spacing = QLineEdit()
        self._spacing.setFixedWidth(64)
        self._spacing.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._spacing.returnPressed.connect(self._apply_spacing)
        layout.addWidget(self._spacing)

        self._spacing_dec = QPushButton("\u2212")
        self._spacing_dec.setFixedSize(24, 24)
        self._spacing_dec.setProperty("role", "icon-sm")
        self._spacing_dec.setToolTip("Halve grid spacing")
        self._spacing_dec.clicked.connect(lambda: self._scale_spacing(0.5))
        layout.addWidget(self._spacing_dec)

        self._spacing_inc = QPushButton("+")
        self._spacing_inc.setFixedSize(24, 24)
        self._spacing_inc.setProperty("role", "icon-sm")
        self._spacing_inc.setToolTip("Double grid spacing")
        self._spacing_inc.clicked.connect(lambda: self._scale_spacing(2.0))
        layout.addWidget(self._spacing_inc)

        layout.addSpacing(6)

        self._snap_btn = QToolButton()
        self._snap_btn.setText("Snap")
        self._snap_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._snap_btn.setToolTip(
            "Choose which geometric constraints are active (hold Alt to bypass)"
        )
        self._snap_btn.setAccessibleName("Snapping options")
        self._snap_menu = QMenu(self._snap_btn)
        self._snap_actions = {}
        for key, label_text, setter in (
            ("snap_master", "Enable snapping", "set_snap_master"),
            ("grid_snap", "Grid points", "set_grid_snap"),
            ("snap_vertex", "Vertices and centers", "set_snap_vertex"),
            ("snap_edge", "Edges and midpoints", "set_snap_edge"),
            ("snap_tangent", "Tangents", "set_snap_tangent"),
            ("snap_extension", "Edge extensions", "set_snap_extension"),
            ("snap_angle", "Angle constraints", "set_snap_angle"),
        ):
            action = self._snap_menu.addAction(label_text)
            action.setCheckable(True)
            action.toggled.connect(lambda checked, method=setter: self._set_snap(method, checked))
            self._snap_actions[key] = action
            if key == "snap_master":
                self._snap_menu.addSeparator()
        self._snap_btn.setMenu(self._snap_menu)
        layout.addWidget(self._snap_btn)

        self._construction_btn = QPushButton("Construction")
        self._construction_btn.setCheckable(True)
        self._construction_btn.setToolTip("Create new geometry as construction/reference geometry")
        self._construction_btn.toggled.connect(self._set_construction)
        layout.addWidget(self._construction_btn)

        self._constraints_btn = QToolButton()
        self._constraints_btn.setText("Constrain")
        self._constraints_btn.setAccessibleName("Geometry constraints")
        self._constraints_btn.setToolTip("Add or remove constraints on selected geometry")
        self._constraints_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        constraints_menu = QMenu(self._constraints_btn)
        for kind, label in (
            ("horizontal", "Horizontal"), ("vertical", "Vertical"),
            ("parallel", "Parallel"), ("perpendicular", "Perpendicular"),
            ("equal_length", "Equal length"), ("coincident", "Coincident"),
            ("fixed", "Fix"),
        ):
            constraints_menu.addAction(label, lambda _checked=False, k=kind: self._add_constraint(k))
        constraints_menu.addSeparator()
        constraints_menu.addAction("Remove selection constraints", self._remove_constraints)
        self._constraints_btn.setMenu(constraints_menu)
        layout.addWidget(self._constraints_btn)

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
        spacing = float(state.get("grid_spacing", 1.0))

        self._pan_btn.blockSignals(True)
        self._pan_btn.setChecked(
            hasattr(self._canvas, "get_mode") and self._canvas.get_mode() == "pan"
        )
        self._pan_btn.blockSignals(False)
        self._grid_btn.setChecked(grid_on)
        self._construction_btn.blockSignals(True)
        self._construction_btn.setChecked(bool(state.get("construction_mode", False)))
        self._construction_btn.blockSignals(False)
        snap_on = bool(state.get("snap_master", True))
        self._snap_btn.setProperty("active", snap_on)
        self._snap_btn.style().unpolish(self._snap_btn)
        self._snap_btn.style().polish(self._snap_btn)
        for key, action in self._snap_actions.items():
            action.blockSignals(True)
            action.setChecked(bool(state.get(key, False if key == "grid_snap" else True)))
            action.blockSignals(False)

        self._spacing.setText(f"{spacing:g}")
        show_spacing = grid_on or bool(state.get("grid_snap", False))
        for widget in (
            self._spacing_label,
            self._spacing,
            self._spacing_dec,
            self._spacing_inc,
        ):
            widget.setVisible(show_spacing)

    def _set_snap(self, method: str, enabled: bool) -> None:
        canvas = self._canvas
        if canvas is not None and hasattr(canvas, method):
            getattr(canvas, method)(enabled)
            self._after_change()

    def _set_construction(self, enabled: bool) -> None:
        if self._canvas is not None and hasattr(self._canvas, "set_construction_mode"):
            self._canvas.set_construction_mode(enabled)
            self._after_change()

    def _add_constraint(self, kind: str) -> None:
        if self._canvas is not None and hasattr(self._canvas, "add_geometric_constraint"):
            self._canvas.add_geometric_constraint(kind)
            self._after_change()

    def _remove_constraints(self) -> None:
        if self._canvas is not None and hasattr(self._canvas, "remove_constraints_for_selection"):
            self._canvas.remove_constraints_for_selection()
            self._after_change()

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

    def _toggle_pan(self) -> None:
        canvas = self._canvas
        if canvas is None or not hasattr(canvas, "set_mode"):
            return
        current = canvas.get_mode() if hasattr(canvas, "get_mode") else "select"
        canvas.set_mode("select" if current == "pan" else "pan")
        self._after_change()

    def _scale_spacing(self, factor: float) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if not hasattr(canvas, "get_precision_state") or not hasattr(canvas, "set_grid_spacing"):
            return
        current = float(canvas.get_precision_state().get("grid_spacing", 1.0))
        canvas.set_grid_spacing(
            max(GRID_SPACING_MIN_MM, min(GRID_SPACING_MAX_MM, current * factor))
        )
        self._after_change()

    def _apply_spacing(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if not hasattr(canvas, "set_grid_spacing"):
            return
        try:
            value = parse_numeric_expression(self._spacing.text(), "mm")
        except (ValueError, ZeroDivisionError, OverflowError):
            set_line_edit_error(self._spacing, "Use a positive number or expression, e.g. 25/2.")
            return
        if value <= 0:
            set_line_edit_error(self._spacing, "Grid spacing must be greater than zero.")
            return
        clear_line_edit_error(self._spacing)
        canvas.set_grid_spacing(max(GRID_SPACING_MIN_MM, min(GRID_SPACING_MAX_MM, value)))
        self._after_change()


__all__ = ["CanvasPrecisionBar"]
