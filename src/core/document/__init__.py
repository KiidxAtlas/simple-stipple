"""Document graph and action layer exports."""

from src.core.document.actions import (
    ActionResult,
    ActionType,
    apply_transform,
    create_segment,
    create_shape,
    delete_entities,
    replace_layer_polylines,
    set_active_layer,
    set_param,
)
from src.core.document.derive import (
    derive_layer,
    derive_objects_from_geometry,
    propagate_invalidation,
)
from src.core.document.graph import DocumentGraph, EntityKind, EntityRef
from src.core.document.migration import graph_from_polylines, polylines_from_graph
from src.core.document.state import (
    WORKSPACE_FILE_SUFFIX,
    WORKSPACE_SCHEMA_VERSION,
    build_workspace_document,
    empty_workspace_document,
    normalize_workspace_path,
    validate_workspace_document,
)

__all__ = [
    "WORKSPACE_FILE_SUFFIX",
    "WORKSPACE_SCHEMA_VERSION",
    "ActionResult",
    "ActionType",
    "DocumentGraph",
    "EntityKind",
    "EntityRef",
    "apply_transform",
    "build_workspace_document",
    "create_segment",
    "create_shape",
    "delete_entities",
    "derive_layer",
    "derive_objects_from_geometry",
    "empty_workspace_document",
    "graph_from_polylines",
    "normalize_workspace_path",
    "polylines_from_graph",
    "propagate_invalidation",
    "replace_layer_polylines",
    "set_active_layer",
    "set_param",
    "validate_workspace_document",
]
