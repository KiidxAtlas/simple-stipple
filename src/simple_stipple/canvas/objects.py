"""Qt-backed canvas model and adapter around the document command service."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
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
from simple_stipple.core.document.identity import EntityId
from simple_stipple.core.document.model import CanvasDocument, EntityRecord, OperationResult
from simple_stipple.core.document.service import DocumentEvent, DocumentService


@dataclass(frozen=True, slots=True)
class CanvasViewportState:
    """Immutable coordinate transform for one canvas paint/input frame."""

    scale: float
    origin_x: float
    origin_y: float
    width: int
    height: int

    def canvas_to_world(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.origin_x) / self.scale, (self.origin_y - y) / self.scale)

    def world_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin_x + x * self.scale, self.origin_y - y * self.scale)


@dataclass(frozen=True, slots=True)
class CanvasInteractionState:
    """Immutable active-tool state sampled for a canvas frame."""

    mode: str = "select"
    draw_primitive: str = "polyline"
    draw_points: tuple[tuple[float, float], ...] = ()
    cursor_world: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class CanvasFrameState:
    """Read-only document and viewport state consumed during one frame."""

    document: CanvasDocument
    viewport: CanvasViewportState
    selection: frozenset[str] = field(default_factory=frozenset)
    interaction: CanvasInteractionState = field(default_factory=CanvasInteractionState)


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


class CanvasViewPort(Protocol):
    """Stable public canvas surface for shared UI modules.

    The concrete ``CanvasView`` retains compatibility adapters, while
    renderers, toolbars, and feature runtimes can depend on this contract
    instead of probing private attributes.
    """

    @property
    def document(self) -> CanvasDocument: ...

    def get_mode(self) -> str: ...
    def get_precision_state(self) -> dict[str, object]: ...
    def get_polylines_state(self) -> list[list[tuple[float, float]]]: ...
    def get_selected_ids(self) -> list[str]: ...
    def get_status_summary(self) -> dict[str, object]: ...
    def get_view_state(self) -> dict[str, object]: ...
    def get_zoom_percent(self) -> float: ...
    def get_cursor_world_pos(self) -> tuple[float, float] | None: ...
    def set_mode(self, mode: str) -> None: ...
    def set_grid_spacing(self, spacing: float) -> None: ...
    def set_grid_snap(self, enabled: bool) -> None: ...
    def set_selection(self, entity_ids: list[EntityId]) -> None: ...
    def fit_selection(self) -> bool: ...
    def set_construction_mode(self, enabled: bool) -> None: ...


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
