"""Process-wide canvas clipboard and duplication operations."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from src.backend.model.commands import MoveEntityCommand
from src.backend.model.document import EntityRecord

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
        self.records = [
            {
                "polyline": list(host._entities[index].points),
                "kind": host._entities[index].kind,
                "meta": deepcopy(host._entities[index].meta),
                "construction": host._entities[index].construction,
                "group": host._entities[index].group,
            }
            for index in sorted(host._sel)
            if index < len(host._entities)
        ]

    def paste_records(
        self, dx: float, dy: float | None = None, *, record_history: bool = True
    ) -> list[int]:
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
        wanted = set(result.created_ids)
        return [index for index, entity in enumerate(host._entities) if entity.id in wanted]

    def paste(self, offset: float = 1.0) -> None:
        if not self.records:
            return
        self._finish(self.paste_records(offset))

    def duplicate(self) -> None:
        if self._host._sel:
            self.copy_selected()
            self.paste()

    def duplicate_with_offset(self) -> None:
        host = self._host
        points = [
            point
            for index in host._sel
            if index < len(host._entities)
            for point in host._entities[index].points
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

    def paste_multiple(
        self, distance: float, count: int, direction: tuple[float, float] = (1.0, 0.0)
    ) -> None:
        if not self.records or count < 1:
            return
        before = self._host._canvas_service.begin_preview()
        dx, dy = direction
        created = [
            index
            for copy in range(1, count + 1)
            for index in self.paste_records(
                dx * distance * copy, dy * distance * copy, record_history=False
            )
        ]
        self._host._canvas_service.commit_preview(before)
        self._finish(created)

    def prompt_multi_paste(self) -> None:
        if not self.records:
            return
        from PySide6.QtWidgets import QDialog

        from src.ui.widgets.dialogs.multi_paste_dialog import MultiPasteDialog

        dialog = MultiPasteDialog(self._host)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            distance, count, direction = dialog.values()
            self.paste_multiple(distance, count, direction)

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
            index
            for row in range(rows)
            for column in range(columns)
            if row or column
            for index in self.paste_records(
                column * spacing, row * spacing, record_history=False
            )
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
            index
            for copy in range(1, count)
            for index in self.paste_records(
                radius * math.cos(2.0 * math.pi * copy / count),
                radius * math.sin(2.0 * math.pi * copy / count),
                record_history=False,
            )
        ]
        host._canvas_service.commit_preview(before)
        self._finish(created)
        host._set_repeat_action(f"Radial array ×{count}", lambda: self.apply_radial(count, radius))
        return True

    def prompt_along_path(self) -> None:
        host = self._host
        selected = [index for index in sorted(host._sel) if index < len(host._entities)]
        if len(selected) < 2:
            host._show_flash("Select source shape(s) and one path", 1400)
            return

        def length(index: int) -> float:
            return sum(
                math.dist(a, b)
                for a, b in zip(host._entities[index].points, host._entities[index].points[1:])
            )

        path_index = max(selected, key=length)
        path = list(host._entities[path_index].points)
        sources = [index for index in selected if index != path_index]
        if len(path) < 2 or length(path_index) <= 1e-9 or not sources:
            host._show_flash("Selected path has no usable length", 1400)
            return

        def apply(value: float) -> None:
            count = max(2, int(round(value)))
            segments = [(a, b, math.dist(a, b)) for a, b in zip(path, path[1:])]
            total = sum(segment[2] for segment in segments)
            source_points = [point for index in sources for point in host._entities[index].points]
            origin = (
                (min(p[0] for p in source_points) + max(p[0] for p in source_points)) / 2.0,
                (min(p[1] for p in source_points) + max(p[1] for p in source_points)) / 2.0,
            )
            selection = set(host._sel)
            host._sel = set(sources)
            self.copy_selected()
            host._sel = selection
            created: list[int] = []
            for copy in range(count):
                distance = total * copy / (count - 1)
                walked = 0.0
                target = path[-1]
                for start, end, segment_length in segments:
                    if walked + segment_length >= distance:
                        ratio = (distance - walked) / segment_length if segment_length else 0.0
                        target = (
                            start[0] + (end[0] - start[0]) * ratio,
                            start[1] + (end[1] - start[1]) * ratio,
                        )
                        break
                    walked += segment_length
                created.extend(self.paste_records(target[0] - origin[0], target[1] - origin[1]))
            self._finish(created)
            host._show_flash(f"Array along path: {count} positions", 1200)

        host._show_hud_prompt("Copies along path", 6.0, apply, minimum=2, is_length=False)

    def cut_selected(self) -> None:
        host = self._host
        dropped = {index for index in host._sel if not host._is_locked(index)}
        if not dropped:
            return
        self.copy_selected()
        entity_ids = tuple(host._entities[index].id for index in sorted(dropped))
        host._canvas_service.delete_entities(entity_ids)
        self._changed()

    def nudge_selected(self, dx: float, dy: float) -> None:
        host = self._host
        indices = [index for index in host._sel if not host._is_locked(index)]
        if not indices:
            return
        entity_ids = tuple(host._entities[index].id for index in indices)
        host._canvas_service.execute(MoveEntityCommand(entity_ids=entity_ids, dx=dx, dy=dy))
        self._changed()

    def _finish(self, indices: list[int]) -> None:
        self._host._sel = set(indices)
        self._changed()

    def _changed(self) -> None:
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
