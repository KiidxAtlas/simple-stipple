"""Canonical document graph for constraint-based interaction.

Mental model:
- Points = atomic mutable state
- Relationships = explicit edges (constraints/derivations)
- Objects = derived structures
- Tools = transient actions recorded as transactions
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Literal

EntityKind = Literal["point", "segment", "param", "source", "layer", "layer-polyline"]
EntityRef = tuple[EntityKind, int | str]


@dataclass
class PointNode:
    id: int
    x: float
    y: float


@dataclass
class SegmentNode:
    id: int
    p0: int
    p1: int
    layer: str = "geometry"


@dataclass
class ParamNode:
    id: int
    name: str
    value: Any


@dataclass
class SourceNode:
    id: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LayerNode:
    id: int
    name: str
    polylines: list[list[tuple[float, float]]] = field(default_factory=list)
    entity_refs: list[EntityRef] = field(default_factory=list)
    dirty: bool = False


@dataclass
class ConstraintEdge:
    id: int
    kind: str
    source: EntityRef
    target: EntityRef
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DerivationEdge:
    id: int
    source_layer: str
    target_layer: str
    operator_name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRecord:
    id: int
    action_type: str
    payload: dict[str, Any]
    touched: list[EntityRef] = field(default_factory=list)
    invalidated_layers: list[str] = field(default_factory=list)
    user_initiated: bool = True


class DocumentGraph:
    """Unified state graph for all tabs/pages."""

    def __init__(self) -> None:
        self._id_counter = count(1)

        self.points: dict[int, PointNode] = {}
        self.segments: dict[int, SegmentNode] = {}
        self.params: dict[str, ParamNode] = {}
        self.sources: dict[int, SourceNode] = {}
        self.layers: dict[str, LayerNode] = {}

        self.constraints: dict[int, ConstraintEdge] = {}
        self.derivations: dict[int, DerivationEdge] = {}

        self.actions: list[ActionRecord] = []
        self.active_layer: str = "geometry"
        self.layer_order: list[str] = []

        self.ensure_layer("geometry")

    def _next_id(self) -> int:
        return next(self._id_counter)

    @staticmethod
    def _clone_polylines(
        polylines: list[list[tuple[float, float]]],
    ) -> list[list[tuple[float, float]]]:
        return [list(poly) for poly in polylines]

    def _ordered_layer_names(self) -> list[str]:
        return [name for name in self.layer_order if name in self.layers]

    def iter_layers(self) -> list[tuple[str, LayerNode]]:
        return [(name, self.layers[name]) for name in self._ordered_layer_names()]

    def _purge_orphan_points(self) -> None:
        """Remove points no longer referenced by any segment.

        This is O(points + segments).
        """
        referenced: set[int] = set()
        for seg in self.segments.values():
            referenced.add(seg.p0)
            referenced.add(seg.p1)
        for pid in list(self.points):
            if pid not in referenced:
                self.points.pop(pid, None)

    def _entity_ref_exists(self, ref) -> bool:
        """Return True if an (kind, id) tuple still resolves in this graph."""
        if not isinstance(ref, tuple) or len(ref) != 2:
            return False
        kind, ident = ref
        if not isinstance(ident, int):
            try:
                ident = int(ident)
            except (TypeError, ValueError):
                return False
        if kind == "point":
            return ident in self.points
        if kind == "segment":
            return ident in self.segments
        if kind == "layer":
            return any(layer.id == ident for layer in self.layers.values())
        # Unknown kinds are tolerated (forward-compat); treat as valid.
        return True

    # ── Layers ────────────────────────────────────────────────────────────

    def ensure_layer(self, name: str) -> LayerNode:
        if name not in self.layers:
            self.layers[name] = LayerNode(id=self._next_id(), name=name)
            if name not in self.layer_order:
                self.layer_order.append(name)
        return self.layers[name]

    def create_layer(self, name: str) -> LayerNode:
        if name in self.layers:
            raise ValueError(f"Layer already exists: {name}")
        layer = LayerNode(id=self._next_id(), name=name)
        self.layers[name] = layer
        self.layer_order.append(name)
        return layer

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
        self.layer_order = [new_name if name == old_name else name for name in self.layer_order]

        for seg in self.segments.values():
            if seg.layer == old_name:
                seg.layer = new_name

        for edge in self.derivations.values():
            if edge.source_layer == old_name:
                edge.source_layer = new_name
            if edge.target_layer == old_name:
                edge.target_layer = new_name

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
        self.layer_order = [layer_name for layer_name in self.layer_order if layer_name != name]

        seg_ids = [sid for sid, seg in self.segments.items() if seg.layer == name]
        for sid in seg_ids:
            self._remove_segment(sid)

        derivation_ids = [
            did
            for did, edge in self.derivations.items()
            if edge.source_layer == name or edge.target_layer == name
        ]
        for did in derivation_ids:
            self.derivations.pop(did, None)

        self._purge_orphan_points()

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

        self.ensure_layer(source_layer)
        target = self.ensure_layer(target_layer)

        moved_polylines: list[list[tuple[float, float]]] = []

        if source_layer == "geometry":
            seg_ids = sorted(
                {
                    int(ref)
                    for kind, ref in refs
                    if kind == "segment" and isinstance(ref, int)
                }
            )
            for sid in seg_ids:
                seg = self.segments.get(sid)
                if seg is None:
                    continue
                p0 = self.points.get(seg.p0)
                p1 = self.points.get(seg.p1)
                if p0 is None or p1 is None:
                    continue
                moved_polylines.append([(p0.x, p0.y), (p1.x, p1.y)])
                self._remove_segment(sid)
        else:
            source = self.ensure_layer(source_layer)
            poly_refs = sorted(
                {
                    int(ref)
                    for kind, ref in refs
                    if kind == "layer-polyline" and isinstance(ref, int)
                },
                reverse=True,
            )
            extracted: list[tuple[int, list[tuple[float, float]]]] = []
            for idx in poly_refs:
                if 0 <= idx < len(source.polylines):
                    extracted.append((idx, list(source.polylines.pop(idx))))
            extracted.sort(key=lambda item: item[0])
            moved_polylines.extend(poly for _idx, poly in extracted)
            source.entity_refs = []

        if not moved_polylines:
            return

        if target_layer == "geometry":
            for poly in moved_polylines:
                self.add_polyline_as_segments(poly, layer="geometry", merge_points=False)
        else:
            target.polylines.extend(self._clone_polylines(moved_polylines))
            target.entity_refs = []

        self._purge_orphan_points()

    def set_layer_polylines(
        self,
        name: str,
        polylines: list[list[tuple[float, float]]],
        *,
        entity_refs: list[EntityRef] | None = None,
        mark_dirty: bool = False,
    ) -> None:
        layer = self.ensure_layer(name)
        layer.polylines = self._clone_polylines(polylines)
        layer.entity_refs = list(entity_refs or [])
        layer.dirty = mark_dirty

    def get_layer_polylines(
        self,
        name: str,
        *,
        fallback_geometry: bool = True,
    ) -> list[list[tuple[float, float]]]:
        layer = self.ensure_layer(name)
        if layer.polylines:
            return self._clone_polylines(layer.polylines)
        if name == "geometry" and fallback_geometry:
            return self._geometry_polylines()
        return []

    def _mark_layer_dirty(self, name: str) -> None:
        self.ensure_layer(name).dirty = True

    # ── Primitives ────────────────────────────────────────────────────────

    def _add_point(self, x: float, y: float) -> PointNode:
        p = PointNode(id=self._next_id(), x=x, y=y)
        self.points[p.id] = p
        return p

    def _find_point_near(self, x: float, y: float, tolerance: float) -> PointNode | None:
        best: PointNode | None = None
        best_d = tolerance
        for point in self.points.values():
            d = ((point.x - x) ** 2 + (point.y - y) ** 2) ** 0.5
            if d <= best_d:
                best = point
                best_d = d
        return best

    def _add_segment(self, p0: int, p1: int, *, layer: str = "geometry") -> SegmentNode:
        if p0 not in self.points or p1 not in self.points:
            raise ValueError("Segment endpoints must exist as points.")
        seg = SegmentNode(id=self._next_id(), p0=p0, p1=p1, layer=layer)
        self.segments[seg.id] = seg
        self.ensure_layer(layer)
        return seg

    def _remove_segment(self, sid: int) -> None:
        self.segments.pop(sid, None)
        # Drop constraint edges targeting this segment
        to_remove = [
            cid
            for cid, edge in self.constraints.items()
            if edge.source == ("segment", sid) or edge.target == ("segment", sid)
        ]
        for cid in to_remove:
            self.constraints.pop(cid, None)
        self._purge_orphan_points()

    def _geometry_polylines(self) -> list[list[tuple[float, float]]]:
        polylines: list[list[tuple[float, float]]] = []
        for seg in sorted(self.segments.values(), key=lambda s: s.id):
            p0 = self.points.get(seg.p0)
            p1 = self.points.get(seg.p1)
            if p0 is None or p1 is None:
                continue
            polylines.append([(p0.x, p0.y), (p1.x, p1.y)])
        return polylines

    def add_polyline_as_segments(
        self,
        polyline: list[tuple[float, float]],
        *,
        layer: str = "geometry",
        merge_points: bool = False,
        tolerance: float = 1e-6,
    ) -> list[int]:
        if len(polyline) < 2:
            return []

        def _same(a: tuple[float, float], b: tuple[float, float]) -> bool:
            return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance

        is_closed = len(polyline) >= 3 and _same(polyline[0], polyline[-1])

        point_ids: list[int] = []
        for idx, (x, y) in enumerate(polyline):
            if is_closed and idx == len(polyline) - 1 and point_ids:
                point_ids.append(point_ids[0])
                continue

            if merge_points:
                existing = self._find_point_near(x, y, tolerance)
                if existing is not None:
                    point_ids.append(existing.id)
                    continue
            point_ids.append(self._add_point(x, y).id)

        seg_ids: list[int] = []
        for i in range(len(point_ids) - 1):
            if point_ids[i] == point_ids[i + 1]:
                continue
            seg = self._add_segment(point_ids[i], point_ids[i + 1], layer=layer)
            seg_ids.append(seg.id)
        return seg_ids

    # ── Params / Sources ─────────────────────────────────────────────────

    # ── Relationships ────────────────────────────────────────────────────

    def reachable_dependents(self, layers: set[str]) -> set[str]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self.derivations.values():
            adjacency[edge.source_layer].add(edge.target_layer)

        seen: set[str] = set()
        queue: deque[str] = deque(layers)
        while queue:
            node = queue.popleft()
            for nxt in adjacency.get(node, set()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue.append(nxt)
        return seen

    # ── Actions / transactions ───────────────────────────────────────────

    def record_action(
        self,
        action_type: str,
        payload: dict[str, Any],
        *,
        touched: list[EntityRef] | None = None,
        invalidated_layers: list[str] | None = None,
        user_initiated: bool = True,
    ) -> ActionRecord:
        rec = ActionRecord(
            id=self._next_id(),
            action_type=action_type,
            payload=dict(payload),
            touched=list(touched or []),
            invalidated_layers=list(invalidated_layers or []),
            user_initiated=user_initiated,
        )
        self.actions.append(rec)
        for layer in rec.invalidated_layers:
            self._mark_layer_dirty(layer)
        return rec

    # ── Serialization ────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        # Capture the current next_id without consuming it, so that
        # calling snapshot() multiple times does not advance the counter.
        next_id_value = next(self._id_counter)
        # Put it back by creating a new counter starting from the same value.
        saved_counter = self._id_counter
        self._id_counter = count(next_id_value)
        try:
            result = {
                "points": {pid: (p.x, p.y) for pid, p in self.points.items()},
                "segments": {
                    sid: (seg.p0, seg.p1, seg.layer) for sid, seg in self.segments.items()
                },
                "params": {
                    name: (node.id, node.value) for name, node in self.params.items()
                },
                "sources": {
                    sid: {
                        "kind": src.kind,
                        "payload": dict(src.payload),
                    }
                    for sid, src in self.sources.items()
                },
                "layers": {
                    name: {
                        "id": layer.id,
                        "polylines": self._clone_polylines(layer.polylines),
                        "entity_refs": list(layer.entity_refs),
                        "dirty": layer.dirty,
                    }
                    for name, layer in self.iter_layers()
                },
                "layer_order": list(self._ordered_layer_names()),
                "constraints": {
                    cid: {
                        "kind": edge.kind,
                        "source": edge.source,
                        "target": edge.target,
                        "data": dict(edge.data),
                    }
                    for cid, edge in self.constraints.items()
                },
                "derivations": {
                    did: {
                        "source_layer": edge.source_layer,
                        "target_layer": edge.target_layer,
                        "operator_name": edge.operator_name,
                        "data": dict(edge.data),
                    }
                    for did, edge in self.derivations.items()
                },
                "actions": [
                    {
                        "id": rec.id,
                        "action_type": rec.action_type,
                        "payload": dict(rec.payload),
                        "touched": list(rec.touched),
                        "invalidated_layers": list(rec.invalidated_layers),
                        "user_initiated": rec.user_initiated,
                    }
                    for rec in self.actions
                ],
                "active_layer": self.active_layer,
                "next_id": next_id_value,
            }
        finally:
            # Always restore the real counter, even if serialization fails.
            self._id_counter = saved_counter
        return result

    def restore(self, state: dict[str, Any]) -> None:
        self.points.clear()
        self.segments.clear()
        self.params.clear()
        self.sources.clear()
        self.layers.clear()
        self.constraints.clear()
        self.derivations.clear()
        self.actions.clear()

        for pid, (x, y) in state.get("points", {}).items():
            pid_i = int(pid)
            self.points[pid_i] = PointNode(id=pid_i, x=float(x), y=float(y))

        for sid, (p0, p1, layer) in state.get("segments", {}).items():
            sid_i = int(sid)
            self.segments[sid_i] = SegmentNode(
                id=sid_i,
                p0=int(p0),
                p1=int(p1),
                layer=str(layer),
            )

        for name, (nid, value) in state.get("params", {}).items():
            self.params[str(name)] = ParamNode(id=int(nid), name=str(name), value=value)

        for sid, payload in state.get("sources", {}).items():
            sid_i = int(sid)
            self.sources[sid_i] = SourceNode(
                id=sid_i,
                kind=str(payload.get("kind", "unknown")),
                payload=dict(payload.get("payload", {})),
            )

        for name, payload in state.get("layers", {}).items():
            self.layers[str(name)] = LayerNode(
                id=int(payload.get("id", self._next_id())),
                name=str(name),
                polylines=self._clone_polylines(payload.get("polylines", [])),
                entity_refs=list(payload.get("entity_refs", [])),
                dirty=bool(payload.get("dirty", False)),
            )

        layer_order = [str(name) for name in state.get("layer_order", [])]
        if not layer_order:
            layer_order = [str(name) for name in state.get("layers", {})]
        self.layer_order = [name for name in layer_order if name in self.layers]
        for name in self.layers:
            if name not in self.layer_order:
                self.layer_order.append(name)

        for cid, payload in state.get("constraints", {}).items():
            cid_i = int(cid)
            src_raw = payload.get("source")
            tgt_raw = payload.get("target")
            if isinstance(src_raw, tuple):
                source = src_raw
            else:
                source = ("point", 0)
            if isinstance(tgt_raw, tuple):
                target = tgt_raw
            else:
                target = ("point", 0)
            # Drop dangling constraint edges whose endpoints don't exist.
            if not self._entity_ref_exists(source) or not self._entity_ref_exists(
                target
            ):
                continue
            self.constraints[cid_i] = ConstraintEdge(
                id=cid_i,
                kind=str(payload.get("kind", "")),
                source=source,
                target=target,
                data=dict(payload.get("data", {})),
            )

        for did, payload in state.get("derivations", {}).items():
            did_i = int(did)
            self.derivations[did_i] = DerivationEdge(
                id=did_i,
                source_layer=str(payload.get("source_layer", "geometry")),
                target_layer=str(payload.get("target_layer", "derived")),
                operator_name=str(payload.get("operator_name", "unknown")),
                data=dict(payload.get("data", {})),
            )

        for payload in state.get("actions", []):
            self.actions.append(
                ActionRecord(
                    id=int(payload.get("id", self._next_id())),
                    action_type=str(payload.get("action_type", "unknown")),
                    payload=dict(payload.get("payload", {})),
                    touched=list(payload.get("touched", [])),
                    invalidated_layers=list(payload.get("invalidated_layers", [])),
                    user_initiated=bool(payload.get("user_initiated", True)),
                )
            )

        self.active_layer = str(state.get("active_layer", "geometry"))
        self.ensure_layer("geometry")
        self.ensure_layer(self.active_layer)

        if "geometry" in self.layer_order:
            self.layer_order.remove("geometry")
        self.layer_order.insert(0, "geometry")

        next_id = int(state.get("next_id", 1))
        self._id_counter = count(next_id)
