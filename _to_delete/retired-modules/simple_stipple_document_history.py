"""Command-oriented undo and redo history."""

from __future__ import annotations

from simple_stipple.document.commands import Command


class CommandStack:
    """History of serializable commands paired with concrete inverses."""

    def __init__(self, max_entries: int = 500) -> None:
        self._max_entries = max(1, int(max_entries))
        self._undo_commands: list[tuple[Command, Command]] = []
        self._redo_commands: list[tuple[Command, Command]] = []

    def record(self, command: Command, inverse: Command) -> None:
        self._undo_commands.append((command, inverse))
        if len(self._undo_commands) > self._max_entries:
            del self._undo_commands[: len(self._undo_commands) - self._max_entries]
        self._redo_commands.clear()

    def take_undo(self) -> tuple[Command, Command] | None:
        if not self._undo_commands:
            return None
        pair = self._undo_commands.pop()
        self._redo_commands.append(pair)
        return pair

    def take_redo(self) -> tuple[Command, Command] | None:
        if not self._redo_commands:
            return None
        pair = self._redo_commands.pop()
        self._undo_commands.append(pair)
        return pair

    def undo_depth(self) -> int:
        """How many actions are currently undoable."""
        return len(self._undo_commands)

    def clear(self) -> None:
        self._undo_commands.clear()
        self._redo_commands.clear()
