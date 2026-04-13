"""Sketch data model.

Points = the only mutable state.
Segments = pure functions of two points (user's "polyline").
Geometry = auto-detected closed loop of 3+ segments (user's "object").
Constraints = rules attached to points/segments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

# ── Core entities ─────────────────────────────────────────────────────────────


@dataclass
class Point:
    """The only mutable state. Everything derives from point positions."""

    id: int
    x: float
    y: float

    def __hash__(self) -> int:
        return self.id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Point) and self.id == other.id


@dataclass
class Segment:
    """Line between two points. In user terminology: a 'polyline'."""

    id: int
    p0: Point
    p1: Point

    def length(self) -> float:
        dx = self.p1.x - self.p0.x
        dy = self.p1.y - self.p0.y
        return (dx * dx + dy * dy) ** 0.5

    def midpoint(self) -> tuple[float, float]:
        return ((self.p0.x + self.p1.x) / 2, (self.p0.y + self.p1.y) / 2)


@dataclass
class Geometry:
    """Closed loop of 3+ segments. In user terminology: an 'object'."""

    id: int
    segments: list[Segment]

    @property
    def is_closed(self) -> bool:
        if len(self.segments) < 3:
            return False
        degree: dict[int, int] = {}
        for seg in self.segments:
            degree[seg.p0.id] = degree.get(seg.p0.id, 0) + 1
            degree[seg.p1.id] = degree.get(seg.p1.id, 0) + 1
        return all(d == 2 for d in degree.values())

    @property
    def points(self) -> list[Point]:
        seen: set[int] = set()
        pts: list[Point] = []
        for seg in self.segments:
            if seg.p0.id not in seen:
                pts.append(seg.p0)
                seen.add(seg.p0.id)
            if seg.p1.id not in seen:
                pts.append(seg.p1)
                seen.add(seg.p1.id)
        return pts


# ── Constraints ───────────────────────────────────────────────────────────────


class ConstraintKind:
    COINCIDENT = "coincident"
    DISTANCE = "distance"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    LENGTH = "length"
    FIXED = "fixed"


@dataclass
class Constraint:
    id: int
    kind: str


@dataclass
class CoincidentConstraint(Constraint):
    p1: Point = field(default=None)  # type: ignore[assignment]
    p2: Point = field(default=None)  # type: ignore[assignment]


@dataclass
class DistanceConstraint(Constraint):
    p1: Point = field(default=None)  # type: ignore[assignment]
    p2: Point = field(default=None)  # type: ignore[assignment]
    distance: float = 0.0


@dataclass
class HorizontalConstraint(Constraint):
    segment: Segment = field(default=None)  # type: ignore[assignment]


@dataclass
class VerticalConstraint(Constraint):
    segment: Segment = field(default=None)  # type: ignore[assignment]


@dataclass
class LengthConstraint(Constraint):
    segment: Segment = field(default=None)  # type: ignore[assignment]
    length: float = 0.0


@dataclass
class FixedConstraint(Constraint):
    point: Point = field(default=None)  # type: ignore[assignment]


# ── Solve result ──────────────────────────────────────────────────────────────


@dataclass
class SolveResult:
    success: bool
    dof: int = 0
    message: str = ""


# ── The graph ─────────────────────────────────────────────────────────────────


class SketchGraph:
    """Constraint graph.

    Nodes: Points (mutable state).
    Edges: Constraints (rules).
    Derived views: Segments, Geometries.

    Points are never duplicated — segments share the same Point object.
    Closure is topological only: no tolerance hacks.
    """

    def __init__(self) -> None:
        self._id_gen = count(1)
        self._points: dict[int, Point] = {}
        self._segments: dict[int, Segment] = {}
        self._constraints: dict[int, Constraint] = {}
        self._geometries: dict[int, Geometry] = {}

    def _next_id(self) -> int:
        return next(self._id_gen)

    # ── Point operations ──────────────────────────────────────────────────

    def add_point(self, x: float, y: float) -> Point:
        p = Point(id=self._next_id(), x=x, y=y)
        self._points[p.id] = p
        return p

    def remove_point(self, point: Point) -> None:
        segs_to_remove = [
            s
            for s in self._segments.values()
            if s.p0.id == point.id or s.p1.id == point.id
        ]
        for s in segs_to_remove:
            self.remove_segment(s)
        self._constraints = {
            k: c
            for k, c in self._constraints.items()
            if not _constraint_refs_point(c, point)
        }
        self._points.pop(point.id, None)
        self._detect_geometries()

    def find_point_near(self, x: float, y: float, tol: float) -> Point | None:
        best: Point | None = None
        best_dist = tol
        for p in self._points.values():
            d = ((p.x - x) ** 2 + (p.y - y) ** 2) ** 0.5
            if d < best_dist:
                best = p
                best_dist = d
        return best

    # ── Segment operations ────────────────────────────────────────────────

    def add_segment(self, p0: Point, p1: Point) -> Segment:
        seg = Segment(id=self._next_id(), p0=p0, p1=p1)
        self._segments[seg.id] = seg
        self._detect_geometries()
        return seg

    def remove_segment(self, seg: Segment) -> None:
        self._constraints = {
            k: c
            for k, c in self._constraints.items()
            if not _constraint_refs_segment(c, seg)
        }
        self._segments.pop(seg.id, None)
        self._detect_geometries()

    def remove_segments(self, indices: set[int]) -> None:
        """Remove segments by their _polys indices (sorted-id order)."""
        sorted_segs = sorted(self._segments.values(), key=lambda s: s.id)
        to_remove = [sorted_segs[i] for i in indices if i < len(sorted_segs)]
        for seg in to_remove:
            self.remove_segment(seg)

    # ── Constraint operations ─────────────────────────────────────────────

    def add_constraint(self, constraint: Constraint) -> Constraint:
        self._constraints[constraint.id] = constraint
        return constraint

    def remove_constraint(self, cid: int) -> None:
        self._constraints.pop(cid, None)

    # ── Geometry detection ────────────────────────────────────────────────

    def _detect_geometries(self) -> None:
        self._geometries.clear()
        if len(self._segments) < 3:
            return
        # Adjacency: point_id → set of segment_ids
        point_segs: dict[int, set[int]] = {}
        for seg in self._segments.values():
            point_segs.setdefault(seg.p0.id, set()).add(seg.id)
            point_segs.setdefault(seg.p1.id, set()).add(seg.id)
        # Connected components via BFS on segments sharing points
        visited: set[int] = set()
        for start_sid in self._segments:
            if start_sid in visited:
                continue
            component: list[Segment] = []
            queue = [start_sid]
            while queue:
                sid = queue.pop()
                if sid in visited:
                    continue
                visited.add(sid)
                seg = self._segments[sid]
                component.append(seg)
                for pid in (seg.p0.id, seg.p1.id):
                    for nbr in point_segs.get(pid, set()):
                        if nbr not in visited:
                            queue.append(nbr)
            # Closed loop: all points in component have degree exactly 2
            if len(component) < 3:
                continue
            degree: dict[int, int] = {}
            for seg in component:
                degree[seg.p0.id] = degree.get(seg.p0.id, 0) + 1
                degree[seg.p1.id] = degree.get(seg.p1.id, 0) + 1
            if all(d == 2 for d in degree.values()):
                geo = Geometry(id=self._next_id(), segments=component)
                self._geometries[geo.id] = geo

    # ── Conversion to PolylineView format ─────────────────────────────────

    def to_polys(self) -> list[list[tuple[float, float]]]:
        """Each segment → one 2-point polyline. Ordered by segment id."""
        return [
            [(seg.p0.x, seg.p0.y), (seg.p1.x, seg.p1.y)]
            for seg in sorted(self._segments.values(), key=lambda s: s.id)
        ]

    def segment_at_index(self, index: int) -> Segment | None:
        sorted_segs = sorted(self._segments.values(), key=lambda s: s.id)
        if 0 <= index < len(sorted_segs):
            return sorted_segs[index]
        return None

    def geometry_for_segment(self, seg: Segment) -> Geometry | None:
        for geo in self._geometries.values():
            if any(s.id == seg.id for s in geo.segments):
                return geo
        return None

    def component_for_segment(self, seg: Segment) -> list[Segment]:
        """Return the full connected segment component containing *seg*."""
        if seg.id not in self._segments:
            return []

        point_segs: dict[int, set[int]] = {}
        for cur in self._segments.values():
            point_segs.setdefault(cur.p0.id, set()).add(cur.id)
            point_segs.setdefault(cur.p1.id, set()).add(cur.id)

        component_ids: set[int] = set()
        queue = [seg.id]
        while queue:
            sid = queue.pop()
            if sid in component_ids:
                continue
            component_ids.add(sid)
            cur = self._segments[sid]
            for pid in (cur.p0.id, cur.p1.id):
                for nbr in point_segs.get(pid, set()):
                    if nbr not in component_ids:
                        queue.append(nbr)

        sorted_segments = sorted(self._segments.values(), key=lambda s: s.id)
        return [cur for cur in sorted_segments if cur.id in component_ids]

    def segments_in_geometry(self, geo: Geometry) -> set[int]:
        sorted_segs = sorted(self._segments.values(), key=lambda s: s.id)
        seg_ids = {s.id for s in geo.segments}
        return {i for i, s in enumerate(sorted_segs) if s.id in seg_ids}

    def segments_in_component(self, seg: Segment) -> set[int]:
        """Return canvas indices for the connected component containing *seg*."""
        sorted_segs = sorted(self._segments.values(), key=lambda s: s.id)
        seg_ids = {cur.id for cur in self.component_for_segment(seg)}
        return {i for i, cur in enumerate(sorted_segs) if cur.id in seg_ids}

    # ── DOF ───────────────────────────────────────────────────────────────

    def total_dof(self) -> int:
        """Total degrees of freedom: 2 per point minus constraints."""
        n_pts = len(self._points)
        n_constraints = 0
        for c in self._constraints.values():
            if c.kind in (ConstraintKind.HORIZONTAL, ConstraintKind.VERTICAL):
                n_constraints += 1
            elif c.kind == ConstraintKind.FIXED:
                n_constraints += 2
            elif c.kind in (
                ConstraintKind.COINCIDENT,
                ConstraintKind.DISTANCE,
                ConstraintKind.LENGTH,
            ):
                n_constraints += 1
        return max(0, 2 * n_pts - n_constraints)

    # ── Snapshot / restore (for undo) ─────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "points": {pid: (p.x, p.y) for pid, p in self._points.items()},
            "segments": {sid: (s.p0.id, s.p1.id) for sid, s in self._segments.items()},
        }

    def restore(self, state: dict) -> None:
        self._points.clear()
        self._segments.clear()
        self._constraints.clear()
        for pid, (x, y) in state["points"].items():
            pid = int(pid)
            self._points[pid] = Point(id=pid, x=x, y=y)
        for sid, (p0id, p1id) in state["segments"].items():
            sid = int(sid)
            self._segments[sid] = Segment(
                id=sid,
                p0=self._points[int(p0id)],
                p1=self._points[int(p1id)],
            )
        self._detect_geometries()

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def points(self) -> dict[int, Point]:
        return self._points

    @property
    def segments(self) -> dict[int, Segment]:
        return self._segments

    @property
    def constraints(self) -> dict[int, Constraint]:
        return self._constraints

    @property
    def geometries(self) -> dict[int, Geometry]:
        return self._geometries


# ── Helpers ───────────────────────────────────────────────────────────────────


def _constraint_refs_point(c: Constraint, p: Point) -> bool:
    if isinstance(c, CoincidentConstraint):
        return c.p1.id == p.id or c.p2.id == p.id
    if isinstance(c, DistanceConstraint):
        return c.p1.id == p.id or c.p2.id == p.id
    if isinstance(c, (HorizontalConstraint, VerticalConstraint, LengthConstraint)):
        return c.segment.p0.id == p.id or c.segment.p1.id == p.id
    if isinstance(c, FixedConstraint):
        return c.point.id == p.id
    return False


def _constraint_refs_segment(c: Constraint, seg: Segment) -> bool:
    if isinstance(c, (HorizontalConstraint, VerticalConstraint, LengthConstraint)):
        return c.segment.id == seg.id
    return False
