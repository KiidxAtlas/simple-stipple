"""Toast notification widget for temporary user feedback."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class ToastNotification(QWidget):
    """Floating toast notification that auto-hides after duration."""

    def __init__(
        self, message: str, parent: QWidget | None = None, duration_ms: int = 2500
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        label = QLabel(message)
        label.setStyleSheet(
            """
            QLabel {
                background: rgba(18, 24, 35, 240);
                color: #e6edf3;
                padding: 12px 16px;
                border-radius: 6px;
                border: 1px solid #303a47;
                font-size: 12px;
                font-weight: 500;
            }
            """
        )
        font = label.font()
        font.setPointSize(11)
        label.setFont(font)

        self.setMaximumSize(300, 50)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # Create simple layout with just the label
        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        self._label = label
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_and_delete)

        # Start auto-hide timer
        if duration_ms > 0:
            self._timer.start(duration_ms)

    def hide_and_delete(self) -> None:
        """Fade out and delete the widget."""
        self.hide()
        self.deleteLater()

    def show_at(self, x: int, y: int) -> None:
        """Show at specific position."""
        self.move(x, y)
        self.show()


class ToastManager:
    """Manages toast notifications in a parent widget."""

    def __init__(self, parent: QWidget):
        self.parent = parent
        self._toast_y = 16

    def show(self, message: str, duration_ms: int = 2500) -> None:
        """Show a toast notification at bottom-right."""
        toast = ToastNotification(message, self.parent, duration_ms)

        # Position at bottom-right of parent
        parent_rect = self.parent.geometry()
        x = parent_rect.right() - toast.width() - 16
        y = parent_rect.bottom() - self._toast_y - toast.height()

        toast.show_at(x, y)
