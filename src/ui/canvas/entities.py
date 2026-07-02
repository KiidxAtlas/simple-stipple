"""Single-source entity storage for the canvas (shape-model migration, step 3).

``EntityRecord`` bundles what used to live in three parallel arrays on
``PolylineView`` (``_polys`` / ``_entity_kinds`` / ``_entity_meta``). Keeping
those arrays index-aligned by hand was the dominant bug source in this
codebase: merge, split, offset, explode, and delete each had to remember to
rebuild every array, and forgetting one silently exported wrong geometry.

The ``*View`` classes are live, sequence-like windows onto the owner's
``_entities`` list so the large body of legacy *read* sites (and in-place
point edits) keeps working unchanged:

* ``view[i]`` / ``view[i] = x`` — element access and replacement work.
* ``PolylinesView[i]`` returns the entity's actual point list, so in-place
  vertex edits (``self._polys[pi][vi] = ...``) behave exactly as before.
* Structural mutation (``append`` / ``pop`` / wholesale assignment) is
  deliberately **not** provided — those operations must go through the
  entity-native primitives (``_append_entity`` / ``_compact_entities`` /
  ``self._entities`` itself), so kind/meta can never desync from geometry
  again. An unconverted writer fails loudly with AttributeError instead of
  corrupting state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

Point = tuple[float, float]


@dataclass
class EntityRecord:
    """One drawable entity: geometry, parametric identity, and flags."""

    points: list[Point]
    kind: str = "polyline"
    meta: dict[str, Any] | None = None
    construction: bool = False
    hidden: bool = False
    locked: bool = False
    group: int | None = None


class _EntityFieldView:
    """Base for live sequence views over ``owner._entities``."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def _ents(self) -> list[EntityRecord]:
        return self._owner._entities

    def __len__(self) -> int:
        return len(self._ents())

    def __bool__(self) -> bool:
        return bool(self._ents())


class PolylinesView(_EntityFieldView):
    """Live view of every entity's point list."""

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [e.points for e in self._ents()[i]]
        return self._ents()[i].points

    def __setitem__(self, i: int, value) -> None:
        self._ents()[i].points = list(value)

    def __iter__(self) -> Iterator[list[Point]]:
        return (e.points for e in self._ents())


class KindsView(_EntityFieldView):
    """Live view of every entity's kind string."""

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [e.kind for e in self._ents()[i]]
        return self._ents()[i].kind

    def __setitem__(self, i: int, value) -> None:
        self._ents()[i].kind = str(value)

    def __iter__(self) -> Iterator[str]:
        return (e.kind for e in self._ents())


class MetaView(_EntityFieldView):
    """Live view of every entity's parametric metadata."""

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [e.meta for e in self._ents()[i]]
        return self._ents()[i].meta

    def __setitem__(self, i: int, value) -> None:
        self._ents()[i].meta = value

    def __iter__(self) -> Iterator[dict[str, Any] | None]:
        return (e.meta for e in self._ents())


class FlagSetView(_EntityFieldView):
    """Live set-of-indices view over a boolean entity flag.

    Supports the operations the canvas actually uses: membership, iteration,
    add/discard/clear, equality against real sets, and being the right-hand
    side of set arithmetic (``plain_set - view`` etc. resolve through the
    reflected operators because ``set.__sub__`` returns NotImplemented for
    non-set operands).
    """

    __slots__ = ("_attr",)

    def __init__(self, owner: Any, attr: str) -> None:
        super().__init__(owner)
        self._attr = attr

    def __contains__(self, idx: object) -> bool:
        ents = self._ents()
        return (
            isinstance(idx, int)
            and 0 <= idx < len(ents)
            and getattr(ents[idx], self._attr)
        )

    def __iter__(self) -> Iterator[int]:
        attr = self._attr
        return (i for i, e in enumerate(self._ents()) if getattr(e, attr))

    def __len__(self) -> int:
        attr = self._attr
        return sum(1 for e in self._ents() if getattr(e, attr))

    def add(self, idx: int) -> None:
        ents = self._ents()
        if 0 <= idx < len(ents):
            setattr(ents[idx], self._attr, True)

    def discard(self, idx: int) -> None:
        ents = self._ents()
        if isinstance(idx, int) and 0 <= idx < len(ents):
            setattr(ents[idx], self._attr, False)

    def clear(self) -> None:
        for e in self._ents():
            setattr(e, self._attr, False)

    def replace(self, indices) -> None:
        """Set the flag to exactly ``indices`` (wholesale assignment)."""
        wanted = {i for i in indices if isinstance(i, int)}
        for i, e in enumerate(self._ents()):
            setattr(e, self._attr, i in wanted)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (set, frozenset, FlagSetView)):
            return set(self) == set(other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        eq = self.__eq__(other)
        return NotImplemented if eq is NotImplemented else not eq

    __hash__ = None  # type: ignore[assignment]  # mutable view — unhashable like set

    # Reflected set arithmetic: plain_set OP view
    def __rsub__(self, other: set) -> set:
        return {x for x in other if x not in self}

    def __rand__(self, other: set) -> set:
        return {x for x in other if x in self}

    def __ror__(self, other: set) -> set:
        return set(other) | set(self)

    # Direct arithmetic (view OP set) for completeness
    def __sub__(self, other) -> set:
        return {x for x in self if x not in other}

    def __or__(self, other) -> set:
        return set(self) | set(other)

    def __and__(self, other) -> set:
        return {x for x in self if x in other}


class GroupsView(_EntityFieldView):
    """Live dict-like view of ``{index: group_id}`` over entity ``group``."""

    def __getitem__(self, idx: int) -> int:
        g = self._ents()[idx].group
        if g is None:
            raise KeyError(idx)
        return g

    def __setitem__(self, idx: int, gid: int) -> None:
        self._ents()[idx].group = int(gid)

    def __delitem__(self, idx: int) -> None:
        self._ents()[idx].group = None

    def __contains__(self, idx: object) -> bool:
        ents = self._ents()
        return (
            isinstance(idx, int)
            and 0 <= idx < len(ents)
            and ents[idx].group is not None
        )

    def __iter__(self) -> Iterator[int]:
        return (i for i, e in enumerate(self._ents()) if e.group is not None)

    def __len__(self) -> int:
        return sum(1 for e in self._ents() if e.group is not None)

    def keys(self):
        return list(self)

    def values(self):
        return [e.group for e in self._ents() if e.group is not None]

    def items(self):
        return [(i, e.group) for i, e in enumerate(self._ents()) if e.group is not None]

    def get(self, idx: int, default=None):
        ents = self._ents()
        if isinstance(idx, int) and 0 <= idx < len(ents) and ents[idx].group is not None:
            return ents[idx].group
        return default

    def clear(self) -> None:
        for e in self._ents():
            e.group = None

    _POP_MISSING = object()

    def pop(self, idx: int, default=_POP_MISSING):
        ents = self._ents()
        if isinstance(idx, int) and 0 <= idx < len(ents) and ents[idx].group is not None:
            g = ents[idx].group
            ents[idx].group = None
            return g
        if default is self._POP_MISSING:
            raise KeyError(idx)
        return default

    def replace(self, mapping) -> None:
        """Set groups to exactly ``mapping`` (wholesale assignment)."""
        ents = self._ents()
        for e in ents:
            e.group = None
        for k, v in dict(mapping).items():
            i = int(k)
            if 0 <= i < len(ents):
                ents[i].group = int(v)
