"""Editor entity organization: layers, groups, and their document mutations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol
from typing import Protocol
from PySide6.QtCore import QObject, Signal
from simple_stipple.core.document.commands import (
    Command,
    CreateCommand,
    DeleteCommand,
    DocumentSnapshot,
    EntitySnapshot,
    ReplaceDocumentCommand,
    UpdateEntitiesCommand,
)
from simple_stipple.core.document.model import CanvasDocument, EntityRecord, OperationResult
from simple_stipple.core.document.service import DocumentEvent, DocumentService


class LayerService:
    def __init__(self, host) -> None:
        self._host = host

    @property
    def active_layer(self) -> str | None:
        return self._host._active_layer

    def names(self) -> list[str]:
        names = list(self._host._layer_order)
        for entity in self._host._entities:
            if entity.layer is not None and entity.layer not in names:
                names.append(entity.layer)
        return names

    def set_model(self, order: list[str], active: str | None) -> None:
        host = self._host
        host._layer_order = [str(name) for name in order if str(name)]
        if active is not None and str(active) not in host._layer_order:
            host._layer_order.append(str(active))
        host._active_layer = str(active) if active is not None else None
        if host._active_layer is not None:
            for entity in host._entities:
                if entity.layer is None:
                    entity.layer = host._active_layer
        self.drop_inactive_selection()
        host._redraw()

    def set_active(self, name: str) -> None:
        host = self._host
        name = str(name)
        if host._active_layer == name:
            return

        def mutate(document) -> None:
            if name not in document.layer_order:
                document.layer_order.append(name)
            document.active_layer = name
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._reset_edit_interaction_state()
        host._redraw()
        host._notify()

    def add(self, name: str, *, activate: bool = False) -> None:
        host = self._host
        name = str(name)
        if name in host._layer_order and not activate:
            return

        def mutate(document) -> None:
            if name not in document.layer_order:
                document.layer_order.append(name)
            if activate:
                document.active_layer = name
                document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()

    def rename(self, old: str, new: str) -> None:
        host = self._host
        old, new = str(old), str(new).strip()
        if not new or old == new or new in host._layer_order:
            return

        def mutate(document) -> None:
            document.layer_order = [new if name == old else name for name in document.layer_order]
            for entity in document.entities:
                if entity.layer == old:
                    entity.layer = new
            if document.active_layer == old:
                document.active_layer = new
            color = document.layer_colors.pop(old, None)
            if color is not None:
                document.layer_colors[new] = color

        host._canvas_service.update_document(mutate)
        host._redraw()

    def delete(self, name: str) -> None:
        host = self._host
        name = str(name)
        dropped = sum(entity.layer == name for entity in host._entities)

        def mutate(document) -> None:
            document.entities = [entity for entity in document.entities if entity.layer != name]
            document.layer_order = [layer for layer in document.layer_order if layer != name]
            if not document.layer_order:
                document.layer_order = [
                    document.active_layer
                    if document.active_layer is not None and document.active_layer != name
                    else "Layer 1"
                ]
            if document.active_layer == name:
                document.active_layer = document.layer_order[0]
            document.layer_colors.pop(name, None)
            document.selection.clear()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        if dropped:
            host._fire_poly_change()

    def color(self, name: str) -> str | None:
        return self._host._layer_colors.get(str(name))

    def set_color(self, name: str, color: str | None) -> None:
        name = str(name)

        def mutate(document) -> None:
            if color is None:
                document.layer_colors.pop(name, None)
            else:
                document.layer_colors[name] = str(color)

        self._host._canvas_service.update_document(mutate)
        self._host._redraw()

    def consolidate(self, sources: list[str], target: str) -> int:
        host = self._host
        target = str(target)
        source_set = {str(source) for source in sources if str(source) and str(source) != target}
        if not source_set:
            return 0
        moved = sum(entity.layer in source_set for entity in host._entities)

        def mutate(document) -> None:
            if target not in document.layer_order:
                document.layer_order.append(target)
            for entity in document.entities:
                if entity.layer in source_set:
                    entity.layer = target
            document.layer_order = [
                name for name in document.layer_order if name not in source_set
            ] or [target]
            if document.active_layer in source_set:
                document.active_layer = target
            for name in source_set:
                document.layer_colors.pop(name, None)
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        if moved:
            host._fire_poly_change()
        return moved

    def move(self, name: str, new_index: int) -> None:
        names = self.names()
        if str(name) not in names:
            return
        names.remove(str(name))
        names.insert(max(0, min(int(new_index), len(names))), str(name))
        self._host._canvas_service.update_document(
            lambda document: setattr(document, "layer_order", names)
        )
        self._host._redraw()

    def move_entities(self, entity_ids: list[str], layer: str) -> int:
        host = self._host
        layer = str(layer)
        moved = [
            eid
            for eid in entity_ids
            if (entity := host._entities_by_id.get(eid)) is not None and entity.layer != layer
        ]
        if not moved:
            return 0

        def mutate(document) -> None:
            if layer not in document.layer_order:
                document.layer_order.append(layer)
            for entity in document.entities:
                if entity.id in moved:
                    entity.layer = layer
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        host._fire_poly_change()
        return len(moved)

    def on_active(self, entity) -> bool:
        return self._host._document.on_active_layer(entity)

    def selectable(self, entity_id: str) -> bool:
        return self._host._document.entity_selectable_by_id(entity_id)

    def noninteractive_indices(self) -> set[str]:
        return {entity.id for entity in self._host._entities if entity.hidden}

    def drop_inactive_selection(self) -> None:
        if self._host._document.drop_inactive_selection():
            self._host._notify()


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


class CanvasModel(QObject):
    """Owns canvas document state and publishes coarse UI invalidations."""

    document_replaced = Signal()
    geometry_changed = Signal()
    selection_changed = Signal(int)

    def __init__(
        self, document: CanvasDocument | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._document = document or CanvasDocument()

    @property
    def document(self) -> CanvasDocument:
        return self._document

    def replace_document(self, document: CanvasDocument) -> None:
        self._document = document
        self.document_replaced.emit()
        self.geometry_changed.emit()
        self.selection_changed.emit(len(document.selection))

    def notify_geometry_changed(self) -> None:
        self.geometry_changed.emit()

    def notify_selection_changed(self) -> None:
        self.selection_changed.emit(len(self._document.selection))


class CanvasModelPort(Protocol):
    """UI-independent interface required by canvas orchestration."""

    document: CanvasDocument

    def replace_document(self, document: CanvasDocument) -> None: ...


class CanvasService:
    def __init__(self, model: CanvasModelPort) -> None:
        self.model = model
        self.documents = DocumentService(model.document)
        self.documents.subscribe(self._document_changed)

    def execute(self, command: Command, *, record: bool = True) -> OperationResult:
        return self.documents.execute(command, record=record)

    def undo_depth(self) -> int:
        return self.documents.history.undo_depth()

    def undo(self) -> OperationResult:
        return self.documents.undo()

    def redo(self) -> OperationResult:
        return self.documents.redo()

    def replace_document(self, document: CanvasDocument) -> None:
        self.documents.replace_document(document)
        self.model.replace_document(document)

    def update_entities(
        self,
        entities: list[EntityRecord],
        *,
        source_ids: tuple[str, ...] | None = None,
        record: bool = True,
    ) -> OperationResult:
        """Commit edited entity copies through the document command boundary."""
        command = UpdateEntitiesCommand(
            entity_ids=source_ids or tuple(entity.id for entity in entities),
            after=tuple(EntitySnapshot.capture(entity) for entity in entities),
        )
        return self.execute(command, record=record)

    def create_entities(
        self, entities: list[EntityRecord], *, record: bool = True
    ) -> OperationResult:
        return self.execute(
            CreateCommand(entities=tuple(EntitySnapshot.capture(entity) for entity in entities)),
            record=record,
        )

    def delete_entities(
        self, entity_ids: tuple[str, ...], *, record: bool = True
    ) -> OperationResult:
        return self.execute(DeleteCommand(entity_ids=entity_ids), record=record)

    def update_document(self, mutate, *, record: bool = True) -> OperationResult:
        """Apply one aggregate-level mutation as an atomic reversible command."""
        candidate = deepcopy(self.documents.document)
        mutate(candidate)
        return self.execute(
            ReplaceDocumentCommand(
                before_document=DocumentSnapshot.capture(self.documents.document),
                after_document=DocumentSnapshot.capture(candidate),
            ),
            record=record,
        )

    def begin_preview(self) -> DocumentSnapshot:
        return DocumentSnapshot.capture(self.documents.document)

    def commit_preview(self, before: DocumentSnapshot | None) -> OperationResult:
        if before is None:
            return OperationResult.unchanged("No preview transaction")
        return self.documents.commit_preview(before)

    def cancel_preview(self, before: DocumentSnapshot | None) -> None:
        if before is None:
            return
        self.documents.restore_preview(before)
        self.model.replace_document(self.documents.document)

    def set_guides(
        self, guides: list[tuple[str, float]], *, record: bool = True
    ) -> OperationResult:
        """Replace all guides atomically."""
        return self.update_document(lambda doc: setattr(doc, "guides", list(guides)), record=record)

    def set_dimensions(self, dimensions: list[dict], *, record: bool = True) -> OperationResult:
        """Replace all dimensions atomically."""
        return self.update_document(
            lambda doc: setattr(doc, "dimensions", list(dimensions)), record=record
        )

    def set_active_layer(self, layer: str | None, *, record: bool = True) -> OperationResult:
        """Set the active layer atomically."""
        return self.update_document(lambda doc: setattr(doc, "active_layer", layer), record=record)

    def set_flagged_ids(self, attr: str, entity_ids, *, record: bool = True) -> OperationResult:
        """Set boolean ``attr`` to exactly ``entity_ids`` (wholesale assignment)."""
        return self.update_document(
            lambda doc: doc.set_flagged_ids(attr, entity_ids), record=record
        )

    def append_entity(self, entity: EntityRecord, *, record: bool = True) -> OperationResult:
        """Append a single entity to the document."""
        return self.update_document(lambda doc: doc.append(entity), record=record)

    def _document_changed(self, _event: DocumentEvent) -> None:
        self.model.replace_document(self.documents.document)
