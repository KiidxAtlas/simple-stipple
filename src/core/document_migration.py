"""Migration helpers between legacy tab states and DocumentGraph."""

from __future__ import annotations

from src.core.document_graph import DocumentGraph


def graph_from_polylines(
    polylines: list[list[tuple[float, float]]],
    *,
    layer: str = "geometry",
    as_segments: bool = True,
) -> DocumentGraph:
    graph = DocumentGraph()
    graph.set_active_layer(layer)

    if as_segments and layer == "geometry":
        for poly in polylines:
            graph.add_polyline_as_segments(poly, layer="geometry", merge_points=False)
    else:
        graph.set_layer_polylines(layer, polylines)

    return graph


def graph_from_sketch(sketch_graph) -> DocumentGraph:
    """Convert `src.core.sketch.SketchGraph` into DocumentGraph.

    Uses direct point/segment copies and maps constraints as explicit edges.
    """
    graph = DocumentGraph()

    point_map: dict[int, int] = {}
    for old_pid, point in sorted(
        sketch_graph.points.items(), key=lambda item: int(item[0])
    ):
        created = graph.add_point(point.x, point.y)
        point_map[int(old_pid)] = created.id

    seg_map: dict[int, int] = {}
    for old_sid, seg in sorted(
        sketch_graph.segments.items(), key=lambda item: int(item[0])
    ):
        p0_id = point_map[int(seg.p0.id)]
        p1_id = point_map[int(seg.p1.id)]
        created = graph.add_segment(p0_id, p1_id, layer="geometry")
        seg_map[int(old_sid)] = created.id

    for constraint in sketch_graph.constraints.values():
        kind = getattr(constraint, "kind", type(constraint).__name__.lower())
        if hasattr(constraint, "segment"):
            seg_id = getattr(constraint.segment, "id", None)
            if seg_id is not None and int(seg_id) in seg_map:
                graph.add_constraint_edge(
                    kind,
                    ("segment", seg_map[int(seg_id)]),
                    ("layer", "geometry"),
                )
        elif hasattr(constraint, "p1") and hasattr(constraint, "p2"):
            graph.add_constraint_edge(
                kind,
                ("point", point_map[int(constraint.p1.id)]),
                ("point", point_map[int(constraint.p2.id)]),
            )

    return graph


def polylines_from_graph(
    graph: DocumentGraph, *, layer: str = "geometry"
) -> list[list[tuple[float, float]]]:
    return graph.get_layer_polylines(layer)
