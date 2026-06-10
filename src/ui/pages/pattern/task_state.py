"""Reusable task state helper for cancellable background work."""

from __future__ import annotations

import threading
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

