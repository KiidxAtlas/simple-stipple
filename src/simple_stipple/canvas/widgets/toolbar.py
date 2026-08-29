"""Compact canvas mode-toolbar (Select/Draw/Edit + secondary actions)."""

from __future__ import annotations

import platform as _platform
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.layout import info_chip
from simple_stipple.ui.components.units import suffix as _unit_suffix
from simple_stipple.ui.components.units import to_display as _to_display
from simple_stipple.ui.style import icon_path

# Platform modifier for human-readable shortcut hints
_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class _ElidingLabel(QLabel):
    """QLabel that ellipsizes to fit its current width instead of silently
    clipping — guidance/selection text here is dynamic and the toolbar's
    responsive layout can squeeze these labels below their sizeHint."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._full_text = self.text()

    def setText(self, text: str) -> None:
        self._full_text = text
        self._apply_elided()

    def _apply_elided(self) -> None:
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width()
        )
        super().setText(elided)
        self.setToolTip(self._full_text)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_elided()


class ResponsiveCanvasToolbar(QWidget):
    """Keeps modes visible and moves registered secondary buttons to overflow."""

    COMPACT_WIDTH = 1000

    def __init__(self) -> None:
        super().__init__()
        self._responsive_widgets: list[QWidget] = []
        self._responsive_buttons: list[QAbstractButton] = []
        self._overflow_actions: dict[QAbstractButton, QAction] = {}
        self._overflow = QToolButton()
        self._overflow.setText("More")
        self._overflow.setAccessibleName("More canvas actions")
        self._overflow.setToolTip("Secondary canvas actions")
        self._overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow_menu = QMenu(self._overflow)
        self._overflow_menu.aboutToShow.connect(self._sync_overflow_actions)
        self._overflow.setMenu(self._overflow_menu)
        self._overflow.hide()
        self._guidance_chip = QLabel("")
        self._guidance_chip.setProperty("role", "toolbar-selection")
        self._guidance_chip.setAccessibleName("Current canvas guidance")
        self._guidance_chip.hide()
        self._full_guidance_label: QLabel | None = None

    def set_guidance(self, text: str) -> None:
        """Keep current-tool guidance available when the full label is hidden."""
        self._guidance_chip.setText(text)
        self._guidance_chip.setToolTip(text)
        self._guidance_chip.setAccessibleDescription(text)
        self._overflow.setToolTip(f"Secondary canvas actions. {text}")
        self._overflow.setAccessibleDescription(text)
        self._update_responsive_state()

    def register_secondary(self, button: QAbstractButton) -> None:
        if button in self._responsive_buttons:
            return
        self._responsive_buttons.append(button)
        self._responsive_widgets.append(button)
        action = self._overflow_menu.addAction(button.text())
        action.setToolTip(button.toolTip())
        action.setCheckable(button.isCheckable())
        action.setChecked(button.isChecked())
        action.triggered.connect(lambda _checked=False, source=button: source.click())
        if button.isCheckable():
            button.toggled.connect(action.setChecked)
        self._overflow_actions[button] = action
        self._update_responsive_state()

    def register_secondary_widget(self, widget: QWidget) -> None:
        if widget not in self._responsive_widgets:
            self._responsive_widgets.append(widget)
            self._update_responsive_state()

    def _sync_overflow_actions(self) -> None:
        for button, action in self._overflow_actions.items():
            action.setText(button.text())
            action.setEnabled(button.isEnabled())
            action.setVisible(not button.isHidden() or self.width() < self.COMPACT_WIDTH)
            if button.isCheckable():
                action.setChecked(button.isChecked())

    def _update_responsive_state(self) -> None:
        compact = self.width() < self.COMPACT_WIDTH
        for widget in self._responsive_widgets:
            widget.setVisible(not compact)
        self._overflow.setVisible(compact and bool(self._responsive_buttons))
        self._guidance_chip.setVisible(compact and bool(self._guidance_chip.text()))
        # Keep a single source of guidance visible.  Previously the full
        # instruction and its compact duplicate were both shown at narrower
        # widths, consuming the room that the canvas actions needed.
        full_guidance = self._full_guidance_label
        if full_guidance is not None:
            full_guidance.setVisible(not compact)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_responsive_state()


def canvas_toolbar(
    on_mode,
    on_fit,
    *,
    modes: tuple[str, ...] = ("Select", "Draw", "Edit"),
    show_fit: bool = True,
    secondary_actions=None,
):
    """Compact canvas toolbar with mode toggles and optional actions.

    Redesigned: larger buttons, clearer visual hierarchy, subtle styling
    that matches GitHub's dark theme while being more polished.
    """
    shell = ResponsiveCanvasToolbar()
    shell.setObjectName("canvas-toolbar")
    shell_layout = QHBoxLayout(shell)
    shell_layout.setContentsMargins(8, 4, 8, 4)
    shell_layout.setSpacing(4)

    mode_buttons: dict[str, QPushButton] = {}
    # Draw/Edit read their live, rebindable shortcut from the canvas Command
    # registry (native_shortcut() already resolves the user's override and
    # renders it platform-correctly, e.g. no more literal "Meta+..." on
    # macOS) — hardcoded literals here went stale after a rebind. Select has
    # no matching Command entry (it's the default mode, not an action), so
    # it keeps its default-only fallback.
    mode_hints = {
        "Select": "Shortcut: S",
        "Draw": f"Shortcut: {canvas_commands.native_shortcut('mode.draw') or 'D'}",
        "Edit": f"Shortcut: {canvas_commands.native_shortcut('mode.edit') or 'E'}",
    }
    for mode in modes:
        btn = QPushButton(mode)
        btn.setProperty("role", "mode-button")
        btn.setProperty("active", mode == modes[0])
        if mode in mode_hints:
            btn.setToolTip(mode_hints[mode])
        btn.clicked.connect(lambda checked=False, m=mode: on_mode(m))
        shell_layout.addWidget(btn)
        mode_buttons[mode] = btn

    if show_fit:
        shell_layout.addSpacing(8)

        fit_btn = QPushButton("Fit")
        fit_btn.setProperty("role", "secondary")
        fit_keys = canvas_commands.native_shortcut("view.fit") or "F"
        fit_btn.setToolTip(f"Fit view to content (Shortcut: {fit_keys})")
        fit_btn.clicked.connect(on_fit)
        shell_layout.addWidget(fit_btn)
        shell.register_secondary(fit_btn)

    if secondary_actions:
        shell_layout.addSpacing(8)
        secondary_hints = {
            "Select All": f"Shortcut: {_KBD_MOD}+A",
            "Deselect": f"Shortcut: {_KBD_MOD}+Shift+A",
            "Delete": "Shortcut: Delete",
            "Undo": f"Shortcut: {_KBD_MOD}+Z",
            "Close": "Shortcut: Shift+C",
            "Open": "Shortcut: Shift+O",
        }
        for spec in secondary_actions:
            label, slot, role = spec if len(spec) == 3 else (*spec, None)
            btn = QPushButton(label)
            btn.setProperty("role", role or "secondary")
            if label in secondary_hints:
                btn.setToolTip(secondary_hints[label])
            btn.clicked.connect(slot)
            shell_layout.addWidget(btn)
            shell.register_secondary(btn)

    guidance_label = _ElidingLabel("Select geometry · Esc clears selection")
    guidance_label.setProperty("role", "toolbar-guidance")
    guidance_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    guidance_label.setToolTip("Current tool and next expected action")
    guidance_label.setMinimumWidth(0)
    guidance_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    shell_layout.addWidget(guidance_label, stretch=1)
    shell._full_guidance_label = guidance_label

    shell_layout.addWidget(shell._guidance_chip)
    shell_layout.addWidget(shell._overflow)

    selection_label = _ElidingLabel("")
    selection_label.setProperty("role", "toolbar-selection")
    selection_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    selection_label.setMinimumWidth(0)
    selection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    shell_layout.addWidget(selection_label)

    # register_secondary() may have run before the full guidance label
    # existed, so do one final pass once the whole toolbar is assembled.
    shell._update_responsive_state()

    return shell, mode_buttons, selection_label, guidance_label


__all__ = [
    "CanvasStatusStrip",
    "ResponsiveCanvasToolbar",
    "canvas_toolbar",
]


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
        layout = self.layout()
        if layout is None:
            return
        cast(QHBoxLayout, layout).insertWidget(self._extras_index, widget)
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
