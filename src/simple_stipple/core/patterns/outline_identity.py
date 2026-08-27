"""Stable identity helpers for editable pattern outlines."""

from __future__ import annotations

from uuid import uuid4

Point = tuple[float, float]
Polyline = list[Point]


def fresh_outline_ids(count: int) -> list[str]:
    """Create independent stable identifiers for newly imported outlines."""
    return [uuid4().hex for _ in range(count)]


def poly_signature(poly: Polyline) -> tuple[Point, ...]:
    """Return a precision-stable geometry signature for identity reconciliation."""
    return tuple((round(x, 6), round(y, 6)) for x, y in poly)


def sync_outline_ids(
    new_polys: list[Polyline],
    old_polys: list[Polyline],
    old_ids: list[str],
    new_entity_ids: list[str] | None = None,
) -> list[str]:
    """Preserve IDs for unchanged geometry and canvas-owned new outlines."""
    if len(new_polys) == len(old_ids) and all(
        poly_signature(new) == poly_signature(old) for new, old in zip(new_polys, old_polys)
    ):
        return list(old_ids)
    available: dict[tuple[Point, ...], list[str]] = {}
    for poly, outline_id in zip(old_polys, old_ids):
        available.setdefault(poly_signature(poly), []).append(outline_id)
    resolved: list[str] = []
    for index, poly in enumerate(new_polys):
        matches = available.get(poly_signature(poly), [])
        if matches:
            resolved.append(matches.pop(0))
        elif new_entity_ids is not None and index < len(new_entity_ids):
            resolved.append(new_entity_ids[index])
        else:
            resolved.append(uuid4().hex)
    return resolved


def resolve_outline_ids(
    ids: list[str], outline_ids: list[str], edit_polys: list[Polyline]
) -> list[Polyline]:
    """Resolve persisted outline IDs to the current editable geometry."""
    by_id = {outline_id: poly for outline_id, poly in zip(outline_ids, edit_polys)}
    return [
        [(float(x), float(y)) for x, y in by_id[outline_id]]
        for outline_id in ids
        if outline_id in by_id
    ]
