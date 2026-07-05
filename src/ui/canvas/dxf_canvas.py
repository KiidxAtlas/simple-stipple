"""DxfCanvas — extended polyline view with quick shape tools and radial menu."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QLineEdit, QMenu

from src.backend.geometry.shapes import (
    shape_circle,
    shape_polygon,
    shape_rect,
    shape_slot,
)
from src.settings import DEFAULT_RADIAL_MENU_TOOLS, RADIAL_MENU_SHORT_LABELS
from src.ui.canvas import commands as canvas_commands
from src.ui.canvas import tools as canvas_tools
from src.ui.canvas.view import PolylineView


class DxfSelectTool(canvas_tools.SelectTool):
    """Select tool with DxfCanvas extras: radial menu, quick-shape drag,
    and click-to-activate for shapes on non-active layers."""

    # DxfSelectTool is only ever constructed with a DxfCanvas (see
    # DxfCanvas.__init__ below), which adds radial-menu/quick-shape state on
    # top of the base PolylineView — narrow the inherited `v` accordingly so
    # those DxfCanvas-only attributes type-check.
    v: "DxfCanvas"

    def press(self, event: QMouseEvent) -> bool:
        c = self.v
        pos = event.position()
        # Radial-menu press/move/paint are handled at the DxfCanvas level
        # (mousePressEvent/mouseMoveEvent/paintEvent below) so the menu opens
        # and works the same regardless of which mode/tool is active — it
        # used to be select-mode-only because it lived here.
        if c._selectable and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            hit = c._find_poly_at(pos.x(), pos.y())
            if hit is None:
                # Clicking a shape on a non-active layer activates that layer
                # and selects the shape (entity index passed to the callback).
                inactive_hit = c._find_inactive_poly_at(pos.x(), pos.y())
                if inactive_hit is not None and callable(c._on_ghost_click):
                    c._on_ghost_click(inactive_hit)
                    return True
                if c._quick_shape_enabled:
                    mode = c._shape_mode_from_modifiers(event.modifiers())
                    c._start_shape_drag(mode, pos)
                    return True
        return super().press(event)

    def move(self, event: QMouseEvent) -> bool:
        c = self.v
        if (
            c._selectable
            and c._shape_drag_active
            and (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            pos = event.position().toPoint()
            c._shape_end_c = pos
            wx, wy = c._c2w(event.position().x(), event.position().y())
            c._cursor_wx = wx
            c._cursor_wy = wy
            c._redraw()
            return True
        return super().move(event)

    def release(self, event: QMouseEvent) -> bool:
        c = self.v
        if (
            c._selectable
            and c._shape_drag_active
            and event.button() == Qt.MouseButton.LeftButton
        ):
            c._finish_shape_drag(event.position().toPoint())
            return True
        return super().release(event)

    def key(self, event: QKeyEvent) -> bool:
        c = self.v
        if not c._selectable:
            return False
        key = event.key()
        shift_mod = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        # "Q" (radial menu) is a canvas Command (see commands.py) so it shows
        # up in the Keybindings dialog and is rebindable — not handled here.
        if shift_mod and key == Qt.Key.Key_R:
            c.set_quick_shape_mode("rectangle")
            return True
        if shift_mod and key == Qt.Key.Key_C:
            c.set_quick_shape_mode("circle")
            return True
        if shift_mod and key == Qt.Key.Key_S:
            c.set_quick_shape_mode("slot")
            return True
        if shift_mod and key == Qt.Key.Key_P:
            c.set_quick_shape_mode("hexagon")
            return True
        return False

    def paint_overlay(self, painter: QPainter) -> None:
        c = self.v
        if (
            c._selectable
            and c._shape_drag_active
            and c._shape_start_w is not None
            and c._shape_end_c is not None
        ):
            sx, sy = c._shape_start_w
            ex, ey = c._c2w(float(c._shape_end_c.x()), float(c._shape_end_c.y()))
            preview = c._build_drag_shape(c._shape_drag_mode, sx, sy, ex, ey)
            if len(preview) >= 2:
                pen = QPen(QColor("#f85149"), 1.5, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                for i in range(1, len(preview)):
                    x0, y0 = c._w2c(*preview[i - 1])
                    x1, y1 = c._w2c(*preview[i])
                    painter.drawLine(int(x0), int(y0), int(x1), int(y1))


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
        self._radial_hover_index: int | None = None
        self._radial_tools: list[str] = list(DEFAULT_RADIAL_MENU_TOOLS)
        self._size_w_edit: QLineEdit | None = None
        self._size_h_edit: QLineEdit | None = None

        # Quick shapes / radial menu / layer-activation live in the tool.
        self._tools["select"] = DxfSelectTool(self)

        if self._draft_profile:
            self.set_rulers_visible(True)
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
        # Handled here (not in a per-mode tool) so the radial menu opens and
        # works identically no matter which mode/tool was active when "Q"
        # was pressed — a left click executes the hovered wedge, anything
        # else just dismisses the menu without reaching the active tool.
        if self._selectable and self._radial_active:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position()
                idx = self._radial_index_at(pos.x(), pos.y())
                self._radial_active = False
                self._radial_hover_index = None
                self._redraw()
                if idx is not None:
                    self._execute_radial_action(idx)
            else:
                self._radial_active = False
                self._radial_hover_index = None
                self._redraw()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._selectable and self._radial_active:
            pos = event.position()
            hover = self._radial_index_at(pos.x(), pos.y())
            if hover != self._radial_hover_index:
                self._radial_hover_index = hover
                self._redraw()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._selectable and self._radial_active:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_radial_menu(painter)
            painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._selectable and event.key() == Qt.Key.Key_Escape:
            self._dismiss_size_hud()
            self._radial_active = False
            self._radial_hover_index = None
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
        super().keyPressEvent(event)

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if self._radial_active:
            self._radial_active = False
            self._redraw()
            return
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
            if self.text_params_at(idx) is not None:
                menu.addAction("Edit text…", lambda _i=idx: self.prompt_edit_text(_i))
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
            *,
            is_length: bool = True,
        ) -> None:
            self._show_hud_prompt(
                label, default, callback, minimum=minimum, is_length=is_length
            )

        if self._sel:
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            menu.addAction(
                canvas_commands.menu_text("edit.duplicate"), self.duplicate_selected
            )
            menu.addAction(
                canvas_commands.menu_text("edit.array_grid"),
                self.array_duplicate_grid,
            )
            menu.addAction(
                canvas_commands.menu_text("edit.array_radial"),
                self.array_duplicate_radial,
            )
            if len(self._sel) >= 2:
                menu.addAction(
                    canvas_commands.menu_text("text.attach_to_path"),
                    self.attach_selected_text_to_path,
                )
            open_count = sum(
                1
                for i in self._sel
                if i < len(self._entities)
                and not self._is_poly_closed(self._entities[i].points)
            )
            if open_count:
                label = "Close path"
                if len(self._sel) > 1:
                    label = f"Close path (join {len(self._sel)} into one)"
                menu.addAction(label, self.close_selection_as_path)
            menu.addAction("Fit selection", self.fit_selection)
            menu.addAction("Smooth", lambda: _run_transform(self.smooth_selected))
            menu.addAction(
                "Simplify…",
                lambda: _run_transform(
                    lambda: _run_prompted_transform(
                        "Simplify",
                        "Tolerance (mm):",
                        0.2,
                        0.001,
                        self.simplify_selected,
                    )
                ),
            )
            menu.addAction(
                "Fit to Curve…",
                lambda: _run_transform(
                    lambda: _run_prompted_transform(
                        "Fit to Curve",
                        "Tolerance (mm):",
                        0.3,
                        0.001,
                        self.fit_selected_to_curve,
                    )
                ),
            )
            if len(self._sel) >= 2:
                menu.addAction(
                    canvas_commands.menu_text("group.create"), self._group_selected
                )
            if any(self._group_of(i) is not None for i in self._sel):
                menu.addAction(
                    canvas_commands.menu_text("group.dissolve"), self._ungroup_selected
                )
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

        if len(self._sel) >= 2:
            bool_menu = menu.addMenu("Boolean")
            for cmd_id in (
                "boolean.union",
                "boolean.subtract",
                "boolean.intersect",
                "boolean.divide",
            ):
                bool_menu.addAction(
                    canvas_commands.menu_text(cmd_id),
                    lambda _c=cmd_id: canvas_commands.run(self, _c),
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
            (
                "Distribute horizontal — gap…",
                "Distribute Horizontal",
                "Spacing (mm):",
                1.0,
                "horizontal",
                "gap",
            ),
            (
                "Distribute vertical — gap…",
                "Distribute Vertical",
                "Spacing (mm):",
                1.0,
                "vertical",
                "gap",
            ),
            (
                "Distribute horizontal — center-to-center…",
                "Distribute Horizontal (Center-to-Center)",
                "Center spacing (mm):",
                10.0,
                "horizontal",
                "center",
            ),
            (
                "Distribute vertical — center-to-center…",
                "Distribute Vertical (Center-to-Center)",
                "Center spacing (mm):",
                10.0,
                "vertical",
                "center",
            ),
        ):
            arrange_menu.addAction(
                label,
                lambda _t=title, _p=prompt, _d=default, _a=axis, _m=dist_mode: (
                    _run_transform(
                        lambda: _run_prompted_transform(
                            _t,
                            _p,
                            _d,
                            0.0,
                            lambda value: self._distribute_selected(_a, value, mode=_m),
                        )
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
            "Edit width + height…", lambda: _run_transform(self._show_size_hud)
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
                    is_length=False,
                )
            ),
        )
        transform_menu.addSeparator()
        transform_menu.addAction(
            canvas_commands.menu_text("mode.trim", "Trim segments…"),
            lambda: canvas_commands.run(self, "mode.trim"),
        )
        transform_menu.addAction(
            canvas_commands.menu_text("mode.extend", "Extend to meet…"),
            lambda: canvas_commands.run(self, "mode.extend"),
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

        wx_txt, wy_txt = self._c2w(cx, cy)
        menu.addAction(
            canvas_commands.menu_text("text.add", "Add text…"),
            lambda: self.prompt_add_text(wx_txt, wy_txt),
        )

        menu.addSeparator()
        menu.addAction(canvas_commands.menu_text("view.fit", "Fit view"), self.fit)
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
        mode_menu.addAction(
            canvas_commands.menu_text("mode.draw", "Draw"),
            lambda: self.set_mode("draw"),
        )
        mode_menu.addAction(
            canvas_commands.menu_text("mode.edit", "Edit"),
            lambda: self.set_mode("edit"),
        )
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

        self._size_w_edit = QLineEdit(self)
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

        self._size_h_edit = QLineEdit(self)
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
            self._radial_hover_index = None
            self._redraw()
            return
        if self._cursor_wx is not None and self._cursor_wy is not None:
            cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
            self._radial_center_c = QPoint(int(cx), int(cy))
        else:
            self._radial_center_c = QPoint(self.width() // 2, self.height() // 2)
        self._radial_active = True
        self._radial_hover_index = None
        self._redraw()

    # A quick-launcher wheel: every wedge is a real canvas Command id (draw
    # primitives, edit/selection ops, booleans, view/grid toggles, ...) so
    # the available pool is exactly "everything commands.py knows how to
    # run" — no separate/parallel action list to keep in sync. Which ones
    # appear, and in what order, is user-customizable — see
    # set_radial_menu_tools() — so the wedge count/angle is computed from
    # len(self._radial_tools), not a fixed number.
    _RADIAL_OUTER = 104.0
    _RADIAL_INNER = 36.0
    _RADIAL_MIN_TOOLS = 3
    _RADIAL_MAX_TOOLS = 12

    @classmethod
    def _radial_geometry(cls, n: int) -> tuple[float, float]:
        """(outer, inner) radii — grows past 6 wedges so more items still
        leave each label enough room; shared by hit-testing and painting so
        the two can never disagree about where a wedge actually is."""
        grow = max(0, n - 6)
        return cls._RADIAL_OUTER + grow * 9.0, cls._RADIAL_INNER + grow * 2.0

    def set_radial_menu_tools(self, tools: list[str] | None) -> None:
        """Set which commands appear as radial-menu wedges, and in what order.

        Unknown/hidden ids are dropped and duplicates collapsed (first
        occurrence wins); if fewer than _RADIAL_MIN_TOOLS survive, falls back
        to the default set entirely rather than showing a degenerate menu.
        """
        valid = {c.id for c in canvas_commands.COMMANDS if not c.hidden}
        seen: set[str] = set()
        cleaned: list[str] = []
        for tool_id in tools or []:
            if tool_id in valid and tool_id not in seen:
                seen.add(tool_id)
                cleaned.append(tool_id)
        if len(cleaned) < self._RADIAL_MIN_TOOLS:
            cleaned = list(DEFAULT_RADIAL_MENU_TOOLS)
        self._radial_tools = cleaned[: self._RADIAL_MAX_TOOLS]
        if self._radial_active:
            self._radial_hover_index = None
            self._redraw()

    def _radial_index_at(self, x: float, y: float) -> int | None:
        n = len(self._radial_tools)
        if n == 0:
            return None
        outer, inner = self._radial_geometry(n)
        dx = x - self._radial_center_c.x()
        dy = y - self._radial_center_c.y()
        r = math.hypot(dx, dy)
        if r < inner - 4.0 or r > outer + 18.0:
            return None
        slice_deg = 360.0 / n
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        return int((angle + slice_deg / 2.0) // slice_deg) % n

    def _execute_radial_action(self, idx: int) -> None:
        if not (0 <= idx < len(self._radial_tools)):
            return
        canvas_commands.run(self, self._radial_tools[idx])

    def _draw_radial_icon(
        self,
        painter: QPainter,
        cmd_id: str,
        cx: float,
        cy: float,
        size: float,
        color: QColor,
        label: str = "",
    ) -> None:
        painter.save()
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = size / 2.0

        def _two_circles(mode: str) -> None:
            ra = half * 0.62
            ax, bx = cx - half * 0.32, cx + half * 0.32
            path_a, path_b = QPainterPath(), QPainterPath()
            path_a.addEllipse(QPointF(ax, cy), ra, ra)
            path_b.addEllipse(QPointF(bx, cy), ra, ra)
            if mode == "union":
                painter.fillPath(path_a.united(path_b), color)
            elif mode == "subtract":
                painter.fillPath(path_a.subtracted(path_b), color)
                painter.drawPath(path_b)
            elif mode == "intersect":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.fillPath(path_a.intersected(path_b), color)
            elif mode == "divide":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.drawLine(QPointF(cx, cy - ra), QPointF(cx, cy + ra))

        if cmd_id in ("canvas.rectangle",):
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.7, size, size * 0.7), 2.0, 2.0
            )
        elif cmd_id == "canvas.circle":
            painter.drawEllipse(QPointF(cx, cy), half, half)
        elif cmd_id == "canvas.polygon":
            pts = [
                QPointF(
                    cx + math.cos(math.radians(60 * k - 90)) * half,
                    cy + math.sin(math.radians(60 * k - 90)) * half,
                )
                for k in range(6)
            ]
            painter.drawPolygon(QPolygonF(pts))
        elif cmd_id == "canvas.line":
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.6), QPointF(cx + half, cy - half * 0.6)
            )
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.6), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.6), 1.4, 1.4)
        elif cmd_id == "canvas.arc":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, 90 * 16)
        elif cmd_id == "canvas.ellipse":
            painter.drawEllipse(QRectF(cx - half, cy - half * 0.6, size, size * 0.6))
        elif cmd_id == "canvas.polyline":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.lineTo(cx - half * 0.15, cy - half * 0.6)
            path.lineTo(cx + half, cy + half * 0.2)
            painter.drawPath(path)
            painter.setBrush(color)
            for px, py in (
                (cx - half, cy + half * 0.5),
                (cx - half * 0.15, cy - half * 0.6),
                (cx + half, cy + half * 0.2),
            ):
                painter.drawEllipse(QPointF(px, py), 1.3, 1.3)
        elif cmd_id == "canvas.spline":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.3, cy - half, cx + half * 0.3, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "mode.pen":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.cubicTo(
                cx - half * 0.2, cy - half, cx + half * 0.2, cy + half, cx + half, cy - half * 0.5
            )
            painter.drawPath(path)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.5), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.5), 1.4, 1.4)
        elif cmd_id == "mode.draw":
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half * 0.4, cy - half))
            tip = QPolygonF(
                [
                    QPointF(cx + half * 0.4, cy - half),
                    QPointF(cx + half, cy - half * 0.7),
                    QPointF(cx + half * 0.7, cy - half * 0.1),
                ]
            )
            painter.setBrush(color)
            painter.drawPolygon(tip)
        elif cmd_id == "mode.edit":
            painter.drawRect(QRectF(cx - half, cy - half, size, size))
            painter.setBrush(color)
            for corner in (
                (cx - half, cy - half),
                (cx + half, cy - half),
                (cx - half, cy + half),
                (cx + half, cy + half),
            ):
                painter.drawRect(QRectF(corner[0] - 1.6, corner[1] - 1.6, 3.2, 3.2))
        elif cmd_id == "edit.undo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 30 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx - half * 0.75, cy - half * 0.55, 200, color)
        elif cmd_id == "edit.redo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 250 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx + half * 0.75, cy - half * 0.55, -20, color)
        elif cmd_id == "clipboard.cut":
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half * 0.2, cy + half * 0.3))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half * 0.2, cy - half * 0.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx - half, cy - half), 2.2, 2.2)
            painter.drawEllipse(QPointF(cx - half, cy + half), 2.2, 2.2)
            painter.drawLine(QPointF(cx + half * 0.2, cy), QPointF(cx + half, cy))
        elif cmd_id == "clipboard.copy":
            painter.drawRoundedRect(QRectF(cx - half, cy - half * 0.75, size * 0.75, size * 0.75), 2.0, 2.0)
            painter.drawRoundedRect(
                QRectF(cx - half * 0.25, cy - half * 0.15, size * 0.75, size * 0.75), 2.0, 2.0
            )
        elif cmd_id == "clipboard.paste":
            painter.drawRoundedRect(QRectF(cx - half * 0.7, cy - half * 0.8, size * 0.7, size), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(cx - half * 0.3, cy - half, size * 0.3, size * 0.25), 1.0, 1.0)
        elif cmd_id in ("edit.duplicate", "edit.duplicate_offset"):
            painter.drawRoundedRect(QRectF(cx - half, cy - half * 0.2, size * 0.65, size * 0.65), 2.0, 2.0)
            painter.drawRoundedRect(
                QRectF(cx - half * 0.35, cy - half, size * 0.65, size * 0.65), 2.0, 2.0
            )
            if cmd_id == "edit.duplicate_offset":
                self._draw_arrowhead(painter, cx + half * 0.55, cy - half * 0.55, -45, color)
        elif cmd_id == "edit.array_grid":
            for dx_ in (-half * 0.55, half * 0.55):
                for dy_ in (-half * 0.55, half * 0.55):
                    painter.drawRect(QRectF(cx + dx_ - 3.0, cy + dy_ - 3.0, 6.0, 6.0))
        elif cmd_id == "edit.array_radial":
            painter.setBrush(color)
            for k in range(5):
                a = math.radians(72 * k - 90)
                painter.drawEllipse(
                    QPointF(cx + math.cos(a) * half * 0.75, cy + math.sin(a) * half * 0.75), 2.0, 2.0
                )
        elif cmd_id == "edit.delete":
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half, cy - half))
        elif cmd_id == "select.all":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
        elif cmd_id == "select.none":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
            painter.setPen(QPen(color, 1.4))
            painter.drawLine(QPointF(cx - half, cy + half * 0.7), QPointF(cx + half, cy - half * 0.7))
        elif cmd_id == "select.invert":
            painter.drawRect(QRectF(cx - half, cy - half * 0.5, size * 0.45, size * 0.9))
            painter.setBrush(color)
            painter.drawRect(QRectF(cx + half * 0.1, cy - half * 0.5, size * 0.45, size * 0.9))
        elif cmd_id in ("group.create", "group.dissolve"):
            gap = 3.0 if cmd_id == "group.dissolve" else 0.0
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx + gap, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
        elif cmd_id in ("path.close", "path.open"):
            span = 260 * 16 if cmd_id == "path.open" else 350 * 16
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, span)
            if cmd_id == "path.close":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx + half, cy), 1.6, 1.6)
        elif cmd_id == "path.offset":
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 2.0, 2.0)
            painter.drawRoundedRect(
                QRectF(cx - half * 0.55, cy - half * 0.55, size * 0.55, size * 0.55), 1.5, 1.5
            )
        elif cmd_id == "construction.toggle":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx - half, cy + half * 0.5), QPointF(cx + half, cy - half * 0.5))
        elif cmd_id in ("vertex.round", "vertex.chamfer"):
            path = QPainterPath()
            path.moveTo(cx - half, cy - half * 0.6)
            if cmd_id == "vertex.round":
                path.lineTo(cx - half * 0.35, cy - half * 0.6)
                path.quadTo(cx + half, cy - half * 0.6, cx + half, cy + half)
            else:
                path.lineTo(cx + half * 0.3, cy - half * 0.6)
                path.lineTo(cx + half, cy + half * 0.15)
                path.lineTo(cx + half, cy + half)
            painter.drawPath(path)
        elif cmd_id in ("text.add", "text.attach_to_path"):
            font = painter.font()
            font.setPointSizeF(size * 0.62)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, "A")
            if cmd_id == "text.attach_to_path":
                painter.drawArc(QRectF(cx - half, cy + half * 0.3, size, size), 200 * 16, 140 * 16)
        elif cmd_id == "path.simplify":
            for k in range(4):
                a = math.radians(90 * k)
                painter.drawLine(
                    QPointF(cx + math.cos(a) * half * 0.4, cy + math.sin(a) * half * 0.4),
                    QPointF(cx + math.cos(a) * half, cy + math.sin(a) * half),
                )
        elif cmd_id == "path.smooth":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.5, cy - half, cx - half * 0.15, cy + half, cx, cy)
            path.cubicTo(cx + half * 0.15, cy - half, cx + half * 0.5, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "path.fit_curve":
            # Rough/dense original points (dots on a jagged path)...
            jagged = [
                (cx - half, cy + half * 0.3),
                (cx - half * 0.45, cy - half * 0.5),
                (cx + half * 0.1, cy + half * 0.6),
                (cx + half * 0.55, cy - half * 0.4),
                (cx + half, cy + half * 0.1),
            ]
            painter.setBrush(color)
            for px, py in jagged:
                painter.drawEllipse(QPointF(px, py), 1.1, 1.1)
            # ...replaced by one smooth fitted curve through the same span.
            path = QPainterPath()
            path.moveTo(jagged[0][0], jagged[0][1])
            path.cubicTo(
                cx - half * 0.3, cy - half * 0.7, cx + half * 0.3, cy + half * 0.7,
                jagged[-1][0], jagged[-1][1],
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif cmd_id == "boolean.union":
            _two_circles("union")
        elif cmd_id == "boolean.subtract":
            _two_circles("subtract")
        elif cmd_id == "boolean.intersect":
            _two_circles("intersect")
        elif cmd_id == "boolean.divide":
            _two_circles("divide")
        elif cmd_id == "mode.trim":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx - 3.0, cy))
            painter.drawLine(QPointF(cx + 3.0, cy), QPointF(cx + half, cy))
            painter.drawLine(QPointF(cx - 3.0, cy - 3.0), QPointF(cx + 3.0, cy + 3.0))
            painter.drawLine(QPointF(cx - 3.0, cy + 3.0), QPointF(cx + 3.0, cy - 3.0))
        elif cmd_id == "mode.extend":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half * 0.4, cy))
            self._draw_arrowhead(painter, cx + half * 0.75, cy, 0, color)
        elif cmd_id == "measure.toggle":
            painter.drawRect(QRectF(cx - half, cy - half * 0.45, size, size * 0.45))
            for k in range(1, 4):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(QPointF(x, cy - half * 0.45), QPointF(x, cy - half * 0.1))
        elif cmd_id == "mode.dimension":
            painter.drawLine(QPointF(cx - half, cy - half * 0.6), QPointF(cx - half, cy + half * 0.6))
            painter.drawLine(QPointF(cx + half, cy - half * 0.6), QPointF(cx + half, cy + half * 0.6))
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            self._draw_arrowhead(painter, cx - half, cy, 0, color, size=3.0)
            self._draw_arrowhead(painter, cx + half, cy, 180, color, size=3.0)
        elif cmd_id == "view.fit":
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                painter.drawLine(
                    QPointF(cx + sx * half * 0.25, cy + sy * half * 0.25),
                    QPointF(cx + sx * half, cy + sy * half),
                )
        elif cmd_id in ("view.zoom_in", "view.zoom_out"):
            painter.drawEllipse(QPointF(cx - half * 0.15, cy - half * 0.15), half * 0.55, half * 0.55)
            painter.drawLine(
                QPointF(cx + half * 0.25, cy + half * 0.25), QPointF(cx + half, cy + half)
            )
            r = half * 0.55 * 0.5
            painter.drawLine(QPointF(cx - half * 0.15 - r, cy - half * 0.15), QPointF(cx - half * 0.15 + r, cy - half * 0.15))
            if cmd_id == "view.zoom_in":
                painter.drawLine(QPointF(cx - half * 0.15, cy - half * 0.15 - r), QPointF(cx - half * 0.15, cy - half * 0.15 + r))
        elif cmd_id == "view.rulers":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            for k in range(5):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(QPointF(x, cy), QPointF(x, cy - (half * 0.5 if k % 2 == 0 else half * 0.25)))
        elif cmd_id in ("grid.toggle", "grid.snap", "grid.coarser", "grid.finer"):
            step = size / (2.0 if cmd_id == "grid.coarser" else 4.0)
            x = cx - half
            while x <= cx + half + 0.01:
                painter.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))
                x += step
            y = cy - half
            while y <= cy + half + 0.01:
                painter.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
                y += step
            if cmd_id == "grid.snap":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        else:
            # Generic fallback: a rounded badge with the label's initials,
            # so every pool entry still gets *some* recognizable glyph.
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 3.0, 3.0)
            initials = "".join(w[0] for w in label.split()[:2]).upper() or "?"
            font = painter.font()
            font.setPointSizeF(size * 0.44)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, initials)
        painter.restore()

    @staticmethod
    def _radial_chord_half(ty: float, cy: float, outer: float) -> float:
        """Half-width of the disc's horizontal chord at label height ``ty`` —
        the widest a label can ever be at that height without spilling past
        the wheel's outer edge, regardless of angle or word length."""
        dy_from_center = ty - cy
        return math.sqrt(max(0.0, outer * outer - dy_from_center * dy_from_center))

    @staticmethod
    def _draw_arrowhead(
        painter: QPainter, x: float, y: float, angle_deg: float, color: QColor, size: float = 3.5
    ) -> None:
        a = math.radians(angle_deg)
        tip = QPointF(x, y)
        back = QPointF(x - math.cos(a) * size * 1.6, y - math.sin(a) * size * 1.6)
        perp = a + math.pi / 2.0
        p1 = QPointF(back.x() + math.cos(perp) * size * 0.6, back.y() + math.sin(perp) * size * 0.6)
        p2 = QPointF(back.x() - math.cos(perp) * size * 0.6, back.y() - math.sin(perp) * size * 0.6)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, p1, p2]))

    def _paint_radial_menu(self, painter: QPainter) -> None:
        tools = self._radial_tools
        n = len(tools)
        if n == 0:
            return
        slice_deg = 360.0 / n
        painter.save()
        cx = float(self._radial_center_c.x())
        cy = float(self._radial_center_c.y())
        outer, inner = self._radial_geometry(n)
        hover = self._radial_hover_index
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Soft drop shadow behind the disc.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawEllipse(QRectF(cx - outer + 2.0, cy - outer + 4.0, outer * 2, outer * 2))

        # Base disc.
        painter.setBrush(QColor(19, 23, 33, 235))
        painter.setPen(QPen(QColor("#2f81f7"), 1.4))
        painter.drawEllipse(QRectF(cx - outer, cy - outer, outer * 2, outer * 2))

        if hover is not None:
            # Highlight the wedge under the cursor — a filled pie slice
            # from center to the rim; the hub fill drawn right after
            # punches the middle back out, leaving a ring highlight
            # matching the actual clickable annulus (_radial_index_at).
            rect = QRectF(cx - outer, cy - outer, outer * 2, outer * 2)
            start_deg = hover * slice_deg - slice_deg / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(47, 129, 247, 110))
            painter.drawPie(rect, int(round(start_deg * 16)), int(round(slice_deg * 16)))

        # Thin spokes marking the wedge boundaries.
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        for i in range(n):
            ang = math.radians(i * slice_deg + slice_deg / 2.0)
            painter.drawLine(
                QPointF(cx + math.cos(ang) * inner, cy - math.sin(ang) * inner),
                QPointF(cx + math.cos(ang) * outer, cy - math.sin(ang) * outer),
            )

        # Center hub.
        painter.setBrush(QColor(12, 16, 24, 245))
        painter.setPen(QPen(QColor("#30363d"), 1.2))
        painter.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        painter.setPen(QColor("#8b949e"))
        painter.drawText(
            QRectF(cx - inner, cy - inner, inner * 2, inner * 2),
            Qt.AlignmentFlag.AlignCenter,
            "Q",
        )

        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_pad = 6.0
        for i, tool in enumerate(tools):
            label = RADIAL_MENU_SHORT_LABELS.get(tool) or canvas_commands.get(tool).label
            ang = math.radians(i * slice_deg)
            active = i == hover
            color = QColor("#ffffff") if active else QColor("#c9d1d9")
            icon_r = outer * 0.53
            label_r = outer * 0.77
            ix = cx + math.cos(ang) * icon_r
            iy = cy - math.sin(ang) * icon_r
            ty = cy - math.sin(ang) * label_r
            self._draw_radial_icon(painter, tool, ix, iy, 15.0, color, label=label)
            painter.setPen(color)

            # Hard cap on label width: the horizontal chord of the disc at
            # this label's height, so a long label can never spill past the
            # circle's edge regardless of angle or word length. Computed
            # *before* eliding (not as a post-hoc position clamp) so a too-
            # long label gets shorter rather than sliding back to overlap
            # its own icon.
            chord_half = self._radial_chord_half(ty, cy, outer)
            text_y = ty + fm.ascent() / 2.0
            cos_a = math.cos(ang)
            # Only truly-horizontal wedges need the icon-dodging side anchor
            # below — anywhere else, icon and label already sit at different
            # enough heights (icon_r vs label_r along the same spoke) that
            # centering doesn't collide, and gets a much bigger width budget.
            if cos_a > 0.97:
                # Due east: label reads outward from the icon, not centered
                # over it — a wide word like "Rectangle" would otherwise
                # overlap the icon since both sit on the same horizontal line.
                text_x = ix + 16.0
                max_w = max(20.0, (cx + chord_half - label_pad) - text_x)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
            elif cos_a < -0.97:
                # Due west: right-anchor so the label ends just before the icon.
                max_w = max(20.0, (ix - 16.0) - (cx - chord_half + label_pad))
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = ix - 16.0 - fm.horizontalAdvance(elided)
            else:
                max_w = max(20.0, chord_half * 2.0 - label_pad * 2.0)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = cx + cos_a * label_r - fm.horizontalAdvance(elided) / 2.0
            painter.drawText(QPointF(text_x, text_y), elided)
        painter.restore()

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
