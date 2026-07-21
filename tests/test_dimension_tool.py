"""Persistent linear, diameter, and angular dimension annotations."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402

from tests.test_canvas_behavior import (  # noqa: E402
    _mouse_event,
    click_world,
    key,
    make_view,
    move,
    square,
)


def _place_dimension(v, p1, p2, offset_click):
    v.toggle_dimension_mode()
    click_world(v, *p1)
    click_world(v, *p2)
    click_world(v, *offset_click)


def test_dimension_tool_places_a_persistent_annotation(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    assert len(v._dimensions) == 1
    dim = v._dimensions[0]
    assert dim["p1"] == pytest.approx((0.0, 0.0))
    assert dim["p2"] == pytest.approx((30.0, 0.0))
    assert dim["offset"] == pytest.approx(8.0, abs=0.5)
    # Placing doesn't leave the tool armed for another dimension.
    assert v._dim_pending_p1 is None
    assert v._dim_pending_p2 is None
    assert not v._dimension_mode


def test_shape_can_be_selected_immediately_after_dimensioning(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 20.0)])
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    click_world(v, 20.0, 0.0)
    click_world(v, 10.0, -6.0)

    assert not v._dimension_mode
    click_world(v, 20.0, 10.0)

    assert v.get_selection_indices() == [0]


def test_deleting_shape_removes_dimensions_that_reference_it(qapp):
    v = make_view(qapp, [square(0.0, 0.0, 20.0)])
    entity_id = v._entities[0].id
    v._dimensions = [
        {
            "type": "linear",
            "p1": (0.0, 0.0),
            "p2": (20.0, 0.0),
            "offset": 5.0,
            "driving": {
                "kind": "segment_length",
                "sources": [{"entity_id": entity_id, "segment_index": 0}],
            },
        }
    ]
    v.set_selection([0])

    assert v.delete_selected() == 1
    assert v._dimensions == []


def test_angular_dimension_rays_do_not_block_shape_selection(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (10.0, 0.0)],
            [(0.0, 0.0), (0.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()
    click_world(v, 7.0, 0.0)
    click_world(v, 0.0, 7.0)
    click_world(v, 4.0, 4.0)

    assert not v._dimension_mode
    click_world(v, 8.0, 0.0)

    assert v.get_selection_indices() == [0]


def test_clicking_parametric_circle_creates_diameter_dimension(qapp):
    v = make_view(qapp, [])
    v.set_entity_records(
        [{"points": [], "kind": "circle", "meta": {"center": (5.0, 5.0), "radius": 4.0}}],
        fit=True,
    )
    v.toggle_dimension_mode()
    click_world(v, 9.0, 5.0)
    click_world(v, 9.0, 9.0)
    assert len(v._dimensions) == 1
    assert v._dimensions[0]["type"] == "diameter"
    assert v._dimensions[0]["p1"] == pytest.approx((1.0, 5.0))
    assert v._dimensions[0]["p2"] == pytest.approx((9.0, 5.0))


def test_diameter_dimension_follows_the_clicked_radial_direction(qapp):
    v = make_view(qapp, [])
    v.set_entity_records(
        [{"points": [], "kind": "circle", "meta": {"center": (5.0, 5.0), "radius": 4.0}}],
        fit=True,
    )
    v.toggle_dimension_mode()

    click_world(v, 5.0, 9.0)
    click_world(v, 9.0, 9.0)

    assert v._dimensions[0]["p1"] == pytest.approx((5.0, 1.0))
    assert v._dimensions[0]["p2"] == pytest.approx((5.0, 9.0))


def test_scale_and_dimension_modes_are_mutually_exclusive(qapp):
    v = make_view(qapp, [])
    v.toggle_measure()
    assert v._measure_mode

    v.toggle_dimension_mode()
    assert v._dimension_mode
    assert not v._measure_mode

    v.toggle_measure()
    assert v._measure_mode
    assert not v._dimension_mode


def test_dimension_guidance_tracks_all_linear_placement_stages(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    assert "Select a segment" in v.get_command_guidance()[0]
    click_world(v, 0.0, 0.0)
    assert "related segment or vertex" in v.get_command_guidance()[0]
    click_world(v, 10.0, 0.0)
    assert "Position the dimension" in v.get_command_guidance()[0]


def test_dimension_overlay_emits_mode_changes_for_header_sync(qapp):
    v = make_view(qapp, [])
    spy = QSignalSpy(v.modeChanged)

    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    key(v, Qt.Key.Key_Escape)

    assert spy.count() >= 4
    assert not v._dimension_mode


def test_dimension_rejects_identical_endpoints(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    click_world(v, 0.0, 0.0)

    assert len(v._dimension_tool.targets) == 1
    assert v._dimension_tool.candidate is None


def test_dimension_right_click_steps_back_through_placement(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)

    v._rightclick_cb(*v._w2c(10.0, 0.0))
    assert v._dimension_mode
    assert v._dimension_tool.candidate is None
    assert len(v._dimension_tool.targets) == 2
    v._rightclick_cb(*v._w2c(0.0, 0.0))
    assert v._dimension_mode
    assert len(v._dimension_tool.targets) == 1
    v._rightclick_cb(*v._w2c(0.0, 0.0))
    assert not v._dimension_tool.targets
    v._rightclick_cb(*v._w2c(0.0, 0.0))
    assert not v._dimension_mode


def test_smart_dimension_same_segment_places_length(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (30.0, 0.0)]])
    v.toggle_dimension_mode()

    click_world(v, 15.0, 0.0)
    assert len(v._dimension_tool.targets) == 1
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 8.0)

    assert not v._dim_selected_segments
    assert len(v._dimensions) == 1
    assert math.dist(v._dimensions[0]["p1"], v._dimensions[0]["p2"]) == pytest.approx(30.0)


def test_smart_dimension_intersecting_segments_place_angle(qapp):
    v = make_view(
        qapp,
        [
            [(-10.0, 0.0), (10.0, 0.0)],
            [(0.0, -10.0), (0.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()

    click_world(v, 5.0, 0.0)
    click_world(v, 0.0, 5.0)
    click_world(v, 4.0, 4.0)

    assert len(v._dimensions) == 1
    assert v._dimensions[0]["type"] == "angle"
    assert v._dimensions[0]["p2"] == pytest.approx((0.0, 0.0))


def test_smart_dimension_parallel_segments_place_perpendicular_spacing(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (30.0, 0.0)],
            [(0.0, 10.0), (30.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()

    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 10.0)
    click_world(v, 20.0, 5.0)

    assert v._dimensions[0]["type"] == "spacing"
    assert math.dist(v._dimensions[0]["p1"], v._dimensions[0]["p2"]) == pytest.approx(10.0)


def test_smart_dimension_separate_segments_place_shortest_distance(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (10.0, 0.0)],
            [(20.0, 5.0), (20.0, 15.0)],
        ],
    )
    v.toggle_dimension_mode()

    click_world(v, 5.0, 0.0)
    click_world(v, 20.0, 10.0)
    click_world(v, 14.0, 7.0)

    assert v._dimensions[0]["type"] == "distance"
    assert v._dimensions[0]["p1"] == pytest.approx((10.0, 0.0))
    assert v._dimensions[0]["p2"] == pytest.approx((20.0, 5.0))


def test_driving_length_dimension_changes_segment_and_is_undoable(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (30.0, 0.0)]])
    v.toggle_dimension_mode()
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 8.0)

    assert v._dimension_tool.set_value(0, 50.0)
    assert v._entities[0].points[1] == pytest.approx((50.0, 0.0))
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(50.0)

    assert v.undo()
    assert v._entities[0].points[1] == pytest.approx((30.0, 0.0))
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(30.0)


def test_driving_spacing_dimension_moves_second_segment(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (30.0, 0.0)],
            [(0.0, 10.0), (30.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 10.0)
    click_world(v, 20.0, 5.0)

    assert v._dimension_tool.set_value(0, 20.0)
    assert v._entities[1].points == pytest.approx([(0.0, 20.0), (30.0, 20.0)])
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(20.0)


def test_driving_spacing_between_edges_of_one_shape_resizes_that_shape(qapp):
    v = make_view(
        qapp,
        [[(0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (0.0, 10.0), (0.0, 0.0)]],
    )
    v.toggle_dimension_mode()
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 10.0)
    click_world(v, 20.0, 5.0)

    assert v._dimension_tool.set_value(0, 20.0)
    assert v._entities[0].points == pytest.approx(
        [(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    )
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(20.0)
    assert v.undo()
    assert v._entities[0].points == pytest.approx(
        [(0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    )


def test_driving_angle_dimension_rotates_second_segment(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (10.0, 0.0)],
            [(0.0, 0.0), (0.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()
    click_world(v, 5.0, 0.0)
    click_world(v, 0.0, 5.0)
    click_world(v, 4.0, 4.0)

    assert v._dimension_tool.set_value(0, 45.0)
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(45.0)


def test_driving_clockwise_angle_keeps_its_side_and_sets_minor_angle(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (10.0, 0.0)],
            [(0.0, 0.0), (0.0, -10.0)],
        ],
    )
    v.toggle_dimension_mode()
    click_world(v, 5.0, 0.0)
    click_world(v, 0.0, -5.0)
    click_world(v, 4.0, -4.0)

    assert v._dimension_tool.set_value(0, 45.0)
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(45.0)
    assert v._entities[1].points[1][1] < 0.0


def test_driving_angle_changes_connected_segments_in_one_polyline(qapp):
    v = make_view(qapp, [[(10.0, 0.0), (0.0, 0.0), (0.0, 10.0)]])
    v.toggle_dimension_mode()
    click_world(v, 5.0, 0.0)
    click_world(v, 0.0, 5.0)
    click_world(v, 4.0, 4.0)

    assert v._dimension_tool.set_value(0, 45.0)
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(45.0)
    assert v._entities[0].points[0] == pytest.approx((10.0, 0.0))
    assert v._entities[0].points[2] == pytest.approx((math.sqrt(50), math.sqrt(50)))


def test_editing_one_angle_moves_open_branch_and_refreshes_other_angle(qapp):
    v = make_view(
        qapp,
        [[(10.0, 0.0), (0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]],
    )
    entity_id = v._entities[0].id

    def source(segment):
        return {"entity_id": entity_id, "segment_index": segment}

    first = {
        "type": "angle",
        "p1": (10.0, 0.0),
        "p2": (0.0, 0.0),
        "p3": (0.0, 10.0),
        "points": [(10.0, 0.0), (0.0, 0.0), (0.0, 10.0)],
        "precision": 1,
        "driving": {"kind": "angle", "sources": [source(0), source(1)]},
    }
    second = {
        "type": "angle",
        "p1": (0.0, 0.0),
        "p2": (0.0, 10.0),
        "p3": (10.0, 10.0),
        "points": [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)],
        "precision": 1,
        "driving": {"kind": "angle", "sources": [source(1), source(2)]},
    }
    v._dimensions = [first, second]

    assert v._dimension_tool.set_value(0, 45.0)
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(45.0)
    assert v._dimension_tool.value(v._dimensions[1]) == pytest.approx(90.0)
    assert v._dimensions[1]["p2"] == pytest.approx(v._entities[0].points[2])
    assert math.dist(v._entities[0].points[2], v._entities[0].points[3]) == pytest.approx(10.0)


def test_driving_metadata_round_trips_with_dimension(qapp):
    v = make_view(qapp, [[(0.0, 0.0), (30.0, 0.0)]])
    v.toggle_dimension_mode()
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 8.0)

    restored = make_view(qapp, [[(0.0, 0.0), (30.0, 0.0)]])
    # Preserve the referenced entity identity, as a workspace restore does.
    state = v.get_view_state()
    restored.set_entity_records(v.get_entity_records())
    restored.set_view_state(state)

    assert restored._dimensions[0]["driving"]["kind"] == "segment_length"
    assert restored._dimension_tool.set_value(0, 40.0)
    assert restored._entities[0].points[1] == pytest.approx((40.0, 0.0))


def test_driving_value_badge_is_clickable_and_opens_measurement_editor(qapp):
    from PySide6.QtCore import QEvent

    v = make_view(qapp, [[(0.0, 0.0), (30.0, 0.0)]])
    v.toggle_dimension_mode()
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 0.0)
    click_world(v, 15.0, 8.0)
    line = v._dimension_line_points(v._dimensions[0])
    assert line is not None
    first, second = (v._w2c(*point) for point in line)
    badge_x = (first[0] + second[0]) / 2.0
    badge_y = (first[1] + second[1]) / 2.0 - 12.0

    assert v._find_dimension_at(badge_x, badge_y) == 0
    v.mouseDoubleClickEvent(_mouse_event(QEvent.Type.MouseButtonDblClick, badge_x, badge_y))

    assert v._selected_dimension == 0
    assert v._hud_prompt_edit is not None
    assert "30" in v._hud_prompt_edit.text()


def test_non_driving_dimension_badge_opens_bounded_precision_editor(qapp):
    from PySide6.QtCore import QEvent

    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    line = v._dimension_line_points(v._dimensions[0])
    assert line is not None
    first, second = (v._w2c(*point) for point in line)

    v.mouseDoubleClickEvent(
        _mouse_event(
            QEvent.Type.MouseButtonDblClick,
            (first[0] + second[0]) / 2.0,
            (first[1] + second[1]) / 2.0 - 12.0,
        )
    )

    assert v._hud_prompt_edit is not None
    assert v._hud_prompt_edit.toolTip() == "Dimension decimals"


def test_vertex_distance_is_driving_after_placement(qapp):
    v = make_view(
        qapp,
        [
            [(0.0, 0.0), (0.0, 10.0)],
            [(30.0, 0.0), (30.0, 10.0)],
        ],
    )
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    click_world(v, 30.0, 0.0)
    assert v._dimension_tool.stage == "place"
    click_world(v, 15.0, 8.0)

    assert v._dimensions[0]["driving"]["kind"] == "point_distance"
    assert v._dimension_tool.set_value(0, 40.0)
    assert v._entities[1].points[0] == pytest.approx((40.0, 0.0))


def test_circle_diameter_is_driving_after_placement(qapp):
    v = make_view(qapp, [])
    v.set_entity_records(
        [{"points": [], "kind": "circle", "meta": {"center": (5.0, 5.0), "radius": 4.0}}],
        fit=True,
    )
    v.toggle_dimension_mode()
    click_world(v, 9.0, 5.0)
    assert v._dimension_tool.stage == "place"
    click_world(v, 9.0, 9.0)

    assert v._dimension_tool.set_value(0, 12.0)
    assert v._entities[0].meta["radius"] == pytest.approx(6.0)
    assert v._dimension_tool.value(v._dimensions[0]) == pytest.approx(12.0)


def test_angular_dimension_uses_three_points_and_round_trips(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode("angle")
    click_world(v, 0.0, 0.0)
    click_world(v, 10.0, 0.0)
    click_world(v, 10.0, 10.0)
    click_world(v, 6.0, 6.0)
    assert len(v._dimensions) == 1
    assert v._dimensions[0]["type"] == "angle"
    assert v._dimensions[0]["p3"] == pytest.approx((10.0, 10.0))

    restored = make_view(qapp, [])
    restored.set_view_state(v.get_view_state())
    assert restored._dimensions[0]["type"] == "angle"
    assert restored._dimensions[0]["p3"] == pytest.approx((10.0, 10.0))


def test_dimension_is_not_undo_tracked(qapp):
    v = make_view(qapp, [square(0, 0)])
    v.set_selection([0])
    v._duplicate_selected()  # a real, undo-tracked mutation
    assert v.poly_count == 2

    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    assert len(v._dimensions) == 1

    assert v.undo()  # undoes the duplicate, not the dimension
    assert v.poly_count == 1
    assert len(v._dimensions) == 1  # untouched


def test_escape_cancels_in_progress_dimension(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    click_world(v, 0.0, 0.0)
    assert v._dim_pending_p1 is not None
    key(v, Qt.Key.Key_Escape)
    assert v._dim_pending_p1 is None
    assert len(v._dimensions) == 0
    # Escape backs all the way out of dimension mode, not just the
    # in-progress placement — the tool shouldn't stay armed.
    assert v._dimension_mode is False


def test_escape_exits_idle_dimension_mode(qapp):
    v = make_view(qapp, [])
    v.toggle_dimension_mode()
    assert v._dimension_mode is True
    key(v, Qt.Key.Key_Escape)
    assert v._dimension_mode is False


def test_delete_removes_selected_dimension(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    v.toggle_dimension_mode()  # back to select mode
    v.set_mode("select")

    # Click on the offset dimension line (midpoint, 8mm above the segment).
    click_world(v, 15.0, 8.0)
    assert v._selected_dimension == 0

    key(v, Qt.Key.Key_Delete)
    assert v._dimensions == []
    assert v._selected_dimension is None


def test_backspace_also_removes_selected_dimension(qapp):
    """Key_Delete is routed through the command registry (edit.delete), but
    Backspace is intercepted earlier by its own dedicated handler — both
    must delete a selected dimension."""
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    v.toggle_dimension_mode()
    v.set_mode("select")

    click_world(v, 15.0, 8.0)
    assert v._selected_dimension == 0

    key(v, Qt.Key.Key_Backspace)
    assert v._dimensions == []
    assert v._selected_dimension is None


def test_dimensions_round_trip_through_view_state(qapp):
    v = make_view(qapp, [])
    _place_dimension(v, (0.0, 0.0), (30.0, 0.0), (15.0, 8.0))
    state = v.get_view_state()
    assert "dimensions" in state

    v2 = make_view(qapp, [])
    v2.set_view_state(state)
    assert len(v2._dimensions) == 1
    assert v2._dimensions[0]["p1"] == pytest.approx((0.0, 0.0))
    assert v2._dimensions[0]["p2"] == pytest.approx((30.0, 0.0))


def test_select_all_includes_and_deletes_dimensions(qapp):
    v = make_view(qapp)
    v.load([[(0.0, 0.0), (10.0, 0.0)]])
    v._dimensions = [{"type": "linear", "p1": (0.0, 0.0), "p2": (10.0, 0.0), "offset": 4.0}]
    v.select_all()
    assert v._all_dimensions_selected
    assert v._selected_dimension == 0
    v._delete_selected_dimension()
    assert v._dimensions == []


def test_gizmo_resize_refreshes_driving_dimension_anchors(qapp):
    v = make_view(qapp)
    v.load([[(0.0, 0.0), (10.0, 0.0)]])
    v._dimensions = [
        {
            "type": "linear",
            "p1": (0.0, 0.0),
            "p2": (10.0, 0.0),
            "offset": 4.0,
            "driving": {
                "kind": "segment_length",
                "sources": [{"entity_id": v._entities[0].id, "segment_index": 0}],
            },
        }
    ]
    v._sel = {0}
    assert v._start_gizmo_drag("scale-e", 10.0, 0.0)
    v._apply_gizmo_drag(20.0, 0.0, Qt.KeyboardModifier.NoModifier)
    assert v._dimensions[0]["p2"] == pytest.approx((20.0, 0.0))


def test_dxf_writer_never_references_dimensions():
    import inspect

    from src.backend.dxf import io as dxf_io

    src = inspect.getsource(dxf_io)
    assert "_dimensions" not in src


def test_hovering_before_first_click_shows_a_snap_indicator(qapp):
    """Before p1 is placed, moving near a snappable vertex must set the
    generic hover-snap indicator (same one Draw/Scale use) — otherwise
    there is no visual feedback that a click here will snap at all."""
    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    cx, cy = v._w2c(10.2, 9.9)  # near the square's (10, 10) corner
    move(v, cx, cy)
    assert v._hover_snap == pytest.approx((10.0, 10.0))


def test_clicking_near_a_vertex_snaps_p1_and_p2_to_it(qapp):
    """The click itself must land exactly on the snap target, not the raw
    (slightly-off) cursor position — this is the actual placement, not just
    the preview."""
    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    click_world(v, 10.2, 9.9)  # near (10, 10) corner -> should snap exactly
    assert v._dim_pending_p1 == pytest.approx((10.0, 10.0))

    click_world(v, -0.2, 0.15)  # near (0, 0) corner -> should snap exactly
    assert v._dim_pending_p2 == pytest.approx((0.0, 0.0))


def test_alt_disables_snapping_while_placing(qapp):
    from PySide6.QtCore import Qt as _Qt

    v = make_view(qapp, [square(0, 0)])
    v.fit()
    v.toggle_dimension_mode()

    cx, cy = v._w2c(10.2, 9.9)
    click_world_pos = (cx, cy)
    from tests.test_canvas_behavior import click

    click(v, *click_world_pos, mods=_Qt.KeyboardModifier.AltModifier)
    wx, wy = v._c2w(cx, cy)
    assert v._dim_pending_p1 == pytest.approx((wx, wy))
    assert v._dim_pending_p1 != pytest.approx((10.0, 10.0))
