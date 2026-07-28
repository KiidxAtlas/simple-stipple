"""Compact canvas mode-toolbar (Select/Draw/Edit + secondary actions)."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from simple_stipple.canvas import commands as canvas_commands

# Platform modifier for human-readable shortcut hints
_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


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

    def set_guidance(self, text: str) -> None:
        """Keep current-tool guidance available when the full label is hidden."""
        self._guidance_chip.setText(text)
        self._guidance_chip.setToolTip(text)
        self._guidance_chip.setAccessibleDescription(text)
        self._overflow.setToolTip(f"Secondary canvas actions. {text}")
        self._overflow.setAccessibleDescription(text)

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
        btn.setMinimumHeight(30)
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
        fit_btn.setMinimumHeight(30)
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
            btn.setMinimumHeight(30)
            if label in secondary_hints:
                btn.setToolTip(secondary_hints[label])
            btn.clicked.connect(slot)
            shell_layout.addWidget(btn)
            shell.register_secondary(btn)

    guidance_label = QLabel("Select geometry · Esc clears selection")
    guidance_label.setProperty("role", "toolbar-guidance")
    guidance_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    guidance_label.setToolTip("Current tool and next expected action")
    guidance_label.setMinimumWidth(0)
    guidance_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    shell_layout.addWidget(guidance_label, stretch=1)

    shell_layout.addWidget(shell._guidance_chip)
    shell_layout.addWidget(shell._overflow)

    selection_label = QLabel("")
    selection_label.setProperty("role", "toolbar-selection")
    selection_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    selection_label.setMinimumWidth(0)
    selection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    shell_layout.addWidget(selection_label)

    return shell, mode_buttons, selection_label, guidance_label


__all__ = ["ResponsiveCanvasToolbar", "canvas_toolbar"]
