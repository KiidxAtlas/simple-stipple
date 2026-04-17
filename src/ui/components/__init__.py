"""Reusable UI component exports."""

from src.ui.components.action_maps import (
    IMAGE_ACTION_MAP,
    PATTERN_ACTION_MAP,
    SHAPE_ACTION_MAP,
    UTILITIES_ACTION_MAP,
)
from src.ui.components.containers import (
    CanvasObjectBrowser,
    CanvasPrecisionBar,
    CanvasStatusStrip,
    CollapsibleSection,
    DxfLayersTree,
)
from src.ui.components.factories import (
    _canvas_toolbar,
    _content_splitter,
    _info_chip,
    _section_label,
    _sep,
    _sidebar_panel,
    _surface_frame,
    clear_line_edit_error,
    parse_float_field,
    set_line_edit_error,
)
from src.ui.components.trace_form import (
    PathField,
    TextField,
    build_lazy_section,
)

__all__ = [
    "IMAGE_ACTION_MAP",
    "PATTERN_ACTION_MAP",
    "SHAPE_ACTION_MAP",
    "UTILITIES_ACTION_MAP",
    "CanvasObjectBrowser",
    "CanvasPrecisionBar",
    "CanvasStatusStrip",
    "CollapsibleSection",
    "DxfLayersTree",
    "PathField",
    "TextField",
    "_canvas_toolbar",
    "_content_splitter",
    "_info_chip",
    "_section_label",
    "_sep",
    "_sidebar_panel",
    "_surface_frame",
    "build_lazy_section",
    "clear_line_edit_error",
    "parse_float_field",
    "set_line_edit_error",
]
