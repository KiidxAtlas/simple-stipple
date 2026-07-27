"""Stable public facade for shared UI components.

Implementations live in concern-specific modules. Production code should
prefer those concrete modules; this facade remains for external consumers.
"""

from .collapsible import CollapsibleSection, collapsible_content_widget
from .feedback import (
    clear_line_edit_error,
    parse_float_field,
    parse_float_field_with_feedback,
    refresh_style,
    set_line_edit_error,
)
from .focus import EscapeBlurFilter, blur_focused_line_edit, install_dialog_focus_lifecycle
from .icons import download_icon, gear_icon, icon_from_painter, tool_icon
from .inputs import ActionButton, browse_row, make_resettable_line_edit, primary_button
from .layout import (
    ResponsiveContentSplitter,
    content_splitter,
    info_chip,
    section_label,
    sep,
    sidebar_panel,
    surface_frame,
)
from .recent_files import RecentFilesButton
from .tokens import MOTION_DURATION_MS, SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XL, SPACE_XS
from .workflow import (
    OperationProgress,
    StatusRegion,
    WorkflowStepper,
    set_status_label,
    workflow_strip,
)

__all__ = [
    "ActionButton",
    "CollapsibleSection",
    "EscapeBlurFilter",
    "MOTION_DURATION_MS",
    "OperationProgress",
    "RecentFilesButton",
    "ResponsiveContentSplitter",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "StatusRegion",
    "WorkflowStepper",
    "blur_focused_line_edit",
    "browse_row",
    "clear_line_edit_error",
    "collapsible_content_widget",
    "content_splitter",
    "download_icon",
    "gear_icon",
    "icon_from_painter",
    "info_chip",
    "install_dialog_focus_lifecycle",
    "make_resettable_line_edit",
    "parse_float_field",
    "parse_float_field_with_feedback",
    "primary_button",
    "refresh_style",
    "section_label",
    "sep",
    "set_line_edit_error",
    "set_status_label",
    "sidebar_panel",
    "surface_frame",
    "tool_icon",
    "workflow_strip",
]
