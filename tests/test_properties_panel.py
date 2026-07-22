"""Properties panel: numeric selection editing."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import bbox, make_canvas, square  # noqa: E402


def make_panel(qapp, polys):
    from src.ui.widgets.canvas.properties_panel import CanvasPropertiesPanel

    canvas = make_canvas(qapp, polys)
    panel = CanvasPropertiesPanel(canvas)
    return canvas, panel


def test_panel_reflects_selection(qapp):
    canvas, panel = make_panel(qapp, [square(10, 20)])
    assert panel._summary.text() == "No selection"
    canvas.set_selection([0])
    assert panel._x.text() == "10.00"
    assert panel._y.text() == "20.00"
    assert panel._w.text() == "10.00"


def test_panel_omits_vertex_edit_and_open_path_actions(qapp):
    _canvas, panel = make_panel(qapp, [square(0, 0)])

    assert "edit" not in panel._context_buttons
    assert "open" not in panel._context_buttons
    assert set(panel._context_buttons) == {"duplicate", "constraints", "close", "delete"}


def test_panel_uses_contextual_scrollable_property_groups(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0)])
    assert panel._scroll.widgetResizable()
    assert panel._geometry_section._title == "Geometry"
    assert panel._transform_section._title == "Transform"
    assert panel._shape_section._title == "Shape Parameters"
    assert panel._selection_constraints_section._title == "Constraints / Dimensions"
    assert panel._actions_section._title == "Actions"

    canvas.set_selection([0])
    panel.refresh()
    assert not panel._shape_section.isVisibleTo(panel)
    assert not panel._selection_constraints_section.isVisibleTo(panel)
    assert panel._actions_section.isVisibleTo(panel)


def test_panel_stacks_action_grids_below_260_pixels(qapp):
    _canvas, panel = make_panel(qapp, [square(0, 0)])
    panel.resize(240, 500)
    panel.show()
    qapp.processEvents()

    positions = [
        panel._actions_layout.getItemPosition(
            panel._actions_layout.indexOf(button)
        )[:2]
        for button in panel._action_buttons
    ]
    assert positions == [(0, 0), (1, 0), (2, 0), (3, 0)]


def test_panel_moves_selection(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0)])
    canvas.set_selection([0])
    panel._x.setText("25")
    panel._commit_pos()
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x0 == pytest.approx(25.0)
    assert canvas.undo()


def test_panel_resizes_selection(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0)])
    canvas.set_selection([0])
    panel._w.setText("40")
    panel._commit_size("w")
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(40.0)


def test_panel_edits_selected_driving_dimension(qapp):
    canvas, panel = make_panel(qapp, [[(0.0, 0.0), (10.0, 0.0)]])
    entity_id = canvas._entities[0].id
    canvas._dimensions = [
        {
            "type": "linear",
            "p1": (0.0, 0.0),
            "p2": (10.0, 0.0),
            "offset": 5.0,
            "precision": 2,
            "driving": {
                "kind": "point_distance",
                "sources": [
                    {"entity_id": entity_id, "vertex_index": 0},
                    {"entity_id": entity_id, "vertex_index": 1},
                ],
            },
        }
    ]
    canvas._selected_dimension = 0
    panel.refresh()

    assert panel._summary.text() == "Driving Linear Dimension"
    assert panel._dimension_container.isHidden() is False
    panel._dimension_value.setText("25")
    panel._commit_dimension_value()

    assert canvas._entities[0].points[1] == pytest.approx((25.0, 0.0))
    assert canvas._dimensions[0]["p2"] == pytest.approx((25.0, 0.0))


def test_aspect_lock_toggle_syncs_with_canvas_flag(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0)])
    assert panel._aspect_lock_btn.isChecked() is False
    panel._aspect_lock_btn.setChecked(True)
    assert canvas._aspect_ratio_locked is True
    panel._aspect_lock_btn.setChecked(False)
    assert canvas._aspect_ratio_locked is False


def test_aspect_lock_keeps_width_and_height_proportional(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0, s=10.0)])  # 10x10 square
    canvas.set_selection([0])
    panel._aspect_lock_btn.setChecked(True)

    panel._w.setText("40")
    panel._commit_size("w")
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(40.0)
    assert y1 - y0 == pytest.approx(40.0)  # height followed width proportionally

    panel._h.setText("20")
    panel._commit_size("h")
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert y1 - y0 == pytest.approx(20.0)
    assert x1 - x0 == pytest.approx(20.0)  # width followed height back down


def test_aspect_lock_applies_to_gizmo_edge_drag(qapp):
    from PySide6.QtCore import Qt

    canvas, panel = make_panel(qapp, [square(0, 0, s=10.0)])
    canvas.fit()
    canvas.set_selection([0])
    canvas.set_aspect_ratio_locked(True)

    assert canvas._start_gizmo_drag("scale-e", 10.0, 5.0)
    canvas._apply_gizmo_drag(20.0, 5.0, Qt.KeyboardModifier.NoModifier)
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(20.0, abs=0.01)
    assert y1 - y0 == pytest.approx(20.0, abs=0.01)  # edge-only drag still scaled H


def test_panel_edits_circle_radius(qapp):
    canvas, panel = make_panel(qapp, [])
    from tests.test_canvas_behavior import click_world

    canvas.set_mode("draw")
    canvas._set_draw_primitive("circle")
    click_world(canvas, 50.0, 50.0)
    click_world(canvas, 60.0, 50.0)
    canvas.set_mode("select")
    canvas.set_selection([0])
    panel.refresh()
    assert panel._param_edits and "radius" in panel._param_edits
    panel._param_edits["radius"].setText("20")
    panel._commit_param("radius")
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(40.0, abs=0.2)
    meta = canvas._entities[0].meta
    assert meta is not None
    assert meta["radius"] == pytest.approx(20.0)
    assert canvas.undo()
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(20.0, abs=0.2)


def test_panel_rotate_via_field(qapp):
    canvas, panel = make_panel(qapp, [square(0, 0)])
    canvas.set_selection([0])
    panel._rot.setText("45")
    panel._commit_rotation()
    import math

    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(10 * math.sqrt(2), abs=1e-6)
    assert float(panel._rot.text()) == pytest.approx(45.0)


def test_panel_shows_live_slot_angle_after_gizmo_rotation(qapp):
    from PySide6.QtCore import Qt

    canvas, panel = make_panel(qapp, [])
    canvas.set_entity_records(
        [
            {
                "points": square(0, 0),
                "kind": "slot",
                "meta": {
                    "center": (5.0, 5.0),
                    "length": 10.0,
                    "width": 4.0,
                    "rotation": 0.0,
                },
            }
        ]
    )
    canvas.set_selection([0])
    assert canvas._start_gizmo_drag("rotate", 10.0, 5.0)
    canvas._apply_gizmo_drag(5.0, 10.0, Qt.KeyboardModifier.NoModifier)
    assert canvas._entities[0].meta["rotation"] == pytest.approx(90.0)
    assert float(panel._rot.text()) == pytest.approx(90.0)


@pytest.mark.parametrize(
    ("points", "label"),
    [
        ([(0.0, 0.0), (10.0, 0.0), (5.0, 8.660254), (0.0, 0.0)], "Triangle"),
        (square(0, 0), "Rectangle"),
    ],
)
def test_panel_recognizes_hand_drawn_closed_shapes(qapp, points, label):
    canvas, panel = make_panel(qapp, [points])
    canvas.set_selection([0])
    assert canvas._entities[0].kind == "polyline"
    assert panel._summary.text() == label


def test_panel_edits_ellipse_radius(qapp):
    canvas, panel = make_panel(qapp, [])
    from tests.test_canvas_behavior import click_world

    canvas.set_mode("draw")
    canvas._set_draw_primitive("ellipse")
    click_world(canvas, 50.0, 50.0)
    click_world(canvas, 70.0, 60.0)
    canvas.set_mode("select")
    canvas.set_selection([0])
    panel.refresh()
    assert "rx" in panel._param_edits
    panel._param_edits["rx"].setText("30")
    panel._commit_param("rx")  # crashed with TypeError before the fix
    x0, y0, x1, y1 = bbox(canvas._entities[0].points)
    assert x1 - x0 == pytest.approx(60.0, abs=0.3)


def test_property_expression_preserves_parametric_width_and_focus_highlights(qapp):
    canvas, panel = make_panel(qapp, [])
    canvas.set_entity_records(
        [
            {
                "points": [(0, 0), (10, 0), (10, 5), (0, 5), (0, 0)],
                "kind": "rectangle",
                "meta": {"center": (5, 2.5), "width": 10, "height": 5, "rotation": 0},
            }
        ]
    )
    canvas.set_selection([0])
    panel.refresh()
    panel.show()
    panel._w.setFocus()
    qapp.processEvents()
    assert canvas._property_highlight == "w"
    panel._w.setText("1in + 3mm")
    panel._commit_size("w")
    assert canvas._entities[0].kind == "rectangle"
    assert canvas._entities[0].meta["width"] == pytest.approx(28.4)
    panel._w.clearFocus()
    panel.close()
