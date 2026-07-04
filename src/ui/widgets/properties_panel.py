"""Persistent properties panel: numeric editing for the current selection.

Shows X / Y / W / H for any selection, rotate/mirror actions, and — for a
single parametric entity — its defining parameters (circle radius, polygon
radius/sides, ellipse rx/ry, arc radius, line length/angle). Replaces the
scattered modal prompts as the always-available numeric editing surface.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_PARAM_FIELDS: dict[str, list[tuple[str, str]]] = {
    # kind → [(meta key, label)]
    "circle": [("radius", "Radius")],
    "polygon": [("radius", "Radius"), ("sides", "Sides")],
    "ellipse": [("rx", "Radius X"), ("ry", "Radius Y")],
    "arc": [("radius", "Radius")],
}


def _num_edit(on_commit) -> QLineEdit:
    edit = QLineEdit()
    edit.setValidator(QDoubleValidator(-1e9, 1e9, 4))
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    edit.setMaximumWidth(90)
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
        for row, (label, edit) in enumerate(
            (("X", self._x), ("Y", self._y), ("W", self._w), ("H", self._h))
        ):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #8b949e;")
            grid.addWidget(lbl, row // 2, (row % 2) * 2)
            grid.addWidget(edit, row // 2, (row % 2) * 2 + 1)
        # keep the label/field pairs packed to the left instead of spreading
        # across the panel width
        grid.setColumnStretch(4, 1)
        grid.setColumnMinimumWidth(2, 18)
        fields_root.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        for text, tip, cb in (
            ("⟲ 90°", "Rotate 90° CCW", lambda: self._rotate(90.0)),
            ("⟳ 90°", "Rotate 90° CW", lambda: self._rotate(-90.0)),
            ("⇋", "Mirror horizontally", lambda: self._mirror("horizontal")),
            ("⇵", "Mirror vertically", lambda: self._mirror("vertical")),
            ("〰", "Smooth jagged corners (Chaikin)", self._smooth),
            ("⤳", "Simplify — reduce vertex count", self._simplify),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setMaximumWidth(52)
            btn.clicked.connect(cb)
            actions.addWidget(btn)
        rot_lbl = QLabel("∠")
        rot_lbl.setToolTip("Rotate by angle (° CCW)")
        actions.addWidget(rot_lbl)
        self._rot = _num_edit(self._commit_rotation)
        self._rot.setPlaceholderText("0")
        self._rot.setMaximumWidth(56)
        actions.addWidget(self._rot)
        actions.addStretch(1)
        fields_root.addLayout(actions)

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
        self.refresh()

    # ── Refresh ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._updating = True
        try:
            info = self._canvas.selection_geometry()
            enabled = info is not None
            self._fields_container.setVisible(enabled)
            self._empty_hint.setVisible(not enabled)
            for edit in (self._x, self._y, self._w, self._h, self._rot):
                edit.setEnabled(enabled)
            if info is None:
                self._summary.setText("No selection")
                for edit in (self._x, self._y, self._w, self._h):
                    edit.clear()
                self._set_param_rows(None, None, {})
                return
            count = info["count"]
            kind = info.get("kind")
            if count == 1 and kind:
                self._summary.setText(kind.capitalize())
            else:
                self._summary.setText(f"{count} shapes")
            self._x.setText(f"{info['x']:.2f}")
            self._y.setText(f"{info['y']:.2f}")
            self._w.setText(f"{info['w']:.2f}")
            self._h.setText(f"{info['h']:.2f}")
            self._set_param_rows(info.get("index"), kind, info.get("meta") or {})
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
                    self._param_edits["angle"].setText(
                        f"{math.degrees(math.atan2(dy, dx)):.1f}"
                    )
            return
        for key, edit in self._param_edits.items():
            value = meta.get(key)
            if value is not None:
                edit.setText(f"{float(value):g}")

    # ── Commits ───────────────────────────────────────────────────────────

    def _value(self, edit: QLineEdit) -> float | None:
        try:
            return float(edit.text())
        except (TypeError, ValueError):
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

    def _commit_rotation(self) -> None:
        if self._updating:
            return
        angle = self._value(self._rot)
        if angle:
            self._canvas.rotate_selected(angle)
            self._rot.clear()
            self.refresh()

    def _rotate(self, angle: float) -> None:
        self._canvas.rotate_selected(angle)
        self.refresh()

    def _mirror(self, axis: str) -> None:
        self._canvas.mirror_selected(axis)
        self.refresh()

    def _smooth(self) -> None:
        self._canvas.smooth_selected()
        self.refresh()

    def _simplify(self) -> None:
        self._canvas.simplify_selected()
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
