"""Shared interaction-tool contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QMouseEvent

if TYPE_CHECKING:
    from simple_stipple.canvas.view.main import CanvasView


class CanvasTool:
    """Base tool: hooks return True when the event was fully handled."""

    def __init__(self, view: CanvasView) -> None:
        self.v = view

    def press(self, event: QMouseEvent) -> bool:
        return False

    def move(self, event: QMouseEvent) -> bool:
        return False

    def release(self, event: QMouseEvent) -> bool:
        return False

    def double_click(self, event: QMouseEvent) -> bool:
        return False

    def key(self, event) -> bool:
        """Tool-specific key handling; runs before the command registry."""
        return False

    def paint_overlay(self, painter) -> None:
        """Draw tool-specific overlays on top of the rendered canvas."""


__all__ = ["CanvasTool"]
