"""Shared geometry-operation outcome contract."""

from src.backend.operations import OperationResult


def test_unchanged_operation_preserves_actionable_warning():
    result = OperationResult.unchanged("Offset produced no geometry", "Use a smaller distance")

    assert not result.changed
    assert result.message == "Offset produced no geometry"
    assert result.warnings == ("Use a smaller distance",)
    assert not result.created_ids
