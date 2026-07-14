"""Explicit structural contracts for canvas behavior helpers.

These protocols document capabilities without importing the concrete Qt view.
They are typing-only boundaries, not new runtime abstractions.
"""

from __future__ import annotations

from typing import Protocol

from src.ui.canvas.document import CanvasDocument, EntityRecord
from src.ui.canvas.geometry_model import CanvasGeometry


class CanvasModelHost(Protocol):
    _document: CanvasDocument
    _entities: list[EntityRecord]
    _sel: set[int]

    def _push_undo(self, coalesce: str | None = None) -> None: ...
    def _redraw(self) -> None: ...
    def _notify(self) -> None: ...
    def _fire_poly_change(self) -> None: ...


class CanvasTransformHost(Protocol):
    _scale: float

    def _w2c(self, x: float, y: float) -> tuple[float, float]: ...
    def _c2w(self, x: float, y: float) -> tuple[float, float]: ...


class CanvasGeometryHost(CanvasModelHost, Protocol):
    """Contract for mixins that consume entity geometry, not raw metadata."""

    def _geometry_for_entity(self, idx: int) -> CanvasGeometry: ...
    def _flattened_points(self, idx: int) -> list[tuple[float, float]]: ...
    def _entity_center(self, idx: int) -> tuple[float, float] | None: ...


class CanvasLayerHost(CanvasModelHost, Protocol):
    _active_layer: str | None
    _layer_order: list[str]
    _layer_colors: dict[str, str]

    def _compact_entities(self, drop: set[int]) -> None: ...
    def _reset_edit_interaction_state(self) -> None: ...


class UndoHost(Protocol):
    def _push_undo(self, coalesce: str | None = None) -> None: ...
