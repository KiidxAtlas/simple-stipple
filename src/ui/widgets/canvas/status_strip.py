"""Compact canvas status strip widget."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton

from src.ui.components import info_chip


class CanvasStatusStrip(QFrame):
    """Compact status bar — mode, selection, zoom, coordinates, and readiness."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "status-strip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self._mode_label = QLabel("MODE · SELECT")
        self._mode_label.setProperty("role", "status-mode")
        self._mode_label.setAccessibleName("Active canvas mode")
        layout.addWidget(self._mode_label)

        self._readiness_dot = self._dot()
        layout.addWidget(self._readiness_dot)

        self._objects_label = QLabel("0 obj")
        self._objects_label.setProperty("role", "status-meta")
        layout.addWidget(self._objects_label)

        layout.addWidget(self._dot())

        self._selection_label = QLabel("0 sel")
        self._selection_label.setProperty("role", "status-selection")
        layout.addWidget(self._selection_label)

        layout.addWidget(self._dot())

        self._precision_label = QLabel("Free move")
        self._precision_label.setProperty("role", "status-detail")
        layout.addWidget(self._precision_label)

        layout.addStretch()

        self._cursor_label = QLabel("")
        self._cursor_label.setProperty("role", "status-coordinates")
        layout.addWidget(self._cursor_label)

        layout.addWidget(self._dot())

        self._zoom_label = QToolButton()
        self._zoom_label.setText("100% ▾")
        self._zoom_label.setProperty("role", "status-zoom")
        self._zoom_label.setToolTip("Zoom level — open presets")
        self._zoom_label.setAccessibleName("Canvas zoom")
        self._zoom_label.setAutoRaise(True)
        self._zoom_label.clicked.connect(self._show_zoom_menu)
        self._on_zoom_selected = None
        layout.addWidget(self._zoom_label)

        layout.addWidget(self._dot())

        self._readiness_chip = info_chip("No geometry", "warn")
        layout.addWidget(self._readiness_chip)
        self._readiness_dot.hide()

    @staticmethod
    def _dot() -> QLabel:
        d = QLabel("·")
        d.setProperty("role", "status-separator")
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
        normalized_mode = mode.replace("_", " ").title()
        self._mode_label.setText(
            f"MODE · {normalized_mode.upper()}"
            + (" · Esc to exit" if mode.lower() != "select" else "")
        )
        self._mode_label.setAccessibleDescription(
            f"{normalized_mode} mode"
            + (". Press Escape to return to Select mode." if mode.lower() != "select" else "")
        )
        self._objects_label.setText(f"{object_count} obj")
        self._selection_label.setText(f"{selected_count} sel")
        self._selection_label.setProperty("active", bool(selected_count))
        self._selection_label.style().unpolish(self._selection_label)
        self._selection_label.style().polish(self._selection_label)
        combined_precision = precision_text
        if topology_text:
            combined_precision = f"{precision_text} · {topology_text}"
        self._precision_label.setText(combined_precision)
        self._zoom_label.setText(f"{zoom_percent}% ▾")
        if cursor_pos:
            self._cursor_label.setText(f"X {cursor_pos[0]:.2f}  Y {cursor_pos[1]:.2f}")
        else:
            self._cursor_label.setText("")
        self._readiness_chip.setText(readiness_text)
        self._readiness_chip.setProperty("tone", readiness_tone)
        self._readiness_chip.style().unpolish(self._readiness_chip)
        self._readiness_chip.style().polish(self._readiness_chip)

    def set_zoom_callback(self, callback) -> None:
        """callback(value) where value is a percent int or "fit"."""
        self._on_zoom_selected = callback

    def _show_zoom_menu(self) -> None:
        callback = self._on_zoom_selected
        if callback is None:
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Fit", lambda: callback("fit"))
        for pct in (50, 100, 200, 400):
            menu.addAction(f"{pct}%", lambda _p=pct: callback(_p))
        menu.popup(self._zoom_label.mapToGlobal(self._zoom_label.rect().bottomLeft()))

    def set_selection_count(self, count: int) -> None:
        """Lightweight update — change only the selection label without a full snapshot."""
        self._selection_label.setText(f"{count} sel")
        self._selection_label.setProperty("active", bool(count))
        self._selection_label.style().unpolish(self._selection_label)
        self._selection_label.style().polish(self._selection_label)

    def set_readiness(self, text: str, tone: str = "neutral") -> None:
        """Lightweight command-lifecycle update without rebuilding page state."""
        self._readiness_chip.setText(text)
        self._readiness_chip.setProperty("tone", tone)
        self._readiness_chip.style().unpolish(self._readiness_chip)
        self._readiness_chip.style().polish(self._readiness_chip)


__all__ = ["CanvasStatusStrip"]
