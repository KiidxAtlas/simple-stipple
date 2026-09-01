"""Clipboard and context-menu interaction services composed by the canvas view."""

from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu
from shiboken6 import isValid

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.core.document.commands import MoveEntityCommand
from simple_stipple.core.document.model import EntityRecord

_SHARED_CLIPBOARD: list[dict[str, Any]] = []


class ClipboardService:
    def __init__(self, host) -> None:
        self._host = host

    @property
    def records(self) -> list[dict[str, Any]]:
        return _SHARED_CLIPBOARD

    @records.setter
    def records(self, value: list[dict[str, Any]]) -> None:
        _SHARED_CLIPBOARD[:] = value

    def copy_selected(self) -> None:
        host = self._host
        self.records = []
        for eid in host._sel:
            entity = host._entity_for_id(eid)
            if entity is None:
                continue
            self.records.append(
                {
                    "polyline": list(entity.points),
                    "kind": entity.kind,
                    "meta": deepcopy(entity.meta),
                    "construction": entity.construction,
                    "group": entity.group,
                }
            )

    def paste_records(
        self, dx: float, dy: float | None = None, *, record_history: bool = True
    ) -> list[str]:
        host = self._host
        dy = dx if dy is None else dy
        entities: list[EntityRecord] = []
        groups: dict[int, int] = {}
        next_group = host._document.next_group_id
        for record in self.records:
            points = [(x + dx, y + dy) for x, y in record.get("polyline", [])]
            kind = str(record.get("kind", "polyline"))
            meta = host._translated_entity_meta(kind, record.get("meta"), dx, dy)
            source_group = record.get("group")
            group = None
            if isinstance(source_group, int):
                if source_group not in groups:
                    groups[source_group] = next_group
                    next_group += 1
                group = groups[source_group]
            entities.append(
                EntityRecord(
                    points=points,
                    kind=kind,
                    meta=meta,
                    construction=bool(record.get("construction")),
                    group=group,
                    layer=host._active_layer,
                )
            )
        result = host._canvas_service.create_entities(entities, record=record_history)
        return result.created_ids

    def paste(self, offset: float = 0.0) -> None:
        if not self.records:
            return
        self._finish(self.paste_records(offset))

    def duplicate(self) -> None:
        if self._host._sel:
            self.copy_selected()
            self.paste(1.0)

    def duplicate_with_offset(self) -> None:
        host = self._host
        points = [
            point
            for eid in host._sel
            for point in (host._entity_for_id(eid).points if host._entity_for_id(eid) else [])
        ]
        if not points:
            return
        width = max(point[0] for point in points) - min(point[0] for point in points)
        height = max(point[1] for point in points) - min(point[1] for point in points)
        self.copy_selected()
        self.paste_with_offset(max(2.0, min(width or 10.0, height or 10.0) * 0.1))

    def paste_with_offset(self, offset: float) -> None:
        if not self.records:
            return
        self._finish(self.paste_records(offset))

    def prompt_grid(self) -> None:
        host = self._host
        if not host._sel:
            host._show_flash("Select shape(s) first", 1000)
            return

        def columns(value: float) -> None:
            def rows(row_value: float) -> None:
                host._show_hud_prompt(
                    "Spacing (mm)",
                    10.0,
                    lambda spacing: self.apply_grid(
                        int(round(value)), int(round(row_value)), spacing
                    ),
                    minimum=0.01,
                )

            host._show_hud_prompt("Rows", 2.0, rows, minimum=1, is_length=False)

        host._show_hud_prompt("Columns", 2.0, columns, minimum=1, is_length=False)

    def apply_grid(self, columns: int, rows: int, spacing: float) -> bool:
        host = self._host
        if not host._sel:
            host._show_flash("Select shape(s) first", 1000)
            return False
        if columns * rows <= 1:
            host._show_flash("Nothing to duplicate (1×1 grid)", 1200)
            return False
        self.copy_selected()
        before = host._canvas_service.begin_preview()
        created = [
            eid
            for row in range(rows)
            for column in range(columns)
            if row or column
            for eid in self.paste_records(column * spacing, row * spacing, record_history=False)
        ]
        host._canvas_service.commit_preview(before)
        self._finish(created)
        host._set_repeat_action(
            f"Grid array {columns}×{rows}", lambda: self.apply_grid(columns, rows, spacing)
        )
        return True

    def prompt_radial(self) -> None:
        host = self._host
        if not host._sel:
            host._show_flash("Select shape(s) first", 1000)
            return

        def copies(value: float) -> None:
            host._show_hud_prompt(
                "Radius (mm)",
                20.0,
                lambda radius: self.apply_radial(int(round(value)), radius),
                minimum=0.01,
            )

        host._show_hud_prompt("Copies", 6.0, copies, minimum=1, is_length=False)

    def apply_radial(self, count: int, radius: float) -> bool:
        host = self._host
        if not host._sel or count <= 1:
            host._show_flash("Select shapes and request at least 2 copies", 1200)
            return False
        self.copy_selected()
        before = host._canvas_service.begin_preview()
        created = [
            eid
            for copy in range(1, count)
            for eid in self.paste_records(
                radius * math.cos(2.0 * math.pi * copy / count),
                radius * math.sin(2.0 * math.pi * copy / count),
                record_history=False,
            )
        ]
        host._canvas_service.commit_preview(before)
        self._finish(created)
        host._set_repeat_action(f"Radial array ×{count}", lambda: self.apply_radial(count, radius))
        return True

    def cut_selected(self) -> None:
        host = self._host
        dropped_ids = [eid for eid in host._sel if not host._entity_for_id(eid).locked]
        if not dropped_ids:
            if host._sel:
                host._show_flash("Shape is locked", 1200)
            return
        self.copy_selected()
        host._canvas_service.delete_entities(tuple(dropped_ids))
        self._changed()

    def nudge_selected(self, dx: float, dy: float) -> None:
        host = self._host
        selected_ids = [eid for eid in host._sel if not host._entity_for_id(eid).locked]
        if not selected_ids:
            if host._sel:
                host._show_flash("Shape is locked", 1200)
            return
        host._canvas_service.execute(
            MoveEntityCommand(entity_ids=tuple(selected_ids), dx=dx, dy=dy)
        )
        self._changed()

    def _finish(self, entity_ids: list[str]) -> None:
        self._host._sel = set(entity_ids)
        self._changed()

    def _changed(self) -> None:
        self._host._refresh_driving_dimensions()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()


