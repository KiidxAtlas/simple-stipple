"""Process-wide canvas clipboard and duplication operations."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

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
