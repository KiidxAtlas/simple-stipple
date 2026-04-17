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

EntityKind = Literal["point", "segment", "param", "source", "layer"]
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

        self.ensure_layer("geometry")

    def _next_id(self) -> int:
        return next(self._id_counter)

    @staticmethod
    def _clone_polylines(
        polylines: list[list[tuple[float, float]]],
    ) -> list[list[tuple[float, float]]]:
        return [list(poly) for poly in polylines]

    # ── Layers ────────────────────────────────────────────────────────────

    def ensure_layer(self, name: str) -> LayerNode:
        if name not in self.layers:
            self.layers[name] = LayerNode(id=self._next_id(), name=name)
        return self.layers[name]

    def set_active_layer(self, name: str) -> None:
        self.ensure_layer(name)
        self.active_layer = name

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
            return self.geometry_polylines()
        return []

    def mark_layer_dirty(self, name: str) -> None:
        self.ensure_layer(name).dirty = True

    def clear_layer_dirty(self, name: str) -> None:
        self.ensure_layer(name).dirty = False

    # ── Primitives ────────────────────────────────────────────────────────

    def add_point(self, x: float, y: float) -> PointNode:
        p = PointNode(id=self._next_id(), x=x, y=y)
        self.points[p.id] = p
        return p

    def remove_point(self, pid: int) -> None:
        if pid not in self.points:
            return
        seg_ids = [
            sid for sid, seg in self.segments.items() if seg.p0 == pid or seg.p1 == pid
        ]
        for sid in seg_ids:
            self.remove_segment(sid)
        self.points.pop(pid, None)

    def update_point(self, pid: int, x: float, y: float) -> None:
        p = self.points.get(pid)
        if p is None:
            return
        p.x = x
        p.y = y

    def find_point_near(self, x: float, y: float, tolerance: float) -> PointNode | None:
        best: PointNode | None = None
        best_d = tolerance
        for point in self.points.values():
            d = ((point.x - x) ** 2 + (point.y - y) ** 2) ** 0.5
            if d <= best_d:
                best = point
                best_d = d
        return best

    def add_segment(self, p0: int, p1: int, *, layer: str = "geometry") -> SegmentNode:
        if p0 not in self.points or p1 not in self.points:
            raise ValueError("Segment endpoints must exist as points.")
        seg = SegmentNode(id=self._next_id(), p0=p0, p1=p1, layer=layer)
        self.segments[seg.id] = seg
        self.ensure_layer(layer)
        return seg

    def remove_segment(self, sid: int) -> None:
        self.segments.pop(sid, None)
        # Drop constraint edges targeting this segment
        to_remove = [
            cid
            for cid, edge in self.constraints.items()
            if edge.source == ("segment", sid) or edge.target == ("segment", sid)
        ]
        for cid in to_remove:
            self.constraints.pop(cid, None)

    def geometry_polylines(self) -> list[list[tuple[float, float]]]:
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
                existing = self.find_point_near(x, y, tolerance)
                if existing is not None:
                    point_ids.append(existing.id)
                    continue
            point_ids.append(self.add_point(x, y).id)

        seg_ids: list[int] = []
        for i in range(len(point_ids) - 1):
            if point_ids[i] == point_ids[i + 1]:
                continue
            seg = self.add_segment(point_ids[i], point_ids[i + 1], layer=layer)
            seg_ids.append(seg.id)
        return seg_ids

    # ── Params / Sources ─────────────────────────────────────────────────

    def upsert_param(self, name: str, value: Any) -> ParamNode:
        if name in self.params:
            self.params[name].value = value
            return self.params[name]
        node = ParamNode(id=self._next_id(), name=name, value=value)
        self.params[name] = node
        return node

    # ── Relationships ────────────────────────────────────────────────────

    def add_derivation_edge(
        self,
        source_layer: str,
        target_layer: str,
        operator_name: str,
        data: dict[str, Any] | None = None,
    ) -> DerivationEdge:
        self.ensure_layer(source_layer)
        self.ensure_layer(target_layer)
        edge = DerivationEdge(
            id=self._next_id(),
            source_layer=source_layer,
            target_layer=target_layer,
            operator_name=operator_name,
            data=dict(data or {}),
        )
        self.derivations[edge.id] = edge
        return edge

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
            self.mark_layer_dirty(layer)
        return rec

    # ── Serialization ────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
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
                for name, layer in self.layers.items()
            },
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
            "next_id": next(self._id_counter),
        }

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

        for cid, payload in state.get("constraints", {}).items():
            cid_i = int(cid)
            self.constraints[cid_i] = ConstraintEdge(
                id=cid_i,
                kind=str(payload.get("kind", "")),
                source=tuple(payload.get("source", ("point", 0))),  # type: ignore[arg-type]
                target=tuple(payload.get("target", ("point", 0))),  # type: ignore[arg-type]
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

        next_id = int(state.get("next_id", 1))
        self._id_counter = count(next_id)