def handle_right_click(host: Any, cx: float, cy: float) -> bool:
    """Build a menu, returning ``False`` when the base canvas must handle the gesture."""
    if host._radial_active:
        return _dismiss_radial_menu(host)
    draw_gesture_active = bool(
        host._draw_pts
        or host._pen_pts
        or host._draw_shape_preview_active
        or host._quick_shape_enabled
    )
    if not host._selectable or (host._mode == "draw" and draw_gesture_active):
        return False

    menu = QMenu(host)
    section_enabled = host._context_menu_section_enabled

    def ensure_context_selection() -> bool:
        if host._sel:
            return True
        poly_hit = host._hit_test.entity_at(cx, cy)
        if poly_hit is None:
            return False
        entity = host._entity_for_id(poly_hit)
        if entity is None:
            return False
        host._sel = {entity.id}
        host._redraw()
        host._notify()
        return True

    def run_transform(action: Callable[[], None]) -> None:
        if ensure_context_selection():
            action()
        else:
            host._show_flash("Select shape(s) first", 1000)

    def run_prompted_transform(
        _title: str,
        label: str,
        default: float,
        minimum: float,
        callback: Callable[[float], None],
        *,
        is_length: bool = True,
    ) -> None:
        host._show_hud_prompt(label, default, callback, minimum=minimum, is_length=is_length)

    poly_hit = host._hit_test.entity_at(cx, cy)
    host._add_context_section(
        menu,
        "create",
        section_enabled,
        lambda: host._build_create_shape_menu(menu, section_enabled, poly_hit),
    )
    if poly_hit is not None:
        host._build_entity_actions(menu, poly_hit, section_enabled, run_transform)
        menu.addSeparator()
    # Generated cells are overlay geometry, not entities — pick them from the
    # result layer so removing one never touches the editable document.
    result_cell = host.result_cell_at(cx, cy) if hasattr(host, "result_cell_at") else None
    toggle_cell = getattr(host, "_on_pattern_cell_cutout_toggle", None)
    convert_cell = getattr(host, "_on_result_cell_convert", None)
    if result_cell is not None and callable(toggle_cell):
        cell_menu = menu.addMenu("Remove Cell")
        cell_menu.addAction(
            "This cell only",
            lambda _checked=False, index=result_cell: toggle_cell(index, "instance"),
        )
        cell_menu.addAction(
            "Every matching tile",
            lambda _checked=False, index=result_cell: toggle_cell(index, "repeat"),
        )
    if result_cell is not None and callable(convert_cell):
        # Promoting a generated cell to a real, editable outline used to be a
        # side effect of assigning a zone while previewing. It is an action.
        menu.addAction(
            "Convert to outline",
            lambda _checked=False, index=result_cell: convert_cell(index),
        )
    if result_cell is not None and (callable(toggle_cell) or callable(convert_cell)):
        menu.addSeparator()
    host._add_context_section(
        menu,
        "selected",
        section_enabled,
        lambda: host._build_selection_actions(
            menu, section_enabled, run_transform, run_prompted_transform, cx, cy
        ),
    )
    host._add_context_section(
        menu, "selection", section_enabled, lambda: host._build_selection_tools(menu)
    )
    host._add_context_section(
        menu,
        "share_diagnostics",
        section_enabled,
        lambda: host._build_share_actions(menu, run_transform),
    )
    host._add_context_section(
        menu, "boolean", section_enabled, lambda: host._build_boolean_actions(menu, section_enabled)
    )
    host._add_context_section(
        menu,
        "arrange",
        section_enabled,
        lambda: host._build_arrange_actions(menu, run_transform, run_prompted_transform),
    )
    host._add_context_section(
        menu,
        "transform",
        section_enabled,
        lambda: host._build_transform_actions(menu, run_transform, run_prompted_transform),
    )
    host._add_context_section(
        menu, "text", section_enabled, lambda: host._build_text_actions(menu, cx, cy)
    )
    host._add_context_section(menu, "view", section_enabled, lambda: host._build_view_actions(menu))
    apply_overflow(host, menu)
    menu.popup(host.mapToGlobal(QPoint(int(cx), int(cy))))
    return True


