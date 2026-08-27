"""Stable public facade for shared UI components.

Implementations live in concern-specific modules. Production code should
prefer those concrete modules; this facade remains for external consumers.
"""

from simple_stipple.ui.components.feedback import (
    clear_line_edit_error,
    parse_float_field,
    parse_float_field_with_feedback,
    refresh_style,
    set_line_edit_error,
)
from simple_stipple.ui.components.focus import (
    EscapeBlurFilter,
    blur_focused_line_edit,
    install_dialog_focus_lifecycle,
)
from simple_stipple.ui.components.icons import (
    download_icon,
    gear_icon,
    icon_from_painter,
    tool_icon,
)
from simple_stipple.ui.components.inputs import (
    ActionButton,
    browse_row,
    make_resettable_line_edit,
    primary_button,
)
from simple_stipple.ui.components.layout import (
    CollapsibleSection,
    ResponsiveContentSplitter,
    collapsible_content_widget,
    content_splitter,
    empty_state,
    info_chip,
    section_label,
    sep,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.recent import RecentFilesButton
from simple_stipple.ui.components.workflow import OperationProgress, StatusRegion, set_status_label
from simple_stipple.ui.style import (
    MOTION_DURATION_MS,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
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
    "blur_focused_line_edit",
    "browse_row",
    "clear_line_edit_error",
    "collapsible_content_widget",
    "content_splitter",
    "download_icon",
    "empty_state",
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
]
