"""Shared UI widgets package."""

from __future__ import annotations

from src.ui.widgets.collapsible import CollapsibleSection
from src.ui.widgets.command_palette import CommandPaletteDialog
from src.ui.widgets.layer_tree import (
    CanvasLayerSidebarController,
    DxfLayersTree,
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
from src.ui.widgets.precision_bar import CanvasPrecisionBar
from src.ui.widgets.recent_files_button import RecentFilesButton
from src.ui.widgets.status_strip import CanvasStatusStrip
from src.ui.widgets.update_dialog import UpdateDialog

__all__ = [
    "CanvasLayerSidebarController",
    "CanvasPrecisionBar",
    "CanvasStatusStrip",
    "CollapsibleSection",
    "CommandPaletteDialog",
    "DxfLayersTree",
    "LayerRowsBuilder",
    "LayerTreeRow",
    "LayerTreeState",
    "RecentFilesButton",
    "UpdateDialog",
    "apply_layer_visibility",
    "apply_shape_visibility",
    "build_layer_row",
    "build_shape_rows",
    "describe_polyline",
    "hidden_bucket",
]
