"""Geometric containment tree over a document's closed outlines.

Replaces the hand-assigned ``cutout`` role. A region that carries a
treatment subtracts itself from its parent automatically, so the user never
declares a fact the geometry already states — see plan Phase 1.

Containment uses the same prepared-geometry ``covers`` test
``PatternProcessor._zone_nested_exclusions`` uses, so "inside" means the
same thing in both places.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.prepared import prep

from simple_stipple.engine.patterns._shared import is_open_polyline

# Two rings whose areas differ by less than this are not in a
# parent/child relationship — they are the same shape twice, or two
# shapes that merely overlap. Either way, siblings.
_AREA_TOL = 1e-9


@dataclass(frozen=True)
class Region:
    """One closed outline's area, positioned in the containment tree."""

    id: str
    outline_id: str
    depth: int  # 0 = outermost
    parent_id: str | None
    children: tuple[str, ...]


def _as_polygon(poly: list[tuple[float, float]]) -> Polygon | None:
    if len(poly) < 3:
        return None
    try:
        shape = Polygon(poly)
    except (TypeError, ValueError):
        return None
    repaired = shape if shape.is_valid else shape.buffer(0)
    if isinstance(repaired, Polygon):
        parts = [repaired]
    elif isinstance(repaired, (MultiPolygon, GeometryCollection)):
        parts = [part for part in repaired.geoms if isinstance(part, Polygon)]
    else:
        parts = []
    usable = [part for part in parts if not part.is_empty and part.area > 0]
    if not usable:
        return None
    return max(usable, key=lambda part: part.area)


def build_region_tree(
    outline_ids: list[str],
    polys: list[list[tuple[float, float]]],
) -> dict[str, Region]:
    """Map each closed outline to a :class:`Region` in a containment tree.

    Open polylines get no region — they are linework, never an area. Shapes
    that merely overlap (neither covers the other) stay siblings at the same
    depth; the fill boolean sorts out the overlap.
    """
    shapes: dict[str, Polygon] = {}
    for outline_id, poly in zip(outline_ids, polys):
        if is_open_polyline(poly):
            continue
        shape = _as_polygon(poly)
        if shape is not None:
            shapes[str(outline_id)] = shape

    # Smallest strict container wins: for a 3-deep nest the innermost ring
    # is covered by both ancestors, and only the nearer one is its parent.
    prepared = {rid: prep(shape) for rid, shape in shapes.items()}
    parents: dict[str, str | None] = {}
    for rid, shape in shapes.items():
        containers = [
            (other.area, oid)
            for oid, other in shapes.items()
            if oid != rid
            and other.area > shape.area + _AREA_TOL
            and prepared[oid].covers(shape)
        ]
        parents[rid] = min(containers)[1] if containers else None

    children: dict[str, list[str]] = {rid: [] for rid in shapes}
    for rid, parent_id in parents.items():
        if parent_id is not None:
            children[parent_id].append(rid)

    def depth_of(rid: str) -> int:
        depth, seen = 0, {rid}
        parent = parents[rid]
        while parent is not None and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = parents[parent]
        return depth

    return {
        rid: Region(
            id=rid,
            outline_id=rid,
            depth=depth_of(rid),
            parent_id=parents[rid],
            children=tuple(sorted(children[rid])),
        )
        for rid in shapes
    }
