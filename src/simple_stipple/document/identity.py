"""Core entity types shared across all layers."""

from __future__ import annotations

import uuid

EntityId = str


def new_entity_id() -> EntityId:
    """Generate a new unique entity identifier."""
    return uuid.uuid4().hex[:12]


__all__ = ["EntityId", "new_entity_id"]
