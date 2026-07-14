"""Entity storage + delta-based undo/redo store for the canvas.

``EntityRecord`` (merged in from the former ``entities.py`` — a 30-line
dataclass with no single dominant consumer among ``render.py``,
``runtime.py``, ``undo.py``, and ``view.py``, so it lives with its most
substantial cross-cutting collaborator instead) bundles what used to live
in seven parallel index-keyed structures on ``PolylineView``. Keeping those
aligned by hand was the dominant bug source in this codebase; geometry,
kind, meta, and flags now travel together, and call sites read
``self._entities[i].points`` directly.

The undo store: the old scheme deep-copied the entire entity list on every
operation (O(document) memory per step, hard 30-step cap). This store
snapshots the pre-state once per operation (``mark``), then lazily computes
a compact delta — only the entities that actually changed, plus length
bookkeeping — when the next operation starts or undo/redo is invoked.
Typical steps (moving a selection, dragging a vertex) now cost O(changed
entities).

``mark(coalesce="nudge")`` merges consecutive same-key operations into one
undo step, replacing the old nudge timer flag special-case.

Wholesale document replacements (load, session restore, preview swaps) must
call ``clear()``: deltas are relative edits, so history from a different
document cannot be replayed safely.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from src.ui.canvas.document import EntityRecord


@dataclass
class HistoryState:
    """Non-entity canvas state that must travel with geometry undo steps."""

    layer_order: tuple[str, ...]
    active_layer: str | None
    constraints: tuple[dict, ...] = field(default_factory=tuple)


LayerState = HistoryState  # compatibility name for the existing store API


@dataclass
class _Delta:
    """One undo step: how to move between the before and after states."""

    back_changed: list[tuple[int, EntityRecord]]
    fwd_changed: list[tuple[int, EntityRecord]]
    back_order: tuple[str, ...]
    fwd_order: tuple[str, ...]
    sel_back: set[int]
    sel_fwd: set[int]
    layers_back: LayerState | None = None  # None = layer model unchanged
    layers_fwd: LayerState | None = None

    @property
    def back_len(self) -> int:
        return len(self.back_order)

    @property
    def fwd_len(self) -> int:
        return len(self.fwd_order)

    def vertex_cost(self) -> int:
        return sum(
            len(rec.points)
            for rec in ([r for _, r in self.back_changed] + [r for _, r in self.fwd_changed])
        )


def _diff(
    before: list[EntityRecord],
    before_sel: set[int],
    after: list[EntityRecord],
    after_sel: set[int],
    layers_before: LayerState | None = None,
    layers_after: LayerState | None = None,
) -> _Delta | None:
    """Build a stable-ID delta; order changes store IDs, not shifted records."""
    before_by_id = {entity.id: entity for entity in before}
    after_by_id = {entity.id: entity for entity in after}
    back_changed: list[tuple[int, EntityRecord]] = []
    fwd_changed: list[tuple[int, EntityRecord]] = []
    for i, entity in enumerate(before):
        if entity.id not in after_by_id or entity != after_by_id[entity.id]:
            back_changed.append((i, deepcopy(entity)))
    for i, entity in enumerate(after):
        if entity.id not in before_by_id or entity != before_by_id[entity.id]:
            fwd_changed.append((i, deepcopy(entity)))
    back_order = tuple(entity.id for entity in before)
    fwd_order = tuple(entity.id for entity in after)
    layers_changed = layers_before is not None and layers_before != layers_after
    if not back_changed and not fwd_changed and back_order == fwd_order and not layers_changed:
        return None  # nothing observable changed; selection-only ≠ a step
    return _Delta(
        back_changed=back_changed,
        fwd_changed=fwd_changed,
        back_order=back_order,
        fwd_order=fwd_order,
        sel_back=set(before_sel),
        sel_fwd=set(after_sel),
        layers_back=layers_before if layers_changed else None,
        layers_fwd=layers_after if layers_changed else None,
    )


class UndoStore:
    """Owns undo/redo history for one canvas."""

    MAX_STEPS = 100
    VERTEX_BUDGET = 400_000

    def __init__(self) -> None:
        self._undo: list[_Delta] = []
        self._redo: list[_Delta] = []
        self._shadow: list[EntityRecord] | None = None
        self._shadow_sel: set[int] = set()
        self._shadow_layers: LayerState | None = None
        self._pending_key: str | None = None

    # ── Recording ─────────────────────────────────────────────────────────

    def mark(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        *,
        coalesce: str | None = None,
        layers: LayerState | None = None,
    ) -> None:
        """Snapshot the pre-state of an operation (call before mutating).

        Consecutive marks with the same non-None ``coalesce`` key merge
        into a single undo step.
        """
        self._redo.clear()
        if self._shadow is not None and coalesce is not None and coalesce == self._pending_key:
            return  # keep the original pre-state; the ops merge
        self._finalize(entities, sel, layers)
        self._shadow = deepcopy(list(entities))
        self._shadow_sel = set(sel)
        self._shadow_layers = layers
        self._pending_key = coalesce

    def break_coalescing(self) -> None:
        self._pending_key = None

    def clear(self) -> None:
        """Reset history — required after wholesale document replacement."""
        self._undo.clear()
        self._redo.clear()
        self._shadow = None
        self._shadow_layers = None
        self._pending_key = None

    def _finalize(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        layers: LayerState | None = None,
    ) -> None:
        if self._shadow is None:
            return
        delta = _diff(
            self._shadow,
            self._shadow_sel,
            entities,
            sel,
            self._shadow_layers,
            layers,
        )
        if delta is not None:
            self._undo.append(delta)
            self._cap()
        self._shadow = None
        self._shadow_layers = None
        self._pending_key = None

    def _cap(self) -> None:
        if len(self._undo) > self.MAX_STEPS:
            del self._undo[0 : len(self._undo) - self.MAX_STEPS]
        total = sum(d.vertex_cost() for d in self._undo)
        while total > self.VERTEX_BUDGET and len(self._undo) > 1:
            total -= self._undo.pop(0).vertex_cost()

    # ── Playback ──────────────────────────────────────────────────────────

    @staticmethod
    def _apply(
        entities: list[EntityRecord],
        target_order: tuple[str, ...],
        changed: list[tuple[int, EntityRecord]],
    ) -> list[EntityRecord]:
        by_id = {entity.id: entity for entity in entities}
        by_id.update({record.id: record for _, record in changed})
        return [deepcopy(by_id[entity_id]) for entity_id in target_order]

    def undo(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        layers: LayerState | None = None,
    ) -> tuple[list[EntityRecord], set[int], LayerState | None] | None:
        self._finalize(entities, sel, layers)
        if not self._undo:
            return None
        d = self._undo.pop()
        self._redo.append(d)
        return (
            self._apply(entities, d.back_order, d.back_changed),
            set(d.sel_back),
            d.layers_back,
        )

    def redo(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        layers: LayerState | None = None,
    ) -> tuple[list[EntityRecord], set[int], LayerState | None] | None:
        self._finalize(entities, sel, layers)
        if not self._redo:
            return None
        d = self._redo.pop()
        self._undo.append(d)
        return (
            self._apply(entities, d.fwd_order, d.fwd_changed),
            set(d.sel_fwd),
            d.layers_fwd,
        )
