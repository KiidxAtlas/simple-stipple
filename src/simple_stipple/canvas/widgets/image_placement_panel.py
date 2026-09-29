"""Numeric placement for a canvas background image.

Styled like the Draft Properties panel — X/Y/W/H grid, quarter-turn buttons,
an absolute rotation field — and accepting the same arithmetic/unit input
(``25/2``, ``1in + 3mm``). The owner applies edits and pushes the resulting
placement back with :meth:`set_placement`, so dragging and typing share one
source of truth. Invalid entries stay in the field, marked, until corrected.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.ui.components.feedback import clear_line_edit_error, reject_input
from simple_stipple.ui.components.layout import CollapsibleSection, container_with_layout
from simple_stipple.ui.components.units import parse_numeric_expression, to_display, unit_suffix

_EXPRESSION_HINT = "Enter a number or expression, e.g. 25/2 or 1in + 3mm"


def _num_edit(on_commit: Callable[[], None], tooltip: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    edit.setMinimumWidth(72)
    edit.setMinimumHeight(34)
    edit.setToolTip(tooltip)
    edit.editingFinished.connect(on_commit)
    return edit


def _normalized_degrees(angle: float) -> float:
    """Map any angle into (-180, 180] so the field never shows 450°."""
    wrapped = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


class ImagePlacementPanel(QWidget):
    """Position, size, and rotation fields for one background image.

    X/Y is the image's bottom-left corner before rotation; rotation turns it
    about its centre. Width and height keep the image's proportions.
    """

    placementEdited = Signal(float, float, float, float, float)  # x, y, w, h, rotation
    centerOnBedRequested = Signal()
    fitToBedRequested = Signal()

    def __init__(self, unit: Callable[[], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._unit = unit
        self._placement: tuple[float, float, float, float, float] | None = None
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)
        title = QLabel("Image")
        title.setProperty("role", "section-label")
        root.addWidget(title)

        self._empty_hint = QLabel("Open an image to set its position, size, and rotation.")
        self._empty_hint.setProperty("role", "hint-sm")
        self._empty_hint.setWordWrap(True)
        root.addWidget(self._empty_hint)

        expression_tip = "Accepts arithmetic and units, e.g. 25/2 or 1in + 3mm"
        geometry, geometry_layout = container_with_layout(None, QVBoxLayout, spacing=4)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        self._x = _num_edit(
            lambda: self._commit_position("x"), f"Left edge before rotation. {expression_tip}"
        )
        self._y = _num_edit(
            lambda: self._commit_position("y"), f"Bottom edge before rotation. {expression_tip}"
        )
        self._w = _num_edit(
            lambda: self._commit_size("w"), f"Width; height follows. {expression_tip}"
        )
        self._h = _num_edit(
            lambda: self._commit_size("h"), f"Height; width follows. {expression_tip}"
        )
        for axis, edit, row, col in (
            ("X", self._x, 0, 0),
            ("Y", self._y, 0, 2),
            ("W", self._w, 1, 0),
            ("H", self._h, 1, 2),
        ):
            label = QLabel(axis)
            label.setProperty("role", "field-label")
            edit.setAccessibleName(f"Image {axis}")
            grid.addWidget(label, row, col)
            grid.addWidget(edit, row, col + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        geometry_layout.addLayout(grid)
        bed_row = QHBoxLayout()
        bed_row.setSpacing(6)
        for text, tip, signal in (
            (
                "Center on bed",
                "Move the image so its centre sits at the centre of the machine bed",
                self.centerOnBedRequested,
            ),
            (
                "Fit to bed",
                "Scale the image, keeping its proportions, to fill the machine bed, then centre it",
                self.fitToBedRequested,
            ),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(signal)
            bed_row.addWidget(button)
        geometry_layout.addLayout(bed_row)
        self._geometry_section = CollapsibleSection("Geometry", geometry, expanded=True)
        root.addWidget(self._geometry_section)

        transform, transform_layout = container_with_layout(None, QVBoxLayout, spacing=8)
        buttons = QGridLayout()
        buttons.setHorizontalSpacing(6)
        buttons.setVerticalSpacing(6)
        for index, (text, tip, callback) in enumerate(
            (
                ("R +90", "Rotate 90° CCW", lambda: self._rotate_by(90.0)),
                ("R -90", "Rotate 90° CW", lambda: self._rotate_by(-90.0)),
                ("Reset", "Back to the origin, unrotated, at the current size", self._reset),
            )
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            buttons.addWidget(button, index // 2, index % 2)
        transform_layout.addLayout(buttons)
        rotation_row = QHBoxLayout()
        rotation_row.setSpacing(8)
        rotation_row.addWidget(QLabel("Rotation"))
        angle_label = QLabel("∠")
        angle_label.setToolTip("Absolute angle in degrees, counter-clockwise")
        rotation_row.addWidget(angle_label)
        self._rotation = _num_edit(
            self._commit_rotation, "Absolute angle in degrees, counter-clockwise, about the centre"
        )
        self._rotation.setAccessibleName("Image rotation")
        rotation_row.addWidget(self._rotation, stretch=1)
        transform_layout.addLayout(rotation_row)
        self._transform_section = CollapsibleSection("Transform", transform, expanded=True)
        root.addWidget(self._transform_section)
        root.addStretch()
        self.set_placement(None)

    # ── Owner API ───────────────────────────────────────────────────────────

    def set_placement(self, placement: tuple[float, float, float, float, float] | None) -> None:
        """Show ``(x, y, w, h, rotation)`` in mm/degrees, or the empty state.

        A rejected entry keeps its typed text while the placement is
        unchanged, so the user can correct it; a new placement replaces it.
        """
        changed = placement != self._placement
        self._placement = placement
        available = placement is not None
        self._empty_hint.setVisible(not available)
        self._geometry_section.setVisible(available)
        self._transform_section.setVisible(available)
        if placement is None:
            return
        unit = self._unit()
        self._geometry_section.set_subtitle(f"Values in {unit_suffix(unit)}")
        x, y, w, h, rotation = placement
        self._updating = True
        try:
            for edit, text in (
                (self._x, f"{to_display(x, unit):.3f}"),
                (self._y, f"{to_display(y, unit):.3f}"),
                (self._w, f"{to_display(w, unit):.3f}"),
                (self._h, f"{to_display(h, unit):.3f}"),
                (self._rotation, f"{_normalized_degrees(rotation):.2f}"),
            ):
                if edit.hasFocus() or (edit.property("error") and not changed):
                    continue
                clear_line_edit_error(edit)
                edit.setText(text)
        finally:
            self._updating = False

    # ── Commits ─────────────────────────────────────────────────────────────

    def _parse(self, edit: QLineEdit, *, is_length: bool = True) -> float | None:
        try:
            value = parse_numeric_expression(edit.text(), self._unit(), is_length=is_length)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            reject_input(
                edit,
                _EXPRESSION_HINT if is_length else "Enter an angle in degrees, e.g. 90 or 45/2",
            )
            return None
        clear_line_edit_error(edit)
        return value

    def _emit(self, x: float, y: float, w: float, h: float, rotation: float) -> None:
        self.placementEdited.emit(x, y, w, h, _normalized_degrees(rotation))

    def _commit_position(self, axis: str) -> None:
        if self._updating or self._placement is None:
            return
        value = self._parse(self._x if axis == "x" else self._y)
        if value is None:
            return
        x, y, w, h, rotation = self._placement
        new_x, new_y = (value, y) if axis == "x" else (x, value)
        if (new_x, new_y) != (x, y):
            self._emit(new_x, new_y, w, h, rotation)

    def _commit_size(self, axis: str) -> None:
        if self._updating or self._placement is None:
            return
        edit = self._w if axis == "w" else self._h
        value = self._parse(edit)
        if value is None:
            return
        x, y, w, h, rotation = self._placement
        if value <= 0:
            reject_input(edit, f"{'Width' if axis == 'w' else 'Height'} must be greater than zero")
            return
        if w <= 0 or h <= 0:
            return
        scale = value / (w if axis == "w" else h)
        if abs(scale - 1.0) > 1e-9:
            self._emit(x, y, w * scale, h * scale, rotation)

    def _commit_rotation(self) -> None:
        if self._updating or self._placement is None:
            return
        angle = self._parse(self._rotation, is_length=False)
        if angle is None:
            return
        x, y, w, h, rotation = self._placement
        if abs(_normalized_degrees(angle - rotation)) > 1e-9:
            self._emit(x, y, w, h, angle)

    def _rotate_by(self, delta: float) -> None:
        if self._placement is not None:
            x, y, w, h, rotation = self._placement
            self._emit(x, y, w, h, rotation + delta)

    def _reset(self) -> None:
        if self._placement is not None:
            _x, _y, w, h, _rotation = self._placement
            self._emit(0.0, 0.0, w, h, 0.0)
