"""TransformCommand reversibility contract.

A ``TransformCommand`` is undone by re-deriving geometry, not by restoring a
snapshot, so it must never be constructed for an operation it cannot reverse
losslessly. These tests pin both halves of that contract:

* a uniform scale round-trips through undo without degrading shape identity, and
* a non-uniform scale (which would silently discard ``kind``/``meta``) is
  rejected at construction, making the lossy state unreachable.
"""

import pytest

from src.app.services.document_service import DocumentService
from src.backend.model.commands import TransformCommand
from src.backend.model.document import CanvasDocument, EntityRecord


def test_non_uniform_scale_transform_command_is_rejected_at_construction():
    with pytest.raises(ValueError):
        TransformCommand(
            entity_ids=("a",), operation="scale", origin=(0.0, 0.0), x=2.0, y=3.0
        )


def test_uniform_scale_round_trips_without_degrading_shape_identity():
    circle = EntityRecord(
        points=[(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)],
        kind="circle",
        meta={"center": (0.0, 0.0), "radius": 1.0},
    )
    service = DocumentService(CanvasDocument([circle]))
    original_points = list(circle.points)

    changed = service.execute(
        TransformCommand(
            entity_ids=(circle.id,), operation="scale", origin=(0.0, 0.0), x=2.0, y=2.0
        )
    )
    assert changed.changed
    scaled = service.document.entity_for_id(circle.id)
    assert scaled is not None
    assert scaled.kind == "circle"  # identity preserved through the transform
    assert scaled.points[0] == (2.0, 0.0)  # geometry actually scaled

    undone = service.undo()
    assert undone.changed
    restored = service.document.entity_for_id(circle.id)
    assert restored is not None
    assert restored.kind == "circle"  # not degraded to a "polyline" on undo
    for (rx, ry), (ox, oy) in zip(restored.points, original_points):
        assert abs(rx - ox) < 1e-9 and abs(ry - oy) < 1e-9