def _dismiss_radial_menu(host: Any) -> bool:
    host._radial_active = False
    host._redraw()
    return True


def apply_overflow(host: Any, menu: QMenu) -> None:
    """Apply action-level customization or the legacy section overflow."""
    if host._context_menu_actions_configured or host._context_menu_item_order:
        apply_item_customization(host, menu)
        return
    tagged_actions = [action for action in menu.actions() if action.property("context_section")]
    grouped: dict[str, list] = {}
    for action in tagged_actions:
        grouped.setdefault(str(action.property("context_section")), []).append(action)
        menu.removeAction(action)
    section_order = list(host._context_menu_section_order)
    section_order.extend(section for section in grouped if section not in section_order)
    for section in section_order:
        if section not in host._context_menu_overflow_sections:
            for action in grouped.get(section, []):
                menu.addAction(action)
    overflow_actions = [
        action
        for section in section_order
        if section in host._context_menu_overflow_sections
        for action in grouped.get(section, [])
    ]
    if overflow_actions:
        for action in overflow_actions:
            menu.removeAction(action)
        more_menu = QMenu("More actions…", menu)
        menu.addMenu(more_menu)
        for action in overflow_actions:
            more_menu.addAction(action)


def apply_item_customization(host: Any, menu: QMenu) -> None:  # noqa: C901
    """Arrange independently configured actions while retaining useful groups."""
    configured = list(host._context_menu_item_order)
    command_by_label = {command.label: command.id for command in canvas_commands.COMMANDS}
    group_labels = {
        "Create shape": "Shapes",
        "Array": "Array",
        "Arrange": "Arrange",
        "Bézier node": "Bézier node",
        "Boolean": "Boolean",
        "Constraints": "Constraints",
        "Corner": "Corner",
        "Mode": "Mode",
        "Move selected to layer": "Move selected to layer",
        "Outline role": "Outline role",
        "Transform": "Transform",
    }

    def action_id(action) -> str | None:
        if not isValid(action):
            return None
        if item := action.property("context_item_id"):
            return str(item)
        if item := action.property("context_item"):
            return f"transform.{item}"
        text = action.text().split("  [", 1)[0]
        if text.startswith("Delete selected"):
            return "edit.delete"
        if text.startswith("Close path"):
            return "context.selection.close_path"
        return command_by_label.get(text) or host._context_static_action_ids.get(text)

    def leaves(parent: QMenu, path: tuple[str, ...] = ()) -> list:
        result = []
        child_menus = {
            child.menuAction().text(): child
            for child in parent.findChildren(
                QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly
            )
            if isValid(child) and isValid(child.menuAction())
        }
        for action in list(parent.actions()):
            if not isValid(action):
                continue
            child = child_menus.get(action.text())
            if child is not None:
                result.extend(leaves(child, (*path, action.text())))
            elif not action.isSeparator():
                group = group_labels.get(path[0], path[0]) if path else None
                result.append((action, group))
        return result

    identified = [
        (action, action_id(action), group) for action, group in leaves(menu) if isValid(action)
    ]
    known: dict[str, list] = {}
    for action, item_id, group in identified:
        if item_id is not None:
            known.setdefault(item_id, []).append((action, group))
    direct = [
        action_group
        for item in configured
        if item in known and item not in host._context_menu_overflow_items
        for action_group in known[item]
    ]
    overflow = [
        action_group
        for item in configured
        if item in known and item in host._context_menu_overflow_items
        for action_group in known[item]
    ]
    original_root_actions = list(menu.actions())

    def place_actions(target: QMenu, entries: list) -> None:
        grouped_menus: dict[str, QMenu] = {}
        for action, group in entries:
            destination = target
            if group is not None:
                grouped = grouped_menus.get(group)
                if grouped is None:
                    grouped = QMenu(group, target)
                    grouped_menus[group] = grouped
                    target.addMenu(grouped)
                destination = grouped
            owner = action.parent()
            action.setParent(destination)
            if isinstance(owner, QMenu):
                owner.removeAction(action)
            destination.addAction(action)

    for action in original_root_actions:
        menu.removeAction(action)
    place_actions(menu, direct)
    if overflow:
        more_menu = QMenu("More actions…", menu)
        place_actions(more_menu, overflow)
        menu.addMenu(more_menu)


def add_context_section(
    menu: QMenu,
    section: str,
    section_enabled: Callable[[str], bool],
    build: Callable[[], None],
) -> None:
    """Build, tag, and conditionally remove one context-menu section."""
    start = len(menu.actions())
    build()
    for action in menu.actions()[start:]:
        action.setProperty("context_section", section)
        if not section_enabled(section):
            menu.removeAction(action)
