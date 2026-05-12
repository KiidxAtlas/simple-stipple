"""Deterministic action protocol for DocumentGraph."""

from __future__ import annotations

from dataclasses import dataclass

from src.backend.document.graph import DocumentGraph, EntityRef


class ActionType:
    CREATE_LAYER = "create_layer"
    DELETE_LAYER = "delete_layer"
    RENAME_LAYER = "rename_layer"
    REORDER_LAYER = "reorder_layer"
    MOVE_LAYER_ENTITIES = "move_layer_entities"
    SET_ACTIVE_LAYER = "set_active_layer"
    REPLACE_LAYER_POLYLINES = "replace_layer_polylines"


@dataclass
class ActionResult:
    action_id: int
    touched: list[EntityRef]
    invalidated_layers: list[str]


def set_active_layer(graph: DocumentGraph, layer: str) -> ActionResult:
    graph.set_active_layer(layer)
    rec = graph.record_action(
        ActionType.SET_ACTIVE_LAYER,
        {"layer": layer},
        touched=[("layer", layer)],
        invalidated_layers=[],
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def create_layer(
    graph: DocumentGraph,
    name: str,
    *,
    activate: bool = True,
) -> ActionResult:
    created = name not in graph.layers
    graph.ensure_layer(name)
    if activate:
        graph.set_active_layer(name)
    rec = graph.record_action(
        ActionType.CREATE_LAYER,
        {"name": name, "activate": activate, "created": created},
        touched=[("layer", name)],
        invalidated_layers=[],
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def rename_layer(
    graph: DocumentGraph,
    old_name: str,
    new_name: str,
) -> ActionResult:
    invalidated = sorted(graph.reachable_dependents({old_name}))
    graph.rename_layer(old_name, new_name)
    rec = graph.record_action(
        ActionType.RENAME_LAYER,
        {"old_name": old_name, "new_name": new_name},
        touched=[("layer", new_name)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def delete_layer(
    graph: DocumentGraph,
    name: str,
    *,
    fallback_layer: str = "geometry",
) -> ActionResult:
    invalidated = sorted(graph.reachable_dependents({name}))
    graph.delete_layer(name, fallback_layer=fallback_layer)
    rec = graph.record_action(
        ActionType.DELETE_LAYER,
        {"name": name, "fallback_layer": fallback_layer},
        touched=[("layer", name)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def reorder_layer(
    graph: DocumentGraph,
    name: str,
    new_index: int,
) -> ActionResult:
    graph.move_layer(name, new_index)
    rec = graph.record_action(
        ActionType.REORDER_LAYER,
        {"name": name, "new_index": new_index},
        touched=[("layer", name)],
        invalidated_layers=[],
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def move_entities_to_layer(
    graph: DocumentGraph,
    refs: list[EntityRef],
    *,
    source_layer: str,
    target_layer: str,
) -> ActionResult:
    invalidated = sorted(graph.reachable_dependents({source_layer, target_layer}))
    graph.move_entities_to_layer(
        refs,
        source_layer=source_layer,
        target_layer=target_layer,
    )
    rec = graph.record_action(
        ActionType.MOVE_LAYER_ENTITIES,
        {
            "refs": refs,
            "source_layer": source_layer,
            "target_layer": target_layer,
        },
        touched=list(refs) + [("layer", source_layer), ("layer", target_layer)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)


def replace_layer_polylines(
    graph: DocumentGraph,
    layer: str,
    polylines: list[list[tuple[float, float]]],
) -> ActionResult:
    graph.set_layer_polylines(layer, polylines)
    invalidated = sorted(graph.reachable_dependents({layer}))
    rec = graph.record_action(
        ActionType.REPLACE_LAYER_POLYLINES,
        {"layer": layer, "count": len(polylines)},
        touched=[("layer", layer)],
        invalidated_layers=invalidated,
    )
    return ActionResult(rec.id, rec.touched, rec.invalidated_layers)
