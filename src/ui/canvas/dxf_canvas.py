"""DxfCanvas — extended polyline view with quick shape tools and radial menu."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QLineEdit, QMenu

from src.backend.cad.geometry import (
    shape_circle,
    shape_polygon,
    shape_rect,
    shape_slot,
)
from src.backend.model.document import EntityRecord
from src.core.settings import DEFAULT_RADIAL_MENU_TOOLS
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.canvas.interaction import tools as canvas_tools
from src.ui.canvas.interaction.tools import RadialMenuService
from src.ui.canvas.view import CanvasView


class DxfSelectTool(canvas_tools.SelectTool):
    """Select tool with DxfCanvas extras: radial menu, quick-shape drag,
    and click-to-activate for shapes on non-active layers."""

    # DxfSelectTool is only ever constructed with a DxfCanvas (see
    # DxfCanvas.__init__ below), which adds radial-menu/quick-shape state on
    # top of the base CanvasView — narrow the inherited `v` accordingly so
    # those DxfCanvas-only attributes type-check.
    v: DxfCanvas

    def press(self, event: QMouseEvent) -> bool:
        c = self.v
        pos = event.position()
        # Radial-menu press/move/paint are handled at the DxfCanvas level
        # (mousePressEvent/mouseMoveEvent/paintEvent below) so the menu opens
        # and works the same regardless of which mode/tool is active — it
        # used to be select-mode-only because it lived here.
        if c._selectable and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
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
        if c._selectable and c._shape_drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
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
        if c._selectable and c._shape_drag_active and event.button() == Qt.MouseButton.LeftButton:
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


class DxfCanvas(CanvasView):
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
        on_use_selected_as_custom_tile=None,
        on_cutout_toggle=None,
        on_outline_role_change=None,
        on_outline_role_explain=None,
        on_pattern_cell_cutout_toggle=None,
        on_create_zone_from_selection=None,
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
        self._use_selected_as_custom_tile_cb = on_use_selected_as_custom_tile
        self._on_cutout_toggle = on_cutout_toggle
        self._on_outline_role_change = on_outline_role_change
        self._on_outline_role_explain = on_outline_role_explain
        self._on_pattern_cell_cutout_toggle = on_pattern_cell_cutout_toggle
        self._on_create_zone_from_selection = on_create_zone_from_selection
        self._on_ghost_click = on_ghost_click
        self._cutout_indices: set[int] = set()
        self._outline_roles: dict[int, str] = {}
        self._pattern_cell_indices: set[int] = set()
        self._pattern_cell_cutout_indices: set[int] = set()
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
        self._radial_menu = RadialMenuService(self)
        self._size_w_edit: QLineEdit | None = None
        self._size_h_edit: QLineEdit | None = None

        # Quick shapes / radial menu / layer-activation live in the tool.
        self._tools["select"] = DxfSelectTool(self)

        if self._draft_profile:
            self.set_rulers_visible(True)
            self.set_grid_visible(True)
            self.set_grid_snap(False)
            self.set_grid_spacing(1.0)

    def _toggle_radial_menu(self) -> None:
        self._radial_menu._toggle_radial_menu()

    def set_radial_menu_tools(self, tools: list[str] | None) -> None:
        self._radial_menu.set_radial_menu_tools(tools)

    def _radial_index_at(self, x: float, y: float) -> int | None:
        return self._radial_menu._radial_index_at(x, y)

    def _execute_radial_action(self, index: int) -> None:
        self._radial_menu._execute_radial_action(index)

    def _paint_radial_menu(self, painter: QPainter) -> None:
        self._radial_menu._paint_radial_menu(painter)

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

    def set_outline_roles(self, roles: dict[int, str]) -> None:
        self._outline_roles = dict(roles)
        colors = {
            "cutout": self._CUTOUT_COLOR,
            "open_path": "#79c0ff",
            "ignore": "#6e7681",
        }
        self.set_accent_polys(
            {index: colors[role] for index, role in roles.items() if role in colors}
        )

    def set_pattern_cell_context(
        self, indices: set[int], cutout_indices: set[int] | None = None
    ) -> None:
        """Identify generated preview cells that can be toggled as fill cutouts."""
        self._pattern_cell_indices = set(indices)
        self._pattern_cell_cutout_indices = set(cutout_indices or set())
        self.set_accent_polys(
            {index: self._CUTOUT_COLOR for index in self._pattern_cell_cutout_indices}
        )

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
        # Draw mode owns right-click as an in-progress gesture/back action.
        # Edit mode still needs the normal object menu: this is where vertex
        # operations such as round and chamfer belong.
        draw_gesture_active = bool(
            self._draw_pts
            or self._pen_pts
            or self._draw_shape_preview_active
            or self._quick_shape_enabled
        )
        if not self._selectable or (self._mode == "draw" and draw_gesture_active):
            super()._rightclick_cb(cx, cy)
            return

        menu = QMenu(self)
        section_enabled = self._context_menu_section_enabled

        def _finish_section(section: str, start: int) -> None:
            actions = menu.actions()[start:]
            for action in actions:
                action.setProperty("context_section", section)
            if not section_enabled(section):
                for action in actions:
                    menu.removeAction(action)

        # "Create shape" only leads the menu when there is nothing to act on —
        # with a selection or a shape under the cursor, the actions the user
        # actually came for (delete/duplicate/close/group) come first.
        poly_hit_early = self._find_poly_at(cx, cy)
        if not self._sel and poly_hit_early is None and section_enabled("create"):
            create_start = len(menu.actions())
            shape_menu = menu.addMenu("Create shape")
            shape_menu.addAction("Rectangle (drag)", lambda: self.set_quick_shape_mode("rectangle"))
            shape_menu.addAction("Circle (drag)", lambda: self.set_quick_shape_mode("circle"))
            shape_menu.addAction("Slot (drag)", lambda: self.set_quick_shape_mode("slot"))
            shape_menu.addAction("Hexagon (drag)", lambda: self.set_quick_shape_mode("hexagon"))
            advanced_menu = shape_menu.addMenu("Procedural")
            for label, primitive in (
                ("Ring", "ring"),
                ("Gear / sprocket", "gear"),
                ("Spiral", "spiral"),
                ("Teardrop", "teardrop"),
                ("Keyhole", "keyhole"),
                ("Superellipse / squircle", "superellipse"),
                ("Rounded star", "rounded_star"),
                ("Chamfered star", "chamfered_star"),
                ("Finger-joint box", "finger_joint_box"),
                ("Dovetail box", "dovetail_box"),
                ("Tabbed panel", "tabbed_panel"),
            ):
                advanced_menu.addAction(
                    label,
                    lambda _checked=False, value=primitive: self.create_procedural_primitive(value),
                )
            menu.addSeparator()
            _finish_section("create", create_start)

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
            if idx in self._pattern_cell_indices and callable(self._on_pattern_cell_cutout_toggle):
                is_cutout = idx in self._pattern_cell_cutout_indices
                toggle_pattern_cutout = self._on_pattern_cell_cutout_toggle
                menu.addAction(
                    "Restore Pattern Cell Fill" if is_cutout else "Mark Pattern Cell as Cutout",
                    lambda _checked=False, target=idx: toggle_pattern_cutout(target),
                )
            elif callable(self._on_outline_role_change):
                change_outline_role = self._on_outline_role_change
                role_menu = menu.addMenu("Outline role")
                current_role = self._outline_roles.get(idx, "boundary")
                for role, label in (
                    ("boundary", "Boundary (fillable)"),
                    ("cutout", "Cutout (subtract)"),
                    ("open_path", "Open path (do not fill)"),
                    ("ignore", "Ignore in generation"),
                ):
                    action = role_menu.addAction(label)
                    action.setCheckable(True)
                    action.setChecked(role == current_role)
                    action.triggered.connect(
                        lambda _checked=False, value=role, target=idx: change_outline_role(
                            target, value
                        )
                    )
                if callable(self._on_outline_role_explain):
                    explain_outline_role = self._on_outline_role_explain
                    role_menu.addSeparator()
                    role_menu.addAction(
                        "Explain this role…",
                        lambda _checked=False, target=idx: explain_outline_role(target),
                    )
            if callable(self._on_cutout_toggle) and not callable(self._on_outline_role_change):
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
                        lambda _cb=cutout_toggle, _sel=sel_snapshot: [_cb(i) for i in _sel],
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
            self._show_hud_prompt(label, default, callback, minimum=minimum, is_length=is_length)

        selected_start = len(menu.actions())
        if self._sel and section_enabled("selected"):
            if callable(self._on_create_zone_from_selection):
                menu.addAction("Create Zone from Selection", self._on_create_zone_from_selection)
                menu.addSeparator()
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            menu.addAction("Move to Coordinate…", self.show_coordinate_entry)
            menu.addAction(canvas_commands.menu_text("edit.duplicate"), self.duplicate_selected)
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
                if i < len(self._entities) and not self._is_poly_closed(self._entities[i].points)
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
            menu.addAction(
                canvas_commands.menu_text("path.recognize_shapes"),
                lambda: _run_transform(self.recognize_selected_shapes),
            )
            path_menu = menu.addMenu("Path Direction & Sampling")
            for command_id in (
                "path.reverse",
                "path.set_start",
                "path.resample_spacing",
                "path.resample_count",
                "path.fit_line",
                "path.fit_circle",
                "path.fit_arc",
            ):
                action = path_menu.addAction(canvas_commands.menu_text(command_id))
                action.setEnabled(canvas_commands.can_run(self, command_id))
                action.triggered.connect(
                    lambda _checked=False, value=command_id: canvas_commands.run(self, value)
                )
            vertex_hit = self._find_nearest_vertex(cx, cy)
            if vertex_hit is not None and vertex_hit[0] in self._sel:
                entity_index, vertex_index = vertex_hit
                corner_menu = menu.addMenu("Corner")

                def _run_vertex_command(command_id: str) -> None:
                    # Corner commands intentionally operate on the vertex at
                    # the menu invocation point, not whichever vertex the
                    # pointer happens to hover after the menu opens.
                    self._hover_vert = (entity_index, vertex_index)
                    canvas_commands.run(self, command_id)

                for command_id in ("vertex.round", "vertex.chamfer"):
                    action = corner_menu.addAction(canvas_commands.menu_text(command_id))
                    action.setEnabled(canvas_commands.can_run(self, command_id))
                    action.triggered.connect(
                        lambda _checked=False, value=command_id: _run_vertex_command(value)
                    )
            if (
                vertex_hit is not None
                and vertex_hit[0] in self._sel
                and self._entities[vertex_hit[0]].kind == "bezier"
            ):
                entity_index, anchor_index = vertex_hit
                metadata = self._entities[entity_index].meta or {}
                node_types = list(metadata.get("node_types", []))
                current_type = (
                    str(node_types[anchor_index]) if anchor_index < len(node_types) else "symmetric"
                )
                node_menu = menu.addMenu("Bézier node")
                for mode, label in (
                    ("corner", "Corner — independent handles"),
                    ("smooth", "Smooth — aligned handles"),
                    ("symmetric", "Symmetric — linked handles"),
                ):
                    action = node_menu.addAction(label)
                    action.setCheckable(True)
                    action.setChecked(mode == current_type)
                    action.triggered.connect(
                        lambda _checked=False, value=mode, ei=entity_index, ai=anchor_index: (
                            self.set_bezier_node_type(ei, ai, value)
                        )
                    )
            if len(self._sel) >= 2:
                menu.addAction(canvas_commands.menu_text("group.create"), self._group_selected)
            if any(self._group_of(i) is not None for i in self._sel):
                menu.addAction(canvas_commands.menu_text("group.dissolve"), self._ungroup_selected)
            constraints_menu = menu.addMenu("Constraints")
            for command_id in (
                "constraint.horizontal",
                "constraint.vertical",
                "constraint.parallel",
                "constraint.perpendicular",
                "constraint.equal_length",
                "constraint.coincident",
                "constraint.fixed",
            ):
                action = constraints_menu.addAction(canvas_commands.menu_text(command_id))
                action.setEnabled(canvas_commands.can_run(self, command_id))
                action.triggered.connect(
                    lambda _checked=False, value=command_id: canvas_commands.run(self, value)
                )
            constraints_menu.addSeparator()
            constraints_menu.addAction(
                canvas_commands.menu_text("constraint.remove"),
                self.remove_constraints_for_selection,
            )
            construct_menu = menu.addMenu("Construct")
            for command_id in (
                "construct.xline",
                "construct.ray",
                "construct.bisector",
                "construct.centerline",
                "construct.circle_3point",
                "construct.point_tangents",
                "construct.common_tangents",
            ):
                action = construct_menu.addAction(canvas_commands.menu_text(command_id))
                action.setEnabled(canvas_commands.can_run(self, command_id))
                action.triggered.connect(
                    lambda _checked=False, value=command_id: canvas_commands.run(self, value)
                )
        _finish_section("selected", selected_start)
        section_start = len(menu.actions())
        if not self._sel:
            menu.addAction("Select all", self.select_all)
        symbols_menu = menu.addMenu("Symbols")
        if self._sel:
            symbols_menu.addAction("Create from selection…", self.create_symbol_from_selection)
            if self._symbol_library:
                symbols_menu.addSeparator()
        if self._symbol_library:
            insert_menu = symbols_menu.addMenu("Insert")
            manage_menu = symbols_menu.addMenu("Manage")
            for symbol_name in sorted(self._symbol_library, key=str.casefold):
                insert_menu.addAction(
                    symbol_name,
                    lambda _checked=False, name=symbol_name: self.insert_symbol_named(name),
                )
                item_menu = manage_menu.addMenu(symbol_name)
                item_menu.addAction(
                    "Rename…",
                    lambda _checked=False, name=symbol_name: self.prompt_rename_symbol(name),
                )
                item_menu.addAction(
                    "Delete",
                    lambda _checked=False, name=symbol_name: self.delete_symbol(name),
                )
        else:
            empty_action = symbols_menu.addAction("No saved symbols")
            empty_action.setEnabled(False)

        menu.addAction(
            canvas_commands.menu_text("select.lasso", "Lasso selection"),
            self.arm_lasso_selection,
        )

        select_menu = menu.addMenu("Select by geometry")
        select_menu.addAction("Open paths", self.select_open_paths)
        select_menu.addAction("Closed paths", self.select_closed_paths)
        select_menu.addSeparator()
        for label, category in (
            ("Parametric shapes", "parametric"),
            ("Generic paths", "generic_paths"),
            ("Text", "text"),
            ("Construction geometry", "construction"),
        ):
            select_menu.addAction(
                label, lambda _checked=False, value=category: self.select_geometry_category(value)
            )
        select_menu.addAction("Invert selection", self._invert_selection)
        _finish_section("selection", section_start)

        section_start = len(menu.actions())
        if callable(self._send_selected_to_pattern_cb):
            menu.addAction("Use as outline", lambda: _run_transform(self._send_selected_to_pattern))
        if callable(self._use_selected_as_custom_tile_cb):
            menu.addAction(
                "Use as Custom Tile",
                lambda: _run_transform(self._use_selected_as_custom_tile),
            )
        if callable(getattr(self, "_send_selected_to_draft_cb", None)):
            menu.addAction(
                "Send to Draft",
                lambda: _run_transform(self._send_selected_to_draft),
            )
        health_action = menu.addAction(
            "Hide Geometry Health" if self._geometry_health_visible else "Show Geometry Health"
        )
        health_action.triggered.connect(
            lambda: self.set_geometry_health_visible(not self._geometry_health_visible)
        )
        menu.addAction("Geometry Preflight…", self._show_geometry_preflight)
        _finish_section("share_diagnostics", section_start)

        section_start = len(menu.actions())
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
        _finish_section("boolean", section_start)

        section_start = len(menu.actions())
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
                lambda _t=title, _p=prompt, _d=default, _a=axis, _m=dist_mode: _run_transform(
                    lambda: _run_prompted_transform(
                        _t,
                        _p,
                        _d,
                        0.0,
                        lambda value: self._distribute_selected(_a, value, mode=_m),
                    )
                ),
            )
        _finish_section("arrange", section_start)

        section_start = len(menu.actions())
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
        transform_menu.addAction(
            canvas_commands.menu_text("mode.knife", "Knife tool"),
            lambda: canvas_commands.run(self, "mode.knife"),
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
        _finish_section("transform", section_start)

        section_start = len(menu.actions())
        wx_txt, wy_txt = self._c2w(cx, cy)
        menu.addAction(
            canvas_commands.menu_text("text.add", "Add text…"),
            lambda: self.prompt_add_text(wx_txt, wy_txt),
        )
        _finish_section("text", section_start)

        section_start = len(menu.actions())
        menu.addSeparator()
        menu.addAction(
            canvas_commands.menu_text("view.previous"),
            lambda: canvas_commands.run(self, "view.previous"),
        )
        menu.addAction(
            canvas_commands.menu_text("view.next"),
            lambda: canvas_commands.run(self, "view.next"),
        )
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
        _finish_section("view", section_start)
        # Overflow is semantic and user-configurable. Never bury an action
        # merely because unrelated items happened to be inserted before it.
        tagged_actions = [action for action in menu.actions() if action.property("context_section")]
        grouped: dict[str, list] = {}
        for action in tagged_actions:
            grouped.setdefault(str(action.property("context_section")), []).append(action)
            menu.removeAction(action)
        section_order = list(self._context_menu_section_order)
        section_order.extend(section for section in grouped if section not in section_order)
        for section in section_order:
            if section not in self._context_menu_overflow_sections:
                for action in grouped.get(section, []):
                    menu.addAction(action)
        overflow_actions = [
            action
            for section in section_order
            if section in self._context_menu_overflow_sections
            for action in grouped.get(section, [])
        ]
        if overflow_actions:
            for action in overflow_actions:
                menu.removeAction(action)
            more_menu = QMenu("More actions…", menu)
            menu.addMenu(more_menu)
            for action in overflow_actions:
                more_menu.addAction(action)
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
        hud_x, hud_y = self._hud_position_near(
            cx,
            cy,
            196,
            24,
            offset_x=-98,
            offset_y=-30,
        )
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
        self._size_w_edit.move(hud_x, hud_y)
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
        self._size_h_edit.move(hud_x + 106, hud_y)
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
            carved = False
            if (
                self._draw_split_enabled
                and not self._draw_construction_mode
                and self._is_poly_closed(poly)
            ):
                before = self._canvas_service.begin_preview()
                carved, carved_count = self._carve_geometry_with_shape(poly)
                if carved:
                    self._entities.append(
                        EntityRecord(
                            points=list(poly),
                            kind=kind,
                            meta=meta,
                            layer=self._active_layer,
                        )
                    )
                    self._document.selection = {len(self._entities) - 1}
                    self._canvas_service.commit_preview(before)
                    self._show_flash(f"Carved {carved_count} region(s)", 1000)
            if not carved:
                self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
                self._sel = {len(self._entities) - 1}
            if was_empty:
                self._fit()
            else:
                self._redraw()
            self._notify()
            self._fire_poly_change()
            if not carved:
                self._show_flash(f"{self._shape_drag_mode.title()} created", 800)
        self._clear_shape_drag()

    def _clear_shape_drag(self) -> None:
        self._shape_drag_active = False
        self._shape_start_w = None
        self._shape_start_c = None
        self._shape_end_c = None
