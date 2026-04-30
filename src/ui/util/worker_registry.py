"""Lightweight worker thread registry per page/runtime.

Lets owners track daemon ``threading.Thread`` instances so they can:
  * cancel via shared ``threading.Event`` tokens,
  * join with a timeout on shutdown for clean-ish exit.
"""

from __future__ import annotations

import logging
import threading
import weakref
from dataclasses import dataclass, field

_LOG = logging.getLogger(__name__)


@dataclass
class WorkerHandle:
    thread: threading.Thread
    cancel_event: threading.Event
    label: str = "worker"
    started_at: float = field(default_factory=lambda: 0.0)


class WorkerRegistry:
    """Tracks live worker threads and their cancellation events."""

    def __init__(self, label: str = "registry"):
        self._label = label
        self._lock = threading.Lock()
        # Use weakref to thread to allow GC of completed threads.
        self._workers: list[WorkerHandle] = []

    def register(
        self,
        thread: threading.Thread,
        cancel_event: threading.Event,
        *,
        label: str = "worker",
    ) -> WorkerHandle:
        handle = WorkerHandle(thread=thread, cancel_event=cancel_event, label=label)
        with self._lock:
            # Compact dead threads opportunistically.
            self._workers = [w for w in self._workers if w.thread.is_alive()]
            self._workers.append(handle)
        return handle

    def cancel_all(self) -> None:
        with self._lock:
            handles = list(self._workers)
        for h in handles:
            try:
                h.cancel_event.set()
            except Exception:  # noqa: BLE001
                pass

    def join_all(self, timeout: float = 2.0) -> int:
        """Cancel and join all live workers. Returns count still alive."""
        self.cancel_all()
        with self._lock:
            handles = list(self._workers)
        per_timeout = max(0.05, timeout / max(1, len(handles)))
        still_alive = 0
        for h in handles:
            try:
                h.thread.join(per_timeout)
                if h.thread.is_alive():
                    still_alive += 1
                    _LOG.warning(
                        "%s: worker %s did not exit within %.2fs",
                        self._label,
                        h.label,
                        per_timeout,
                    )
            except RuntimeError:
                pass
        return still_alive

    def shutdown(self, timeout: float = 2.0) -> None:
        self.join_all(timeout)


_OWNED: weakref.WeakKeyDictionary[object, WorkerRegistry] = weakref.WeakKeyDictionary()


def registry_for(owner: object, label: str | None = None) -> WorkerRegistry:
    """Get-or-create a per-owner registry, keyed weakly so it dies with owner."""
    reg = _OWNED.get(owner)
    if reg is None:
        reg = WorkerRegistry(label or owner.__class__.__name__)
        _OWNED[owner] = reg
    return reg


__all__ = ["WorkerHandle", "WorkerRegistry", "registry_for"]
