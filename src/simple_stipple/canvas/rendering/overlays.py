"""Selection and editor-overlay paint passes for the canvas renderer."""

from __future__ import annotations

from typing import Any


def paint_selection_overlay(renderer: Any, painter: Any, visible: Any) -> None:
    """Paint selection bounds/readouts and the active edit handles above the scene."""
    renderer._paint_selection_bbox(painter, visible)
    renderer._paint_selection_readout(painter)
    if renderer._host._mode == "edit":
        renderer._paint_edit_handles(painter)
    elif renderer._host._mode == "select" and renderer._host._sel:
        renderer._paint_select_handles(painter)


def paint_chrome_rulers(renderer: Any, painter: Any) -> None:
    """Paint rulers last, above every scene and tool overlay."""
    renderer._paint_rulers(painter, max(renderer._host.width(), 100), max(renderer._host.height(), 100))
