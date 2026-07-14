"""UI-independent vocabulary for cancellable page work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    FAILED = "failed"
    COMPLETE = "complete"


@dataclass(frozen=True)
class TaskRevision:
    """Identity carried by worker results to reject stale completion."""

    value: int

    def next(self) -> TaskRevision:
        return TaskRevision(self.value + 1)

    def accepts(self, result_revision: int) -> bool:
        return result_revision == self.value
