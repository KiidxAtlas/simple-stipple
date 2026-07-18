"""Entity grouping operations composed by the canvas view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol


class GroupingHost(Protocol):
    _canvas_service: Any
    _entities: list
    _sel: set[int]
    _next_group_id: int
    _group_labels: dict[int, str]

    def _show_flash(self, text: str, ms: int) -> None: ...
    def _notify(self) -> None: ...
    def _fire_poly_change(self) -> None: ...


class GroupingService:
    def __init__(self, host: GroupingHost) -> None:
        self._host = host

    def group_of(self, index: int) -> int | None:
        host = self._host
        return host._entities[index].group if 0 <= index < len(host._entities) else None

    def group_map(self) -> dict[int, int]:
        return {
            index: entity.group
            for index, entity in enumerate(self._host._entities)
            if entity.group is not None
        }

    def group_selected(self) -> None:
        if len(self._host._sel) < 2:
            self._host._show_flash("Select 2+ shapes to group", 1000)
            return
        self.group_indices(list(self._host._sel), select=False)

    def set_label(self, group_id: int, label: str) -> None:
        clean = str(label).strip()
        group_id = int(group_id)

        def update_label(document) -> None:
            if clean:
                document.group_labels[group_id] = clean
            else:
                document.group_labels.pop(group_id, None)

        self._host._canvas_service.update_document(update_label)
        self._changed()

    def ungroup_selected(self) -> None:
        self.ungroup_indices(list(self._host._sel))

    def group_indices(self, indices: list[int], *, select: bool = True) -> int:
        host = self._host
        valid = [index for index in indices if 0 <= index < len(host._entities)]
        if len(valid) < 2:
            host._show_flash("Select 2+ shapes to group", 1000)
            return 0
        group_id = host._next_group_id
        candidates = [deepcopy(host._entities[index]) for index in valid]
        for entity in candidates:
            entity.group = group_id
        host._canvas_service.update_entities(candidates)
        if select:
            host._sel = set(valid)
        host._show_flash(f"Grouped {len(valid)} shapes", 900)
        self._changed()
        return len(valid)

    def ungroup_indices(self, indices: list[int]) -> int:
        host = self._host
        valid = [index for index in indices if 0 <= index < len(host._entities)]
        groups = {group_id for index in valid if (group_id := self.group_of(index)) is not None}
        if not groups:
            host._show_flash("Shapes are not grouped", 700)
            return 0
        candidates = [deepcopy(entity) for entity in host._entities if entity.group in groups]
        for entity in candidates:
            entity.group = None
        host._canvas_service.update_entities(candidates)
        host._show_flash("Ungrouped", 700)
        self._changed()
        return len(valid)

    def _changed(self) -> None:
        self._host._notify()
        self._host._fire_poly_change()
