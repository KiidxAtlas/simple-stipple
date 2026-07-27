"""Entity grouping operations composed by the canvas view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol


class GroupingHost(Protocol):
    _canvas_service: Any
    _entities: list
    _entities_by_id: dict
    _sel: set[str]
    _next_group_id: int
    _group_labels: dict[int, str]
    _document: Any

    def _show_flash(self, text: str, ms: int) -> None: ...
    def _notify(self) -> None: ...
    def _fire_poly_change(self) -> None: ...


class GroupingService:
    def __init__(self, host: GroupingHost) -> None:
        self._host = host

    def group_of(self, entity_id: str) -> int | None:
        host = self._host
        entity = host._entities_by_id.get(entity_id)
        return entity.group if entity is not None else None

    def group_map(self) -> dict[str, int | None]:
        return {
            entity.id: entity.group
            for entity in self._host._entities_by_id.values()
            if entity.group is not None
        }

    def group_selected(self) -> None:
        if len(self._host._sel) < 2:
            self._host._show_flash("Select 2+ shapes to group", 1000)
            return
        self.group_by_ids(list(self._host._sel), select=False)

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
        self.ungroup_by_ids(list(self._host._sel))

    def group_by_ids(self, entity_ids: list[str], *, select: bool = True) -> int:
        host = self._host
        valid = [eid for eid in entity_ids if host._document.entity_for_id(eid) is not None]
        if len(valid) < 2:
            host._show_flash("Select 2+ shapes to group", 1000)
            return 0
        group_id = host._next_group_id
        candidates = [deepcopy(host._document.entity_for_id(eid)) for eid in valid]
        for entity in candidates:
            if entity is not None:
                entity.group = group_id
        host._canvas_service.update_entities(candidates)
        if select:
            host._sel = set(valid)
        host._show_flash(f"Grouped {len(valid)} shapes", 900)
        self._changed()
        return len(valid)

    def ungroup_by_ids(self, entity_ids: list[str]) -> int:
        host = self._host
        valid = [eid for eid in entity_ids if host._document.entity_for_id(eid) is not None]
        groups = {
            entity.group
            for eid in valid
            if (entity := host._document.entity_for_id(eid)) is not None
            and entity.group is not None
        }
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

    def group_entities(self, entity_ids: list[str], *, select: bool = True) -> int:
        return self.group_by_ids(entity_ids, select=select)

    def ungroup_entities(self, entity_ids: list[str]) -> int:
        return self.ungroup_by_ids(entity_ids)

    def _changed(self) -> None:
        self._host._notify()
        self._host._fire_poly_change()
