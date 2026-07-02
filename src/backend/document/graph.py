"""Layered polyline document — named layers with an active layer.

Historical note: this module used to hold a full constraint-graph design
(point/segment/param nodes, constraint/derivation edges, an action log).
Nothing in the application ever consumed that machinery — every live call
site stored and read plain polylines per layer — so it was removed. What
remains is exactly the API the UI uses. ``restore()`` still accepts the
old snapshot format so existing session files keep loading (including
rebuilding geometry-layer polylines from legacy point/segment tables).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any

# ("layer-polyline", index) — the legacy ("segment", id) form is accepted
# by move_entities_to_layer for compatibility and treated identically.
EntityRef = tuple[str, int]

Polyline = list[tuple[float, float]]


@dataclass
class LayerNode:
    id: int
    name: str
    polylines: list[Polyline] = field(default_factory=list)
    entity_refs: list[EntityRef] = field(default_factory=list)
    dirty: bool = False
    # Full entity records (points/kind/meta/flags/group) captured from the
    # canvas. ``polylines`` stays as the derived flat geometry consumed by
    # exports and ghost rendering; records preserve everything else across
    # layer switches and sessions.
    records: list[dict] | None = None


class DocumentGraph:
    """Ordered, named layers of polylines with one active layer."""

    def __init__(self) -> None:
        self.layers: dict[str, LayerNode] = {}
        self.layer_order: list[str] = []
        self.active_layer: str = "geometry"
        self._id_counter = count(1)
        self.ensure_layer("geometry")

    # ── Internals ─────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        return next(self._id_counter)

    @staticmethod
    def _clone_polylines(polylines: list[Polyline]) -> list[Polyline]:
        return [[(float(x), float(y)) for x, y in poly] for poly in polylines]

    def _ordered_layer_names(self) -> list[str]:
        ordered = [name for name in self.layer_order if name in self.layers]
        ordered.extend(name for name in self.layers if name not in ordered)
        return ordered

    # ── Layer management ──────────────────────────────────────────────────

    def iter_layers(self) -> list[tuple[str, LayerNode]]:
        return [(name, self.layers[name]) for name in self._ordered_layer_names()]

    def ensure_layer(self, name: str) -> LayerNode:
        if name not in self.layers:
            self.layers[name] = LayerNode(id=self._next_id(), name=name)
            if name not in self.layer_order:
                self.layer_order.append(name)
        return self.layers[name]

    def create_layer(self, name: str) -> LayerNode:
        if name in self.layers:
            raise ValueError(f"Layer already exists: {name}")
        return self.ensure_layer(name)

    def set_active_layer(self, name: str) -> None:
        self.ensure_layer(name)
        self.active_layer = name

    def rename_layer(self, old_name: str, new_name: str) -> LayerNode:
        old_name = str(old_name)
        new_name = str(new_name).strip()
        if not new_name:
            raise ValueError("Layer name cannot be empty.")
        if old_name == "geometry" or new_name == "geometry":
            raise ValueError("geometry is reserved and cannot be renamed.")
        if old_name not in self.layers:
            raise ValueError(f"Layer does not exist: {old_name}")
        if old_name == new_name:
            return self.layers[old_name]
        if new_name in self.layers:
            raise ValueError(f"Layer already exists: {new_name}")

        layer = self.layers.pop(old_name)
        layer.name = new_name
        self.layers[new_name] = layer
        self.layer_order = [
            new_name if name == old_name else name for name in self.layer_order
        ]
        if self.active_layer == old_name:
            self.active_layer = new_name
        return layer

    def delete_layer(self, name: str, *, fallback_layer: str = "geometry") -> None:
        name = str(name)
        if name == "geometry":
            raise ValueError("geometry is reserved and cannot be deleted.")
        if name not in self.layers:
            raise ValueError(f"Layer does not exist: {name}")
        if self.active_layer == name:
            fallback = fallback_layer if fallback_layer in self.layers else "geometry"
            self.active_layer = fallback if fallback != name else "geometry"
            self.ensure_layer(self.active_layer)
        self.layers.pop(name, None)
        self.layer_order = [n for n in self.layer_order if n != name]

    def move_layer(self, name: str, new_index: int) -> None:
        if name == "geometry":
            raise ValueError("geometry cannot be reordered.")
        if name not in self.layers:
            raise ValueError(f"Layer does not exist: {name}")
        ordered = self._ordered_layer_names()
        if name not in ordered:
            return
        ordered.remove(name)
        new_index = max(0, min(int(new_index), len(ordered)))
        ordered.insert(new_index, name)
        self.layer_order = ordered

    def move_entities_to_layer(
        self,
        refs: list[EntityRef],
        *,
        source_layer: str,
        target_layer: str,
    ) -> None:
        source_layer = str(source_layer)
        target_layer = str(target_layer)
        if source_layer == target_layer:
            return
        source = self.ensure_layer(source_layer)
        target = self.ensure_layer(target_layer)

        # Both ref kinds index into the source layer's polyline list.
        indices = sorted(
            {int(ref) for kind, ref in refs if isinstance(ref, int)},
            reverse=True,
        )
        extracted: list[tuple[int, Polyline]] = []
        moved_records: list[dict] = []
        src_records = source.records if source.records is not None else None
        for idx in indices:
            if 0 <= idx < len(source.polylines):
                extracted.append((idx, list(source.polylines.pop(idx))))
                if src_records is not None and 0 <= idx < len(src_records):
                    moved_records.append(src_records.pop(idx))
        source.entity_refs = []
        if not extracted:
            return
        extracted.sort(key=lambda item: item[0])
        moved_polys = [poly for _idx, poly in extracted]
        target.polylines.extend(self._clone_polylines(moved_polys))
        if moved_records:
            if target.records is None:
                # Synthesize plain records for pre-existing target geometry
                # so indices stay aligned.
                existing = len(target.polylines) - len(moved_polys)
                target.records = [
                    {"points": [list(pt) for pt in p], "kind": "polyline", "meta": None}
                    for p in target.polylines[:existing]
                ]
            target.records.extend(reversed(moved_records))
        elif target.records is not None:
            target.records = None
        target.entity_refs = []

    # ── Polyline access ───────────────────────────────────────────────────

    def set_layer_polylines(
        self,
        name: str,
        polylines: list[Polyline],
        *,
        entity_refs: list[EntityRef] | None = None,
        mark_dirty: bool = False,
        records: list[dict] | None = None,
    ) -> None:
        layer = self.ensure_layer(name)
        layer.polylines = self._clone_polylines(polylines)
        layer.entity_refs = list(entity_refs or [])
        layer.dirty = mark_dirty
        layer.records = records

    def get_layer_polylines(
        self,
        name: str,
        *,
        fallback_geometry: bool = True,  # kept for call-site compatibility
    ) -> list[Polyline]:
        layer = self.ensure_layer(name)
        return self._clone_polylines(layer.polylines)

    # ── Persistence ───────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "layers": {
                name: {
                    "id": layer.id,
                    "polylines": self._clone_polylines(layer.polylines),
                    "entity_refs": list(layer.entity_refs),
                    "dirty": layer.dirty,
                    "records": layer.records,
                }
                for name, layer in self.layers.items()
            },
            "layer_order": list(self._ordered_layer_names()),
            "active_layer": self.active_layer,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Load a snapshot — either the slim format or the legacy
        constraint-graph format (from which geometry polylines are rebuilt
        out of the point/segment tables)."""
        self.layers = {}
        self.layer_order = []
        self._id_counter = count(1)
        max_id = 0

        raw_layers = state.get("layers", {}) or {}
        for name, payload in raw_layers.items():
            if not isinstance(payload, dict):
                continue
            try:
                layer_id = int(payload.get("id", 0))
            except (TypeError, ValueError):
                layer_id = 0
            polylines = [
                [(float(x), float(y)) for x, y in poly]
                for poly in payload.get("polylines", []) or []
                if isinstance(poly, (list, tuple))
            ]
            refs = [
                (str(kind), int(ref))
                for kind, ref in payload.get("entity_refs", []) or []
            ]
            records = payload.get("records")
            node = LayerNode(
                id=layer_id or 0,
                name=str(name),
                polylines=polylines,
                entity_refs=refs,
                dirty=bool(payload.get("dirty", False)),
                records=records if isinstance(records, list) else None,
            )
            self.layers[str(name)] = node
            max_id = max(max_id, node.id)

        order = state.get("layer_order", []) or []
        self.layer_order = [str(n) for n in order if str(n) in self.layers]

        # Legacy sessions stored geometry as point/segment tables with an
        # empty geometry polyline list — rebuild 2-point polylines exactly
        # as the old _geometry_polylines() did.
        points = state.get("points") or {}
        segments = state.get("segments") or {}
        geometry = self.ensure_layer("geometry")
        if segments and not geometry.polylines:
            pts = {int(k): (float(v[0]), float(v[1])) for k, v in points.items()}
            for sid in sorted(segments, key=lambda s: int(s)):
                seg = segments[sid]
                p0 = pts.get(int(seg[0]))
                p1 = pts.get(int(seg[1]))
                if p0 is not None and p1 is not None:
                    geometry.polylines.append([p0, p1])

        # Assign ids to any layers that lacked one and resume the counter.
        self._id_counter = count(max_id + 1)
        for node in self.layers.values():
            if node.id <= 0:
                node.id = self._next_id()

        active = str(state.get("active_layer", "geometry") or "geometry")
        self.set_active_layer(active if active in self.layers else "geometry")
