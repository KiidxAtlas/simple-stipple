"""Pure model tests for stable canvas identity and selection."""

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
    document = CanvasDocument(
        [
            EntityRecord(points=[(0.0, 0.0)]),
            EntityRecord(points=[(1.0, 1.0)]),
        ],
        {1},
    )
    selected = document.selected_ids()
    document.entities.reverse()
    document.select_ids(selected)
    assert document.selection == {0}


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
