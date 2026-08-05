"""Document-scene paint pass for :mod:`simple_stipple.canvas.renderer`."""

from __future__ import annotations

from typing import Any


def paint_document_scene(renderer: Any, painter: Any, width: int, height: int, visible: Any) -> None:
    """Paint persistent document content below selection and interaction chrome."""
    renderer._paint_guides(painter, width, height)
    renderer._paint_dimensions(painter, width, height)
    renderer._paint_ghost_polys(painter, visible)
    # Result sits under the outlines: the user edits geometry, not output.
    renderer._paint_result_polys(painter, visible)
    renderer._paint_main_polys(painter, visible)
    renderer._paint_operation_preview(painter)
    # Findings ride on top of everything: a problem you cannot see is
    # a problem you will only meet at the machine.
    renderer._paint_issue_markers(painter)
