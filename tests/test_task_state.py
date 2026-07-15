import threading

from src.ui.pages.base import TaskPhase, TaskRevision
from src.ui.pages.pattern.workers import CANCELLED_MESSAGE, CancellableTaskState, compute_preview


def test_task_revision_rejects_stale_results():
    current = TaskRevision(3)
    assert current.accepts(3)
    assert not current.accepts(2)
    assert current.next() == TaskRevision(4)


def test_cancellable_task_exposes_shared_phase_vocabulary():
    task = CancellableTaskState()
    assert task.phase is TaskPhase.IDLE
    _, active_token = task.request_start()
    assert task.phase is TaskPhase.RUNNING
    task.request_start()
    assert task.phase is TaskPhase.CANCELLING
    assert active_token.is_set()


def test_cancelled_preview_worker_always_reports_completion_boundary():
    event = threading.Event()
    event.set()
    errors: list[tuple[int, str]] = []
    compute_preview(
        [],
        "Grid",
        {},
        (1.0, 1.0),
        None,
        preview_token=7,
        cancel_event=event,
        pattern_service=object(),
        orig_w=1.0,
        orig_h=1.0,
        on_done=lambda _payload: None,
        on_error=errors.append,
    )
    assert errors == [(7, CANCELLED_MESSAGE)]
