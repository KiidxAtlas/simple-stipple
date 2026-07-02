"""Migration helpers between legacy tab states and DocumentGraph."""

from __future__ import annotations

from src.backend.document.graph import DocumentGraph


def graph_from_polylines(
    polylines: list[list[tuple[float, float]]],
    *,
    layer: str = "geometry",
    as_segments: bool = True,  # kept for call-site compatibility; unused
) -> DocumentGraph:
    """Build a ``DocumentGraph`` from polyline geometry for a target layer."""
    graph = DocumentGraph()
    graph.set_active_layer(layer)
    graph.set_layer_polylines(layer, polylines)
    return graph


def polylines_from_graph(
    graph: DocumentGraph, *, layer: str = "geometry"
) -> list[list[tuple[float, float]]]:
    """Extract layer polylines from a ``DocumentGraph``."""
    return graph.get_layer_polylines(layer)
