"""Persistent properties panel: numeric editing for the current selection.

Shows X / Y / W / H for any selection, rotate/mirror actions, and — for a
single parametric entity — its defining parameters (circle radius, polygon
radius/sides, ellipse rx/ry, arc radius, line length/angle). Replaces the
scattered modal prompts as the always-available numeric editing surface.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.util import parse_numeric_expression, to_display
from src.ui.util import suffix as unit_suffix

_PARAM_FIELDS: dict[str, list[tuple[str, str]]] = {
    # kind → [(meta key, label)]
    "circle": [("radius", "Radius")],
    "polygon": [("radius", "Radius"), ("sides", "Sides")],
    # Width/height already live in the common W/H row; only expose unique
    # defining parameters here to avoid a duplicated, noisy inspector.
    "rounded_rectangle": [("radius", "Corner radius")],
    "star": [
        ("radius", "Radius"),
        ("points", "Points"),
        ("inner_ratio", "Inner ratio"),
    ],
    "ellipse": [("rx", "Radius X"), ("ry", "Radius Y")],
    "arc": [("radius", "Radius")],
}


def _num_edit(on_commit) -> QLineEdit:
    edit = QLineEdit()
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    edit.setMinimumWidth(88)
    edit.setMaximumWidth(160)
    edit.editingFinished.connect(on_commit)
    return edit


class CanvasPropertiesPanel(QWidget):
    """Docked inspector bound to one canvas."""

    def __init__(self, canvas: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        title = QLabel("Properties")
        title.setProperty("role", "panel-title")
        root.addWidget(title)

        self._summary = QLabel("No selection")
        self._summary.setStyleSheet("color: #8b949e;")
        root.addWidget(self._summary)

        self._metrics = QLabel()
        self._metrics.setProperty("role", "hint-sm")
        self._metrics.setWordWrap(True)
        root.addWidget(self._metrics)

        self._empty_hint = QLabel(
            "Select a shape to edit its position, size, or\nshape-specific properties."
        )
        self._empty_hint.setStyleSheet("color: #484f58; font-size: 11px;")
        self._empty_hint.setWordWrap(True)
        root.addWidget(self._empty_hint)

        self._fields_container = QWidget()
        fields_root = QVBoxLayout(self._fields_container)
        fields_root.setContentsMargins(0, 0, 0, 0)
        fields_root.setSpacing(4)
        root.addWidget(self._fields_container)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        self._x = _num_edit(lambda: self._commit_pos())
        self._y = _num_edit(lambda: self._commit_pos())
        self._w = _num_edit(lambda: self._commit_size("w"))
        self._h = _num_edit(lambda: self._commit_size("h"))
        for key, edit in (("x", self._x), ("y", self._y), ("w", self._w), ("h", self._h)):
            edit.setProperty("geometry-key", key)
            edit.installEventFilter(self)
            edit.setToolTip("Accepts arithmetic and units, e.g. 25/2 or 1in + 3mm")
        self._axis_labels: dict[str, QLabel] = {}
        for row, (axis, edit) in enumerate(
            (("X", self._x), ("Y", self._y), ("W", self._w), ("H", self._h))
        ):
            lbl = QLabel(axis)
            lbl.setStyleSheet("color: #8b949e;")
            self._axis_labels[axis] = lbl
            grid.addWidget(lbl, row, 0)
            grid.addWidget(edit, row, 1)
        grid.setColumnStretch(1, 1)

        self._aspect_lock_btn = QPushButton("Lock")
        self._aspect_lock_btn.setCheckable(True)
        self._aspect_lock_btn.setMinimumWidth(64)
        self._aspect_lock_btn.setToolTip(
            "Lock aspect ratio\nKeeps width/height proportional for both "
            "typed W/H edits and gizmo-handle drags"
        )
        self._aspect_lock_btn.setStyleSheet(
            "QPushButton:checked { background: #1f3a6e; border: 1px solid #2f81f7; }"
        )
        self._aspect_lock_btn.toggled.connect(self._on_aspect_lock_toggled)
        grid.addWidget(self._aspect_lock_btn, 2, 2, 2, 1)
        fields_root.addLayout(grid)

        actions = QGridLayout()
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(6)
        for index, (text, tip, cb) in enumerate((
            ("R +90", "Rotate 90° CCW", lambda: self._rotate(90.0)),
            ("R -90", "Rotate 90° CW", lambda: self._rotate(-90.0)),
            ("Flip H", "Mirror horizontally", lambda: self._mirror("horizontal")),
            ("Flip V", "Mirror vertically", lambda: self._mirror("vertical")),
            ("Smooth", "Smooth jagged corners (Chaikin)", self._smooth),
            ("Simplify", "Simplify — reduce vertex count", self._simplify),
        )):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setMinimumHeight(28)
            btn.clicked.connect(cb)
            actions.addWidget(btn, index // 2, index % 2)
        fields_root.addLayout(actions)

        rotation_row = QHBoxLayout()
        rotation_row.setSpacing(6)
        rotation_row.addWidget(QLabel("Rotation"))
        rot_lbl = QLabel("∠")
        rot_lbl.setToolTip("Rotate by angle (° CCW)")
        rotation_row.addWidget(rot_lbl)
        self._rot = _num_edit(self._commit_rotation)
        self._rot.setProperty("geometry-key", "rotation")
        self._rot.installEventFilter(self)
        self._rot.setPlaceholderText("Angle")
        self._rot.setToolTip("Absolute angle of the selected shape in degrees")
        rotation_row.addWidget(self._rot, stretch=1)
        fields_root.addLayout(rotation_row)

        context = QGridLayout()
        context.setHorizontalSpacing(6)
        context.setVerticalSpacing(6)
        self._context_buttons: dict[str, QPushButton] = {}
        for index, (key, text, tip, callback) in enumerate((
            ("duplicate", "Duplicate", "Duplicate the current selection", canvas.duplicate_selected),
            ("close", "Close path", "Close selected open paths", canvas.close_selected_polylines),
            ("delete", "Delete", "Delete selected geometry", canvas.delete_selected),
        )):
            button = QPushButton(text)
            button.setMinimumHeight(28)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            context.addWidget(button, index // 2, index % 2)
            self._context_buttons[key] = button
        fields_root.addLayout(context)

        # Shape-parameter rows (built per selection kind)
        self._param_grid = QGridLayout()
        self._param_grid.setContentsMargins(0, 2, 0, 0)
        self._param_grid.setHorizontalSpacing(6)
        self._param_grid.setVerticalSpacing(3)
        self._param_grid.setColumnStretch(2, 1)
        fields_root.addLayout(self._param_grid)
        self._param_edits: dict[str, QLineEdit] = {}
        self._param_index: int | None = None
        self._param_kind: str | None = None

        canvas.selectionChanged.connect(lambda _n: self.refresh())
        if hasattr(canvas, "geometryChanged"):
            canvas.geometryChanged.connect(self.refresh)
        self.refresh()

    # ── Refresh ───────────────────────────────────────────────────────────

    def _unit(self) -> str:
        return getattr(self._canvas, "_unit_system", "mm")

    def refresh(self) -> None:
        self._updating = True
        try:
            unit = self._unit()
            for axis, lbl in self._axis_labels.items():
                lbl.setText(f"{axis} ({unit_suffix(unit)})")
            self._aspect_lock_btn.setChecked(getattr(self._canvas, "_aspect_ratio_locked", False))
            info = self._canvas.selection_geometry()
            enabled = info is not None
            self._fields_container.setVisible(enabled)
            self._empty_hint.setVisible(not enabled)
            for edit in (self._x, self._y, self._w, self._h, self._rot):
                edit.setEnabled(enabled)
            if info is None:
                self._summary.setText("No selection")
                self._metrics.clear()
                for edit in (self._x, self._y, self._w, self._h, self._rot):
                    edit.clear()
                self._set_param_rows(None, None, {})
                return
            count = info["count"]
            kind = info.get("kind")
            display_kind = info.get("display_kind") or kind
            if count == 1 and display_kind:
                self._summary.setText(str(display_kind).replace("_", " ").title())
            else:
                self._summary.setText(f"{count} shapes")
            metric_parts = [f"Length {to_display(info['length'], unit):.3g} {unit_suffix(unit)}"]
            if info.get("area", 0.0) > 0:
                area_scale = to_display(1.0, unit) ** 2
                metric_parts.append(
                    f"Area {float(info['area']) * area_scale:.3g} {unit_suffix(unit)}²"
                )
            if info.get("diameter") is not None:
                metric_parts.append(
                    f"Diameter {to_display(float(info['diameter']), unit):.3g} {unit_suffix(unit)}"
                )
            if info.get("clearance") is not None:
                metric_parts.append(
                    f"Clearance {to_display(float(info['clearance']), unit):.3g} "
                    f"{unit_suffix(unit)}"
                )
            self._metrics.setText(" · ".join(metric_parts))
            self._x.setText(f"{to_display(info['x'], unit):.2f}")
            self._y.setText(f"{to_display(info['y'], unit):.2f}")
            self._w.setText(f"{to_display(info['w'], unit):.2f}")
            self._h.setText(f"{to_display(info['h'], unit):.2f}")
            self._rot.setText(f"{float(info.get('rotation', 0.0)):.1f}")
            self._set_param_rows(info.get("index"), kind, info.get("meta") or {})
            self._context_buttons["duplicate"].setVisible(count > 0)
            self._context_buttons["delete"].setVisible(count > 0)
            selected = [
                self._canvas._entities[i]
                for i in getattr(self._canvas, "_sel", set())
                if 0 <= i < len(self._canvas._entities)
            ]
            self._context_buttons["close"].setVisible(
                any(len(e.points) >= 3 and e.points[0] != e.points[-1] for e in selected)
            )
        finally:
            self._updating = False

    def _set_param_rows(self, index: int | None, kind: str | None, meta: dict) -> None:
        wanted = _PARAM_FIELDS.get(kind or "", [])
        if kind == "line":
            wanted = [("length", "Length"), ("angle", "Angle °")]
        if kind != self._param_kind:
            while self._param_grid.count():
                item = self._param_grid.takeAt(0)
                if item is None:
                    continue
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self._param_edits = {}
            for row, (key, label) in enumerate(wanted):
                lbl = QLabel(label)
                lbl.setStyleSheet("color: #8b949e;")
                edit = _num_edit(lambda k=key: self._commit_param(k))
                edit.setProperty("geometry-key", key)
                edit.installEventFilter(self)
                self._param_grid.addWidget(lbl, row, 0)
                self._param_grid.addWidget(edit, row, 1)
                self._param_edits[key] = edit
            self._param_kind = kind
        self._param_index = index
        if not wanted:
            return
        # Fill values
        if kind == "line":
            pts = None
            if index is not None:
                pts = self._canvas._entities[index].points
            if pts and len(pts) >= 2:
                import math

                dx = pts[-1][0] - pts[0][0]
                dy = pts[-1][1] - pts[0][1]
                if "length" in self._param_edits:
                    self._param_edits["length"].setText(f"{math.hypot(dx, dy):.2f}")
                if "angle" in self._param_edits:
                    self._param_edits["angle"].setText(f"{math.degrees(math.atan2(dy, dx)):.1f}")
            return
        for key, edit in self._param_edits.items():
            value = meta.get(key)
            if value is not None:
                edit.setText(f"{float(value):g}")

    # ── Commits ───────────────────────────────────────────────────────────

    def eventFilter(self, watched, event) -> bool:
        key = watched.property("geometry-key") if isinstance(watched, QLineEdit) else None
        if key and hasattr(self._canvas, "set_property_highlight"):
            if event.type() == QEvent.Type.FocusIn:
                self._canvas.set_property_highlight(str(key))
            elif event.type() == QEvent.Type.Enter:
                self._canvas.set_property_highlight(str(key))
            elif event.type() == QEvent.Type.FocusOut:
                self._canvas.set_property_highlight(None)
            elif event.type() == QEvent.Type.Leave and not watched.hasFocus():
                self._canvas.set_property_highlight(None)
        return super().eventFilter(watched, event)

    def _value(self, edit: QLineEdit) -> float | None:
        key = str(edit.property("geometry-key") or "")
        is_length = key not in {"rotation", "angle", "sides", "points", "inner_ratio"}
        try:
            return parse_numeric_expression(edit.text(), self._unit(), is_length=is_length)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            return None

    def _commit_pos(self) -> None:
        if self._updating:
            return
        x = self._value(self._x)
        y = self._value(self._y)
        if self._canvas.move_selection_to(x, y):
            self.refresh()

    def _commit_size(self, axis: str) -> None:
        if self._updating:
            return
        value = self._value(self._w if axis == "w" else self._h)
        if value is None or value <= 0:
            self.refresh()
            return
        if axis == "w":
            self._canvas._set_selected_width(value)
        else:
            self._canvas._set_selected_height(value)
        self.refresh()

    def _on_aspect_lock_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self._canvas.set_aspect_ratio_locked(checked)

    def _commit_rotation(self) -> None:
        if self._updating:
            return
        angle = self._value(self._rot)
        info = self._canvas.selection_geometry()
        if angle is not None and info is not None:
            current = float(info.get("rotation", 0.0))
            delta = (angle - current + 180.0) % 360.0 - 180.0
            if abs(delta) > 1e-9:
                self._canvas.rotate_selected(delta)
            self.refresh()

    def _rotate(self, angle: float) -> None:
        self._canvas.rotate_selected(angle)
        self.refresh()

    def _mirror(self, axis: str) -> None:
        self._canvas.mirror_selected(axis)
        self.refresh()

    def _smooth(self) -> None:
        self._canvas.smooth_selected(self._canvas._smooth_iterations)
        self.refresh()

    def _simplify(self) -> None:
        self._canvas.simplify_selected(self._canvas._simplify_tolerance)
        self.refresh()

    def _commit_param(self, key: str) -> None:
        if self._updating or self._param_index is None:
            return
        edit = self._param_edits.get(key)
        value = self._value(edit) if edit else None
        if value is None:
            return
        if self._param_kind == "line":
            if key == "length":
                self._canvas._set_selected_line_length(value)
            else:
                self._canvas._set_selected_line_angle(value)
        else:
            self._canvas.set_shape_param(self._param_index, key, value)
        self.refresh()
