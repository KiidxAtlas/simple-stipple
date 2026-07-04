"""Draw sidebar widgets for the interactive canvas.

Redesigned with a clean, modern aesthetic: larger touch targets,
clear visual hierarchy, grouped sections, and intuitive icons.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class DrawSidebar(QFrame):
    """Simplified draw sidebar with modal tool picker."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        on_draw_clicked: Callable[[], None],
        on_finish_open: Callable[[], None],
        on_close_edit: Callable[[], None],
        on_undo_point: Callable[[], None],
        on_toggle_snap: Callable[[], None],
        on_toggle_split: Callable[[], None],
        on_cycle_arc_mode: Callable[[], None],
        on_cycle_constraint_mode: Callable[[], None],
        on_cancel_draw: Callable[[], None],
        on_back_to_select: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._tool_label: QLabel | None = None
        self.draw_button: QPushButton | None = None
        self.finish_open_button: QPushButton | None = None
        self.close_edit_button: QPushButton | None = None
        self.undo_point_button: QPushButton | None = None
        self.snap_button: QPushButton | None = None
        self.split_button: QPushButton | None = None
        self.arc_mode_button: QPushButton | None = None
        self.constraint_mode_button: QPushButton | None = None

        self.setObjectName("draw-side-panel")
        # Redesigned stylesheet: larger buttons, better spacing, modern look.
        self.setStyleSheet(
            """
            QFrame#draw-side-panel {
                background: rgba(13, 17, 23, 240);
                border: 1px solid #30363d;
                border-radius: 12px;
            }
            QFrame#draw-side-panel QLabel[role='title'] {
                color: #f0f6fc;
                font-size: 13px;
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
            QFrame#draw-side-panel QLabel[role='hint'] {
                color: #6e7681;
                font-size: 9px;
            }
            QFrame#draw-side-panel QPushButton {
                min-height: 40px;
                max-height: 44px;
                border-radius: 8px;
                background: #161b22;
                border: 1px solid #30363d;
                color: #c9d1d9;
                font-size: 12px;
                padding: 0px 6px;
                text-align: center;
            }
            QFrame#draw-side-panel QPushButton#draw-primary-tool {
                font-size: 22px;
            }
            QFrame#draw-side-panel QPushButton:hover {
                background: #1c2128;
                border-color: #58a6ff;
            }
            QFrame#draw-side-panel QPushButton:pressed {
                background: #21262d;
                border-color: #79c0ff;
            }
            QFrame#draw-side-panel QPushButton:disabled {
                background: #0d1117;
                border-color: #21262d;
                color: #484f58;
            }
            QFrame#draw-side-panel QPushButton[role='primary'] {
                background: #1f3a6e;
                border-color: #2f81f7;
                color: #79c0ff;
            }
            QFrame#draw-side-panel QPushButton[role='primary']:hover {
                background: #264078;
                border-color: #58a6ff;
            }
            QFrame#draw-side-panel QPushButton[role='danger'] {
                background: #3d1f2e;
                border-color: #f85149;
                color: #f85149;
            }
            QFrame#draw-side-panel QPushButton[role='danger']:hover {
                background: #4a2437;
                border-color: #f85149;
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
        self.setFixedWidth(172)  # wide enough that button labels never clip

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget(scroll)
        col = QVBoxLayout(content)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(8)

        # ── Header ───────────────────────────────────────────────────
        title = QLabel("Draw Tools")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Tool Selector ────────────────────────────────────────────
        tool_group = QFrame(self)
        tool_group.setObjectName("tool-group")
        tool_layout = QVBoxLayout(tool_group)
        tool_layout.setContentsMargins(4, 4, 4, 4)
        tool_layout.setSpacing(4)

        tool_label = QLabel("TOOL")
        tool_label.setProperty("role", "section-title")
        tool_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        tool_layout.addWidget(tool_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.draw_button = QPushButton("➕", self)
        self.draw_button.setToolTip(
            "Select drawing tool\n"
            "Choose from: Polyline, Line, Arc, Circle,\n"
            "Ellipse, Rectangle, Polygon"
        )
        self.draw_button.setObjectName("draw-primary-tool")
        self.draw_button.clicked.connect(on_draw_clicked)
        self.draw_button.setFixedSize(132, 44)
        tool_layout.addWidget(self.draw_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._tool_label = QLabel("—")
        self._tool_label.setProperty("role", "hint")
        self._tool_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._tool_label.setStyleSheet(
            "QLabel { font-size: 10px; color: #6e7681; margin-top: 2px; }"
        )
        tool_layout.addWidget(self._tool_label)

        col.addWidget(tool_group)

        # ── Shape Modes ──────────────────────────────────────────────
        mode_group = QFrame(self)
        mode_group.setObjectName("mode-group")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setContentsMargins(4, 4, 4, 4)
        mode_layout.setSpacing(6)

        mode_hint = QLabel("SHAPE MODE")
        mode_hint.setProperty("role", "section-title")
        mode_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mode_layout.addWidget(mode_hint, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.arc_mode_button = QPushButton("3-Point", self)
        self.arc_mode_button.setToolTip(
            "Arc mode: Three-point\nClick center, start point, end point"
        )
        self.arc_mode_button.setFixedSize(132, 34)
        mode_layout.addWidget(
            self.arc_mode_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        self.constraint_mode_button = QPushButton("Free", self)
        self.constraint_mode_button.setToolTip(
            "Draw constraint lock: Free\nHold Shift for horizontal/vertical constraints"
        )
        self.constraint_mode_button.setFixedSize(132, 34)
        mode_layout.addWidget(
            self.constraint_mode_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        col.addWidget(mode_group)

        # ── Drawing Actions ──────────────────────────────────────────
        action_group = QFrame(self)
        action_group.setObjectName("action-group")
        action_layout = QVBoxLayout(action_group)
        action_layout.setContentsMargins(4, 4, 4, 4)
        action_layout.setSpacing(6)

        actions_hint = QLabel("ACTIONS")
        actions_hint.setProperty("role", "section-title")
        actions_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        action_layout.addWidget(actions_hint, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.finish_open_button = QPushButton("✓  Finish", self)
        self.finish_open_button.setToolTip(
            "Finish open polyline\nDouble-click, press Enter, or right-click to finish"
        )
        self.finish_open_button.clicked.connect(on_finish_open)
        self.finish_open_button.setFixedSize(132, 38)
        action_layout.addWidget(
            self.finish_open_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        self.close_edit_button = QPushButton("◯  Close", self)
        self.close_edit_button.setToolTip(
            "Close polyline and enter edit mode\nConnects end point to start point"
        )
        self.close_edit_button.clicked.connect(on_close_edit)
        self.close_edit_button.setFixedSize(132, 38)
        action_layout.addWidget(
            self.close_edit_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        self.undo_point_button = QPushButton("↶  Undo Pt", self)
        self.undo_point_button.setToolTip(
            "Undo last placed point\nRemoves the most recent vertex"
        )
        self.undo_point_button.clicked.connect(on_undo_point)
        self.undo_point_button.setFixedSize(132, 38)
        action_layout.addWidget(
            self.undo_point_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        col.addWidget(action_group)

        # ── Toggles ──────────────────────────────────────────────────
        toggle_group = QFrame(self)
        toggle_group.setObjectName("toggle-group")
        toggle_layout = QVBoxLayout(toggle_group)
        toggle_layout.setContentsMargins(4, 4, 4, 4)
        toggle_layout.setSpacing(6)

        toggle_hint = QLabel("TOGGLES")
        toggle_hint.setProperty("role", "section-title")
        toggle_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        toggle_layout.addWidget(toggle_hint, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.snap_button = QPushButton("◎  Snap", self)
        self.snap_button.setToolTip(
            "Midpoint / Object snap\n"
            "Snaps to vertices, midpoints,\n"
            "intersections, and centers"
        )
        self.snap_button.clicked.connect(on_toggle_snap)
        self.snap_button.setFixedSize(132, 34)
        toggle_layout.addWidget(
            self.snap_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        self.split_button = QPushButton("✂  Split", self)
        self.split_button.setToolTip(
            "Toggle auto-split on draw\n"
            "When enabled, new shapes split\n"
            "existing ones at intersection"
        )
        self.split_button.clicked.connect(on_toggle_split)
        self.split_button.setFixedSize(132, 34)
        toggle_layout.addWidget(
            self.split_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        col.addWidget(toggle_group)

        # ── Navigation ───────────────────────────────────────────────
        nav_group = QFrame(self)
        nav_group.setObjectName("nav-group")
        nav_layout = QVBoxLayout(nav_group)
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(6)

        nav_hint = QLabel("NAVIGATION")
        nav_hint.setProperty("role", "section-title")
        nav_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        nav_layout.addWidget(nav_hint, alignment=Qt.AlignmentFlag.AlignHCenter)

        cancel_button = QPushButton("✕  Cancel", self)
        cancel_button.setProperty("role", "danger")
        cancel_button.setToolTip("Cancel current draw\nDiscard all placed points")
        cancel_button.clicked.connect(on_cancel_draw)
        cancel_button.setFixedSize(132, 38)
        nav_layout.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        select_button = QPushButton("⎋  Select", self)
        select_button.setToolTip(
            "Back to select mode\nExit drawing and return to selection"
        )
        select_button.clicked.connect(on_back_to_select)
        select_button.setFixedSize(132, 38)
        nav_layout.addWidget(select_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        col.addWidget(nav_group)

        col.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def set_active_tool(self, tool: str) -> None:
        """Update the tool label to show the currently active tool."""
        if self._tool_label is not None:
            # Capitalize for display
            display_name = tool.replace("_", " ").title()
            self._tool_label.setText(display_name)

    def set_polyline_actions_enabled(
        self, *, can_finish: bool, can_close: bool, can_undo: bool
    ) -> None:
        if self.finish_open_button is not None:
            self.finish_open_button.setEnabled(can_finish)
        if self.close_edit_button is not None:
            self.close_edit_button.setEnabled(can_close)
        if self.undo_point_button is not None:
            self.undo_point_button.setEnabled(can_undo)

    def set_snap_label(self, enabled: bool) -> None:
        if self.snap_button is not None:
            self.snap_button.setProperty("role", "primary" if enabled else None)
            self.snap_button.style().unpolish(self.snap_button)
            self.snap_button.style().polish(self.snap_button)

    def set_split_label(self, enabled: bool) -> None:
        if self.split_button is not None:
            self.split_button.setProperty("role", "primary" if enabled else None)
            self.split_button.style().unpolish(self.split_button)
            self.split_button.style().polish(self.split_button)

    def set_arc_mode(self, mode: str) -> None:
        if self.arc_mode_button is None:
            return
        if mode == "center-start-end":
            self.arc_mode_button.setText("CSE")
            self.arc_mode_button.setToolTip(
                "Arc mode: Center → Start → End\n"
                "Click center, then start and end points"
            )
        else:
            self.arc_mode_button.setText("3-Point")
            self.arc_mode_button.setToolTip(
                "Arc mode: Three-point\nClick center, start point, end point"
            )

    def set_arc_mode_enabled(self, enabled: bool) -> None:
        if self.arc_mode_button is not None:
            self.arc_mode_button.setEnabled(enabled)

    def set_constraint_mode(self, mode: str | None) -> None:
        if self.constraint_mode_button is None:
            return
        label = mode or "Free"
        self.constraint_mode_button.setText(label)
        self.constraint_mode_button.setToolTip(
            f"Draw constraint lock: {mode}"
            if mode
            else "Draw constraint lock: Free\nHold Shift for horizontal/vertical constraints"
        )

    def set_constraint_mode_enabled(self, enabled: bool) -> None:
        if self.constraint_mode_button is not None:
            self.constraint_mode_button.setEnabled(enabled)
