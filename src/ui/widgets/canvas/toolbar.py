"""Compact canvas mode-toolbar (Select/Draw/Edit + secondary actions)."""

from __future__ import annotations

import platform as _platform

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

# Platform modifier for human-readable shortcut hints
_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


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
    shell = QWidget()
    shell.setObjectName("canvas-toolbar")
    shell_layout = QHBoxLayout(shell)
    shell_layout.setContentsMargins(6, 3, 6, 3)
    shell_layout.setSpacing(4)

    mode_buttons: dict[str, QPushButton] = {}
    mode_hints = {
        "Select": "Shortcut: S",
        "Draw": "Shortcut: D",
        "Edit": "Shortcut: E",
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
        shell_layout.addSpacing(6)

        fit_btn = QPushButton("Fit")
        fit_btn.setProperty("role", "secondary")
        fit_btn.setMinimumHeight(30)
        fit_btn.setToolTip("Fit view to content (Shortcut: F)")
        fit_btn.clicked.connect(on_fit)
        shell_layout.addWidget(fit_btn)

    if secondary_actions:
        shell_layout.addSpacing(6)
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

    selection_label = QLabel("")
    selection_label.setProperty("role", "toolbar-selection")
    selection_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    shell_layout.addWidget(selection_label, stretch=1)

    return shell, mode_buttons, selection_label


__all__ = ["canvas_toolbar"]
