"""view.fit_selected_to_curve(): converts a dense/jagged polyline entity
into a kind="bezier" entity — real editable curve, not just a smoothed or
reduced polyline. Registered as commands.path.fit_curve / path.smooth so
both show up in the Keybindings dialog, radial menu, and context menu.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import make_view  # noqa: E402


def _dense_arc_view(qapp, radius: float = 20.0, n: int = 80):
    v = make_view(qapp, [])
    pts = [
        (
            radius * math.cos(math.radians(90.0 * i / (n - 1))),
            radius * math.sin(math.radians(90.0 * i / (n - 1))),
        )
        for i in range(n)
    ]
    v.load([pts])
    return v


def test_fit_selected_to_curve_converts_kind_to_bezier(qapp):
    v = _dense_arc_view(qapp)
    v.set_selection([v._entities[0].id])
    n = v.fit_selected_to_curve(tolerance=0.5)
    assert n == 1
    ent = v._entities[0]
    assert ent.kind == "bezier"
    assert ent.meta is not None and "control_points" in ent.meta
    assert len(ent.points) < 80  # far fewer anchors than the original dense trace


def test_fit_selected_to_curve_is_undo_tracked(qapp):
    v = _dense_arc_view(qapp)
    v.set_selection([v._entities[0].id])
    before = list(v._entities[0].points)
    v.fit_selected_to_curve(tolerance=0.5)
    assert v._entities[0].kind == "bezier"
    assert v.undo()
    assert v._entities[0].kind == "polyline"
    assert v._entities[0].points == before


def test_fit_selected_to_curve_result_tessellates_via_flattened_points(qapp):
    """The whole point of using kind="bezier": get_selected()/export must
    re-tessellate it smoothly (the curve-fidelity architecture from
    earlier this session), not hand back the sparse anchor list."""
    v = _dense_arc_view(qapp)
    v.set_selection([v._entities[0].id])
    v.fit_selected_to_curve(tolerance=0.5)
    raw_anchor_count = len(v._entities[0].points)
    tessellated = v.get_selected()[0]
    assert len(tessellated) > raw_anchor_count


def test_fit_selected_to_curve_no_selection_is_a_noop(qapp):
    v = _dense_arc_view(qapp)
    assert v.fit_selected_to_curve() == 0


def test_path_fit_curve_and_path_smooth_are_registered_commands():
    from src.ui.canvas.interaction import commands as canvas_commands

    fit_cmd = canvas_commands.get("path.fit_curve")
    smooth_cmd = canvas_commands.get("path.smooth")
    assert fit_cmd.category == "Path"
    assert smooth_cmd.category == "Path"


def test_path_fit_curve_appears_in_radial_menu_pool():
    from src.ui.widgets.dialogs.customize_dialogs import _POOL

    assert "path.fit_curve" in _POOL
    assert "path.smooth" in _POOL
