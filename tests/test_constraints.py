import math

import pytest

from src.backend.cad.constraints import GeometricConstraint, solve_constraints
from tests.test_canvas_behavior import make_canvas


def test_pure_solver_handles_parallel_perpendicular_equal_and_coincident():
    geometry = {"a": [(0, 0), (10, 0)], "b": [(20, 4), (23, 8)]}
    parallel = solve_constraints(geometry, [GeometricConstraint("parallel", ("a", "b"))])
    assert parallel["b"][1][1] == pytest.approx(4)
    assert math.dist(*parallel["b"]) == pytest.approx(5)

    perpendicular = solve_constraints(geometry, [GeometricConstraint("perpendicular", ("a", "b"))])
    assert perpendicular["b"][1][0] == pytest.approx(20)

    equal = solve_constraints(geometry, [GeometricConstraint("equal_length", ("a", "b"))])
    assert math.dist(*equal["b"]) == pytest.approx(10)

    coincident = solve_constraints(
        geometry,
        [
            GeometricConstraint(
                "coincident",
                ("a", "b"),
                {"first_endpoint": 1, "second_endpoint": 0},
            )
        ],
    )
    assert coincident["b"][0] == (10, 0)


def test_canvas_constraint_is_persistent_and_undoable(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 4)]])
    canvas.set_selection([0])
    assert canvas.add_geometric_constraint("horizontal") == 1
    assert canvas._entities[0].points[0][1] == pytest.approx(canvas._entities[0].points[1][1])
    assert len(canvas._constraints) == 1

    assert canvas.undo()
    assert canvas._entities[0].points == [(0, 0), (10, 4)]
    assert canvas._constraints == []
    assert canvas.redo()
    assert len(canvas._constraints) == 1


def test_constraint_round_trips_with_entity_ids(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 4)]])
    canvas.set_selection([0])
    canvas.add_geometric_constraint("vertical")
    records = canvas.get_entity_records()
    view_state = canvas.get_view_state()

    restored = make_canvas(qapp)
    restored.set_entity_records(records)
    restored.set_view_state(view_state)
    assert len(restored._constraints) == 1
    assert restored._constraints[0].entity_ids == (restored._entities[0].id,)


def test_fixed_constraint_restores_geometry_after_edit(qapp):
    canvas = make_canvas(qapp, [[(0, 0), (10, 0)]])
    canvas.set_selection([0])
    canvas.add_geometric_constraint("fixed")
    canvas._entities[0].points = [(0, 0), (20, 5)]
    canvas._fire_poly_change()
    assert canvas._entities[0].points == [(0.0, 0.0), (10.0, 0.0)]
