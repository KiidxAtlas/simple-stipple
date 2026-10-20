"""Keyed background jobs with latest-request-wins semantics.

Every slow task (pattern solve, trace, file conversion, git, autosave) runs
through a :class:`JobRunner` instead of a hand-rolled ``threading.Thread``.
The runner owns the parts every caller used to re-implement:

* at most one running job per key;
* a newer request cancels the running job, and the superseded job's result
  is dropped instead of reaching the UI;
* results, errors, and cancellations are delivered on the runner's (GUI)
  thread through a queued Qt signal;
* :meth:`JobRunner.shutdown` cancels everything and joins the worker threads.

Work functions receive a :class:`threading.Event` that is set when the job is
cancelled or superseded. They either return a value, raise
:class:`JobCancelled` after noticing the event, or raise any other exception
to report a failure.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QObject, Qt, Signal

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class JobCancelled(Exception):
    """Raised by work that stopped because its cancel event was set."""


class Policy(Enum):
    #: Cancel the running job and start this one once that job has finished.
    #: Requests made while waiting replace each other; only the newest runs.
    QUEUE_LATEST = "queue_latest"
    #: Cancel the running job and start this one immediately. Use when the
    #: running job may be stalled (e.g. "Restart trace").
    REPLACE = "replace"


@dataclass(eq=False)
class _Job(Generic[T]):
    key: str
    work: Callable[[threading.Event], T]
    on_done: Callable[[T], None] | None
    on_error: Callable[[BaseException], None] | None
    on_cancelled: Callable[[], None] | None
    cancel: threading.Event = field(default_factory=threading.Event)
    superseded: bool = False
    thread: threading.Thread | None = None


class JobRunner(QObject):
    """Run keyed jobs on worker threads and report back on the GUI thread."""

    _finished = Signal(object, str, object)  # job, outcome, value

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running: dict[str, _Job[Any]] = {}
        self._pending: dict[str, _Job[Any]] = {}
        # Jobs replaced under Policy.REPLACE keep running until they notice
        # their cancel event; shutdown still joins them.
        self._detached: set[_Job[Any]] = set()
        self._closed = False
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    # ── Submission ────────────────────────────────────────────────────────

    def submit(
        self,
        key: str,
        work: Callable[[threading.Event], T],
        *,
        on_done: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        policy: Policy = Policy.QUEUE_LATEST,
    ) -> threading.Event:
        """Request ``work`` under ``key``; return the job's cancel event.

        Callbacks run on the GUI thread. A job that is superseded by a newer
        request for the same key gets no callback at all; ``on_cancelled``
        is reserved for an explicit :meth:`cancel` (or work that raised
        :class:`JobCancelled` on its own).
        """
        job: _Job[T] = _Job(key, work, on_done, on_error, on_cancelled)
        if self._closed:
            job.cancel.set()
            return job.cancel
        running = self._running.get(key)
        if running is None:
            self._start(job)
            return job.cancel
        running.superseded = True
        running.cancel.set()
        previous = self._pending.pop(key, None)
        if previous is not None:
            previous.cancel.set()
        if policy is Policy.REPLACE:
            del self._running[key]
            self._detached.add(running)
            self._start(job)
        else:
            self._pending[key] = job
        return job.cancel

    def cancel(self, key: str) -> bool:
        """Cancel the running and pending job for ``key``; True if one ran."""
        pending = self._pending.pop(key, None)
        if pending is not None:
            pending.cancel.set()
        running = self._running.get(key)
        if running is None:
            return False
        running.cancel.set()
        return True

    # ── Inspection ────────────────────────────────────────────────────────

    def is_running(self, key: str) -> bool:
        return key in self._running

    def is_busy(self, key: str) -> bool:
        """True while a job for ``key`` is running or waiting to start."""
        return key in self._running or key in self._pending

    def wait(self, key: str, timeout: float | None = None) -> bool:
        """Join the running thread for ``key`` (results still need the event loop)."""
        job = self._running.get(key)
        if job is None or job.thread is None:
            return True
        job.thread.join(timeout)
        return not job.thread.is_alive()

    # ── Shutdown ──────────────────────────────────────────────────────────

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Cancel every job, drop pending ones and join workers.

        ``timeout`` is the total budget across all threads. Returns whether
        every worker exited. No callbacks are delivered after shutdown.
        """
        self._closed = True
        for job in self._pending.values():
            job.cancel.set()
        self._pending.clear()
        jobs = [*self._running.values(), *self._detached]
        for job in jobs:
            job.cancel.set()
        deadline = time.monotonic() + max(0.0, timeout)
        finished = True
        for job in jobs:
            if job.thread is None:
                continue
            job.thread.join(max(0.0, deadline - time.monotonic()))
            finished = finished and not job.thread.is_alive()
        return finished

    # ── Internals ─────────────────────────────────────────────────────────

    def _start(self, job: _Job[Any]) -> None:
        def run() -> None:
            outcome: str
            value: object
            try:
                value = job.work(job.cancel)
                outcome = "done"
            except JobCancelled:
                outcome, value = "cancelled", None
            except Exception as exc:  # noqa: BLE001 - worker boundary
                outcome, value = "error", exc
            try:
                self._finished.emit(job, outcome, value)
            except RuntimeError:
                # The runner was destroyed with its owner; nobody is listening.
                pass

        job.thread = threading.Thread(target=run, name=f"job:{job.key}", daemon=True)
        self._running[job.key] = job
        job.thread.start()

    def _deliver(self, job: _Job[Any], outcome: str, value: object) -> None:
        self._detached.discard(job)
        successor = None
        if self._running.get(job.key) is job:
            del self._running[job.key]
            successor = self._pending.pop(job.key, None)
        if not self._closed and not job.superseded:
            self._notify(job, outcome, value)
        if successor is not None and not self._closed:
            self._start(successor)

    @staticmethod
    def _notify(job: _Job[Any], outcome: str, value: object) -> None:
        if outcome == "done" and job.cancel.is_set():
            outcome = "cancelled"
        if outcome == "done":
            if job.on_done is not None:
                job.on_done(value)
        elif outcome == "cancelled":
            if job.on_cancelled is not None:
                job.on_cancelled()
        elif isinstance(value, BaseException):
            if job.cancel.is_set():
                if job.on_cancelled is not None:
                    job.on_cancelled()
            elif job.on_error is not None:
                job.on_error(value)
            else:
                LOGGER.warning("Background job %r failed: %s", job.key, value, exc_info=value)


__all__ = ["JobCancelled", "JobRunner", "Policy"]
