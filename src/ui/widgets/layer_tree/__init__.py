"""Layer tree — re-exports all public symbols for backward compatibility."""

from .model import (
    LayerRowsBuilder,
    LayerTreeRow,
    LayerTreeState,
    apply_layer_visibility,
    apply_shape_visibility,
    build_layer_row,
    build_shape_rows,
    describe_polyline,
    hidden_bucket,
)
from .widget import DxfLayersTree
from .controller import CanvasLayerSidebarController

__all__ = [
    "LayerTreeState",
    "LayerTreeRow",
    "LayerRowsBuilder",
    "hidden_bucket",
    "apply_layer_visibility",
    "apply_shape_visibility",
    "build_shape_rows",
    "build_layer_row",
    "describe_polyline",
    "DxfLayersTree",
    "CanvasLayerSidebarController",
]
