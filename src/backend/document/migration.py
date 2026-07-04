"""Legacy-workspace read helpers (DocumentGraph is no longer written).

Current sessions store flat polyline lists / entity records directly;
these helpers only exist so old workspace files keep loading.
"""

from __future__ import annotations

from src.backend.document.graph import DocumentGraph


def polylines_from_graph(
    graph: DocumentGraph, *, layer: str = "geometry"
) -> list[list[tuple[float, float]]]:
    """Extract layer polylines from a ``DocumentGraph``."""
    return graph.get_layer_polylines(layer)
