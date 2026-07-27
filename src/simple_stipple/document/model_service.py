"""Document model and command types for the UI layer.

Wraps ``backend.model.document`` and ``backend.model.commands`` — a thin
re-export, not new logic, so ``ui`` doesn't reach past ``app`` down to
``backend`` directly (see plan.md Section 8.1 / LP-5). Regressed at some
point after being marked complete 2026-07-22 (the file no longer existed);
rebuilt 2026-07-25.
"""

from __future__ import annotations

from simple_stipple.document.commands import (
    Command,
    CreateCommand,
    DeleteCommand,
    DocumentSnapshot,
    EntitySnapshot,
    MoveEntityCommand,
    ReplaceDocumentCommand,
    RestoreEntitiesCommand,
    SelectCommand,
    TransformCommand,
    UpdateEntitiesCommand,
)
from simple_stipple.document.history import CommandStack
from simple_stipple.document.identity import EntityId
from simple_stipple.document.model import (
    CanvasDocument,
    Document,
    EntityRecord,
    OperationResult,
)

__all__ = [
    "CanvasDocument",
    "Command",
    "CommandStack",
    "CreateCommand",
    "DeleteCommand",
    "Document",
    "DocumentSnapshot",
    "EntityId",
    "EntityRecord",
    "EntitySnapshot",
    "MoveEntityCommand",
    "OperationResult",
    "ReplaceDocumentCommand",
    "RestoreEntitiesCommand",
    "SelectCommand",
    "TransformCommand",
    "UpdateEntitiesCommand",
]
