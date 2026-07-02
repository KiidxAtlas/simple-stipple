"""Layer operations on a DocumentGraph.

These used to wrap every call in an ActionRecord protocol (action ids,
touched refs, invalidated-layer tracking) that nothing ever read; the
wrappers now delegate directly.
"""

from __future__ import annotations

from src.backend.document.graph import DocumentGraph, EntityRef


def set_active_layer(graph: DocumentGraph, layer: str) -> None:
    graph.set_active_layer(layer)


def create_layer(graph: DocumentGraph, name: str, *, activate: bool = True) -> None:
    graph.ensure_layer(name)
    if activate:
        graph.set_active_layer(name)


def rename_layer(graph: DocumentGraph, old_name: str, new_name: str) -> None:
    graph.rename_layer(old_name, new_name)


def delete_layer(
    graph: DocumentGraph, name: str, *, fallback_layer: str = "geometry"
) -> None:
    graph.delete_layer(name, fallback_layer=fallback_layer)


def reorder_layer(graph: DocumentGraph, name: str, new_index: int) -> None:
    graph.move_layer(name, new_index)


def move_entities_to_layer(
    graph: DocumentGraph,
    refs: list[EntityRef],
    *,
    source_layer: str,
    target_layer: str,
) -> None:
    graph.move_entities_to_layer(
        refs, source_layer=source_layer, target_layer=target_layer
    )


def replace_layer_polylines(
    graph: DocumentGraph,
    layer: str,
    polylines: list[list[tuple[float, float]]],
) -> None:
    graph.set_layer_polylines(layer, polylines)
