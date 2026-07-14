from src.ui.pages.pattern.workers import CancellableTaskState
from src.ui.pages.task_state import TaskPhase, TaskRevision


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
