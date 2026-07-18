"""Bridge a reactive CanvasModel to command-oriented document orchestration."""

from __future__ import annotations

from copy import deepcopy

from src.app.services.document_service import DocumentEvent, DocumentService
from src.backend.model.commands import (
    Command,
    CreateCommand,
    DeleteCommand,
    DocumentSnapshot,
    EntitySnapshot,
    ReplaceDocumentCommand,
    UpdateEntitiesCommand,
)
from src.backend.model.document import CanvasDocument, EntityRecord, OperationResult
from src.ui.canvas.canvas_model import CanvasModel


class CanvasService:
    def __init__(self, model: CanvasModel) -> None:
        self.model = model
        self.documents = DocumentService(model.document)
        self.documents.subscribe(self._document_changed)

    def execute(self, command: Command, *, record: bool = True) -> OperationResult:
        return self.documents.execute(command, record=record)

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

    def _document_changed(self, _event: DocumentEvent) -> None:
        self.model.replace_document(self.documents.document)
