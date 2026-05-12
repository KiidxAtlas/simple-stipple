"""Compact canvas status strip widget."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from src.ui.core.factories import info_chip


class CanvasStatusStrip(QFrame):
    """Compact status bar — mode, selection, zoom, coordinates, and readiness."""

    def __init__(self, *, show_readiness: bool = True) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "status-strip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self._mode_label = QLabel("Select")
        self._mode_label.setStyleSheet(
            "color: #79c0ff; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self._mode_label)

        self._readiness_dot = self._dot()
        layout.addWidget(self._readiness_dot)

        self._objects_label = QLabel("0 obj")
        self._objects_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._objects_label)

        layout.addWidget(self._dot())

        self._selection_label = QLabel("0 sel")
        self._selection_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._selection_label)

        layout.addWidget(self._dot())

        self._precision_label = QLabel("Free move")
        self._precision_label.setStyleSheet("color: #6e7681; font-size: 10px;")
        layout.addWidget(self._precision_label)

        layout.addStretch()

        self._cursor_label = QLabel("")
        self._cursor_label.setStyleSheet(
            "color: #6e7681; font-size: 10px; font-family: 'Menlo', 'Courier New';"
        )
        layout.addWidget(self._cursor_label)

        layout.addWidget(self._dot())

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        self._zoom_label.setToolTip("Zoom level (scroll to zoom)")
        layout.addWidget(self._zoom_label)

        layout.addWidget(self._dot())

        self._readiness_chip = info_chip("No geometry", "warn")
        layout.addWidget(self._readiness_chip)
        self.set_readiness_visible(show_readiness)

    def set_readiness_visible(self, visible: bool) -> None:
        self._readiness_dot.setVisible(visible)
        self._readiness_chip.setVisible(visible)

    @staticmethod
    def _dot() -> QLabel:
        d = QLabel("·")
        d.setStyleSheet("color: #30363d; font-size: 11px;")
        return d

    def set_snapshot(
        self,
        *,
        mode: str,
        selected_count: int,
        object_count: int,
        precision_text: str,
        topology_text: str = "",
        readiness_text: str,
        readiness_tone: str = "neutral",
        zoom_percent: int = 100,
        cursor_pos: tuple[float, float] | None = None,
    ) -> None:
        self._mode_label.setText(mode.title())
        self._objects_label.setText(f"{object_count} obj")
        self._selection_label.setText(f"{selected_count} sel")
        self._selection_label.setStyleSheet(
            f"color: {'#79c0ff' if selected_count else '#8b949e'}; font-size: 11px;"
        )
        combined_precision = precision_text
        if topology_text:
            combined_precision = f"{precision_text} · {topology_text}"
        self._precision_label.setText(combined_precision)
        self._zoom_label.setText(f"{zoom_percent}%")
        if cursor_pos:
            self._cursor_label.setText(f"X {cursor_pos[0]:.2f}  Y {cursor_pos[1]:.2f}")
        else:
            self._cursor_label.setText("")
        self._readiness_chip.setText(readiness_text)
        self._readiness_chip.setProperty("tone", readiness_tone)
        self._readiness_chip.style().unpolish(self._readiness_chip)
        self._readiness_chip.style().polish(self._readiness_chip)

    def set_selection_count(self, count: int) -> None:
        """Lightweight update — change only the selection label without a full snapshot."""
        self._selection_label.setText(f"{count} sel")
        self._selection_label.setStyleSheet(
            f"color: {'#79c0ff' if count else '#8b949e'}; font-size: 11px;"
        )


__all__ = ["CanvasStatusStrip"]
