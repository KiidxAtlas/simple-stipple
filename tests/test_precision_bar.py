"""Precision bar is the compact home for grid and object-snap controls.

Construction and measurement are drawing tools and live in the Draw/tool
surfaces. These tests cover the button-click path
that test_canvas_behavior.py's precision-state test doesn't: actually
clicking the widget, not just calling the canvas method directly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.widgets.precision_bar import CanvasPrecisionBar
from tests.test_canvas_behavior import make_canvas


def test_snap_menu_actions_drive_canvas_flags(qapp):
    canvas = make_canvas(qapp)
    bar = CanvasPrecisionBar(canvas)

    assert canvas._snap_master_enabled is True
    bar._snap_actions["snap_master"].trigger()
    assert canvas._snap_master_enabled is False

    assert canvas._grid_snap is False
    bar._snap_actions["grid_snap"].trigger()
    assert canvas._grid_snap is True

    assert canvas._snap_vertex_enabled is True
    bar._snap_actions["snap_vertex"].trigger()
    assert canvas._snap_vertex_enabled is False

    assert canvas._snap_edge_enabled is True
    bar._snap_actions["snap_edge"].trigger()
    assert canvas._snap_edge_enabled is False

    assert canvas._snap_tangent_enabled is True
    bar._snap_actions["snap_tangent"].trigger()
    assert canvas._snap_tangent_enabled is False

    assert canvas._snap_extension_enabled is True
    bar._snap_actions["snap_extension"].trigger()
    assert canvas._snap_extension_enabled is False

    assert canvas._snap_angle_enabled is True
    bar._snap_actions["snap_angle"].trigger()
    assert canvas._snap_angle_enabled is False


def test_grid_spacing_is_progressively_disclosed(qapp):
    canvas = make_canvas(qapp)
    canvas.set_grid_visible(False)
    canvas.set_grid_snap(False)
    bar = CanvasPrecisionBar(canvas)
    assert not bar._spacing.isVisible()
    bar._grid_btn.click()
    assert not bar._spacing.isHidden()
