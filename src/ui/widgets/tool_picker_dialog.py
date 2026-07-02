"""Modal tool picker dialog for draw mode."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_TOOL_ICON_COLOR = QColor("#bcd3ea")

TOOL_SPECS = [
    ("Arc", "arc"),
    ("Polyline", "polyline"),
    ("Spline", "spline"),
    ("Rectangle", "rectangle"),
    ("Circle", "circle"),
    ("Ellipse", "ellipse"),
    ("Polygon", "polygon"),
    ("Text", "text"),
]


class ToolButton(QPushButton):
    """Custom painted tool button with geometric icon."""

    def __init__(self, label: str, tool: str, parent: QWidget | None = None):
        super().__init__("", parent)
        self._tool = tool
        self.setToolTip(label)
        self.setMinimumSize(64, 64)
        self.setMaximumSize(64, 64)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_TOOL_ICON_COLOR, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = self.rect().adjusted(10, 10, -10, -10)
        side = float(max(10, min(rect.width(), rect.height())))
        icon_rect = QRectF(
            rect.center().x() - side / 2.0,
            rect.center().y() - side / 2.0,
            side,
            side,
        )
        cx = icon_rect.center().x()
        cy = icon_rect.center().y()

        if self._tool == "arc":
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
        elif self._tool == "spline":
            path = QPainterPath()
            path.moveTo(icon_rect.left() + 1, icon_rect.bottom() - 2)
            path.cubicTo(
                icon_rect.left() + 4,
                icon_rect.top() + 1,
                icon_rect.right() - 4,
                icon_rect.bottom() - 1,
                icon_rect.right() - 1,
                icon_rect.top() + 2,
            )
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
        elif self._tool == "text":
            # Serif "T" glyph
            painter.drawLine(
                QPointF(icon_rect.left() + 2, icon_rect.top() + 2),
                QPointF(icon_rect.right() - 2, icon_rect.top() + 2),
            )
            painter.drawLine(
                QPointF(cx, icon_rect.top() + 2),
                QPointF(cx, icon_rect.bottom() - 1),
            )
        painter.end()


class ToolPickerDialog(QDialog):
    """Modal dialog for selecting a draw tool. 2x4 grid layout."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select Tool")
        self.setModal(True)
        self.setStyleSheet(
            """
            QDialog {
                background: rgba(12, 18, 26, 245);
                border: 1px solid #2b3440;
                border-radius: 8px;
            }
            QDialog QLabel {
                color: #f0f6fc;
                font-size: 12px;
                font-weight: 500;
            }
            """
        )
        self.setObjectName("tool-picker-dialog")

        self._selected_tool: str | None = None
        self._tool_buttons: dict[str, ToolButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Choose a drawing tool")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)

        for i, (label, tool_id) in enumerate(TOOL_SPECS):
            btn = ToolButton(label, tool_id, self)
            btn.clicked.connect(
                lambda checked=False, t=tool_id: self._on_tool_clicked(t)
            )
            row = i // 2
            col = i % 2
            grid.addWidget(btn, row, col)
            self._tool_buttons[tool_id] = btn

        root.addLayout(grid)
        root.setContentsMargins(16, 16, 16, 16)

        self.setFixedSize(280, 360)

    def _on_tool_clicked(self, tool: str) -> None:
        self._selected_tool = tool
        self.accept()

    def get_selected_tool(self) -> str | None:
        return self._selected_tool
