"""Shared result contract for document-changing geometry operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OperationResult:
    """Outcome of one editor operation, expressed using stable entity IDs.

    UI layers can select and describe results without depending on transient
    list indices.  Warnings are non-fatal; ``changed`` is the authoritative
    signal for whether undo/document notifications are required.
    """

    changed: bool
    message: str = ""
    created_ids: tuple[str, ...] = ()
    removed_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unchanged(cls, message: str, *warnings: str) -> OperationResult:
        return cls(False, message=message, warnings=tuple(warnings))
