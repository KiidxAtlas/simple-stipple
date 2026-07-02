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
    """One drawable entity: geometry plus its parametric identity."""

    points: list[Point]
    kind: str = "polyline"
    meta: dict[str, Any] | None = None


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
