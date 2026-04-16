"""Simple undo/redo manager for core workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class UndoManager(Generic[T]):
    """In-memory undo/redo state manager."""

    _undo: list[T] = field(default_factory=list)
    _redo: list[T] = field(default_factory=list)

    def push(self, state: T) -> None:
        """Push a new state and clear redo history."""
        self._undo.append(state)
        self._redo.clear()

    def undo(self) -> T | None:
        """Pop one undo state and move it to redo stack."""
        if not self._undo:
            return None
        state = self._undo.pop()
        self._redo.append(state)
        return state

    def redo(self) -> T | None:
        """Pop one redo state and move it back to undo stack."""
        if not self._redo:
            return None
        state = self._redo.pop()
        self._undo.append(state)
        return state


__all__ = ["UndoManager"]
