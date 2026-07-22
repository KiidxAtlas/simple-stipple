"""Guides and dimensions are document state and must survive the snapshot path.

They were moved off the canvas view onto the document so they share one undo
stack with geometry. That only holds if ``DocumentSnapshot`` captures and
restores them losslessly (the same path ``ReplaceDocumentCommand`` uses), so
this pins the round-trip.
"""

from src.app.services.document_service import _document
from src.backend.model.commands import DocumentSnapshot
from src.backend.model.document import CanvasDocument, EntityRecord


def _document_with_annotations():
    return CanvasDocument(
        entities=[EntityRecord(points=[(0.0, 0.0), (1.0, 1.0)])],
        guides=[("h", 12.5), ("v", -3.0)],
        dimensions=[
            {"kind": "segment", "p1": (0.0, 0.0), "p2": (10.0, 0.0), "precision": 2},
        ],
    )


def test_snapshot_capture_restore_preserves_annotations():
    original = _document_with_annotations()
    restored = _document(DocumentSnapshot.capture(original))

    assert restored.guides == [("h", 12.5), ("v", -3.0)]
    assert len(restored.dimensions) == 1
    dim = restored.dimensions[0]
    assert dim["kind"] == "segment"
    assert dim["precision"] == 2
    # Points survive value-wise (tuples thaw to lists through the frozen form).
    assert list(dim["p1"]) == [0.0, 0.0]
    assert list(dim["p2"]) == [10.0, 0.0]


def test_snapshot_json_round_trip_preserves_annotations():
    original = _document_with_annotations()
    snapshot = DocumentSnapshot.capture(original)
    rebuilt = DocumentSnapshot.from_dict(snapshot.to_dict())

    assert rebuilt.guides == (("h", 12.5), ("v", -3.0))
    assert rebuilt.dimension_dicts()[0]["kind"] == "segment"


def test_empty_document_has_no_annotations():
    restored = _document(DocumentSnapshot.capture(CanvasDocument()))
    assert restored.guides == []
    assert restored.dimensions == []
