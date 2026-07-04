"""Draw sidebar widgets for the interactive canvas."""

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
        self.setStyleSheet(
            """
            QFrame#draw-side-panel {
                background: rgba(12, 18, 26, 235);
                border: 1px solid #2b3440;
                border-radius: 8px;
            }
            QFrame#draw-side-panel QLabel[role='title'] {
                color: #f0f6fc;
                font-size: 11px;
                font-weight: 700;
            }
            QFrame#draw-side-panel QLabel[role='hint'] {
                color: #8b949e;
                font-size: 9px;
            }
            QFrame#draw-side-panel QPushButton {
                min-height: 28px;
                border-radius: 5px;
                background: #1a222d;
                border: 1px solid #303a47;
                color: #e6edf3;
                font-size: 14px;
                padding: 0px;
                text-align: center;
            }
            QFrame#draw-side-panel QPushButton:hover {
                background: #212b37;
                border-color: #58a6ff;
            }
            QFrame#draw-side-panel QPushButton[role='primary'] {
                background: #1f3a6e;
                border-color: #2f81f7;
                color: #79c0ff;
            }
            QFrame#draw-side-panel QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 0;
            }
            QFrame#draw-side-panel QScrollBar::handle:vertical {
                background: #303a47;
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
        # 74px clipped the labels once the scroll bar appeared; 92 fits the
        # 48px buttons + styled 8px scroll bar with breathing room.
        self.setFixedWidth(92)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget(scroll)
        col = QVBoxLayout(content)
        col.setContentsMargins(2, 2, 2, 2)
        col.setSpacing(6)

        title = QLabel("Draw")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(title)

        # ── Tool Selector ──────────────────────────────────────────

        self.draw_button = QPushButton("➕", self)
        self.draw_button.setToolTip("Select drawing tool")
        self.draw_button.clicked.connect(on_draw_clicked)
        self.draw_button.setFixedSize(48, 38)
        col.addWidget(self.draw_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._tool_label = QLabel("—")
        self._tool_label.setProperty("role", "hint")
        self._tool_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._tool_label.setStyleSheet(
            "QLabel { font-size: 10px; color: #8b949e; margin-top: 2px; }"
        )
        col.addWidget(self._tool_label)

        tool_sep = QFrame(self)
        tool_sep.setFrameShape(QFrame.Shape.HLine)
        tool_sep.setFrameShadow(QFrame.Shadow.Plain)
        col.addWidget(tool_sep)

        # ── Modes ──────────────────────────────────────────────────

        mode_hint = QLabel("Modes")
        mode_hint.setProperty("role", "hint")
        mode_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(mode_hint)

        self.arc_mode_button = QPushButton("A3", self)
        self.arc_mode_button.setToolTip("Cycle arc mode")
        self.arc_mode_button.clicked.connect(on_cycle_arc_mode)
        self.arc_mode_button.setFixedSize(48, 30)
        col.addWidget(self.arc_mode_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.constraint_mode_button = QPushButton("Free", self)
        self.constraint_mode_button.setToolTip("Cycle draw constraint lock")
        self.constraint_mode_button.clicked.connect(on_cycle_constraint_mode)
        self.constraint_mode_button.setFixedSize(48, 30)
        col.addWidget(
            self.constraint_mode_button, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        action_sep = QFrame(self)
        action_sep.setFrameShape(QFrame.Shape.HLine)
        action_sep.setFrameShadow(QFrame.Shadow.Plain)
        col.addWidget(action_sep)

        # ── Actions ────────────────────────────────────────────────

        actions_hint = QLabel("Actions")
        actions_hint.setProperty("role", "hint")
        actions_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(actions_hint)

        self.finish_open_button = QPushButton("✓", self)
        self.finish_open_button.setToolTip("Finish open polyline")
        self.finish_open_button.clicked.connect(on_finish_open)
        self.finish_open_button.setFixedSize(48, 38)
        col.addWidget(self.finish_open_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.close_edit_button = QPushButton("◯", self)
        self.close_edit_button.setToolTip("Close polyline and enter edit mode")
        self.close_edit_button.clicked.connect(on_close_edit)
        self.close_edit_button.setFixedSize(48, 38)
        col.addWidget(self.close_edit_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.undo_point_button = QPushButton("↶", self)
        self.undo_point_button.setToolTip("Undo point")
        self.undo_point_button.clicked.connect(on_undo_point)
        self.undo_point_button.setFixedSize(48, 38)
        col.addWidget(self.undo_point_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.snap_button = QPushButton("◎", self)
        self.snap_button.setToolTip("Midpoint/Object snap")
        self.snap_button.clicked.connect(on_toggle_snap)
        self.snap_button.setFixedSize(48, 38)
        col.addWidget(self.snap_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.split_button = QPushButton("✂", self)
        self.split_button.setToolTip("Toggle auto-split on draw")
        self.split_button.clicked.connect(on_toggle_split)
        self.split_button.setFixedSize(48, 38)
        col.addWidget(self.split_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        cancel_button = QPushButton("✕", self)
        cancel_button.setToolTip("Cancel current draw")
        cancel_button.clicked.connect(on_cancel_draw)
        cancel_button.setFixedSize(48, 38)
        col.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        select_button = QPushButton("⎋", self)
        select_button.setToolTip("Back to select")
        select_button.clicked.connect(on_back_to_select)
        select_button.setFixedSize(48, 38)
        col.addWidget(select_button, alignment=Qt.AlignmentFlag.AlignHCenter)

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
            self.arc_mode_button.setToolTip("Arc mode: Center → Start → End")
        else:
            self.arc_mode_button.setText("A3")
            self.arc_mode_button.setToolTip("Arc mode: Three-point")

    def set_arc_mode_enabled(self, enabled: bool) -> None:
        if self.arc_mode_button is not None:
            self.arc_mode_button.setEnabled(enabled)

    def set_constraint_mode(self, mode: str | None) -> None:
        if self.constraint_mode_button is None:
            return
        label = mode or "Free"
        self.constraint_mode_button.setText(label)
        self.constraint_mode_button.setToolTip(
            f"Draw constraint lock: {label}" if mode else "Draw constraint lock: Free"
        )

    def set_constraint_mode_enabled(self, enabled: bool) -> None:
        if self.constraint_mode_button is not None:
            self.constraint_mode_button.setEnabled(enabled)
