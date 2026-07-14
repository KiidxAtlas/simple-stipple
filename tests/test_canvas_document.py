"""Pure model tests for stable canvas identity and selection."""

from src.ui.canvas.document import CanvasDocument, EntityRecord
from src.ui.canvas.undo import UndoStore


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


def test_undo_middle_insertion_stores_only_inserted_record():
    before = [
        EntityRecord(points=[(0.0, 0.0), (1.0, 0.0)]),
        EntityRecord(points=[(2.0, 0.0), (3.0, 0.0)]),
        EntityRecord(points=[(4.0, 0.0), (5.0, 0.0)]),
    ]
    inserted = EntityRecord(points=[(10.0, 0.0), (11.0, 0.0)])
    after = [before[0], inserted, before[1], before[2]]
    store = UndoStore()
    store.mark(before, set())

    undone = store.undo(after, set())

    assert undone is not None
    restored, _, _ = undone
    assert [entity.id for entity in restored] == [entity.id for entity in before]
    delta = store._redo[-1]
    assert len(delta.fwd_changed) == 1
    assert delta.fwd_changed[0][1].id == inserted.id
