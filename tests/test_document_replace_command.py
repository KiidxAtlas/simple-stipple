"""ReplaceDocumentCommand — the update_document() path used by set_guides,
set_dimensions, append_entity, and friends. DocumentService.execute() skips
its usual pre-apply deepcopy for this command type (it rebuilds self.document
wholesale from a snapshot instead), so this locks in that the skip is safe:
the mutation still applies, undo still restores exactly, and entities
untouched by the mutation survive the round trip.
"""

from __future__ import annotations

from simple_stipple.canvas.objects import CanvasService
from simple_stipple.core.document.model import CanvasDocument, EntityRecord


class _Model:
    def __init__(self, document: CanvasDocument) -> None:
        self.document = document

    def replace_document(self, document: CanvasDocument) -> None:
        self.document = document


def test_update_document_applies_mutation_and_undo_restores_prior_state() -> None:
    doc = CanvasDocument()
    doc.append(EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], kind="line"))
    service = CanvasService(_Model(doc))

    result = service.set_guides([("x", 5.0)])
    assert result.changed
    assert service.documents.document.guides == [("x", 5.0)]

    undo_result = service.undo()
    assert undo_result.changed
    assert service.documents.document.guides == []
    entities = service.documents.document.entities
    assert len(entities) == 1
    assert entities[0].points == [(0.0, 0.0), (1.0, 0.0)]


def test_update_document_no_op_mutation_reports_unchanged() -> None:
    service = CanvasService(_Model(CanvasDocument()))
    result = service.set_guides([])
    assert not result.changed
    assert service.documents.document.guides == []
