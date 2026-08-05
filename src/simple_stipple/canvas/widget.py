# pyright: reportAttributeAccessIssue=false
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

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.canvas.interaction import context_menu, quick_shapes
from simple_stipple.canvas.tools import tools as canvas_tools
from simple_stipple.canvas.tools.tools import RadialMenuService
from simple_stipple.canvas.view.main import CanvasView
from simple_stipple.document.model import EntityRecord
from simple_stipple.platform.config import (
    CONTEXT_MENU_TRANSFORM_ITEMS,
    DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS,
    DEFAULT_RADIAL_MENU_TOOLS,
)

_CONTEXT_STATIC_ACTION_IDS = {
    "Rectangle (drag)": "context.create.rectangle",
    "Circle (drag)": "context.create.circle",
    "Slot (drag)": "context.create.slot",
    "Hexagon (drag)": "context.create.hexagon",
    "Ring": "context.create.ring",
    "Gear / sprocket": "context.create.gear",
    "Spiral": "context.create.spiral",
    "Teardrop": "context.create.teardrop",
    "Keyhole": "context.create.keyhole",
    "Superellipse / squircle": "context.create.superellipse",
    "Rounded star": "context.create.rounded_star",
    "Chamfered star": "context.create.chamfered_star",
    "Finger-joint box": "context.create.finger_joint_box",
    "Dovetail box": "context.create.dovetail_box",
    "Tabbed panel": "context.create.tabbed_panel",
    "Select": "context.entity.select",
    "Deselect": "context.entity.deselect",
    "Delete": "context.entity.delete",
    "Edit text…": "context.entity.edit_text",
    "This cell only": "context.pattern_cell.instance",
    "Every matching tile": "context.pattern_cell.repeat",
    "Move to Coordinate…": "context.selection.move",
    "Frame selection": "context.selection.fit",
    "Smooth": "context.selection.smooth",
    "Simplify…": "context.selection.simplify",
    "Apply treatment to selection": "context.selection.create_zone",
    "Grid array…": "context.selection.array_grid",
    "Radial array…": "context.selection.array_radial",
    "Select all": "select.all",
    "Lasso selection": "select.lasso",
    "Corner — independent handles": "context.bezier_node.corner",
    "Smooth — aligned handles": "context.bezier_node.smooth",
    "Symmetric — linked handles": "context.bezier_node.symmetric",
    "Align left": "context.arrange.left",
    "Align center X": "context.arrange.center_x",
    "Align right": "context.arrange.right",
    "Align top": "context.arrange.top",
    "Align center Y": "context.arrange.center_y",
    "Align bottom": "context.arrange.bottom",
    "Distribute horizontal — gap…": "context.arrange.distribute_horizontal_gap",
    "Distribute vertical — gap…": "context.arrange.distribute_vertical_gap",
    "Distribute horizontal — center-to-center…": "context.arrange.distribute_horizontal_centers",
    "Distribute vertical — center-to-center…": "context.arrange.distribute_vertical_centers",
    "Use as outline": "context.share.outline",
    "Use as Custom Tile": "context.share.custom_tile",
    "Send to Draft": "context.share.draft",
    "Add text…": "text.add",
    "Select [Esc]": "context.view.select",
    "Fit view": "view.fit",
    "Show grid": "grid.toggle",
    "Snap to grid": "grid.snap",
}


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
        if c._quick_shape_enabled:
            mode = c._shape_mode_from_modifiers(event.modifiers())
            if mode in c._PROCEDURAL_QUICK_SHAPES:
                if c._shape_click_placement_active:
                    c._finish_shape_drag(pos.toPoint(), allow_click_placement=True)
                else:
                    c._start_shape_drag(mode, pos)
                    c._shape_click_placement_active = True
                return True
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
        if c._selectable and c._shape_drag_active and (
            c._shape_click_placement_active or event.buttons() & Qt.MouseButton.LeftButton
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
        if c._selectable and c._shape_drag_active and event.button() == Qt.MouseButton.LeftButton:
            if c._shape_click_placement_active:
                return True
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
            previews = c._build_drag_shapes(c._shape_drag_mode, sx, sy, ex, ey)
            pen = QPen(QColor("#f85149"), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            for preview in previews:
                for i in range(1, len(preview)):
                    x0, y0 = c._w2c(*preview[i - 1])
                    x1, y1 = c._w2c(*preview[i])
                    painter.drawLine(int(x0), int(y0), int(x1), int(y1))


class DxfCanvas(CanvasView):
    """Unified shared canvas used across Draft, Pattern, Trace, and preview surfaces."""

    quickShapeChanged = Signal(str)
    quickShapeEnabledChanged = Signal(bool)

    _PROCEDURAL_QUICK_SHAPES = frozenset(
        {
            "ring",
            "gear",
            "spiral",
            "teardrop",
            "keyhole",
            "superellipse",
            "rounded_star",
            "chamfered_star",
            "finger_joint_box",
            "dovetail_box",
            "tabbed_panel",
        }
    )
    _VALID_QUICK_SHAPES = frozenset({"rectangle", "circle", "slot", "hexagon"}) | _PROCEDURAL_QUICK_SHAPES

    _CUTOUT_COLOR = "#f0883e"
    _context_static_action_ids = _CONTEXT_STATIC_ACTION_IDS

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
        self._on_pattern_cell_cutout_toggle = on_pattern_cell_cutout_toggle
        self._on_create_zone_from_selection = on_create_zone_from_selection
        self._on_ghost_click = on_ghost_click
        self._pattern_cell_indices: set[str] = set()
        self._pattern_cell_cutout_indices: set[str] = set()
        self._draft_profile = bool(draft_profile or selectable)

        self._quick_shape_mode: str = "rectangle"
        self._quick_shape_enabled: bool = False
        self._shape_drag_active: bool = False
        self._shape_click_placement_active: bool = False
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

    def _entity_for_id(self, entity_id: int | str) -> EntityRecord | None:
        if isinstance(entity_id, str):
            return next((e for e in self._entities if e.id == entity_id), None)
        elif isinstance(entity_id, int) and 0 <= entity_id < len(self._entities):
            return self._entities[entity_id]
        return None

    def _toggle_radial_menu(self) -> None:
        self._radial_menu._toggle_radial_menu()

    def set_radial_menu_tools(self, tools: list[str] | None) -> None:
        self._radial_menu.set_radial_menu_tools(tools)

    def set_context_menu_profiles(self, profiles: dict) -> None:
        """Use the leaf-action menu as the default, including its More set."""
        super().set_context_menu_profiles(profiles)
        if self._context_menu_actions_configured:
            return
        default_items = [
            *(command.id for command in canvas_commands.COMMANDS if not command.hidden),
            *_CONTEXT_STATIC_ACTION_IDS.values(),
            *(f"transform.{key}" for key, _label in CONTEXT_MENU_TRANSFORM_ITEMS),
        ]
        self._context_menu_item_order = list(dict.fromkeys(default_items))
        self._context_menu_overflow_items = set(DEFAULT_CONTEXT_MENU_ACTION_OVERFLOW_ITEMS)
        self._context_menu_actions_configured = True

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
            gesture = "Click start and end" if m in self._PROCEDURAL_QUICK_SHAPES else "Drag shape"
            self._show_flash(f"{gesture}: {m}", 900)
        self._redraw()

    def activate_procedural_draw(self, primitive: str) -> None:
        """Arm a procedural shape in the standard click-to-click Draw flow."""
        if primitive not in self._PROCEDURAL_QUICK_SHAPES:
            return
        self.set_quick_shape_enabled(False)
        self.set_mode("draw")
        self._set_draw_primitive(primitive)

    def set_pattern_cell_context(
        self, entity_ids: set[str], cutout_indices: set[str] | None = None
    ) -> None:
        """Identify generated preview cells that can be removed from the fill."""
        self._pattern_cell_indices = set(entity_ids)
        cutout_ids = cutout_indices or set()
        self._pattern_cell_cutout_indices = cutout_ids
        self.set_accent_polys({eid: self._CUTOUT_COLOR for eid in cutout_ids})

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
            self._shape_click_placement_active = False
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
        if not context_menu.handle_right_click(self, cx, cy):
            super()._rightclick_cb(cx, cy)

    def _apply_context_menu_overflow(self, menu: QMenu) -> None:
        context_menu.apply_overflow(self, menu)

    def _apply_context_menu_item_customization(self, menu: QMenu) -> None:
        context_menu.apply_item_customization(self, menu)

    # ── Context menu builders ──────────────────────────────────────────────

    @staticmethod
    def _add_context_section(menu, section, section_enabled, build) -> None:
        context_menu.add_context_section(menu, section, section_enabled, build)

    def _build_create_shape_menu(self, menu, section_enabled, poly_hit_early) -> None:
        if not self._sel and poly_hit_early is None and section_enabled("create"):
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
                    lambda _checked=False, value=primitive: self.activate_procedural_draw(value),
                )
            menu.addSeparator()

    def _build_entity_actions(self, menu, poly_hit, section_enabled, _run_transform) -> None:
        entity = self._entity_for_id(poly_hit)
        entity_id = entity.id if entity else None
        if entity_id and self.text_params_at(entity_id) is not None:
            menu.addAction("Edit text…", lambda eid=entity_id: self.prompt_edit_text(eid))
        if entity_id and entity_id in self._sel:
            menu.addAction("Deselect", lambda eid=entity_id: self._ctx_deselect(eid))
        elif entity_id:
            menu.addAction("Select", lambda eid=entity_id: self._ctx_select(eid))
        menu.addAction("Delete", lambda: self._ctx_delete_poly(entity_id))
        menu.addSeparator()

    def _build_selection_actions(
        self,
        menu,
        section_enabled,
        _run_transform,
        _run_prompted_transform,
        cx,
        cy,
    ) -> None:
        if not section_enabled("selected"):
            return
        if callable(self._on_create_zone_from_selection):
            menu.addAction("Apply treatment to selection", self._on_create_zone_from_selection)
            menu.addSeparator()
        for command_id in ("clipboard.cut", "clipboard.copy", "clipboard.paste"):
            action = menu.addAction(canvas_commands.menu_text(command_id))
            action.setEnabled(canvas_commands.can_run(self, command_id))
            action.triggered.connect(
                lambda _checked=False, value=command_id: canvas_commands.run(self, value)
            )
        menu.addAction(canvas_commands.menu_text("edit.delete"), self.delete_selected)
        menu.addAction("Move to Coordinate…", self.show_coordinate_entry)
        menu.addAction(canvas_commands.menu_text("edit.duplicate"), self.duplicate_selected)
        array_menu = menu.addMenu("Array")
        array_menu.addAction(
            canvas_commands.menu_text("edit.array_grid"), self.array_duplicate_grid
        )
        array_menu.addAction(
            canvas_commands.menu_text("edit.array_radial"), self.array_duplicate_radial
        )
        if len(self._sel) >= 2:
            menu.addAction(
                canvas_commands.menu_text("text.attach_to_path"),
                self.attach_selected_text_to_path,
            )
        open_count = sum(
            1
            for entity in self._entities
            if entity.id in self._sel and not self._is_poly_closed(entity.points)
        )
        if open_count:
            label = "Close path"
            if len(self._sel) > 1:
                label = f"Close path (join {len(self._sel)} into one)"
            menu.addAction(label, self.close_selection_as_path)
        menu.addAction("Frame selection", self.fit_selection)
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
        vertex_hit = self._find_nearest_vertex_by_id(cx, cy)
        if vertex_hit is not None and vertex_hit[0] in self._sel:
            entity_id, vertex_index = vertex_hit
            corner_menu = menu.addMenu("Corner")

            def _run_vertex_command(command_id: str) -> None:
                eid: str = vertex_hit[0]  # type: ignore[assignment]
                self._hover_vert = (eid, vertex_index)
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
            and (ent := self._entity_for_id(vertex_hit[0])) is not None
            and ent.kind == "bezier"
        ):
            entity_id, anchor_index = vertex_hit
            if (ent2 := self._entity_for_id(entity_id)) is not None:
                metadata = ent2.meta or {}
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
                        lambda _checked=False, value=mode, ei=entity_id, ai=anchor_index: (
                            self.set_bezier_node_type(ei, ai, value)
                        )
                    )
        if len(self._sel) >= 2:
            menu.addAction(canvas_commands.menu_text("group.create"), self._group_selected)
        if any(
            self._entity_for_id(eid) is not None and self._group_of(eid) is not None
            for eid in self._sel
        ):
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

    def _build_selection_tools(self, menu) -> None:
        if not self._sel:
            menu.addAction("Select all", self.select_all)
        menu.addAction(
            canvas_commands.menu_text("select.lasso", "Lasso selection"),
            self.arm_lasso_selection,
        )

    def _build_share_actions(self, menu, _run_transform) -> None:
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
        selected_ids = self.get_selected_ids()
        layers = [name for name in self.layer_names() if name and name != self.active_layer]
        if selected_ids and layers:
            move_menu = menu.addMenu("Move selected to layer")
            for layer in layers:
                action = move_menu.addAction(
                    layer,
                    lambda _checked=False, target=layer: _run_transform(
                        lambda: self.move_indices_to_layer(selected_ids, target)
                    ),
                )
                action.setProperty("context_item_id", "context.share.move_to_layer")

    def _build_boolean_actions(self, menu, section_enabled) -> None:
        if not section_enabled("boolean"):
            return
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

    def _build_arrange_actions(self, menu, _run_transform, _run_prompted_transform) -> None:
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

    def _build_transform_actions(self, menu, _run_transform, _run_prompted_transform) -> None:
        transform_menu = menu.addMenu("Transform")
        def add(item_id, label, callback):
            action = transform_menu.addAction(label, callback)
            action.setProperty("context_item", item_id)
            return action

        add(
            "rotate_cw",
            "Rotate +90°", lambda: _run_transform(lambda: self.rotate_selected(90.0))
        )
        add(
            "rotate_ccw",
            "Rotate -90°", lambda: _run_transform(lambda: self.rotate_selected(-90.0))
        )
        add(
            "mirror_horizontal",
            "Mirror horizontal",
            lambda: _run_transform(lambda: self.mirror_selected("horizontal")),
        )
        add(
            "mirror_vertical",
            "Mirror vertical",
            lambda: _run_transform(lambda: self.mirror_selected("vertical")),
        )
        transform_menu.addSeparator()
        add(
            "size",
            "Edit width + height…", lambda: _run_transform(self._show_size_hud)
        )
        add(
            "length",
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
        add(
            "angle",
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
        add(
            "trim",
            canvas_commands.menu_text("mode.trim", "Trim segments…"),
            lambda: canvas_commands.run(self, "mode.trim"),
        )
        add(
            "extend",
            canvas_commands.menu_text("mode.extend", "Extend to meet…"),
            lambda: canvas_commands.run(self, "mode.extend"),
        )
        add(
            "knife",
            canvas_commands.menu_text("mode.knife", "Knife tool"),
            lambda: canvas_commands.run(self, "mode.knife"),
        )
        transform_menu.addSeparator()
        add(
            "explode",
            "Explode to segments",
            lambda: _run_transform(self.explode_selected_to_segments),
        )
        add(
            "merge",
            "Merge segments to object",
            lambda: _run_transform(self.merge_selected_segments_to_objects),
        )
        configured = list(getattr(self, "_context_menu_transform_items", []))
        if configured:
            actions = [a for a in transform_menu.actions() if a.property("context_item")]
            by_id = {str(action.property("context_item")): action for action in actions}
            for action in list(transform_menu.actions()):
                transform_menu.removeAction(action)
            for item_id in configured:
                if action := by_id.get(item_id):
                    transform_menu.addAction(action)

    def _build_text_actions(self, menu, cx, cy) -> None:
        wx_txt, wy_txt = self._c2w(cx, cy)
        menu.addAction(
            canvas_commands.menu_text("text.add", "Add text…"),
            lambda: self.prompt_add_text(wx_txt, wy_txt),
        )

    def _build_view_actions(self, menu) -> None:
        menu.addSeparator()
        menu.addAction(canvas_commands.menu_text("view.fit"), self.fit)
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

    # ── End context menu builders ──────────────────────────────────────────

    def _show_size_hud(self) -> None:
        indices = self._selected_ids()
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
        self._size_w_edit = QLineEdit(self)
        self._size_w_edit.setFixedWidth(90)
        self._size_w_edit.setFixedHeight(24)
        self._size_w_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._size_w_edit.setText(f"{cur_w:.2f}")
        self._size_w_edit.setPlaceholderText("W")
        self._size_w_edit.setProperty("role", "canvas-hud-input")
        self._size_w_edit.setAccessibleName("Selected width")
        self._size_w_edit.setToolTip("Enter the selected shape width")
        self._size_w_edit.move(hud_x, hud_y)
        self._size_w_edit.returnPressed.connect(self._apply_size_hud)
        self._size_w_edit.editingFinished.connect(self._apply_size_hud)
        self._size_w_edit.textEdited.connect(self._clear_size_hud_error)
        self._size_w_edit.show()

        self._size_h_edit = QLineEdit(self)
        self._size_h_edit.setFixedWidth(90)
        self._size_h_edit.setFixedHeight(24)
        self._size_h_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._size_h_edit.setText(f"{cur_h:.2f}")
        self._size_h_edit.setPlaceholderText("H")
        self._size_h_edit.setProperty("role", "canvas-hud-input")
        self._size_h_edit.setAccessibleName("Selected height")
        self._size_h_edit.setToolTip("Enter the selected shape height")
        self._size_h_edit.move(hud_x + 106, hud_y)
        self._size_h_edit.returnPressed.connect(self._apply_size_hud)
        self._size_h_edit.editingFinished.connect(self._apply_size_hud)
        self._size_h_edit.textEdited.connect(self._clear_size_hud_error)
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

    def _clear_size_hud_error(self, _text: str) -> None:
        """Remove stale validation styling as soon as either dimension is edited."""
        for edit in (self._size_w_edit, self._size_h_edit):
            if edit is not None and edit.property("error"):
                edit.setProperty("error", None)
                edit.style().unpolish(edit)
                edit.style().polish(edit)

    @staticmethod
    def _show_size_hud_error(*edits: QLineEdit) -> None:
        for edit in edits:
            edit.setProperty("error", "true")
            edit.style().unpolish(edit)
            edit.style().polish(edit)

    def _apply_size_hud(self) -> None:
        if self._size_w_edit is None or self._size_h_edit is None:
            return
        try:
            new_w = float(self._size_w_edit.text().strip())
            new_h = float(self._size_h_edit.text().strip())
        except ValueError:
            self._show_size_hud_error(self._size_w_edit, self._size_h_edit)
            self._show_flash("Invalid size", 900)
            return
        invalid_edits = tuple(
            edit
            for value, edit in ((new_w, self._size_w_edit), (new_h, self._size_h_edit))
            if value <= 0
        )
        if invalid_edits:
            self._show_size_hud_error(*invalid_edits)
            self._show_flash("Dimensions must be greater than zero", 1200)
            return
        indices = self._selected_ids()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            self._dismiss_size_hud()
            return

        cur_w = max(bounds[2] - bounds[0], 0.0)
        cur_h = max(bounds[3] - bounds[1], 0.0)
        changed_w = abs(new_w - cur_w) > 1e-9
        changed_h = abs(new_h - cur_h) > 1e-9
        if changed_w:
            self._set_selected_width(new_w)
        if changed_h:
            self._set_selected_height(new_h)
        if changed_w or changed_h:
            self._show_flash("Dimensions updated", 900)
            # Keep HUD open with committed values for iterative edits.
            self._size_w_edit.setText(f"{new_w:.2f}")
            self._size_h_edit.setText(f"{new_h:.2f}")

    def _shape_mode_from_modifiers(self, mods) -> str:
        return quick_shapes.mode_from_modifiers(self, mods)

    def _start_shape_drag(self, mode: str, pos_f) -> None:
        quick_shapes.start_drag(self, mode, pos_f)

    @staticmethod
    def _translate(
        coords: list[tuple[float, float]],
        cx: float,
        cy: float,
    ) -> list[tuple[float, float]]:
        return quick_shapes.translate(coords, cx, cy)

    def _build_drag_shapes(
        self,
        mode: str,
        sx: float,
        sy: float,
        ex: float,
        ey: float,
    ) -> list[list[tuple[float, float]]]:
        return quick_shapes.build_shapes(self, mode, sx, sy, ex, ey)

    def _build_drag_shape(
        self, mode: str, sx: float, sy: float, ex: float, ey: float
    ) -> list[tuple[float, float]]:
        """Compatibility helper for callers that only need the first contour."""
        return quick_shapes.build_first_shape(self, mode, sx, sy, ex, ey)

    @staticmethod
    def _build_drag_procedural_shapes(
        mode: str, width: float, height: float, cx: float, cy: float
    ) -> list[list[tuple[float, float]]]:
        return quick_shapes.build_procedural_shapes(mode, width, height, cx, cy)

    def _finish_shape_drag(self, end_c: QPoint, *, allow_click_placement: bool = False) -> None:
        if (
            not self._shape_drag_active
            or self._shape_start_w is None
            or self._shape_start_c is None
        ):
            self._clear_shape_drag()
            return
        start_c = self._shape_start_c
        drag_px = abs(end_c.x() - start_c.x()) + abs(end_c.y() - start_c.y())
        if drag_px < 8 and not allow_click_placement:
            self._clear_shape_drag()
            if self._mode == "select" and self._sel:
                self.deselect_all()
            self._redraw()
            return
        sx, sy = self._shape_start_w
        ex, ey = self._c2w(float(end_c.x()), float(end_c.y()))
        polys = self._build_drag_shapes(self._shape_drag_mode, sx, sy, ex, ey)
        if polys:
            poly = polys[0]
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
            elif self._shape_drag_mode in self._PROCEDURAL_QUICK_SHAPES:
                kind = self._shape_drag_mode
                meta = {
                    "generator": self._shape_drag_mode,
                    "center": (cx, cy),
                    "width": w,
                    "height": h,
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
                    new_entity = EntityRecord(
                        points=list(poly),
                        kind=kind,
                        meta=meta,
                        layer=self._active_layer,
                    )
                    self._entities.append(new_entity)
                    self._document.selection = {new_entity.id}
                    self._canvas_service.commit_preview(before)
                    self._show_flash(f"Carved {carved_count} region(s)", 1000)
            if not carved and len(polys) == 1:
                self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
                self._sel = {self._entities[-1].id}
            elif not carved:
                group = self._next_group_id
                entities = [
                    EntityRecord(
                        points=points,
                        kind=self._shape_drag_mode,
                        meta={
                            "generator": self._shape_drag_mode,
                            "center": (cx, cy),
                            "width": w,
                            "height": h,
                        },
                        group=group,
                        layer=self._active_layer,
                        construction=self._draw_construction_mode,
                    )
                    for points in polys
                ]
                self._canvas_service.create_entities(entities)
                self._sel = {entity.id for entity in entities}
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
        self._shape_click_placement_active = False
        self._shape_start_w = None
        self._shape_start_c = None
        self._shape_end_c = None
