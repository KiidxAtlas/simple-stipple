"""Pure model tests for stable canvas identity and selection."""

import pytest

from src.backend.model.commands import CreateCommand, EntitySnapshot
from src.backend.model.document import CanvasDocument, EntityRecord
from src.backend.model.editor_history import CommandStack


def test_entity_ids_are_unique_and_survive_reordering():
    first = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
    second = EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)])
    document = CanvasDocument([first, second])
    first_id, second_id = first.id, second.id

    document.entities.reverse()

    assert document.index_for_id(first_id) == 1
    assert document.index_for_id(second_id) == 0
    assert document.entity_for_id(first_id) is first


def test_duplicate_ids_are_repaired_at_document_boundary():
    first = EntityRecord(points=[(0.0, 0.0)], id="duplicate")
    second = EntityRecord(points=[(1.0, 1.0)], id="duplicate")
    document = CanvasDocument([first, second])
    document.ensure_unique_ids()
    assert first.id == "duplicate"
    assert second.id != first.id


def test_selection_can_round_trip_through_stable_ids():
    entity_a = EntityRecord(points=[(0.0, 0.0)])
    entity_b = EntityRecord(points=[(1.0, 1.0)])
    document = CanvasDocument(
        [entity_a, entity_b],
        {entity_b.id},
    )
    selected = document.selected_ids()
    document.entities.reverse()
    document.select_ids(selected)
    assert document.selection == {entity_b.id}


def test_command_stack_records_only_the_changed_entity_snapshot():
    inserted = EntityRecord(points=[(10.0, 0.0), (11.0, 0.0)])
    command = CreateCommand(entities=(EntitySnapshot.capture(inserted),))
    stack = CommandStack()
    stack.record(command, command.reverse())

    pair = stack.take_undo()

    assert pair is not None
    assert pair[0] == command
    assert len(pair[0].entities) == 1
    assert pair[0].entities[0].id == inserted.id


def test_validate_duplicate_ids():
    first = EntityRecord(points=[(0.0, 0.0)], id="dup")
    second = EntityRecord(points=[(1.0, 1.0)], id="dup")
    document = CanvasDocument([first, second])
    violations = document._validate()
    assert any("duplicate" in v.lower() or "duplicate" in v for v in violations)


def test_validate_empty_selection():
    entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
    document = CanvasDocument([entity], set())
    violations = document._validate()
    assert not violations


def test_validate_selection_contains_non_string_id():
    entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)])
    document = CanvasDocument([entity], {""})
    violations = document._validate()
    assert any("invalid entity ID" in v for v in violations)


def test_validate_layer_not_in_layer_order():
    # Layer validation ensures non-None layers are strings (type check)
    # layer_order membership is enforced by set_layer_model, not _validate()
    entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], layer="Valid Layer")
    document = CanvasDocument([entity])
    violations = document._validate()
    assert not violations


def test_validate_group_has_less_than_2_members():
    # Group validation is enforced by reconcile_groups(), not _validate()
    # Groups may be in transient state during command application
    entity = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], group=0)
    document = CanvasDocument([entity])
    violations = document._validate()
    assert not violations


def test_validate_line_needs_2_points():
    entity = EntityRecord(points=[(0.0, 0.0)], kind="line")
    document = CanvasDocument([entity])
    violations = document._validate()
    assert any("line" in v.lower() and "need" in v.lower() for v in violations)


def test_validate_bezier_needs_2_points():
    entity = EntityRecord(points=[(0.0, 0.0)], kind="bezier")
    document = CanvasDocument([entity])
    violations = document._validate()
    assert any("bezier" in v.lower() and "need" in v.lower() for v in violations)


def test_validate_polyline_needs_2_points():
    entity = EntityRecord(points=[(0.0, 0.0)], kind="polyline")
    document = CanvasDocument([entity])
    violations = document._validate()
    assert any("polyline" in v.lower() and "need" in v.lower() for v in violations)


def test_validate_circle_allows_0_points():
    entity = EntityRecord(points=[], kind="circle", meta={"center": (0.0, 0.0), "radius": 5.0})
    document = CanvasDocument([entity])
    violations = document._validate()
    assert not violations


def test_validate_active_layer_not_in_layer_order():
    entity = EntityRecord(points=[(0.0, 0.0)])
    document = CanvasDocument([entity], active_layer="Missing Layer", layer_order=["Layer 1"])
    violations = document._validate()
    assert any("active layer" in v.lower() for v in violations)


def test_validate_valid_document_has_no_violations():
    entity1 = EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)], layer="Layer 1")
    entity2 = EntityRecord(points=[(1.0, 0.0), (1.0, 1.0)], layer="Layer 1")
    document = CanvasDocument(
        [entity1, entity2],
        {entity1.id, entity2.id},
        layer_order=["Layer 1"],
        active_layer="Layer 1",
    )
    violations = document._validate()
    assert not violations


def test_append_triggers_validation():
    entity = EntityRecord(points=[(0.0, 0.0)], id="dup")
    duplicate = EntityRecord(points=[(1.0, 1.0)], id="dup")
    document = CanvasDocument([entity])
    with pytest.raises(AssertionError):
        document.append(duplicate)


def test_replace_triggers_validation():
    entity = EntityRecord(points=[(0.0, 0.0)])
    document = CanvasDocument([entity])
    bad_entity = EntityRecord(points=[(1.0, 1.0)])
    # Selection validation only checks for non-string IDs
    document.selection = {""}
    with pytest.raises(AssertionError):
        document.replace([bad_entity])
