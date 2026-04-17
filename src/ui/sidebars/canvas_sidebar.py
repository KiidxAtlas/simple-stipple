"""Draw sidebar widgets for the interactive canvas."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_TOOL_ICON_COLOR = QColor("#bcd3ea")


class DrawToolButton(QPushButton):
    def __init__(self, label: str, tool: str, parent: QWidget | None = None):
        super().__init__("", parent)
        self._tool = tool
        self.setToolTip(label)
        self.setMinimumSize(48, 38)
        self.setMaximumSize(48, 38)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_TOOL_ICON_COLOR, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = self.rect().adjusted(7, 6, -7, -6)
        side = float(max(10, min(rect.width(), rect.height())))
        icon_rect = QRectF(
            rect.center().x() - side / 2.0,
            rect.center().y() - side / 2.0,
            side,
            side,
        )
        cx = icon_rect.center().x()
        cy = icon_rect.center().y()

        if self._tool == "line":
            painter.drawLine(
                QPointF(icon_rect.left() + 1, icon_rect.bottom() - 1),
                QPointF(icon_rect.right() - 1, icon_rect.top() + 1),
            )
        elif self._tool == "arc":
            path = QPainterPath()
            path.moveTo(icon_rect.left() + 1, icon_rect.bottom() - 2)
            path.quadTo(
                cx, icon_rect.top() - 1, icon_rect.right() - 1, icon_rect.bottom() - 2
            )
            painter.drawPath(path)
        elif self._tool == "polyline":
            path = QPainterPath()
            path.moveTo(icon_rect.left() + 1, icon_rect.bottom() - 2)
            path.lineTo(cx - 1, cy)
            path.lineTo(icon_rect.right() - 1, icon_rect.top() + 2)
            painter.drawPath(path)
        elif self._tool == "rectangle":
            painter.drawRect(icon_rect.adjusted(1, 2, -1, -2))
        elif self._tool == "circle":
            painter.drawEllipse(icon_rect.adjusted(1, 1, -1, -1))
        elif self._tool == "ellipse":
            painter.drawEllipse(icon_rect.adjusted(0, 3, 0, -3))
        elif self._tool == "polygon":
            path = QPainterPath()
            path.moveTo(cx, icon_rect.top() + 1)
            path.lineTo(icon_rect.right() - 2, cy - 2)
            path.lineTo(icon_rect.right() - 4, icon_rect.bottom() - 1)
            path.lineTo(icon_rect.left() + 4, icon_rect.bottom() - 1)
            path.lineTo(icon_rect.left() + 2, cy - 2)
            path.closeSubpath()
            painter.drawPath(path)
        painter.end()


class DrawSidebar(QFrame):
    def __init__(
        self,
        *,
        parent: QWidget | None,
        on_tool_selected: Callable[[str], None],
        on_apply_shape_size: Callable[[], None],
        on_finish_open: Callable[[], None],
        on_close_edit: Callable[[], None],
        on_undo_point: Callable[[], None],
        on_toggle_snap: Callable[[], None],
        on_toggle_construction: Callable[[], None],
        on_toggle_split: Callable[[], None],
        on_cycle_arc_mode: Callable[[], None],
        on_cycle_constraint_mode: Callable[[], None],
        on_cancel_draw: Callable[[], None],
        on_back_to_select: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._tool_buttons: dict[str, DrawToolButton] = {}
        self.shape_width_edit = None
        self.shape_height_edit = None
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
            """
        )
        self.setFixedWidth(74)

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

        tools = [
            ("Line", "line"),
            ("Arc", "arc"),
            ("Polyline", "polyline"),
            ("Rectangle", "rectangle"),
            ("Circle", "circle"),
            ("Ellipse", "ellipse"),
            ("Polygon", "polygon"),
        ]
        for label, tool in tools:
            button = DrawToolButton(label, tool, self)
            button.clicked.connect(lambda checked=False, t=tool: on_tool_selected(t))
            col.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._tool_buttons[tool] = button

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
        for key, button in self._tool_buttons.items():
            button.setProperty("role", "primary" if key == tool else None)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_shape_size_enabled(self, enabled: bool) -> None:
        _ = enabled

    def set_shape_size_values(self, width_text: str, height_text: str) -> None:
        _ = (width_text, height_text)

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

    def set_construction_label(self, enabled: bool) -> None:
        _ = enabled

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
