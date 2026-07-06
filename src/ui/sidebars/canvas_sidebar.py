"""Draw sidebar — compact, icon-first, grouped controls.

Every multi-state control (tool-family pickers, snap toggles, arc mode,
constraint mode, split, construction) uses ``CycleIconButton``: left-click
cycles, right-click opens a small modal for direct selection, hovering
shows a flyout preview of every state. Single-purpose actions (Text,
Dimension, Measure, the contextual polyline-editing row) use the same
widget with one state so the visuals stay consistent.

The panel's width is user-resizable (drag the right edge) and its sections
can be shown/hidden and reordered — see ``src.ui.shell.settings_dialog``'s
"Customize draw sidebar…" button and
``src.settings.DEFAULT_DRAW_SIDEBAR_SECTIONS``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.settings import (
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    MAX_DRAW_SIDEBAR_WIDTH,
    MIN_DRAW_SIDEBAR_WIDTH,
)
from src.ui.core.icons import tool_icon
from src.ui.sidebars.cycle_icon_button import CycleIconButton, StateEntry


def _state(icon_name: str, state_id: str, label: str) -> StateEntry:
    return (state_id, tool_icon(icon_name), label)


def _toggle_states(icon_name: str, label: str) -> list[StateEntry]:
    """A 2-state (off/on) cycle sharing one icon — CycleIconButton's own
    checked styling communicates on/off, so both states use the same
    glyph and differ only in id/label."""
    icon = tool_icon(icon_name)
    return [("off", icon, f"{label}: Off"), ("on", icon, f"{label}: On")]


class _ResizeHandle(QFrame):
    """Thin drag handle docked to the sidebar's right edge."""

    def __init__(self, sidebar: "DrawSidebar") -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar
        self._dragging = False
        self.setFixedWidth(6)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setStyleSheet(
            "QFrame { background: transparent; }"
            "QFrame:hover { background: rgba(88, 166, 255, 60); }"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        delta = int(event.globalPosition().x() - self.mapToGlobal(QPoint(0, 0)).x())
        self._sidebar._apply_width(self._sidebar.width() + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._sidebar._on_width_committed()


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
        on_snap_master: Callable[[bool], None],
        on_snap_grid: Callable[[bool], None],
        on_snap_angle: Callable[[bool], None],
        on_constraint: Callable[[str], None],
        on_snap_vertex: Callable[[bool], None],
        on_snap_edge: Callable[[bool], None],
        on_split: Callable[[bool], None],
        on_construction: Callable[[bool], None],
        on_dimension: Callable[[], None],
        on_measure: Callable[[], None],
        on_smoothing_method: Callable[[str], None],
        on_finish_open: Callable[[], None],
        on_close_edit: Callable[[], None],
        on_undo_point: Callable[[], None],
        on_cancel_draw: Callable[[], None],
        on_back_to_select: Callable[[], None],
        width: int | None = None,
        sections: list[str] | None = None,
        on_width_changed: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)

        self._on_width_changed = on_width_changed
        self._sections = list(sections) if sections else list(DEFAULT_DRAW_SIDEBAR_SECTIONS)

        self.setObjectName("draw-side-panel")
        self.setStyleSheet(
            """
            QFrame#draw-side-panel {
                background: rgba(13, 17, 23, 240);
                border: 1px solid #30363d;
                border-radius: 12px;
            }
            QFrame#draw-side-panel QLabel[role='title'] {
                color: #f0f6fc;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
            QFrame#draw-side-panel QLabel[role='section-title'] {
                color: #8b949e;
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 1px;
                text-transform: uppercase;
            }
            QFrame#draw-side-panel QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 0;
            }
            QFrame#draw-side-panel QScrollBar::handle:vertical {
                background: #30363d;
                border-radius: 4px;
                min-height: 24px;
            }
            QFrame#draw-side-panel QScrollBar::add-line:vertical,
            QFrame#draw-side-panel QScrollBar::sub-line:vertical {
                height: 0;
            }
            QFrame#draw-side-panel QScrollBar::add-page:vertical,
            QFrame#draw-side-panel QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )
        self._apply_width(width if width is not None else MIN_DRAW_SIDEBAR_WIDTH + 12)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 2, 6)
        outer.setSpacing(4)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget(scroll)
        col = QVBoxLayout(content)
        col.setContentsMargins(2, 2, 2, 2)
        col.setSpacing(8)

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
        # already used for Text/Dimension/Measure/the editing row below) —
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
                ("slot", "slot", "Slot"),
                ("circle", "circle", "Circle"),
                ("ellipse", "ellipse", "Ellipse"),
                ("polygon", "polygon", "Polygon"),
            )
        }
        self._text_button = CycleIconButton(
            [_state("text", "text", "Text")], lambda _sid: on_text()
        )
        self._snap_master_button = CycleIconButton(
            _toggle_states("master_snap", "All snapping"), self._bool_cb(on_snap_master)
        )
        self._snap_grid_button = CycleIconButton(
            _toggle_states("grid_snap", "Grid snap"), self._bool_cb(on_snap_grid)
        )
        self._snap_angle_button = CycleIconButton(
            _toggle_states("angle_snap", "Angle snap"), self._bool_cb(on_snap_angle)
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
        self._snap_vertex_button = CycleIconButton(
            _toggle_states("vertex_snap", "Vertex snap"), self._bool_cb(on_snap_vertex)
        )
        self._snap_edge_button = CycleIconButton(
            _toggle_states("edge_snap", "Edge snap"), self._bool_cb(on_snap_edge)
        )
        self._split_button = CycleIconButton(
            _toggle_states("split", "Auto-split"), self._bool_cb(on_split)
        )
        self._construction_button = CycleIconButton(
            _toggle_states("construction", "Construction"), self._bool_cb(on_construction)
        )
        self._dimension_button = CycleIconButton(
            [_state("dimension", "dimension", "Sketch Dimension (Shift+M)")],
            lambda _sid: on_dimension(),
        )
        self._measure_button = CycleIconButton(
            [_state("measure", "measure", "Measure (M)")], lambda _sid: on_measure()
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

        section_frames: dict[str, QFrame] = {
            "path": self._section("Path", [self._polyline_button, self._arc_mode_button]),
            "shapes": self._section("Shapes", [self._shapes_button]),
            "text": self._section("Text", [self._text_button]),
            "snapping": self._section(
                "Snapping",
                [
                    self._snap_master_button,
                    self._snap_grid_button,
                    self._snap_angle_button,
                    self._constraint_button,
                    self._snap_vertex_button,
                    self._snap_edge_button,
                ],
                columns=2,
            ),
            "mode": self._section(
                "Mode", [self._split_button, self._construction_button], columns=2
            ),
            "sketch": self._section(
                "Sketch", [self._dimension_button, self._measure_button], columns=2
            ),
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
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(_ResizeHandle(self))

    # -- resize --------------------------------------------------------------

    def _apply_width(self, width: int) -> None:
        clamped = max(MIN_DRAW_SIDEBAR_WIDTH, min(MAX_DRAW_SIDEBAR_WIDTH, width))
        self.setFixedWidth(clamped)

    def _on_width_committed(self) -> None:
        if self._on_width_changed is not None:
            self._on_width_changed(self.width())

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _bool_cb(fn: Callable[[bool], None]) -> Callable[[str], None]:
        """Adapt a bool-taking callback to a 2-state ("off"/"on")
        CycleIconButton's str-id contract."""
        return lambda sid: fn(sid == "on")

    def _section(
        self, caption: str, buttons: list[CycleIconButton], *, columns: int = 1
    ) -> QFrame:
        frame = QFrame(self)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        label = QLabel(caption)
        label.setProperty("role", "section-title")
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # Word-wrap defensively: a caption that renders wider than the
        # sidebar under the real system font shouldn't be able to force
        # the whole panel wider — wrapping keeps the layout's width fixed.
        label.setWordWrap(True)
        label.setMaximumWidth(90)
        layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignHCenter)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for i, btn in enumerate(buttons):
            grid.addWidget(btn, i // columns, i % columns)
        grid_wrap = QWidget(frame)
        grid_wrap.setLayout(grid)
        layout.addWidget(grid_wrap, alignment=Qt.AlignmentFlag.AlignHCenter)
        return frame

    # -- state sync (called from view.py) ------------------------------------

    def set_active_tool(self, tool: str) -> None:
        # Exactly one tool-family button (if any) should read as "active" —
        # explicitly un-check the other, otherwise whichever family was
        # last used stays highlighted forever after switching families.
        is_polyline = tool in {"polyline", "spline", "arc", "bezier"}
        is_shape = tool in {"rectangle", "slot", "circle", "ellipse", "polygon"}
        if is_polyline:
            self._polyline_button.set_current_state(tool)
        self._polyline_button.setChecked(is_polyline)
        if is_shape:
            self._shapes_button.set_current_state(tool)
        self._shapes_button.setChecked(is_shape)

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

    def set_snap_master(self, enabled: bool) -> None:
        self._sync_toggle(self._snap_master_button, enabled)

    def set_snap_grid(self, enabled: bool) -> None:
        self._sync_toggle(self._snap_grid_button, enabled)

    def set_snap_angle(self, enabled: bool) -> None:
        self._sync_toggle(self._snap_angle_button, enabled)

    def set_snap_vertex(self, enabled: bool) -> None:
        self._sync_toggle(self._snap_vertex_button, enabled)

    def set_snap_edge(self, enabled: bool) -> None:
        self._sync_toggle(self._snap_edge_button, enabled)

    def set_split_enabled(self, enabled: bool) -> None:
        self._sync_toggle(self._split_button, enabled)

    def set_construction_enabled(self, enabled: bool) -> None:
        self._sync_toggle(self._construction_button, enabled)

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
