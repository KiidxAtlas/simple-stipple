"""DxfCanvas — extended polyline view with quick shape tools and radial menu."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu

from src.backend.geometry.shapes import (
    shape_circle,
    shape_polygon,
    shape_rect,
    shape_slot,
)
from src.ui.canvas.view import PolylineView


class DxfCanvas(PolylineView):
    """Unified shared canvas used across Draft, Pattern, Trace, and preview surfaces."""

    quickShapeChanged = Signal(str)
    quickShapeEnabledChanged = Signal(bool)

    _VALID_QUICK_SHAPES = frozenset({"rectangle", "circle", "slot", "hexagon"})

    _CUTOUT_COLOR = "#f0883e"

    def __init__(
        self,
        parent=None,
        selectable: bool = True,
        on_change=None,
        on_mode_change=None,
        on_poly_change=None,
        on_send_selected_to_pattern=None,
        on_send_selected_to_draft=None,
        on_use_selected_as_fill_pattern=None,
        on_cutout_toggle=None,
        on_ghost_click=None,
        draft_profile: bool = False,
    ):
        super().__init__(
            parent=parent,
            selectable=selectable,
            on_change=on_change,
            on_mode_change=on_mode_change,
            on_poly_change=on_poly_change,
        )
        self._send_selected_to_pattern_cb = on_send_selected_to_pattern
        self._send_selected_to_draft_cb = on_send_selected_to_draft
        self._use_selected_as_fill_pattern_cb = on_use_selected_as_fill_pattern
        self._on_cutout_toggle = on_cutout_toggle
        self._on_ghost_click = on_ghost_click
        self._cutout_indices: set[int] = set()
        self._draft_profile = bool(draft_profile or selectable)

        self._quick_shape_mode: str = "rectangle"
        self._quick_shape_enabled: bool = False
        self._shape_drag_active: bool = False
        self._shape_drag_mode: str = "rectangle"
        self._shape_start_w: tuple[float, float] | None = None
        self._shape_start_c: QPoint | None = None
        self._shape_end_c: QPoint | None = None
        self._radial_active: bool = False
        self._radial_center_c: QPoint = QPoint(0, 0)
        self._size_w_edit: QLineEdit | None = None
        self._size_h_edit: QLineEdit | None = None

        if self._draft_profile:
            self.set_grid_visible(True)
            self.set_grid_snap(False)
            self.set_grid_spacing(1.0)

    @property
    def quick_shape_mode(self) -> str:
        return self._quick_shape_mode

    @property
    def quick_shape_enabled(self) -> bool:
        return self._quick_shape_enabled

    def set_quick_shape_enabled(self, enabled: bool) -> None:
        self._quick_shape_enabled = enabled
        self.quickShapeEnabledChanged.emit(enabled)
        self._redraw()

    def set_quick_shape_mode(self, mode: str, *, flash: bool = True) -> None:
        m = mode.strip().lower()
        if m not in self._VALID_QUICK_SHAPES:
            return
        self._quick_shape_mode = m
        if not self._quick_shape_enabled:
            self._quick_shape_enabled = True
            self.quickShapeEnabledChanged.emit(True)
        self.quickShapeChanged.emit(m)
        if flash:
            self._show_flash(f"Drag shape: {m}", 900)
        self._redraw()

    def set_cutout_indices(self, indices: set[int]) -> None:
        """Mark poly indices as cutout shapes, rendering them in amber."""
        self._cutout_indices = set(indices)
        self.set_accent_polys({idx: self._CUTOUT_COLOR for idx in indices})

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._selectable and self._radial_active:
            if event.button() == Qt.MouseButton.LeftButton:
                idx = self._radial_index_at(event.position().x(), event.position().y())
                self._radial_active = False
                self._redraw()
                if idx is not None:
                    self._execute_radial_action(idx)
                return
            if event.button() in (
                Qt.MouseButton.RightButton,
                Qt.MouseButton.MiddleButton,
            ):
                self._radial_active = False
                self._redraw()
                return

        if (
            self._selectable
            and event.button() == Qt.MouseButton.LeftButton
            and self._mode == "select"
            and not self._measure_mode
        ):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().mousePressEvent(event)
                return
            pos = event.position()
            hit = self._find_poly_at(pos.x(), pos.y())
            if hit is None:
                # Check if the user clicked on a ghost (other-layer) poly.
                ghost_hit = self._find_ghost_poly_at(pos.x(), pos.y())
                if ghost_hit is not None and callable(self._on_ghost_click):
                    self._on_ghost_click(ghost_hit)
                    return
                if self._quick_shape_enabled:
                    mode = self._shape_mode_from_modifiers(event.modifiers())
                    self._start_shape_drag(mode, pos)
                    return
                super().mousePressEvent(event)
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._selectable
            and self._shape_drag_active
            and (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            pos = event.position().toPoint()
            self._shape_end_c = pos
            wx, wy = self._c2w(event.position().x(), event.position().y())
            self._cursor_wx = wx
            self._cursor_wy = wy
            self._redraw()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._selectable
            and self._shape_drag_active
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._finish_shape_drag(event.position().toPoint())
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._selectable and event.key() == Qt.Key.Key_Escape:
            self._dismiss_size_hud()
            self._radial_active = False
            self.set_quick_shape_enabled(False)
            self._shape_drag_active = False
            super().keyPressEvent(event)
            return
        if (
            self._selectable
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and (self._size_w_edit or self._size_h_edit)
        ):
            self._apply_size_hud()
            return

        if self._selectable and self._mode == "select":
            key = event.key()
            shift_mod = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if key == Qt.Key.Key_Q:
                self._toggle_radial_menu()
                return
            if shift_mod and key == Qt.Key.Key_R:
                self.set_quick_shape_mode("rectangle")
                return
            if shift_mod and key == Qt.Key.Key_C:
                self.set_quick_shape_mode("circle")
                return
            if shift_mod and key == Qt.Key.Key_S:
                self.set_quick_shape_mode("slot")
                return
            if shift_mod and key == Qt.Key.Key_P:
                self.set_quick_shape_mode("hexagon")
                return

        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)

        if (
            self._selectable
            and self._shape_drag_active
            and self._shape_start_w is not None
            and self._shape_end_c is not None
        ):
            sx, sy = self._shape_start_w
            ex, ey = self._c2w(
                float(self._shape_end_c.x()), float(self._shape_end_c.y())
            )
            preview = self._build_drag_shape(self._shape_drag_mode, sx, sy, ex, ey)
            if len(preview) >= 2:
                painter_preview = QPainter(self.viewport())
                painter_preview.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(QColor("#f85149"), 1.5, Qt.PenStyle.DashLine)
                painter_preview.setPen(pen)
                for i in range(1, len(preview)):
                    x0, y0 = self._w2c(*preview[i - 1])
                    x1, y1 = self._w2c(*preview[i])
                    painter_preview.drawLine(int(x0), int(y0), int(x1), int(y1))
                painter_preview.end()

            if self._mode == "select" and self._radial_active:
                painter = QPainter(self.viewport())
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._paint_radial_menu(painter)
                painter.end()

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if not self._selectable or self._mode in ("draw", "edit"):
            super()._rightclick_cb(cx, cy)
            return

        menu = QMenu(self)
        # "Create shape" only leads the menu when there is nothing to act on —
        # with a selection or a shape under the cursor, the actions the user
        # actually came for (delete/duplicate/close/group) come first.
        poly_hit_early = self._find_poly_at(cx, cy)
        if not self._sel and poly_hit_early is None:
            shape_menu = menu.addMenu("Create shape")
            shape_menu.addAction(
                "Rectangle (drag)", lambda: self.set_quick_shape_mode("rectangle")
            )
            shape_menu.addAction(
                "Circle (drag)", lambda: self.set_quick_shape_mode("circle")
            )
            shape_menu.addAction(
                "Slot (drag)", lambda: self.set_quick_shape_mode("slot")
            )
            shape_menu.addAction(
                "Hexagon (drag)", lambda: self.set_quick_shape_mode("hexagon")
            )
            menu.addSeparator()

        poly_hit = poly_hit_early
        if poly_hit is not None:
            idx = poly_hit
            if idx in self._sel:
                menu.addAction("Deselect", lambda: self._ctx_deselect(idx))
            else:
                menu.addAction("Select", lambda: self._ctx_select(idx))
            menu.addAction("Delete", lambda: self._ctx_delete_poly(idx))
            if callable(self._on_cutout_toggle):
                is_cutout = idx in self._cutout_indices
                cutout_label = "Remove Cutout" if is_cutout else "Mark as Cutout"
                cutout_toggle = self._on_cutout_toggle
                if callable(cutout_toggle):
                    menu.addAction(cutout_label, lambda _idx=idx: cutout_toggle(_idx))
                # When multiple shapes are selected, offer bulk cutout toggle.
                if len(self._sel) > 1 and idx in self._sel:
                    all_cutout = all(i in self._cutout_indices for i in self._sel)
                    bulk_label = (
                        "Remove Cutout for all selected"
                        if all_cutout
                        else "Mark all selected as Cutout"
                    )
                    sel_snapshot = set(self._sel)
                    menu.addAction(
                        bulk_label,
                        lambda _cb=cutout_toggle, _sel=sel_snapshot: [
                            _cb(i) for i in _sel
                        ],
                    )
            menu.addSeparator()

        context_idx = poly_hit

        def _ensure_context_selection() -> bool:
            if self._sel:
                return True
            if context_idx is None:
                return False
            self._sel = {context_idx}
            self._redraw()
            self._notify()
            return True

        def _run_transform(action) -> None:
            if _ensure_context_selection():
                action()
            else:
                self._show_flash("Select shape(s) first", 1000)

        def _run_prompted_transform(
            title: str,
            label: str,
            default: float,
            minimum: float,
            callback,
        ) -> None:
            value, ok = QInputDialog.getDouble(
                self,
                title,
                label,
                default,
                minimum,
                1_000_000.0,
                3,
            )
            if ok:
                callback(value)

        if self._sel:
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            menu.addAction("Duplicate  [⌘D]", self.duplicate_selected)
            open_count = sum(
                1
                for i in self._sel
                if i < len(self._entities) and not self._is_poly_closed(self._entities[i].points)
            )
            if open_count:
                label = "Close path"
                if len(self._sel) > 1:
                    label = f"Close path (join {len(self._sel)} into one)"
                menu.addAction(label, self.close_selection_as_path)
            menu.addAction("Fit selection", self.fit_selection)
            if len(self._sel) >= 2:
                menu.addAction("Group", self._group_selected)
            if any(i in self._groups for i in self._sel):
                menu.addAction("Ungroup", self._ungroup_selected)
        else:
            menu.addAction("Select all", self.select_all)

        menu.addAction(
            "Use as outline", lambda: _run_transform(self._send_selected_to_pattern)
        )
        if callable(getattr(self, "_use_selected_as_fill_pattern_cb", None)):
            menu.addAction(
                "Use as pattern fill",
                lambda: _run_transform(self._use_selected_as_fill_pattern),
            )
        if callable(getattr(self, "_send_selected_to_draft_cb", None)):
            menu.addAction(
                "Send to Draft",
                lambda: _run_transform(self._send_selected_to_draft),
            )

        arrange_menu = menu.addMenu("Arrange")
        for label, mode in (
            ("Align left", "left"),
            ("Align center X", "center-x"),
            ("Align right", "right"),
            ("Align top", "top"),
            ("Align center Y", "center-y"),
            ("Align bottom", "bottom"),
        ):
            arrange_menu.addAction(
                label, lambda _m=mode: _run_transform(lambda: self.align_selected(_m))
            )
        arrange_menu.addSeparator()
        for label, title, prompt, default, axis, dist_mode in (
            ("Distribute horizontal — gap…", "Distribute Horizontal", "Spacing (mm):", 1.0, "horizontal", "gap"),
            ("Distribute vertical — gap…", "Distribute Vertical", "Spacing (mm):", 1.0, "vertical", "gap"),
            ("Distribute horizontal — center-to-center…", "Distribute Horizontal (Center-to-Center)", "Center spacing (mm):", 10.0, "horizontal", "center"),
            ("Distribute vertical — center-to-center…", "Distribute Vertical (Center-to-Center)", "Center spacing (mm):", 10.0, "vertical", "center"),
        ):
            arrange_menu.addAction(
                label,
                lambda _t=title, _p=prompt, _d=default, _a=axis, _m=dist_mode: _run_transform(
                    lambda: _run_prompted_transform(
                        _t, _p, _d, 0.0,
                        lambda value: self._distribute_selected(_a, value, mode=_m),
                    )
                ),
            )

        transform_menu = menu.addMenu("Transform")
        transform_menu.addAction(
            "Rotate +90°", lambda: _run_transform(lambda: self.rotate_selected(90.0))
        )
        transform_menu.addAction(
            "Rotate -90°", lambda: _run_transform(lambda: self.rotate_selected(-90.0))
        )
        transform_menu.addAction(
            "Mirror horizontal",
            lambda: _run_transform(lambda: self.mirror_selected("horizontal")),
        )
        transform_menu.addAction(
            "Mirror vertical",
            lambda: _run_transform(lambda: self.mirror_selected("vertical")),
        )
        transform_menu.addSeparator()
        transform_menu.addAction(
            "Edit width + height…  [⌘⇧D]", lambda: _run_transform(self._show_size_hud)
        )
        transform_menu.addAction(
            "Set line length…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Set Line Length",
                    "Line length (mm):",
                    10.0,
                    0.001,
                    self._set_selected_line_length,
                )
            ),
        )
        transform_menu.addAction(
            "Set line angle…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Set Line Angle",
                    "Angle (° CCW from +X):",
                    0.0,
                    -360.0,
                    self._set_selected_line_angle,
                )
            ),
        )
        transform_menu.addSeparator()
        transform_menu.addAction(
            "Explode to segments",
            lambda: _run_transform(self.explode_selected_to_segments),
        )
        transform_menu.addAction(
            "Merge segments to object",
            lambda: _run_transform(self.merge_selected_segments_to_objects),
        )

        menu.addSeparator()
        menu.addAction("Fit view [F]", self.fit)
        grid_action = menu.addAction("Show grid")
        grid_action.setCheckable(True)
        grid_action.setChecked(self._grid_visible)
        grid_action.triggered.connect(self.set_grid_visible)
        snap_action = menu.addAction("Snap to grid")
        snap_action.setCheckable(True)
        snap_action.setChecked(self._grid_snap)
        snap_action.triggered.connect(self.set_grid_snap)
        mode_menu = menu.addMenu("Mode")
        mode_menu.addAction("Select [Esc]", lambda: self.set_mode("select"))
        mode_menu.addAction("Draw [D]", lambda: self.set_mode("draw"))
        mode_menu.addAction("Edit [E]", lambda: self.set_mode("edit"))
        menu.popup(self.mapToGlobal(QPoint(int(cx), int(cy))))

    def _show_size_hud(self) -> None:
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            self._show_flash("Select shape(s) first", 1200)
            return

        self._dismiss_size_hud()
        cur_w = max(bounds[2] - bounds[0], 0.0)
        cur_h = max(bounds[3] - bounds[1], 0.0)
        cx_w = (bounds[0] + bounds[2]) / 2.0
        cy_w = (bounds[1] + bounds[3]) / 2.0
        cx, cy = self._w2c(cx_w, cy_w)
        style = (
            "background: #1a1f2e; color: #ffffff; border: 1px solid #4a9eff;"
            "border-radius: 3px; font-size: 11px; font-family: 'Menlo';"
            "padding: 2px 6px;"
        )

        self._size_w_edit = QLineEdit(self.viewport())
        self._size_w_edit.setFixedWidth(90)
        self._size_w_edit.setFixedHeight(24)
        self._size_w_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._size_w_edit.setText(f"{cur_w:.3f}")
        self._size_w_edit.setPlaceholderText("W")
        self._size_w_edit.setStyleSheet(style)
        self._size_w_edit.move(int(cx - 98), int(cy - 30))
        self._size_w_edit.returnPressed.connect(self._apply_size_hud)
        self._size_w_edit.editingFinished.connect(self._apply_size_hud)
        self._size_w_edit.show()

        self._size_h_edit = QLineEdit(self.viewport())
        self._size_h_edit.setFixedWidth(90)
        self._size_h_edit.setFixedHeight(24)
        self._size_h_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._size_h_edit.setText(f"{cur_h:.3f}")
        self._size_h_edit.setPlaceholderText("H")
        self._size_h_edit.setStyleSheet(style)
        self._size_h_edit.move(int(cx + 8), int(cy - 30))
        self._size_h_edit.returnPressed.connect(self._apply_size_hud)
        self._size_h_edit.editingFinished.connect(self._apply_size_hud)
        self._size_h_edit.show()
        self._size_w_edit.setFocus()
        self._size_w_edit.selectAll()

    def _dismiss_size_hud(self) -> None:
        if self._size_w_edit is not None:
            self._size_w_edit.hide()
            self._size_w_edit.deleteLater()
            self._size_w_edit = None
        if self._size_h_edit is not None:
            self._size_h_edit.hide()
            self._size_h_edit.deleteLater()
            self._size_h_edit = None

    def _apply_size_hud(self) -> None:
        if self._size_w_edit is None or self._size_h_edit is None:
            return
        try:
            new_w = float(self._size_w_edit.text().strip())
            new_h = float(self._size_h_edit.text().strip())
        except ValueError:
            self._show_flash("Invalid size", 900)
            return
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            self._dismiss_size_hud()
            return

        cur_w = max(bounds[2] - bounds[0], 0.0)
        cur_h = max(bounds[3] - bounds[1], 0.0)
        changed_w = abs(new_w - cur_w) > 1e-9 and new_w > 0
        changed_h = abs(new_h - cur_h) > 1e-9 and new_h > 0
        if changed_w:
            self._set_selected_width(new_w)
        if changed_h:
            self._set_selected_height(new_h)
        if changed_w or changed_h:
            self._show_flash("Dimensions updated", 900)
            # Keep HUD open with committed values for iterative edits.
            self._size_w_edit.setText(f"{new_w:.3f}")
            self._size_h_edit.setText(f"{new_h:.3f}")

    def _toggle_radial_menu(self) -> None:
        if self._radial_active:
            self._radial_active = False
            self._redraw()
            return
        if self._cursor_wx is not None and self._cursor_wy is not None:
            cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
            self._radial_center_c = QPoint(int(cx), int(cy))
        else:
            vp = self.viewport()
            self._radial_center_c = QPoint(vp.width() // 2, vp.height() // 2)
        self._radial_active = True
        self._redraw()

    def _radial_index_at(self, x: float, y: float) -> int | None:
        dx = x - self._radial_center_c.x()
        dy = y - self._radial_center_c.y()
        r = math.hypot(dx, dy)
        if r < 28 or r > 110:
            return None
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        return int((angle + 30.0) // 60.0) % 6

    def _execute_radial_action(self, idx: int) -> None:
        if idx == 0:
            self.set_mode("draw")
        elif idx == 1:
            self.set_mode("edit")
        elif idx == 2:
            self.toggle_measure()
        elif idx == 3:
            self.fit_selection() if self._sel else self.fit()
        elif idx == 4:
            self.set_quick_shape_enabled(not self.quick_shape_enabled)
        elif idx == 5:
            self._show_size_hud()

    def _paint_radial_menu(self, painter: QPainter) -> None:
        cx = float(self._radial_center_c.x())
        cy = float(self._radial_center_c.y())
        labels = ["Draw", "Edit", "Measure", "Fit", "Quick", "Size"]
        outer = 92.0
        inner = 30.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(20, 26, 38, 220))
        painter.setPen(QPen(QColor("#2f81f7"), 1.2))
        painter.drawEllipse(
            int(cx - outer), int(cy - outer), int(outer * 2), int(outer * 2)
        )
        painter.setBrush(QColor(12, 16, 24, 230))
        painter.setPen(QPen(QColor("#30363d"), 1.0))
        painter.drawEllipse(
            int(cx - inner), int(cy - inner), int(inner * 2), int(inner * 2)
        )
        for i, label in enumerate(labels):
            ang = math.radians(i * 60.0)
            tx = cx + math.cos(ang) * 62.0
            ty = cy - math.sin(ang) * 62.0
            painter.setPen(QColor("#e6edf3"))
            painter.drawText(int(tx - 18), int(ty + 4), label)
        painter.setPen(QColor("#8b949e"))
        painter.drawText(int(cx - 8), int(cy + 4), "Q")

    def _shape_mode_from_modifiers(self, mods) -> str:
        if mods & Qt.KeyboardModifier.AltModifier:
            return "circle"
        if mods & Qt.KeyboardModifier.ControlModifier:
            return "slot"
        return self._quick_shape_mode

    def _start_shape_drag(self, mode: str, pos_f) -> None:
        pos = pos_f.toPoint()
        wx, wy = self._c2w(pos_f.x(), pos_f.y())
        self._shape_drag_active = True
        self._shape_drag_mode = mode
        self._shape_start_w = (wx, wy)
        self._shape_start_c = pos
        self._shape_end_c = pos

    @staticmethod
    def _translate(
        coords: list[tuple[float, float]],
        cx: float,
        cy: float,
    ) -> list[tuple[float, float]]:
        return [(x + cx, y + cy) for x, y in coords]

    def _build_drag_shape(
        self,
        mode: str,
        sx: float,
        sy: float,
        ex: float,
        ey: float,
    ) -> list[tuple[float, float]]:
        w = abs(ex - sx)
        h = abs(ey - sy)
        if w < 1e-6 or h < 1e-6:
            return []
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        if mode == "rectangle":
            return self._translate(shape_rect(w, h), cx, cy)
        if mode == "circle":
            r = min(w, h) / 2.0
            return self._translate(shape_circle(r, 64), cx, cy)
        if mode == "slot":
            length = max(w, h)
            width = min(w, h)
            return self._translate(shape_slot(length, width), cx, cy)
        if mode == "hexagon":
            r = min(w, h) / 2.0
            return self._translate(shape_polygon(6, r), cx, cy)
        return []

    def _finish_shape_drag(self, end_c: QPoint) -> None:
        if (
            not self._shape_drag_active
            or self._shape_start_w is None
            or self._shape_start_c is None
        ):
            self._clear_shape_drag()
            return
        start_c = self._shape_start_c
        drag_px = abs(end_c.x() - start_c.x()) + abs(end_c.y() - start_c.y())
        if drag_px < 8:
            self._clear_shape_drag()
            if self._mode == "select" and self._sel:
                self.deselect_all()
            self._redraw()
            return
        sx, sy = self._shape_start_w
        ex, ey = self._c2w(float(end_c.x()), float(end_c.y()))
        poly = self._build_drag_shape(self._shape_drag_mode, sx, sy, ex, ey)
        if poly:
            was_empty = len(self._entities) == 0
            kind = "polyline"
            meta = None
            cx = (sx + ex) / 2.0
            cy = (sy + ey) / 2.0
            w = abs(ex - sx)
            h = abs(ey - sy)
            if self._shape_drag_mode == "circle":
                kind = "circle"
                meta = {"center": (cx, cy), "radius": min(w, h) / 2.0}
            elif self._shape_drag_mode == "ellipse":
                kind = "ellipse"
                meta = {
                    "center": (cx, cy),
                    "rx": w / 2.0,
                    "ry": h / 2.0,
                    "rotation": 0.0,
                }
            self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._sel = {len(self._entities) - 1}
            if was_empty:
                self._fit()
            else:
                self._redraw()
            self._notify()
            self._fire_poly_change()
            self._show_flash(f"{self._shape_drag_mode.title()} created", 800)
        self._clear_shape_drag()

    def _clear_shape_drag(self) -> None:
        self._shape_drag_active = False
        self._shape_start_w = None
        self._shape_start_c = None
        self._shape_end_c = None
