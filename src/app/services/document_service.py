"""Single command-oriented mutation boundary for canvas documents."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace

from src.app.services.geometry_service import GeometryService
from src.backend.model.commands import (
    BooleanOpCommand,
    Command,
    CreateCommand,
    DeleteCommand,
    DocumentSnapshot,
    EntityChangeCommand,
    EntitySnapshot,
    ExplodeCommand,
    MergeCommand,
    MoveEntityCommand,
    ReplaceDocumentCommand,
    ResampleCommand,
    RestoreEntitiesCommand,
    SelectCommand,
    SplitCommand,
    TransformCommand,
    UpdateEntitiesCommand,
    freeze_value,
)
from src.backend.model.document import CanvasDocument, EntityRecord, OperationResult, new_entity_id
from src.backend.model.editor_history import CommandStack


@dataclass(frozen=True, slots=True)
class DocumentEvent:
    kind: str
    result: OperationResult
    command: Command


def _snapshot(entity: EntityRecord) -> EntitySnapshot:
    return EntitySnapshot(
        id=entity.id,
        points=tuple(entity.points),
        kind=entity.kind,
        meta=freeze_value(deepcopy(entity.meta)),
        construction=entity.construction,
        hidden=entity.hidden,
        locked=entity.locked,
        group=entity.group,
        layer=entity.layer,
    )


def _entity(snapshot: EntitySnapshot) -> EntityRecord:
    data = snapshot.to_dict()
    return EntityRecord(
        id=snapshot.id,
        points=[tuple(point) for point in data["points"]],
        kind=snapshot.kind,
        meta=deepcopy(data["meta"]),
        construction=snapshot.construction,
        hidden=snapshot.hidden,
        locked=snapshot.locked,
        group=snapshot.group,
        layer=snapshot.layer,
    )


def _document(snapshot: DocumentSnapshot) -> CanvasDocument:
    constraints = GeometryService.constraints_from_dicts(snapshot.constraint_dicts())
    document = CanvasDocument(
        entities=[_entity(entity) for entity in snapshot.entities],
        layer_order=list(snapshot.layer_order),
        active_layer=snapshot.active_layer,
        layer_colors=dict(snapshot.layer_colors),
        group_labels=dict(snapshot.group_labels),
        next_group_id=snapshot.next_group_id,
        constraints=constraints,
        guides=[
            (str(orientation), float(coordinate)) for orientation, coordinate in snapshot.guides
        ],
        dimensions=[deepcopy(item) for item in snapshot.dimension_dicts()],
    )
    document.select_ids(snapshot.selection_ids)
    document.ensure_unique_ids()
    document.reconcile_groups()
    return document


class DocumentService:
    """Validate and execute commands against one canvas document."""

    document: CanvasDocument

    def __init__(self, document: CanvasDocument | None = None) -> None:
        self.document = document or CanvasDocument()
        self.history = CommandStack()
        self._subscribers: list[Callable[[DocumentEvent], None]] = []

    def subscribe(self, callback: Callable[[DocumentEvent], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def replace_document(self, document: CanvasDocument) -> None:
        self.document = document
        self.history.clear()

    def commit_preview(self, before: DocumentSnapshot) -> OperationResult:
        """Commit a transient in-place UI preview as one atomic command."""
        after = DocumentSnapshot.capture(self.document)
        if before == after:
            return OperationResult.unchanged("Document unchanged")
        self.document = _document(before)
        return self.execute(ReplaceDocumentCommand(before_document=before, after_document=after))

    def restore_preview(self, snapshot: DocumentSnapshot) -> None:
        self.document = _document(snapshot)

    def execute(self, command: Command, *, record: bool = True) -> OperationResult:
        # Selection changes don't need document copying - they're just set updates
        if isinstance(command, SelectCommand):
            if not command.previous_ids:
                command = replace(command, previous_ids=tuple(self.document.selected_ids()))
            previous = self.document.selected_ids()
            self.document.select_ids(command.entity_ids)
            changed = self.document.selected_ids() != previous
            event = DocumentEvent(
                "document_changed",
                OperationResult(
                    changed,
                    "Selection changed" if changed else "Selection unchanged",
                    selected_ids=command.entity_ids,
                ),
                command,
            )
            for callback in tuple(self._subscribers):
                callback(event)
            return OperationResult(
                changed,
                "Selection changed" if changed else "Selection unchanged",
                selected_ids=command.entity_ids,
            )
        prepared = self._prepare(command)
        doc_before = self.document
        self.document = deepcopy(doc_before)
        result = self._apply(prepared)
        if not result.changed:
            self.document = doc_before
        if result.changed and record:
            self.history.record(prepared, prepared.reverse())
        if result.changed:
            event = DocumentEvent("document_changed", result, prepared)
            for callback in tuple(self._subscribers):
                callback(event)
        return result

    def undo(self) -> OperationResult:
        pair = self.history.take_undo()
        return (
            OperationResult.unchanged("Nothing to undo")
            if pair is None
            else self.execute(pair[1], record=False)
        )

    def redo(self) -> OperationResult:
        pair = self.history.take_redo()
        return (
            OperationResult.unchanged("Nothing to redo")
            if pair is None
            else self.execute(pair[0], record=False)
        )

    def _selected(self, ids: tuple[str, ...]) -> list[EntityRecord]:
        wanted = set(ids)
        return [entity for entity in self.document.entities if entity.id in wanted]

    def _prepare(self, command: Command) -> Command:
        if isinstance(command, DeleteCommand) and not command.entities:
            wanted = set(command.entity_ids)
            return replace(
                command,
                entities=tuple(_snapshot(entity) for entity in self._selected(command.entity_ids)),
                positions=tuple(
                    index
                    for index, entity in enumerate(self.document.entities)
                    if entity.id in wanted
                ),
            )
        if isinstance(command, SelectCommand) and not command.previous_ids:
            return replace(command, previous_ids=tuple(self.document.selected_ids()))
        if isinstance(command, EntityChangeCommand) and not command.before:
            command = replace(
                command,
                before=tuple(_snapshot(entity) for entity in self._selected(command.entity_ids)),
            )
        if not isinstance(command, EntityChangeCommand) or command.after:
            return command
        sources = self._selected(command.entity_ids)
        snapshots: tuple[EntitySnapshot, ...] = ()
        if isinstance(command, SplitCommand):
            entity_ids = [entity.id for entity in sources]
            result = GeometryService.split_paths(
                [entity.points for entity in sources], list(command.cutter), entity_ids
            )
            changed_source_ids = {path.source_id for path in result.paths if path.changed}
            if not result.changed or not changed_source_ids:
                return replace(command, before=(), after=())
            output = []
            emitted: dict[str, int] = {}
            for path in result.paths:
                if path.source_id not in changed_source_ids:
                    continue
                source_entity = next((s for s in sources if s.id == path.source_id), None)
                if source_entity is None:
                    continue
                count = emitted.get(path.source_id, 0)
                emitted[path.source_id] = count + 1
                output.append(
                    _snapshot(
                        EntityRecord(
                            id=source_entity.id if count == 0 else new_entity_id(),
                            points=path.points,
                            kind="polyline" if path.changed else source_entity.kind,
                            meta=None if path.changed else deepcopy(source_entity.meta),
                            construction=source_entity.construction,
                            hidden=source_entity.hidden,
                            locked=source_entity.locked,
                            layer=source_entity.layer,
                        )
                    )
                )
            changed_sources = tuple(s for s in sources if s.id in changed_source_ids)
            command = replace(
                command,
                entity_ids=tuple(source.id for source in changed_sources),
                before=tuple(_snapshot(source) for source in changed_sources),
            )
            snapshots = tuple(output)
        elif isinstance(command, BooleanOpCommand):
            snapshots = tuple(
                _snapshot(EntityRecord(points=ring))
                for ring in GeometryService.boolean_polylines(
                    [entity.points for entity in sources], command.operation
                )
            )
        elif isinstance(command, ResampleCommand):
            output = []
            for entity in sources:
                points = (
                    GeometryService.resample_by_count(entity.points, int(command.value))
                    if command.by_count
                    else GeometryService.resample_by_spacing(entity.points, command.value)
                )
                copy = deepcopy(entity)
                copy.points, copy.kind, copy.meta = points, "polyline", None
                output.append(_snapshot(copy))
            snapshots = tuple(output)
        elif isinstance(command, MergeCommand):
            from src.app.services.geometry_service import PathInput

            merged = GeometryService.merge_paths_with_construction(
                [PathInput(entity.points, entity.construction) for entity in sources],
                tolerance=0.01,
            )
            snapshots = tuple(
                _snapshot(
                    EntityRecord(
                        points=item.points,
                        id=sources[0].id if index == 0 else new_entity_id(),
                        kind="polyline",
                        construction=item.construction,
                        hidden=sources[0].hidden,
                        locked=sources[0].locked,
                        layer=sources[0].layer,
                    )
                )
                for index, item in enumerate(merged)
            )
        elif isinstance(command, ExplodeCommand):
            snapshots = tuple(
                _snapshot(
                    EntityRecord(
                        points=segment,
                        kind="line",
                        construction=entity.construction,
                        hidden=entity.hidden,
                        locked=entity.locked,
                        layer=entity.layer,
                    )
                )
                for entity in sources
                for segment in GeometryService.explode_path(entity.points)
            )
        return replace(command, after=snapshots)

    def _replace_entities(
        self, ids: tuple[str, ...], replacements: tuple[EntitySnapshot, ...]
    ) -> None:
        wanted = set(ids)
        retained_selection = self.document.selected_ids()
        first = next(
            (index for index, entity in enumerate(self.document.entities) if entity.id in wanted),
            len(self.document.entities),
        )
        retained = [entity for entity in self.document.entities if entity.id not in wanted]
        retained[first:first] = [_entity(snapshot) for snapshot in replacements]
        self.document.entities = retained
        self.document.ensure_unique_ids()
        self.document.select_ids(retained_selection)

    def _apply(self, command: Command) -> OperationResult:
        if isinstance(command, ReplaceDocumentCommand):
            if DocumentSnapshot.capture(self.document) == command.after_document:
                return OperationResult.unchanged("Document unchanged")
            self.document = _document(command.after_document)
            return OperationResult(
                True,
                "Document updated",
                selected_ids=command.after_document.selection_ids,
            )
        if isinstance(command, RestoreEntitiesCommand):
            self._replace_entities(command.entity_ids, command.after)
            restored_ids = tuple(item.id for item in command.after)
            self.document.select_ids(restored_ids)
            return OperationResult(
                True,
                "Restored entities",
                selected_ids=restored_ids,
            )
        if isinstance(command, UpdateEntitiesCommand):
            self._replace_entities(command.entity_ids, command.after)
            selected = self.document.selected_ids()
            selected_ids = tuple(
                entity.id for entity in self.document.entities if entity.id in selected
            )
            return OperationResult(
                bool(command.after),
                "Updated entities",
                selected_ids=selected_ids,
            )
        if isinstance(command, SelectCommand):
            previous = self.document.selected_ids()
            self.document.select_ids(command.entity_ids)
            changed = self.document.selected_ids() != previous
            return OperationResult(
                changed,
                "Selection changed" if changed else "Selection unchanged",
                selected_ids=command.entity_ids,
            )
        if isinstance(command, CreateCommand):
            if command.positions and len(command.positions) == len(command.entities):
                for position, snapshot in sorted(zip(command.positions, command.entities)):
                    self.document.entities.insert(
                        max(0, min(position, len(self.document.entities))), _entity(snapshot)
                    )
                self.document.ensure_unique_ids()
            else:
                for snapshot in command.entities:
                    self.document.append(_entity(snapshot))
            created_ids = tuple(item.id for item in command.entities)
            self.document.select_ids(created_ids)
            return OperationResult(
                True,
                "Created",
                created_ids=created_ids,
                selected_ids=created_ids,
            )
        if isinstance(command, DeleteCommand):
            before = len(self.document.entities)
            self._replace_entities(command.entity_ids, ())
            changed = len(self.document.entities) != before
            return OperationResult(
                changed, "Deleted" if changed else "Nothing deleted", removed_ids=command.entity_ids
            )
        if isinstance(command, MoveEntityCommand):
            for entity in self._selected(command.entity_ids):
                entity.points = GeometryService.translate_points(
                    entity.points, command.dx, command.dy
                )
                GeometryService.transform_entity_metadata(
                    entity,
                    transform="translate",
                    center=(0.0, 0.0),
                    dx=command.dx,
                    dy=command.dy,
                )
            return OperationResult(
                bool(command.entity_ids), "Moved", selected_ids=command.entity_ids
            )
        if isinstance(command, TransformCommand):
            for entity in self._selected(command.entity_ids):
                if command.operation == "translate":
                    entity.points = GeometryService.translate_points(
                        entity.points, command.x, command.y
                    )
                    GeometryService.transform_entity_metadata(
                        entity,
                        transform="translate",
                        center=command.origin,
                        dx=command.x,
                        dy=command.y,
                    )
                elif command.operation == "rotate":
                    entity.points = GeometryService.rotate_points(
                        entity.points, command.origin, command.x
                    )
                    GeometryService.transform_entity_metadata(
                        entity,
                        transform="rotate",
                        center=command.origin,
                        angle_degrees=command.x,
                    )
                elif command.operation == "scale":
                    cx, cy = command.origin
                    entity.points = [
                        (cx + (x - cx) * command.x, cy + (y - cy) * command.y)
                        for x, y in entity.points
                    ]
                    if abs(command.x - command.y) <= 1e-12:
                        GeometryService.transform_entity_metadata(
                            entity,
                            transform="scale",
                            center=command.origin,
                            factor=command.x,
                        )
                    else:
                        entity.kind = "polyline"
                        entity.meta = None
                elif command.operation == "mirror":
                    axis = "horizontal" if command.x else "vertical"
                    entity.points = GeometryService.mirror_points(
                        entity.points, command.origin, axis
                    )
                    GeometryService.transform_entity_metadata(
                        entity,
                        transform="mirror",
                        center=command.origin,
                        axis=axis,
                    )
                else:
                    raise ValueError(f"Unknown transform: {command.operation}")
            return OperationResult(
                bool(command.entity_ids), "Transformed", selected_ids=command.entity_ids
            )
        if isinstance(command, SplitCommand):
            if not command.after:
                return OperationResult.unchanged("Knife did not cross any geometry")
            self._replace_entities(command.entity_ids, command.after)
            selected_ids = tuple(item.id for item in command.after)
            self.document.select_ids(selected_ids)
            return OperationResult(bool(command.after), "Split", selected_ids=selected_ids)
        if isinstance(command, BooleanOpCommand):
            self._replace_entities(command.entity_ids, command.after)
            selected_ids = tuple(item.id for item in command.after)
            self.document.select_ids(selected_ids)
            return OperationResult(
                bool(command.after),
                f"Boolean {command.operation}",
                created_ids=selected_ids,
                removed_ids=command.entity_ids,
                selected_ids=selected_ids,
            )
        if isinstance(command, ResampleCommand):
            self._replace_entities(command.entity_ids, command.after)
            self.document.select_ids(command.entity_ids)
            return OperationResult(
                bool(command.after), "Resampled", selected_ids=command.entity_ids
            )
        if isinstance(command, MergeCommand):
            self._replace_entities(command.entity_ids, command.after)
            selected_ids = tuple(item.id for item in command.after)
            self.document.select_ids(selected_ids)
            return OperationResult(bool(command.after), "Merged", selected_ids=selected_ids)
        if isinstance(command, ExplodeCommand):
            self._replace_entities(command.entity_ids, command.after)
            selected_ids = tuple(item.id for item in command.after)
            self.document.select_ids(selected_ids)
            return OperationResult(
                bool(command.after),
                "Exploded",
                selected_ids=selected_ids,
            )
        raise TypeError(f"Unsupported command: {type(command).__name__}")
