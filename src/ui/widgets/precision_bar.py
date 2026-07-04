"""Persistent precision/grid controls widget."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton


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

        self._grid_btn = QPushButton("Grid")
        self._grid_btn.setMinimumHeight(24)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setToolTip("Toggle canvas grid overlay")
        self._grid_btn.clicked.connect(self._toggle_grid)
        layout.addWidget(self._grid_btn)

        self._construction_btn = QPushButton("Guides")
        self._construction_btn.setMinimumHeight(24)
        self._construction_btn.setCheckable(True)
        self._construction_btn.setToolTip("Toggle construction guide lines")
        self._construction_btn.clicked.connect(self._toggle_construction)
        layout.addWidget(self._construction_btn)

        self._measure_btn = QPushButton("Measure")
        self._measure_btn.setMinimumHeight(24)
        self._measure_btn.setCheckable(True)
        self._measure_btn.setToolTip("Toggle distance measurement tool")
        self._measure_btn.clicked.connect(self._toggle_measure)
        layout.addWidget(self._measure_btn)

        layout.addWidget(QLabel("Grid mm"))
        self._spacing = QLineEdit()
        self._spacing.setFixedWidth(64)
        self._spacing.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._spacing.returnPressed.connect(self._apply_spacing)
        layout.addWidget(self._spacing)

        self._spacing_dec = QPushButton("\u2212")
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
        construction_on = bool(state.get("construction_mode", False))
        measure_on = bool(state.get("measure_mode", False))
        spacing = float(state.get("grid_spacing", 1.0))

        self._grid_btn.setChecked(grid_on)
        self._construction_btn.setChecked(construction_on)
        self._measure_btn.setChecked(measure_on)

        self._spacing.setText(f"{spacing:g}")

    def _after_change(self) -> None:
        self.refresh()
        if callable(self._on_changed):
            self._on_changed()

    def _toggle_grid(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "set_grid_visible") and hasattr(
            canvas, "get_precision_state"
        ):
            state = canvas.get_precision_state()
            canvas.set_grid_visible(not bool(state.get("grid_visible", False)))
            self._after_change()

    def _toggle_construction(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        if hasattr(canvas, "set_construction_mode") and hasattr(
            canvas, "get_precision_state"
        ):
            state = canvas.get_precision_state()
            canvas.set_construction_mode(
                not bool(state.get("construction_mode", False))
            )
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
        if not hasattr(canvas, "get_precision_state") or not hasattr(
            canvas, "set_grid_spacing"
        ):
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


__all__ = ["CanvasPrecisionBar"]
