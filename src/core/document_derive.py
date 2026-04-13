"""Derived layer operations for DocumentGraph."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable

from src.core.document_graph import DocumentGraph

Polyline = list[tuple[float, float]]
Operator = Callable[[list[Polyline], dict], list[Polyline]]


def _segment_components(graph: DocumentGraph) -> list[list[int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    point_to_segments: dict[int, set[int]] = defaultdict(set)

    for sid, seg in graph.segments.items():
        point_to_segments[seg.p0].add(sid)
        point_to_segments[seg.p1].add(sid)

    for seg_ids in point_to_segments.values():
        seg_list = list(seg_ids)
        for i in range(len(seg_list)):
            for j in range(i + 1, len(seg_list)):
                a = seg_list[i]
                b = seg_list[j]
                adjacency[a].add(b)
                adjacency[b].add(a)

    visited: set[int] = set()
    components: list[list[int]] = []
    for sid in graph.segments:
        if sid in visited:
            continue
        queue = deque([sid])
        comp: list[int] = []
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            comp.append(cur)
            for nxt in adjacency.get(cur, set()):
                if nxt not in visited:
                    queue.append(nxt)
        components.append(comp)
    return components


def derive_objects_from_geometry(
    graph: DocumentGraph,
    *,
    target_layer: str = "objects",
) -> list[Polyline]:
    """Derive closed object loops from geometry segments.

    Closure criterion is topological: every endpoint degree == 2 in component.
    """
    result: list[Polyline] = []

    for comp in _segment_components(graph):
        if len(comp) < 3:
            continue

        degree: dict[int, int] = defaultdict(int)
        comp_segs = [graph.segments[sid] for sid in comp if sid in graph.segments]
        for seg in comp_segs:
            degree[seg.p0] += 1
            degree[seg.p1] += 1

        if not degree or not all(d == 2 for d in degree.values()):
            continue

        # Build an ordered loop by walking segment graph
        start_seg = comp_segs[0]
        start_pid = start_seg.p0
        current_pid = start_pid
        used: set[int] = set()
        loop_pts: list[tuple[float, float]] = []

        while True:
            point = graph.points.get(current_pid)
            if point is None:
                break
            loop_pts.append((point.x, point.y))

            # Find next unused segment touching current point
            next_sid = None
            next_pid = None
            for seg in comp_segs:
                if seg.id in used:
                    continue
                if seg.p0 == current_pid:
                    next_sid = seg.id
                    next_pid = seg.p1
                    break
                if seg.p1 == current_pid:
                    next_sid = seg.id
                    next_pid = seg.p0
                    break
            if next_sid is None or next_pid is None:
                break

            used.add(next_sid)
            current_pid = next_pid

            if current_pid == start_pid:
                p = graph.points.get(start_pid)
                if p is not None:
                    loop_pts.append((p.x, p.y))
                break

        if len(loop_pts) >= 4 and loop_pts[0] == loop_pts[-1]:
            result.append(loop_pts)

    graph.set_layer_polylines(target_layer, result)
    graph.add_derivation_edge("geometry", target_layer, "derive_objects_from_geometry")
    graph.clear_layer_dirty(target_layer)
    return result


def derive_layer(
    graph: DocumentGraph,
    *,
    source_layer: str,
    target_layer: str,
    operator_name: str,
    operator: Operator,
    params: dict | None = None,
) -> list[Polyline]:
    """Derive target layer by applying operator(source_polylines, params)."""
    source = graph.get_layer_polylines(source_layer)
    result = operator(source, dict(params or {}))
    graph.set_layer_polylines(target_layer, result)
    graph.add_derivation_edge(
        source_layer, target_layer, operator_name, dict(params or {})
    )
    graph.clear_layer_dirty(target_layer)
    return result


def propagate_invalidation(graph: DocumentGraph, changed_layers: set[str]) -> set[str]:
    """Mark all reachable dependents dirty and return affected layer names."""
    affected = graph.reachable_dependents(changed_layers)
    for layer_name in affected:
        graph.mark_layer_dirty(layer_name)
    return affected
