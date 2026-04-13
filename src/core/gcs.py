"""Constraint solver wrapper around python-solvespace."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from python_solvespace import ResultFlag, SolverSystem

if TYPE_CHECKING:
    from src.core.sketch import Point, SketchGraph

from src.core.sketch import (
    CoincidentConstraint,
    DistanceConstraint,
    FixedConstraint,
    HorizontalConstraint,
    LengthConstraint,
    SolveResult,
    VerticalConstraint,
)

log = logging.getLogger(__name__)

_RESULT_MESSAGES = {
    ResultFlag.OKAY: "OK",
    ResultFlag.INCONSISTENT: "Inconsistent constraints",
    ResultFlag.DIDNT_CONVERGE: "Solver didn't converge",
    ResultFlag.TOO_MANY_UNKNOWNS: "Too many unknowns",
}


class ConstraintSolver:
    """Thin wrapper that rebuilds and solves a SolverSystem from a SketchGraph."""

    def solve(
        self,
        graph: SketchGraph,
        dragged: list[Point] | None = None,
    ) -> SolveResult:
        if not graph.points:
            return SolveResult(success=True, dof=0, message="OK")

        sys = SolverSystem()
        wp = sys.create_2d_base()

        # Map graph point ids → solver entities
        entity_map: dict[int, object] = {}
        for p in graph.points.values():
            entity_map[p.id] = sys.add_point_2d(p.x, p.y, wp)

        # Map graph segment ids → solver line entities
        line_map: dict[int, object] = {}
        for seg in graph.segments.values():
            line_map[seg.id] = sys.add_line_2d(
                entity_map[seg.p0.id], entity_map[seg.p1.id], wp
            )

        # Add constraints
        for c in graph.constraints.values():
            try:
                self._add_constraint(sys, wp, c, entity_map, line_map)
            except Exception:
                log.warning("Failed to add constraint %s", c, exc_info=True)

        # Mark dragged points
        if dragged:
            for p in dragged:
                if p.id in entity_map:
                    sys.dragged(entity_map[p.id], wp)

        result = sys.solve()
        msg = _RESULT_MESSAGES.get(result, f"Unknown: {result}")

        if result == ResultFlag.OKAY:
            for p in graph.points.values():
                coords = sys.params(entity_map[p.id].params)
                p.x = coords[0]
                p.y = coords[1]

        return SolveResult(
            success=(result == ResultFlag.OKAY),
            dof=sys.dof(),
            message=msg,
        )

    @staticmethod
    def _add_constraint(sys, wp, c, entity_map, line_map) -> None:
        if isinstance(c, CoincidentConstraint):
            sys.coincident(entity_map[c.p1.id], entity_map[c.p2.id], wp)
        elif isinstance(c, DistanceConstraint):
            sys.distance(entity_map[c.p1.id], entity_map[c.p2.id], c.distance, wp)
        elif isinstance(c, HorizontalConstraint):
            sys.horizontal(line_map[c.segment.id], wp)
        elif isinstance(c, VerticalConstraint):
            sys.vertical(line_map[c.segment.id], wp)
        elif isinstance(c, LengthConstraint):
            sys.distance(
                entity_map[c.segment.p0.id],
                entity_map[c.segment.p1.id],
                c.length,
                wp,
            )
        elif isinstance(c, FixedConstraint):
            sys.dragged(entity_map[c.point.id], wp)
