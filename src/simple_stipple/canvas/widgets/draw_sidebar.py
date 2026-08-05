"""Draw sidebar — compact, icon-first, grouped controls.

Every multi-state control (tool-family pickers, arc mode, constraint mode,
split) uses ``CycleIconButton``: left-click cycles, right-click opens a
small modal for direct selection, hovering shows a flyout preview of every
state. Single-purpose actions (Text, Dimension, the contextual
polyline-editing row) use the same widget with one state so the visuals
stay consistent.

Snap toggles (master/grid/vertex/edge/angle), Construction, and Scale
live only in the Precision bar (``simple_stipple.canvas.widgets.precision_bar``) — it's
docked in every mode, not just Draw, so it's the single home for those
instead of duplicating them here.

The panel's width is user-resizable (drag the right edge) and its sections
can be shown/hidden and reordered — see ``simple_stipple.ui.dialogs.settings_dialog``'s
"Customize draw sidebar…" button and
``src.settings.DEFAULT_DRAW_SIDEBAR_SECTIONS``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.platform.config import (
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    MAX_DRAW_SIDEBAR_HEIGHT,
    MAX_DRAW_SIDEBAR_WIDTH,
    MIN_DRAW_SIDEBAR_HEIGHT,
    MIN_DRAW_SIDEBAR_WIDTH,
    normalize_draw_sidebar_shape_tools,
)
from simple_stipple.ui.components.cycle_button import CycleIconButton, StateEntry
from simple_stipple.ui.components.icons import tool_icon


def _state(icon_name: str, state_id: str, label: str) -> StateEntry:
    return (state_id, tool_icon(icon_name), label)


def _toggle_states(icon_name: str, label: str) -> list[StateEntry]:
    """A 2-state (off/on) cycle sharing one icon — CycleIconButton's own
    checked styling communicates on/off, so both states use the same
    glyph and differ only in id/label."""
    icon = tool_icon(icon_name)
    return [("off", icon, f"{label}: Off"), ("on", icon, f"{label}: On")]


class _ResizeHandle(QFrame):
    """Wide invisible hit target with a thin visual resize affordance."""

    def __init__(self, sidebar: DrawSidebar) -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar
        self._dragging = False
        self._drag_start_global_x = 0.0
        self._drag_start_width = 0
        self.setFixedWidth(24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Resize draw sidebar width")
        self.setAccessibleDescription(
            "Use Left and Right arrows to resize. Hold Shift for larger steps."
        )
        self.setToolTip("Drag to resize · Left/Right keys resize · Shift for larger steps")
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setProperty("role", "drawer-resize-handle")
        self.setProperty("edge", "right")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = 24 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 8
        if event.key() == Qt.Key.Key_Left:
            self._sidebar._apply_width(self._sidebar.width() - step)
        elif event.key() == Qt.Key.Key_Right:
            self._sidebar._apply_width(self._sidebar.width() + step)
        elif event.key() == Qt.Key.Key_Home:
            self._sidebar._apply_width(MIN_DRAW_SIDEBAR_WIDTH)
        elif event.key() == Qt.Key.Key_End:
            self._sidebar._apply_width(MAX_DRAW_SIDEBAR_WIDTH)
        else:
            super().keyPressEvent(event)
            return
        self._sidebar._on_width_committed()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_global_x = event.globalPosition().x()
            self._drag_start_width = self._sidebar.width()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        delta = event.globalPosition().x() - self._drag_start_global_x
        self._sidebar._apply_width(int(self._drag_start_width + delta))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._sidebar._on_width_committed()


class _BottomResizeHandle(QFrame):
    """Thin drag handle docked to the sidebar's bottom edge — vertical
    resize, mirrors _ResizeHandle's horizontal drag math."""

    def __init__(self, sidebar: DrawSidebar) -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar
        self._dragging = False
        self._drag_start_global_y = 0.0
        self._drag_start_height = 0
        self.setFixedHeight(24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Resize draw sidebar height")
        self.setAccessibleDescription(
            "Use Up and Down arrows to resize. Hold Shift for larger steps."
        )
        self.setToolTip("Drag to resize · Up/Down keys resize · Shift for larger steps")
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setProperty("role", "drawer-resize-handle")
        self.setProperty("edge", "bottom")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = 24 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 8
        if event.key() == Qt.Key.Key_Up:
            self._sidebar._apply_height(self._sidebar.height() - step)
        elif event.key() == Qt.Key.Key_Down:
            self._sidebar._apply_height(self._sidebar.height() + step)
        elif event.key() == Qt.Key.Key_Home:
            self._sidebar._apply_height(MIN_DRAW_SIDEBAR_HEIGHT)
        elif event.key() == Qt.Key.Key_End:
            self._sidebar._apply_height(MAX_DRAW_SIDEBAR_HEIGHT)
        else:
            super().keyPressEvent(event)
            return
        self._sidebar._on_height_committed()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_global_y = event.globalPosition().y()
            self._drag_start_height = self._sidebar.height()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        delta = event.globalPosition().y() - self._drag_start_global_y
        self._sidebar._apply_height(int(self._drag_start_height + delta))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._sidebar._on_height_committed()


class DrawSidebar(QFrame):
    """Compact icon-grid draw sidebar."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        on_polyline_family: Callable[[str], None],
        on_shapes_family: Callable[[str], None],
        on_text: Callable[[], None],
        on_arc_mode: Callable[[str], None],
        on_constraint: Callable[[str], None],
        on_split: Callable[[bool], None],
        on_dimension: Callable[[], None],
        on_smoothing_method: Callable[[str], None],
        on_finish_open: Callable[[], None],
        on_close_edit: Callable[[], None],
        on_undo_point: Callable[[], None],
        on_cancel_draw: Callable[[], None],
        on_back_to_select: Callable[[], None],
        width: int | None = None,
        sections: list[str] | None = None,
        path_tools: list[str] | None = None,
        shape_tools: list[str] | None = None,
        on_width_changed: Callable[[int], None] | None = None,
        on_height_changed: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)

        self._on_width_changed = on_width_changed
        self._on_height_changed = on_height_changed
        self._sections = list(sections) if sections else list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        self._path_tools = [
            t for t in (path_tools or []) if t in DEFAULT_DRAW_SIDEBAR_PATH_TOOLS
        ] or list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
        self._shape_tools = normalize_draw_sidebar_shape_tools(shape_tools)

        self.setObjectName("draw-side-panel")
        self.setProperty("role", "draw-sidebar")
        self._apply_width(width if width is not None else MIN_DRAW_SIDEBAR_WIDTH + 12)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        scroll = QScrollArea(self)
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Keep a fallback for translated/long tool names; otherwise the
        # narrow drawer silently clips labels and controls at its minimum
        # width instead of exposing the full content.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget(scroll)
        self._content = content
        col = QVBoxLayout(content)
        # The scroll area reserves its own 8 px scrollbar. This content gutter
        # keeps controls visually separate without pretending padding can
        # compensate for an undersized viewport (the minimum-width contract
        # below guarantees both 44 px tool columns actually fit).
        col.setContentsMargins(4, 4, 8, 4)
        col.setSpacing(16)

        title = QLabel("Draw")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Build every control unconditionally (state-sync methods called
        # from view.py must never crash just because a section is hidden),
        # but only *add* a section's frame to the layout — in the
        # configured order — if it's actually enabled. ─────────────────────

        # Polyline/Shapes are direct-select icon grids, not cycle buttons:
        # a cycle button always advances on click, so clicking the tool
        # that's *already* shown (the common case — you open Draw wanting
        # the default/last tool) instead switched to the next one. Each
        # icon here is its own single-state CycleIconButton (same pattern
        # already used for Text/Dimension/Scale/the editing row below) —
        # clicking it always re-selects that exact tool, no cycling, and
        # set_active_tool() drives which one shows checked/highlighted.
        self._polyline_buttons: dict[str, CycleIconButton] = {
            tool_id: CycleIconButton(
                [_state(icon_name, tool_id, label)],
                (lambda tid: lambda _sid: on_polyline_family(tid))(tool_id),
            )
            for tool_id, icon_name, label in (
                ("polyline", "polyline", "Polyline"),
                ("spline", "spline", "Spline"),
                ("arc", "arc", "Arc"),
                ("bezier", "bezier", "Bezier Pen"),
            )
        }
        self._arc_mode_button = CycleIconButton(
            [
                _state("arc", "3point", "Arc: 3-Point"),
                _state("arc", "center-start-end", "Arc: Center→Start→End"),
            ],
            on_arc_mode,
        )
        self._shapes_buttons: dict[str, CycleIconButton] = {
            tool_id: CycleIconButton(
                [_state(icon_name, tool_id, label)],
                (lambda tid: lambda _sid: on_shapes_family(tid))(tool_id),
            )
            for tool_id, icon_name, label in (
                ("rectangle", "rectangle", "Rectangle"),
                ("rounded_rectangle", "rounded_rectangle", "Rounded Rectangle"),
                ("slot", "slot", "Slot"),
                ("circle", "circle", "Circle"),
                ("ellipse", "ellipse", "Ellipse"),
                ("polygon", "polygon", "Polygon"),
                ("star", "star", "Star"),
            )
        }
        self._text_button = CycleIconButton(
            [_state("text", "text", "Text")], lambda _sid: on_text()
        )
        self._constraint_button = CycleIconButton(
            [
                _state("constraint", "Free", "Constraint: Free"),
                _state("constraint", "H", "Constraint: Horizontal"),
                _state("constraint", "V", "Constraint: Vertical"),
                _state("constraint", "45", "Constraint: 45°"),
            ],
            on_constraint,
        )
        self._split_button = CycleIconButton(
            _toggle_states("split", "Auto-split"), self._bool_cb(on_split)
        )
        self._dimension_button = CycleIconButton(
            [_state("dimension", "dimension", "Sketch Dimension (Shift+M)")],
            lambda _sid: on_dimension(),
        )
        self._smoothing_button = CycleIconButton(
            [
                _state("smooth_chaikin", "chaikin", "Smoothing: Chaikin"),
                _state("smooth_gaussian", "gaussian", "Smoothing: Gaussian"),
                _state("smooth_catmull", "catmull_rom", "Smoothing: Catmull-Rom"),
            ],
            on_smoothing_method,
        )
        self._finish_button = CycleIconButton(
            [_state("finish", "finish", "Finish open polyline")],
            lambda _sid: on_finish_open(),
        )
        self._close_button = CycleIconButton(
            [_state("close_path", "close", "Close into a shape")],
            lambda _sid: on_close_edit(),
        )
        self._undo_button = CycleIconButton(
            [_state("undo_point", "undo", "Undo last point")],
            lambda _sid: on_undo_point(),
        )
        self._cancel_button = CycleIconButton(
            [_state("cancel", "cancel", "Cancel draw")], lambda _sid: on_cancel_draw()
        )
        self._select_button = CycleIconButton(
            [_state("select_arrow", "select", "Back to Select")],
            lambda _sid: on_back_to_select(),
        )

        path_buttons = [self._polyline_buttons[t] for t in self._path_tools]
        if "arc" in self._path_tools:
            path_buttons.append(self._arc_mode_button)
        shape_buttons = [self._shapes_buttons[t] for t in self._shape_tools]

        section_frames: dict[str, QWidget] = {
            "path": self._section("Path", path_buttons, columns=2),
            "shapes": self._section("Shapes", shape_buttons, columns=2),
            "text": self._section("Text", [self._text_button]),
            # Snap master/grid/vertex/edge/angle toggles live only in the
            # Precision bar now (docked in every mode, not just Draw) — this
            # section keeps its settings key for backward-compatible saved
            # section lists, but only Constraint (unique to Draw) remains.
            "snapping": self._section("Constraint", [self._constraint_button]),
            "mode": self._section("Mode", [self._split_button]),
            "sketch": self._section("Sketch", [self._dimension_button]),
            "smoothing": self._section("Smoothing", [self._smoothing_button]),
            "editing": self._section(
                "Editing",
                [
                    self._finish_button,
                    self._close_button,
                    self._undo_button,
                    self._cancel_button,
                    self._select_button,
                ],
                columns=2,
            ),
        }
        for key in self._sections:
            frame = section_frames.get(key)
            if frame is not None:
                col.addWidget(frame)

        col.addStretch()
        scroll.setWidget(content)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(scroll, stretch=1)
        row.addWidget(_ResizeHandle(self))
        outer.addLayout(row, stretch=1)
        outer.addWidget(_BottomResizeHandle(self))

    # -- resize --------------------------------------------------------------

    def _apply_width(self, width: int) -> None:
        clamped = max(MIN_DRAW_SIDEBAR_WIDTH, min(MAX_DRAW_SIDEBAR_WIDTH, width))
        self.setFixedWidth(clamped)

    def _on_width_committed(self) -> None:
        if self._on_width_changed is not None:
            self._on_width_changed(self.width())

    def _apply_height(self, height: int) -> None:
        clamped = max(MIN_DRAW_SIDEBAR_HEIGHT, min(MAX_DRAW_SIDEBAR_HEIGHT, height))
        self.setFixedHeight(clamped)

    def _on_height_committed(self) -> None:
        if self._on_height_changed is not None:
            self._on_height_changed(self.height())

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _bool_cb(fn: Callable[[bool], None]) -> Callable[[str], None]:
        """Adapt a bool-taking callback to a 2-state ("off"/"on")
        CycleIconButton's str-id contract."""
        return lambda sid: fn(sid == "on")

    def _section(
        self, caption: str, buttons: list[CycleIconButton], *, columns: int = 1
    ) -> QWidget:
        # Deliberately borderless: proximity, whitespace, and typography do
        # the grouping instead of nesting a card inside the sidebar card.
        section = QWidget(self)
        section.setProperty("role", "draw-section")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # Qt QSS doesn't support text-transform, so the uppercase small-caps
        # look this role's letter-spacing implies has to come from the text
        # itself, not the stylesheet.
        label = QLabel(caption.upper())
        label.setProperty("role", "section-title")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # Word-wrap defensively: a caption that renders wider than the
        # sidebar under the real system font shouldn't be able to force
        # the whole panel wider — wrapping keeps the layout's width fixed.
        label.setWordWrap(True)
        layout.addWidget(label)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for i, btn in enumerate(buttons):
            grid.addWidget(btn, i // columns, i % columns)
        grid_wrap = QWidget(section)
        grid_wrap.setLayout(grid)
        layout.addWidget(grid_wrap, alignment=Qt.AlignmentFlag.AlignHCenter)
        return section

    # -- state sync (called from view.py) ------------------------------------

    def set_active_tool(self, tool: str) -> None:
        # Each Path/Shapes icon is single-state now (no cycling) — the only
        # sync needed is which one shows checked/highlighted.
        for tool_id, btn in self._polyline_buttons.items():
            btn.setChecked(tool_id == tool)
        for tool_id, btn in self._shapes_buttons.items():
            btn.setChecked(tool_id == tool)

    def set_polyline_actions_enabled(
        self, *, can_finish: bool, can_close: bool, can_undo: bool
    ) -> None:
        self._finish_button.setEnabled(can_finish)
        self._close_button.setEnabled(can_close)
        self._undo_button.setEnabled(can_undo)

    @staticmethod
    def _sync_toggle(button: CycleIconButton, enabled: bool) -> None:
        """Sync a 2-state ("off"/"on") button's index (not just its visual
        checked state) so the next left-click cycles from the right
        baseline instead of desyncing from external state changes."""
        button.set_current_state("on" if enabled else "off")

    def set_split_enabled(self, enabled: bool) -> None:
        self._sync_toggle(self._split_button, enabled)

    def set_arc_mode(self, mode: str) -> None:
        self._arc_mode_button.set_current_state(mode)

    def set_arc_mode_enabled(self, enabled: bool) -> None:
        self._arc_mode_button.setEnabled(enabled)

    def set_constraint_mode(self, mode: str | None) -> None:
        self._constraint_button.set_current_state(mode or "Free")

    def set_constraint_mode_enabled(self, enabled: bool) -> None:
        self._constraint_button.setEnabled(enabled)

    def set_smoothing_method(self, method: str) -> None:
        self._smoothing_button.set_current_state(method)
