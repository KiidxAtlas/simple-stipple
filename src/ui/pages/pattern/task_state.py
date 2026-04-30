"""Reusable task state helper for cancellable background work."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class CancellableTaskState:
    """Track running/pending state and cancellation token for threaded tasks."""

    running: bool = False
    pending: bool = False
    _cancel_event: threading.Event = field(default_factory=threading.Event)

    def request_start(self) -> tuple[bool, threading.Event]:
        """Request a new run.

        Returns (can_start_now, cancel_event_for_run).
        If already running, marks pending and returns (False, current_event).
        """
        if self.running:
            self.pending = True
            return False, self._cancel_event

        # Cancel any prior in-flight token, then mint a fresh event so the
        # caller can pass it into the new worker thread atomically.
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        self.running = True
        self.pending = False
        return True, self._cancel_event

    def finish_run(self) -> bool:
        """Mark run complete and return whether another run is pending."""
        self.running = False
        if self.pending:
            self.pending = False
            return True
        return False

    def has_pending(self) -> bool:
        return self.pending

    def cancel(self) -> None:
        """Signal the in-flight worker (if any) to stop."""
        self._cancel_event.set()

    @contextmanager
    def run_scope(self):
        """Context manager guaranteeing ``finish_run`` even on exception.

        Usage::

            can_start, cancel_event = task.request_start()
            if not can_start:
                return
            with task.run_scope() as restart:
                worker.start(cancel_event)
            if restart:
                ...

        ``restart`` is a list-of-one so the body can flip the value if it
        wants to keep the task in the running state (e.g. when handing off
        to a thread that will call ``finish_run`` itself).
        """
        keep_running = [False]
        try:
            yield keep_running
        finally:
            if not keep_running[0]:
                self.finish_run()
