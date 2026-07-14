"""Plain canvas document state, independent of Qt widgets and painting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

Point = tuple[float, float]
EntityId = str


def new_entity_id() -> EntityId:
    return uuid4().hex


@dataclass
class EntityRecord:
    """One document entity: identity, geometry payload, and editor flags."""

    points: list[Point]
    id: EntityId = field(default_factory=new_entity_id)
    kind: str = "polyline"
    meta: dict[str, Any] | None = None
    construction: bool = False
    hidden: bool = False
    locked: bool = False
    group: int | None = None
    layer: str | None = None


@dataclass
class CanvasDocument:
    """Canonical entity and selection state for one canvas."""

    entities: list[EntityRecord] = field(default_factory=list)
    selection: set[int] = field(default_factory=set)
    layer_order: list[str] = field(default_factory=list)
    active_layer: str | None = None
    layer_colors: dict[str, str] = field(default_factory=dict)
    group_labels: dict[int, str] = field(default_factory=dict)
    next_group_id: int = 0

    def replace(self, entities: Iterable[EntityRecord]) -> None:
        self.entities = list(entities)
        self.selection.clear()
        self.ensure_unique_ids()

    def append(self, entity: EntityRecord) -> int:
        if self.index_for_id(entity.id) is not None:
            entity.id = new_entity_id()
        self.entities.append(entity)
        return len(self.entities) - 1

    def ensure_unique_ids(self) -> None:
        seen: set[EntityId] = set()
        for entity in self.entities:
            if not entity.id or entity.id in seen:
                entity.id = new_entity_id()
            seen.add(entity.id)

    def index_for_id(self, entity_id: EntityId) -> int | None:
        return next((i for i, entity in enumerate(self.entities) if entity.id == entity_id), None)

    def entity_for_id(self, entity_id: EntityId) -> EntityRecord | None:
        index = self.index_for_id(entity_id)
        return self.entities[index] if index is not None else None

    def selected_ids(self) -> set[EntityId]:
        return {
            self.entities[index].id for index in self.selection if 0 <= index < len(self.entities)
        }

    def select_ids(self, entity_ids: Iterable[EntityId]) -> None:
        wanted = set(entity_ids)
        self.selection = {
            index for index, entity in enumerate(self.entities) if entity.id in wanted
        }

    def flagged_indices(self, attribute: str) -> set[int]:
        return {
            index
            for index, entity in enumerate(self.entities)
            if bool(getattr(entity, attribute, False))
        }

    def set_flagged_indices(self, attribute: str, indices: Iterable[int]) -> None:
        wanted = {index for index in indices if isinstance(index, int)}
        for index, entity in enumerate(self.entities):
            setattr(entity, attribute, index in wanted)

    def on_active_layer(self, entity: EntityRecord) -> bool:
        return (
            self.active_layer is None or entity.layer is None or entity.layer == self.active_layer
        )

    def entity_selectable(self, index: int) -> bool:
        if not 0 <= index < len(self.entities):
            return False
        entity = self.entities[index]
        return not entity.hidden and self.on_active_layer(entity)

    def drop_inactive_selection(self) -> bool:
        selection = {index for index in self.selection if self.entity_selectable(index)}
        changed = selection != self.selection
        self.selection = selection
        return changed
