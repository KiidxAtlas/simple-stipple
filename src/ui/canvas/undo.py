"""Delta-based undo/redo store for the canvas.

The old scheme deep-copied the entire entity list on every operation
(O(document) memory per step, hard 30-step cap). This store snapshots the
pre-state once per operation (``mark``), then lazily computes a compact
delta — only the entities that actually changed, plus length bookkeeping —
when the next operation starts or undo/redo is invoked. Typical steps
(moving a selection, dragging a vertex) now cost O(changed entities).

``mark(coalesce="nudge")`` merges consecutive same-key operations into one
undo step, replacing the old nudge timer flag special-case.

Wholesale document replacements (load, session restore, preview swaps) must
call ``clear()``: deltas are relative edits, so history from a different
document cannot be replayed safely.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from src.ui.canvas.entities import EntityRecord


@dataclass
class _Delta:
    """One undo step: how to move between the before and after states."""

    back_changed: list[tuple[int, EntityRecord]]
    fwd_changed: list[tuple[int, EntityRecord]]
    back_tail: list[EntityRecord]  # re-appended when going back (before was longer)
    fwd_tail: list[EntityRecord]  # re-appended when going forward (after is longer)
    back_len: int
    fwd_len: int
    sel_back: set[int]
    sel_fwd: set[int]

    def vertex_cost(self) -> int:
        return sum(
            len(rec.points)
            for rec in (
                [r for _, r in self.back_changed]
                + [r for _, r in self.fwd_changed]
                + self.back_tail
                + self.fwd_tail
            )
        )


def _diff(
    before: list[EntityRecord],
    before_sel: set[int],
    after: list[EntityRecord],
    after_sel: set[int],
) -> _Delta | None:
    """Element-wise diff. Middle insertions/deletions degrade to storing the
    shifted suffix — never wrong, just less compact for those operations."""
    common = min(len(before), len(after))
    back_changed: list[tuple[int, EntityRecord]] = []
    fwd_changed: list[tuple[int, EntityRecord]] = []
    for i in range(common):
        if before[i] != after[i]:
            back_changed.append((i, before[i]))
            fwd_changed.append((i, deepcopy(after[i])))
    back_tail = list(before[len(after) :])
    fwd_tail = [deepcopy(e) for e in after[len(before) :]]
    if not back_changed and not back_tail and not fwd_tail:
        return None  # geometry unchanged; selection-only diffs are not steps
    return _Delta(
        back_changed=back_changed,
        fwd_changed=fwd_changed,
        back_tail=back_tail,
        fwd_tail=fwd_tail,
        back_len=len(before),
        fwd_len=len(after),
        sel_back=set(before_sel),
        sel_fwd=set(after_sel),
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
        self._pending_key: str | None = None

    # ── Recording ─────────────────────────────────────────────────────────

    def mark(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        *,
        coalesce: str | None = None,
    ) -> None:
        """Snapshot the pre-state of an operation (call before mutating).

        Consecutive marks with the same non-None ``coalesce`` key merge
        into a single undo step.
        """
        self._redo.clear()
        if (
            self._shadow is not None
            and coalesce is not None
            and coalesce == self._pending_key
        ):
            return  # keep the original pre-state; the ops merge
        self._finalize(entities, sel)
        self._shadow = deepcopy(list(entities))
        self._shadow_sel = set(sel)
        self._pending_key = coalesce

    def break_coalescing(self) -> None:
        self._pending_key = None

    def clear(self) -> None:
        """Reset history — required after wholesale document replacement."""
        self._undo.clear()
        self._redo.clear()
        self._shadow = None
        self._pending_key = None

    def _finalize(self, entities: list[EntityRecord], sel: set[int]) -> None:
        if self._shadow is None:
            return
        delta = _diff(self._shadow, self._shadow_sel, entities, sel)
        if delta is not None:
            self._undo.append(delta)
            self._cap()
        self._shadow = None
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
        target_len: int,
        tail: list[EntityRecord],
        changed: list[tuple[int, EntityRecord]],
    ) -> list[EntityRecord]:
        lst = list(entities)
        if target_len < len(lst):
            del lst[target_len:]
        elif target_len > len(lst):
            lst.extend(deepcopy(e) for e in tail)
        for i, rec in changed:
            if 0 <= i < len(lst):
                lst[i] = deepcopy(rec)
        return lst

    def undo(
        self, entities: list[EntityRecord], sel: set[int]
    ) -> tuple[list[EntityRecord], set[int]] | None:
        self._finalize(entities, sel)
        if not self._undo:
            return None
        d = self._undo.pop()
        self._redo.append(d)
        return (
            self._apply(entities, d.back_len, d.back_tail, d.back_changed),
            set(d.sel_back),
        )

    def redo(
        self, entities: list[EntityRecord], sel: set[int]
    ) -> tuple[list[EntityRecord], set[int]] | None:
        self._finalize(entities, sel)
        if not self._redo:
            return None
        d = self._redo.pop()
        self._undo.append(d)
        return (
            self._apply(entities, d.fwd_len, d.fwd_tail, d.fwd_changed),
            set(d.sel_fwd),
        )
