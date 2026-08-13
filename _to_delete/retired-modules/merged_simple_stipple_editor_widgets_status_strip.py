"""Compact canvas status strip widget."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QIcon, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QToolButton

from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.layout import info_chip
from simple_stipple.ui.components.units import suffix as _unit_suffix
from simple_stipple.ui.components.units import to_display as _to_display
from simple_stipple.ui.style.theme import icon_path


class CanvasStatusStrip(QFrame):
    """Compact status bar — mode, selection, zoom, coordinates, and readiness."""

    # The full diagnostic readout is useful on a wide canvas, but it becomes
    # crowded before the page reaches its compact layout. Switch to Details
    # early enough that the status bar never imposes a desktop-only minimum
    # width on the whole workspace.
    COMPACT_WIDTH = 960
    contextActionRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "status-strip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)
        self._selection_count = 0
        self._cursor_source = None

        self._mode_label = QLabel("Select")
        self._mode_label.setProperty("role", "status-mode")
        self._mode_label.setAccessibleName("Active canvas mode")
        layout.addWidget(self._mode_label)

        self._readiness_dot = self._dot()
        layout.addWidget(self._readiness_dot)

        self._objects_label = QLabel("0 obj")
        self._objects_label.setProperty("role", "status-meta")
        layout.addWidget(self._objects_label)

        self._objects_dot = self._dot()
        layout.addWidget(self._objects_dot)

        self._selection_label = QLabel("0 sel")
        self._selection_label.setProperty("role", "status-selection")
        layout.addWidget(self._selection_label)

        self._selection_dot = self._dot()
        layout.addWidget(self._selection_dot)

        self._precision_label = QLabel("Free move")
        self._precision_label.setProperty("role", "status-detail")
        layout.addWidget(self._precision_label)

        self._details_button = QToolButton()
        self._details_button.setText("Details")
        self._details_button.setAccessibleName("Canvas status details")
        self._details_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._details_menu = QMenu(self._details_button)
        self._objects_action = self._details_menu.addAction("")
        self._selection_action = self._details_menu.addAction("")
        self._precision_action = self._details_menu.addAction("")
        for action in self._details_menu.actions():
            action.setEnabled(False)
        self._details_button.setMenu(self._details_menu)
        layout.addWidget(self._details_button)

        self._context_buttons: list[QToolButton] = []
        self._context_menu_actions: list[QAction] = []
        for _ in range(3):
            button = QToolButton()
            button.setProperty("role", "context-action")
            button.setMinimumHeight(28)
            button.setVisible(False)
            button.clicked.connect(
                lambda _checked=False, source=button: self.contextActionRequested.emit(
                    str(source.property("action"))
                )
            )
            layout.addWidget(button)
            self._context_buttons.append(button)

        # Page-specific transient controls (e.g. "cancel the solve in flight")
        # mount here, before the stretch, so they read as part of the status.
        self._extras_index = layout.count()
        layout.addStretch()

        self._cursor_label = QLabel("")
        self._cursor_label.setProperty("role", "status-coordinates")
        self._cursor_label.setAccessibleName("Canvas cursor coordinates")
        self._cursor_label.setMinimumWidth(150)
        layout.addWidget(self._cursor_label)

        layout.addWidget(self._dot())

        self._zoom_label = QToolButton()
        self._zoom_label.setText("100%")
        self._zoom_label.setIcon(QIcon(str(icon_path("chevron_down.svg"))))
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
        self._update_responsive_visibility()

    def add_status_widget(self, widget) -> None:  # type: ignore[no-untyped-def]
        """Mount a page-specific transient control in the strip."""
        self.layout().insertWidget(self._extras_index, widget)
        self._extras_index += 1

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
        unit: str = "mm",
    ) -> None:
        normalized_mode = mode.replace("_", " ").title()
        self._mode_label.setText(
            normalized_mode + (" · Esc → Select" if mode.lower() != "select" else "")
        )
        self._mode_label.setAccessibleDescription(
            f"{normalized_mode} mode"
            + (". Press Escape to return to Select mode." if mode.lower() != "select" else "")
        )
        self._objects_label.setText(f"{object_count} obj")
        self._selection_count = selected_count
        self._selection_label.setText(f"{selected_count} sel")
        self._selection_label.setProperty("active", bool(selected_count))
        refresh_style(self._selection_label)
        combined_precision = precision_text
        if topology_text:
            combined_precision = f"{precision_text} · {topology_text}"
        self._precision_label.setText(combined_precision)
        self._zoom_label.setText(f"{zoom_percent}%")
        if cursor_pos:
            self._cursor_label.setText(self._format_cursor(cursor_pos, unit))
        else:
            self._cursor_label.setText("")
        self._readiness_chip.setText(readiness_text)
        self._readiness_chip.setProperty("tone", readiness_tone)
        refresh_style(self._readiness_chip)
        self._update_details_tooltip()
        self._update_responsive_visibility()

    def _update_details_tooltip(self) -> None:
        details = (
            f"{self._objects_label.text()} · {self._selection_label.text()} · "
            f"{self._precision_label.text()}"
        )
        self._mode_label.setToolTip(details)
        self._readiness_chip.setToolTip(details)
        self._details_button.setToolTip(details)
        self._details_button.setAccessibleDescription(details)
        self._objects_action.setText(self._objects_label.text())
        self._selection_action.setText(self._selection_label.text())
        self._precision_action.setText(self._precision_label.text())

    def _update_responsive_visibility(self) -> None:
        compact = self.width() < self.COMPACT_WIDTH
        self._objects_label.setVisible(not compact)
        self._objects_dot.setVisible(not compact)
        # A zero-selection counter does not help the next action and was
        # visual noise in every idle canvas.  Selection remains prominent as
        # soon as there is something selected, while Details always exposes
        # the complete snapshot in compact layouts.
        self._selection_label.setVisible(self._selection_count > 0)
        self._selection_dot.setVisible(not compact and self._selection_count > 0)
        self._precision_label.setVisible(not compact)
        self._details_button.setVisible(compact)
        for button in self._context_buttons:
            button.setVisible(not compact and bool(button.property("action")))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_responsive_visibility()

    def set_zoom_callback(self, callback) -> None:
        """callback(value) where value is a percent int or "fit"."""
        self._on_zoom_selected = callback

    def bind_canvas(self, canvas) -> None:
        """Keep the compact status readout live without a full page refresh."""
        if canvas is self._cursor_source:
            return
        previous = self._cursor_source
        if previous is not None:
            for signal, slot in (
                (getattr(previous, "cursorPositionChanged", None), self._on_canvas_cursor_moved),
                (getattr(previous, "viewChanged", None), self._refresh_bound_cursor),
            ):
                if signal is not None:
                    try:
                        signal.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
        self._cursor_source = canvas
        if canvas is None:
            self._cursor_label.clear()
            return
        cursor_signal = getattr(canvas, "cursorPositionChanged", None)
        if cursor_signal is not None:
            cursor_signal.connect(self._on_canvas_cursor_moved)
        view_signal = getattr(canvas, "viewChanged", None)
        if view_signal is not None:
            view_signal.connect(self._refresh_bound_cursor)
        self._refresh_bound_cursor()

    def _on_canvas_cursor_moved(self, x: float, y: float) -> None:
        source = self._cursor_source
        self.set_cursor_position(
            (x, y),
            str(getattr(source, "_unit_system", "mm")),
        )

    def _refresh_bound_cursor(self) -> None:
        source = self._cursor_source
        if source is None or not hasattr(source, "get_cursor_world_pos"):
            return
        self.set_cursor_position(
            source.get_cursor_world_pos(),
            str(getattr(source, "_unit_system", "mm")),
        )

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

    @staticmethod
    def _format_cursor(cursor_pos: tuple[float, float], unit: str) -> str:
        return (
            f"X {_to_display(cursor_pos[0], unit):.2f}  "
            f"Y {_to_display(cursor_pos[1], unit):.2f} {_unit_suffix(unit)}"
        )

    def set_cursor_position(
        self,
        cursor_pos: tuple[float, float] | None,
        unit: str = "mm",
    ) -> None:
        """Update only the live coordinate text; safe to call every mouse move."""
        self._cursor_label.setText(
            self._format_cursor(cursor_pos, unit) if cursor_pos is not None else ""
        )

    def set_zoom(
        self,
        zoom_percent: int,
        cursor_pos: tuple[float, float] | None = None,
        unit: str = "mm",
    ) -> None:
        """Lightweight view update — refresh only zoom (and cursor) labels.

        Called on every wheel/pinch zoom, so it must avoid the full snapshot
        rebuild (which also rebuilds the layer tree in the page).
        """
        self._zoom_label.setText(f"{zoom_percent}%")
        if cursor_pos is not None:
            self.set_cursor_position(cursor_pos, unit)

    def set_selection_count(self, count: int) -> None:
        """Lightweight update — change only the selection label without a full snapshot."""
        self._selection_label.setText(f"{count} sel")
        self._selection_count = count
        self._selection_label.setProperty("active", bool(count))
        refresh_style(self._selection_label)
        self._update_details_tooltip()
        self._update_responsive_visibility()

    def set_readiness(self, text: str, tone: str = "neutral", detail: str = "") -> None:
        """Lightweight command-lifecycle update without rebuilding page state."""
        self._readiness_chip.setText(text)
        self._readiness_chip.setProperty("tone", tone)
        if detail:
            self._readiness_chip.setToolTip(detail)
            self._readiness_chip.setAccessibleDescription(detail)
        refresh_style(self._readiness_chip)

    def set_context_actions(self, actions: tuple[tuple[str, str, str], ...]) -> None:
        """Update stable canvas actions without rebuilding the status layout."""
        for action in self._context_menu_actions:
            self._details_menu.removeAction(action)
        self._context_menu_actions.clear()
        for button, entry in zip(self._context_buttons, actions[:3], strict=False):
            action_id, label, tooltip = entry
            button.setText(label)
            button.setProperty("action", action_id)
            button.setToolTip(tooltip)
            button.setAccessibleName(label)
            menu_action = self._details_menu.addAction(label)
            menu_action.setToolTip(tooltip)
            menu_action.triggered.connect(
                lambda _checked=False, requested=action_id: self.contextActionRequested.emit(
                    requested
                )
            )
            self._context_menu_actions.append(menu_action)
        for button in self._context_buttons[len(actions[:3]) :]:
            button.setProperty("action", "")
            button.setVisible(False)
        self._update_responsive_visibility()


__all__ = ["CanvasStatusStrip"]
