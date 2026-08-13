from __future__ import annotations

from types import SimpleNamespace

from simple_stipple.canvas.view.main import CanvasView
from simple_stipple.canvas.view.preferences import (
    set_grid_snap,
    set_snap_master,
    set_snap_strength,
    set_snap_vertex,
)


class _PreferenceHost(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self.redraws = 0
        self.sidebar_refreshes = 0

    def _redraw(self) -> None:
        self.redraws += 1

    def _refresh_draw_sidebar_state(self) -> None:
        self.sidebar_refreshes += 1


def test_canvas_view_keeps_preference_methods_at_its_public_api() -> None:
    assert CanvasView.set_grid_snap is set_grid_snap
    assert CanvasView.set_snap_master is set_snap_master
    assert CanvasView.set_snap_vertex is set_snap_vertex
    assert CanvasView.set_snap_strength is set_snap_strength


def test_snap_preference_feedback_matches_each_setting_kind() -> None:
    host = _PreferenceHost()

    set_snap_vertex(host, True)
    assert host._snap_vertex_enabled is True
    assert (host.sidebar_refreshes, host.redraws) == (1, 0)

    set_snap_master(host, True)
    assert host._snap_master_enabled is True
    assert (host.sidebar_refreshes, host.redraws) == (2, 1)

    set_grid_snap(host, True)
    assert host._grid_snap is True
    assert (host.sidebar_refreshes, host.redraws) == (3, 2)


def test_snap_strength_is_clamped_and_invalid_values_restore_default() -> None:
    host = _PreferenceHost()

    set_snap_strength(host, 4.0)
    assert host._snap_strength == 2.0
    set_snap_strength(host, "not-a-number")
    assert host._snap_strength == 1.0
    assert (host.sidebar_refreshes, host.redraws) == (2, 2)
