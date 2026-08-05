"""Unified snap engine for the canvas.

One entry point (``query``) merges every snap source — polyline vertices,
midpoints, edges, intersections (via the pure candidate functions in
src/simple_stipple/engine/cad/snapping.py), parametric-shape points (circle centers, arc
endpoints, …), the grid, and guide lines — and returns the best candidate in
screen space. Previously this logic was split across three modules plus four
glue methods on the view, and drag vs. hover snapping threaded 13+ parameters
each.

``ShapeSnapEngine`` (merged in from the former ``shape_snapping.py`` — its
only consumer was this module) provides the shape-aware candidate points
(circle/arc/ellipse/spline centers and control points) that ``_shape_candidate``
below draws from.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simple_stipple.engine.cad.constants import SNAP_DIST
from simple_stipple.engine.cad.shapes import (
    ArcShape,
    CircleShape,
    EllipseShape,
    PolygonShape,
    RectangleShape,
    RoundedRectangleShape,
    SlotShape,
    SplineShape,
    StarShape,
)
from simple_stipple.engine.cad.snapping import (
    angle_snap,
    resolve_drag_snap,
    resolve_snap,
)

if TYPE_CHECKING:
    from simple_stipple.canvas.view.main import CanvasView
    from simple_stipple.engine.cad.shapes import Shape

SnapResult = tuple[float, float, str]
RelationshipReference = tuple[str, int, tuple[float, float], tuple[float, float]]
_ACTIVE_DRAW_REFERENCE = "__active_draw__"


class SnapEngine:
    """Snap resolution bound to one canvas view."""

    GUIDE_SNAP_PX = 8.0
    # Relationship snaps should be intentional, local references.  A broad
    # source search makes an unrelated segment elsewhere in the document win
    # simply because its length happens to put an equal-length endpoint under
    # the cursor.  Sources may be approached from either end of the new
    # stroke, so test both the anchor and the live pointer.
    RELATIONSHIP_REFERENCE_PX = 48.0
    # A combined relationship has one exact endpoint, but asking users to
    # land inside the generic 10px vertex radius defeats the point of an
    # intelligent constraint snap.  It gets a forgiving acquisition band
    # and a small hysteresis band after it has been acquired.
    COMBINED_RELATIONSHIP_ACQUIRE_PX = 28.0
    COMBINED_RELATIONSHIP_RETAIN_PX = 36.0
    # Equal length also resolves to one exact endpoint, so it needs the same
    # magnetic range as the combined relationship.  Directional constraints
    # resolve to a line rather than a point, and deliberately use a smaller
    # band to keep a freehand segment easy to draw.
    EQUAL_LENGTH_ACQUIRE_PX = 28.0
    EQUAL_LENGTH_RETAIN_PX = 36.0
    DIRECTIONAL_RELATIONSHIP_ACQUIRE_PX = 18.0
    DIRECTIONAL_RELATIONSHIP_RETAIN_PX = 24.0
    # Axis alignment and extensions are inferred construction lines.  Give
    # them a little more room than a precise vertex/curve target, without
    # allowing them to pull geometry from across the canvas.
    INFERRED_LINE_SNAP_PX = 18.0

    def __init__(self, view: CanvasView) -> None:
        self.v = view
        self.last_relationship_reference: (
            tuple[str, int, tuple[float, float], tuple[float, float]] | None
        ) = None
        self.last_relationship_type: str | None = None

    def _snap_strength(self) -> float:
        """Return the user-selected magnetic capture multiplier."""
        try:
            # A real canvas always supplies its persisted 50% default. Keep
            # the fallback at the historical full strength for lightweight
            # host adapters that do not expose snap configuration.
            value = float(getattr(self.v, "_snap_strength", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(2.0, value))

    # ── Public API ────────────────────────────────────────────────────────

    def query(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        drag: bool = False,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        allow_vertex: bool = True,
        allow_edge: bool = True,
        exclude_vertices: set[tuple[str, int]] | None = None,
        exclude_segments: set[tuple[str, int]] | None = None,
        exclude_polys: set[str] | None = None,
        reference_point: tuple[float, float] | None = None,
        allow_inferred: bool = True,
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_master_enabled", True):
            self.clear_relationship_reference()
            return None
        # Zero strength intentionally bypasses every snap family, including
        # grid points. The individual snap toggles stay untouched so a user
        # can temporarily draw freehand and then restore their setup.
        if self._snap_strength() <= 0.0:
            self.clear_relationship_reference()
            return None
        polylines = {e.id: e.points for e in v._entities}
        # Locked and non-active-layer entities remain useful references, but
        # explicitly hidden geometry must not create invisible snap targets.
        hidden_polys = v._flagged("hidden")
        snap_dist = SNAP_DIST * self._snap_strength()
        vertex_enabled = allow_vertex and getattr(v, "_snap_vertex_enabled", True)
        midpoint_enabled = allow_vertex and getattr(v, "_snap_midpoint_enabled", True)
        intersection_enabled = allow_vertex and getattr(v, "_snap_intersection_enabled", True)
        edge_enabled = allow_edge and getattr(v, "_snap_edge_enabled", True)
        if drag:
            best = resolve_drag_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                allow_vertex=vertex_enabled,
                allow_midpoint=midpoint_enabled,
                allow_intersection=intersection_enabled,
                allow_edge=edge_enabled,
                exclude_vertices=exclude_vertices,
                exclude_segments=exclude_segments,
                grid_snap_enabled=v._grid_snap,
                grid_spacing=v._grid_spacing,
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=v._segment_intersection_point,
                mode=getattr(v, "_mode", None),
                reference_point=reference_point,
                draw_points=getattr(v, "_draw_pts", []),
                snap_dist=snap_dist,
            )
        else:
            best = resolve_snap(
                cx,
                cy,
                wx,
                wy,
                allow_polyline=allow_polyline,
                allow_grid=allow_grid,
                allow_vertex=vertex_enabled,
                allow_midpoint=midpoint_enabled,
                allow_intersection=intersection_enabled,
                allow_edge=edge_enabled,
                grid_snap_enabled=v._grid_snap,
                grid_spacing=v._grid_spacing,
                polylines=polylines,
                hidden_polys=hidden_polys,
                scale=v._scale,
                w2c=v._w2c,
                c2w=v._c2w,
                poly_bounds=v._poly_bounds,
                is_poly_closed=v._is_poly_closed,
                segment_intersection_point=v._segment_intersection_point,
                mode=getattr(v, "_mode", None),
                reference_point=reference_point,
                draw_points=getattr(v, "_draw_pts", []),
                snap_dist=snap_dist,
            )
        best = self._pick_better(cx, cy, best, self._shape_candidate(cx, cy, exclude=exclude_polys))
        # Tangent/extension are inferred edge snaps and deliberately lower
        # priority than explicit vertices, intersections, and finite edges.
        if (
            allow_polyline
            and edge_enabled
            and getattr(v, "_snap_tangent_enabled", True)
            and reference_point is not None
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._tangent_candidate(cx, cy, reference_point, exclude=exclude_polys),
            )
        # Nearest-curve is evaluated after tangent so an exact tangency keeps
        # its more informative role when both candidates are identical.
        if allow_polyline and edge_enabled:
            best = self._pick_better(
                cx, cy, best, self._curve_candidate(cx, cy, exclude=exclude_polys)
            )
        if (
            allow_polyline
            and edge_enabled
            and reference_point is not None
            and (
                getattr(v, "_snap_parallel_enabled", getattr(v, "_snap_angle_enabled", True))
                or getattr(
                    v, "_snap_perpendicular_enabled", getattr(v, "_snap_angle_enabled", True)
                )
                or getattr(v, "_snap_equal_length_enabled", True)
            )
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._relationship_candidate(
                    cx, cy, wx, wy, reference_point, exclude=exclude_polys
                ),
            )
        if allow_inferred and allow_polyline and vertex_enabled and (
            getattr(v, "_snap_align_x_enabled", getattr(v, "_snap_axis_alignment_enabled", True))
            or getattr(v, "_snap_align_y_enabled", getattr(v, "_snap_axis_alignment_enabled", True))
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._axis_alignment_candidate(
                    cx, cy, wx, wy, reference=reference_point, exclude=exclude_polys
                ),
            )
        if (
            allow_inferred
            and allow_polyline
            and edge_enabled
            and getattr(v, "_snap_extension_enabled", True)
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._extension_candidate(
                    cx, cy, reference=reference_point, exclude=exclude_polys
                ),
            )
        best = self._pick_better(cx, cy, best, self._guide_candidate(cx, cy, wx, wy))
        if best is None or best[2] not in {
            "parallel",
            "perpendicular",
            "equal_length",
            "parallel_equal_length",
            "perpendicular_equal_length",
        }:
            self.clear_relationship_reference()
        return best

    def _relationship_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        reference: tuple[float, float],
        *,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Infer parallel, perpendicular, and equal-length line endpoints.

        Only nearby source segments participate, and the selected source is
        retained so feedback can identify the exact referenced geometry.
        """
        # Spline controls do not form straight segments. Treating their
        # control polygon as line geometry produced false relationship hints.
        if getattr(self.v, "_draw_primitive", None) in {"spline", "bezier"}:
            self.clear_relationship_reference()
            return None
        locked_reference = self.last_relationship_reference
        locked_type = self.last_relationship_type
        ax, ay = reference
        pointer_angle = math.atan2(wy - ay, wx - ax)
        pointer_length = math.hypot(wx - ax, wy - ay)
        if pointer_length <= 1e-12:
            return None
        hidden_ids = self.v._flagged("hidden")
        reference_c = self.v._w2c(ax, ay)
        candidates: list[tuple[SnapResult, RelationshipReference]] = []
        equal_candidates: list[tuple[SnapResult, RelationshipReference]] = []
        combined_candidates: list[tuple[SnapResult, RelationshipReference]] = []
        sources = [
            (entity.id, entity.points, False)
            for entity in self.v._entities
            if entity.id not in (exclude or ()) and entity.id not in hidden_ids
            and getattr(entity, "kind", "polyline") not in {"spline", "bezier"}
        ]
        draw_points = list(getattr(self.v, "_draw_pts", []))
        if len(draw_points) >= 2:
            # Committed segments of the in-progress polyline are always
            # relevant to the next segment, even though the unfinished shape
            # is not yet present in the document entity list.
            sources.append((_ACTIVE_DRAW_REFERENCE, draw_points, True))
        for entity_id, points, is_active_draw in sources:
            self._collect_relationship_candidates(
                entity_id,
                points,
                is_active_draw,
                cx=cx,
                cy=cy,
                reference_c=reference_c,
                reference=reference,
                pointer=(wx, wy),
                pointer_angle=pointer_angle,
                pointer_length=pointer_length,
                locked_reference=locked_reference,
                locked_type=locked_type,
                directional=candidates,
                equal=equal_candidates,
                combined=combined_candidates,
            )

        combined_radius = (
            self.COMBINED_RELATIONSHIP_RETAIN_PX
            if locked_type in {"parallel_equal_length", "perpendicular_equal_length"}
            else self.COMBINED_RELATIONSHIP_ACQUIRE_PX
        ) * self._snap_strength()
        combined = self._nearest_relationship_candidate(
            cx, cy, combined_radius, combined_candidates
        )
        if combined is not None:
            return combined
        equal_radius = (
            self.EQUAL_LENGTH_RETAIN_PX
            if locked_type == "equal_length"
            else self.EQUAL_LENGTH_ACQUIRE_PX
        ) * self._snap_strength()
        equal = self._nearest_relationship_candidate(cx, cy, equal_radius, equal_candidates)
        directional_radius = (
            self.DIRECTIONAL_RELATIONSHIP_RETAIN_PX
            if locked_type in {"parallel", "perpendicular"}
            else self.DIRECTIONAL_RELATIONSHIP_ACQUIRE_PX
        ) * self._snap_strength()
        result = (
            equal
            if equal is not None
            else self._nearest_relationship_candidate(cx, cy, directional_radius, candidates)
        )
        if result is not None:
            return result
        if locked_reference is not None:
            # The locked edge disappeared, became hidden, or ceased matching.
            # Release it and allow a nearby source to acquire in this query.
            self.clear_relationship_reference()
            return self._relationship_candidate(cx, cy, wx, wy, reference, exclude=exclude)
        self.clear_relationship_reference()
        return None

    def _collect_relationship_candidates(
        self,
        entity_id: str,
        points: list[tuple[float, float]],
        is_active_draw: bool,
        *,
        cx: float,
        cy: float,
        reference_c: tuple[float, float],
        reference: tuple[float, float],
        pointer: tuple[float, float],
        pointer_angle: float,
        pointer_length: float,
        locked_reference: RelationshipReference | None,
        locked_type: str | None,
        directional: list[tuple[SnapResult, RelationshipReference]],
        equal: list[tuple[SnapResult, RelationshipReference]],
        combined: list[tuple[SnapResult, RelationshipReference]],
    ) -> None:
        """Append every usable relationship candidate from one geometry source."""
        if len(points) < 2:
            return
        ax, ay = reference
        wx, wy = pointer
        parallel_enabled = getattr(
            self.v, "_snap_parallel_enabled", getattr(self.v, "_snap_angle_enabled", True)
        )
        perpendicular_enabled = getattr(
            self.v, "_snap_perpendicular_enabled", getattr(self.v, "_snap_angle_enabled", True)
        )
        equal_enabled = getattr(self.v, "_snap_equal_length_enabled", True)
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                continue
            start_c, end_c = self.v._w2c(*start), self.v._w2c(*end)
            if not self._relationship_source_is_eligible(
                cx,
                cy,
                reference_c,
                entity_id,
                segment_index,
                start_c,
                end_c,
                locked_reference,
                always_available=is_active_draw,
            ):
                continue
            source: RelationshipReference = (entity_id, segment_index, start, end)
            angle = math.atan2(dy, dx) % math.pi
            directions = tuple(
                direction
                for direction in (
                    (angle, "parallel") if parallel_enabled else None,
                    (angle + math.pi / 2, "perpendicular") if perpendicular_enabled else None,
                )
                if direction is not None
            )
            if directions:
                self._append_directional_candidates(
                    directional,
                    source,
                    directions,
                    ax,
                    ay,
                    pointer_angle,
                    pointer_length,
                    locked_type,
                )
            if equal_enabled and self._relationship_type_is_allowed("equal_length", locked_type):
                ux, uy = (wx - ax) / pointer_length, (wy - ay) / pointer_length
                equal.append(((ax + length * ux, ay + length * uy, "equal_length"), source))
            if directions and equal_enabled:
                self._append_combined_candidates(
                    combined,
                    source,
                    directions,
                    ax,
                    ay,
                    pointer_angle,
                    length,
                    locked_type,
                )

    def _append_directional_candidates(
        self,
        options: list[tuple[SnapResult, RelationshipReference]],
        source: RelationshipReference,
        directions: tuple[tuple[float, str], ...],
        ax: float,
        ay: float,
        pointer_angle: float,
        pointer_length: float,
        locked_type: str | None,
    ) -> None:
        for target_angle, role in directions:
            if not self._relationship_type_is_allowed(role, locked_type):
                continue
            if math.cos(pointer_angle - target_angle) < 0:
                target_angle += math.pi
            options.append(
                (
                    (
                        ax + pointer_length * math.cos(target_angle),
                        ay + pointer_length * math.sin(target_angle),
                        role,
                    ),
                    source,
                )
            )

    def _append_combined_candidates(
        self,
        options: list[tuple[SnapResult, RelationshipReference]],
        source: RelationshipReference,
        directions: tuple[tuple[float, str], ...],
        ax: float,
        ay: float,
        pointer_angle: float,
        length: float,
        locked_type: str | None,
    ) -> None:
        """Add exact angle-and-length candidates for a source segment."""
        for target_angle, role in directions:
            combined_role = f"{role}_equal_length"
            if not self._relationship_type_is_allowed(combined_role, locked_type):
                continue
            if math.cos(pointer_angle - target_angle) < 0:
                target_angle += math.pi
            options.append(
                (
                    (
                        ax + length * math.cos(target_angle),
                        ay + length * math.sin(target_angle),
                        combined_role,
                    ),
                    source,
                )
            )

    @staticmethod
    def _relationship_type_is_allowed(role: str, locked_type: str | None) -> bool:
        """Keep one acquired relationship stable without hiding a paired one."""
        if locked_type is None:
            return True
        if role == locked_type:
            return True
        if role == "equal_length":
            return locked_type in {"parallel_equal_length", "perpendicular_equal_length"}
        if role in {"parallel_equal_length", "perpendicular_equal_length"}:
            # A line already held parallel/perpendicular should promote to
            # the paired length relationship when its endpoint reaches the
            # source length. This keeps the intended direction while still
            # making the exact length easy to acquire.
            return locked_type in {role.removesuffix("_equal_length"), "equal_length"}
        return False

    def _nearest_relationship_candidate(
        self,
        cx: float,
        cy: float,
        radius: float,
        options: list[tuple[SnapResult, RelationshipReference]],
    ) -> SnapResult | None:
        best: SnapResult | None = None
        best_reference: RelationshipReference | None = None
        best_distance = radius
        for candidate, source in options:
            pcx, pcy = self.v._w2c(candidate[0], candidate[1])
            distance = math.hypot(cx - pcx, cy - pcy)
            if distance < best_distance:
                best, best_reference, best_distance = candidate, source, distance
        if best is not None:
            self.last_relationship_reference = best_reference
            self.last_relationship_type = best[2]
        return best

    def clear_relationship_reference(self) -> None:
        """Release relationship hysteresis after commit/cancel or invalidation."""
        self.last_relationship_reference = None
        self.last_relationship_type = None

    def _relationship_source_is_eligible(
        self,
        cx: float,
        cy: float,
        reference_c: tuple[float, float],
        entity_id: str,
        segment_index: int,
        start_c: tuple[float, float],
        end_c: tuple[float, float],
        locked: tuple[str, int, tuple[float, float], tuple[float, float]] | None,
        *,
        always_available: bool,
    ) -> bool:
        """Keep new relationship references local to the active stroke.

        The source can be near either the stroke anchor or its live endpoint:
        the former supports starting beside a reference and drawing away from
        it, while the latter supports approaching a reference to use it.
        """
        is_locked = locked is not None and locked[0] == entity_id and locked[1] == segment_index
        if locked is not None:
            return is_locked
        if always_available:
            return True
        return min(
            self._screen_distance_to_segment(cx, cy, start_c, end_c),
            self._screen_distance_to_segment(*reference_c, start_c, end_c),
        ) <= self.RELATIONSHIP_REFERENCE_PX

    @staticmethod
    def _screen_distance_to_segment(
        px: float,
        py: float,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        """Return the shortest screen-space distance to a finite segment."""
        ax, ay = start
        bx, by = end
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _axis_alignment_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        reference: tuple[float, float] | None = None,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Align the moving endpoint's X or Y coordinate to visible endpoints."""
        hidden_ids = self.v._flagged("hidden")
        best: SnapResult | None = None
        best_distance = self.INFERRED_LINE_SNAP_PX * self._snap_strength()
        align_x_enabled = getattr(
            self.v, "_snap_align_x_enabled", getattr(self.v, "_snap_axis_alignment_enabled", True)
        )
        align_y_enabled = getattr(
            self.v, "_snap_align_y_enabled", getattr(self.v, "_snap_axis_alignment_enabled", True)
        )
        reference_c = self.v._w2c(*reference) if reference is not None else None
        for entity in self.v._entities:
            if entity.id in (exclude or ()) or entity.id in hidden_ids or not entity.points:
                continue
            # Open-path endpoints are the primary intent. Closed paths have no
            # topological endpoint, so their vertices remain regular vertex
            # snaps rather than creating alignment guides everywhere.
            points = (
                (entity.points[0], entity.points[-1])
                if not self.v._is_poly_closed(entity.points)
                else ()
            )
            for px, py in points:
                endpoint_c = self.v._w2c(px, py)
                if not self._source_is_local(
                    cx, cy, endpoint_c, endpoint_c, reference_c
                ):
                    continue
                pcx, _ = self.v._w2c(px, wy)
                x_distance = abs(cx - pcx)
                if align_x_enabled and x_distance < best_distance:
                    best_distance = x_distance
                    best = (px, wy, "axis_x")
                _, pcy = self.v._w2c(wx, py)
                y_distance = abs(cy - pcy)
                if align_y_enabled and y_distance < best_distance:
                    best_distance = y_distance
                    best = (wx, py, "axis_y")
        return best

    def angle(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        if not getattr(self.v, "_snap_angle_enabled", True):
            return (wx, wy)
        return angle_snap(
            ax,
            ay,
            wx,
            wy,
            getattr(self.v, "_rotation_snap_increment", 15.0),
        )

    # ── Candidate sources ─────────────────────────────────────────────────

    def _shape_candidate(
        self, cx: float, cy: float, exclude: set[str] | None = None
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_vertex_enabled", True):
            return None  # shape center/start/end points are "vertex family"
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        excluded = exclude or ()
        hidden_ids = self.v._flagged("hidden")
        # Shape snapping works across visible layers — shapes on non-active
        # layers remain valid targets even when not selectable/editable.
        # ``exclude`` skips the entity being dragged itself — otherwise its
        # OWN (stale, pre-drag) cached shape stays a valid snap target and
        # the drag can stick to a "ghost" of where it started.
        for eid, shape in v._snap_shapes().items():
            if eid in excluded or eid in hidden_ids:
                continue
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = v._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= best_dist:
                    best_dist = dist
                    best = (sx, sy, snap_type)
        return best

    def _guide_candidate(self, cx: float, cy: float, wx: float, wy: float) -> SnapResult | None:
        """Snap to user guide lines (see the rulers/guides feature)."""
        v = self.v
        guides = getattr(v, "_guides", None)
        if not guides:
            return None
        best: SnapResult | None = None
        best_dist = self.GUIDE_SNAP_PX * self._snap_strength()
        for orient, coord in guides:
            if orient == "v":
                gx, _ = v._w2c(coord, wy)
                d = abs(cx - gx)
                if d < best_dist:
                    best_dist = d
                    best = (coord, wy, "guide")
            else:
                _, gy = v._w2c(wx, coord)
                d = abs(cy - gy)
                if d < best_dist:
                    best_dist = d
                    best = (wx, coord, "guide")
        return best

    @staticmethod
    def _angle_on_arc(angle: float, shape: ArcShape) -> bool:
        """Whether an angle lies on the arc's counter-clockwise sweep."""
        sweep = (shape.end_angle - shape.start_angle) % 360.0
        offset = (angle - shape.start_angle) % 360.0
        return offset <= sweep + 1e-9

    def _curve_candidate(
        self, cx: float, cy: float, *, exclude: set[str] | None = None
    ) -> SnapResult | None:
        """Nearest analytic point on circles, arcs, and rotated ellipses."""
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_distance = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        for eid, shape in self.v._snap_shapes().items():
            if eid in (exclude or ()) or eid in hidden_ids:
                continue
            point: tuple[float, float] | None = None
            if isinstance(shape, (CircleShape, ArcShape)):
                angle = math.degrees(math.atan2(wy - shape.center[1], wx - shape.center[0])) % 360.0
                if isinstance(shape, ArcShape) and not self._angle_on_arc(angle, shape):
                    continue
                radians = math.radians(angle)
                point = (
                    shape.center[0] + shape.radius * math.cos(radians),
                    shape.center[1] + shape.radius * math.sin(radians),
                )
            elif isinstance(shape, EllipseShape) and shape.rx > 0 and shape.ry > 0:
                rotation = math.radians(shape.rotation)
                cosine, sine = math.cos(rotation), math.sin(rotation)
                dx, dy = wx - shape.center[0], wy - shape.center[1]
                lx, ly = dx * cosine + dy * sine, -dx * sine + dy * cosine
                t = math.atan2(ly * shape.rx, lx * shape.ry)
                # Newton refinement of squared-distance derivative.
                for _ in range(8):
                    ct, st = math.cos(t), math.sin(t)
                    ex, ey = shape.rx * ct, shape.ry * st
                    first = (ex - lx) * (-shape.rx * st) + (ey - ly) * (shape.ry * ct)
                    second = (
                        shape.rx * shape.rx * st * st
                        + shape.ry * shape.ry * ct * ct
                        + (ex - lx) * (-shape.rx * ct)
                        + (ey - ly) * (-shape.ry * st)
                    )
                    if abs(second) < 1e-12:
                        break
                    t -= first / second
                ex, ey = shape.rx * math.cos(t), shape.ry * math.sin(t)
                point = (
                    shape.center[0] + ex * cosine - ey * sine,
                    shape.center[1] + ex * sine + ey * cosine,
                )
            if point is None:
                continue
            pcx, pcy = self.v._w2c(*point)
            distance = math.hypot(cx - pcx, cy - pcy)
            if distance < best_distance:
                best_distance = distance
                best = (*point, "edge")
        return best

    def _tangent_candidate(
        self,
        cx: float,
        cy: float,
        reference: tuple[float, float] | None,
        *,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Tangency points from the active draw/reference point to circles."""
        if reference is None or not getattr(self.v, "_snap_tangent_enabled", True):
            return None
        ax, ay = reference
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        for eid, shape in self.v._snap_shapes().items():
            if eid in (exclude or ()) or eid in hidden_ids or not isinstance(shape, CircleShape):
                continue
            dx, dy = ax - shape.center[0], ay - shape.center[1]
            distance_sq = dx * dx + dy * dy
            radius_sq = shape.radius * shape.radius
            if distance_sq <= radius_sq + 1e-12:
                continue
            base = radius_sq / distance_sq
            turn = shape.radius * math.sqrt(distance_sq - radius_sq) / distance_sq
            for sign in (-1.0, 1.0):
                tx = shape.center[0] + base * dx - sign * turn * dy
                ty = shape.center[1] + base * dy + sign * turn * dx
                tcx, tcy = self.v._w2c(tx, ty)
                distance = math.hypot(cx - tcx, cy - tcy)
                if distance < best_dist:
                    best_dist = distance
                    best = (tx, ty, "tangent")
        return best

    def _extension_candidate(
        self,
        cx: float,
        cy: float,
        *,
        reference: tuple[float, float] | None = None,
        exclude: set[str] | None = None,
    ) -> SnapResult | None:
        """Project onto the infinite extension of visible straight segments."""
        if not getattr(self.v, "_snap_extension_enabled", True):
            return None
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_dist = self.INFERRED_LINE_SNAP_PX * self._snap_strength()
        hidden_ids = self.v._flagged("hidden")
        reference_c = self.v._w2c(*reference) if reference is not None else None
        for entity in self.v._entities:
            if entity.id in (exclude or ()) or entity.id in hidden_ids:
                continue
            points = entity.points
            for start, end in zip(points, points[1:]):
                if not self._source_is_local(
                    cx,
                    cy,
                    self.v._w2c(*start),
                    self.v._w2c(*end),
                    reference_c,
                ):
                    continue
                dx, dy = end[0] - start[0], end[1] - start[1]
                length_sq = dx * dx + dy * dy
                if length_sq <= 1e-12:
                    continue
                t = ((wx - start[0]) * dx + (wy - start[1]) * dy) / length_sq
                if -1e-6 <= t <= 1.0 + 1e-6:
                    continue
                px, py = start[0] + t * dx, start[1] + t * dy
                pcx, pcy = self.v._w2c(px, py)
                distance = math.hypot(cx - pcx, cy - pcy)
                if distance < best_dist:
                    best_dist = distance
                    best = (px, py, "extension")
        return best

    def _source_is_local(
        self,
        cx: float,
        cy: float,
        start_c: tuple[float, float],
        end_c: tuple[float, float],
        reference_c: tuple[float, float] | None,
    ) -> bool:
        """Whether an inferred source belongs to the active drawing area."""
        distances = [self._screen_distance_to_segment(cx, cy, start_c, end_c)]
        if reference_c is not None:
            distances.append(self._screen_distance_to_segment(*reference_c, start_c, end_c))
        return min(distances) <= self.RELATIONSHIP_REFERENCE_PX

    def _pick_better(
        self,
        cx: float,
        cy: float,
        first: SnapResult | None,
        second: SnapResult | None,
    ) -> SnapResult | None:
        if first is None:
            return second
        if second is None:
            return first
        v = self.v
        fcx, fcy = v._w2c(first[0], first[1])
        scx, scy = v._w2c(second[0], second[1])
        fd = math.hypot(cx - fcx, cy - fcy)
        sd = math.hypot(cx - scx, cy - scy)
        first_priority = self._snap_priority(first[2])
        second_priority = self._snap_priority(second[2])
        first_explicit = self._is_explicit_finite_geometry(first[2])
        second_explicit = self._is_explicit_finite_geometry(second[2])
        if first_explicit != second_explicit:
            # Explicit finite geometry always beats inferred construction
            # when both candidates are inside their acquisition radii.
            return first if first_explicit else second
        if not first_explicit and first_priority != second_priority:
            return first if first_priority > second_priority else second
        # A point has a small magnetic core over an edge, but outside that
        # core competing explicit targets resolve by proximity. This avoids a
        # distant circle quadrant stealing an exact tangent/curve hit.
        magnetic_core = 6.0 * self._snap_strength()
        if self._is_magnetic_point(first[2]) and second_priority < 105 and fd <= magnetic_core:
            return first
        if self._is_magnetic_point(second[2]) and first_priority < 105 and sd <= magnetic_core:
            return second
        # Preserve source priority for visually coincident candidates. Tiny
        # floating-point differences must not relabel a tangent as generic
        # "On Edge" or make overlapping snap roles flicker frame-to-frame.
        if abs(fd - sd) <= 0.25:
            return first
        return second if sd < fd else first

    @staticmethod
    def _snap_priority(snap_type: str) -> int:
        """CAD-style hierarchy: exact geometry before inferred relationships."""
        if snap_type == "intersection":
            return 120
        if snap_type == "vertex" or snap_type.startswith(
            ("vertex_", "spline_control_", "arc_start", "arc_end")
        ):
            return 115
        if snap_type == "center" or snap_type.startswith(("circle_", "ellipse_", "quadrant_")):
            return 110
        if snap_type == "midpoint":
            return 105
        if snap_type == "edge":
            return 100
        if snap_type == "tangent":
            return 95
        if snap_type == "grid":
            return 80
        if snap_type == "guide":
            return 70
        if snap_type == "extension":
            return 60
        if snap_type in {
            "equal_length",
            "parallel",
            "perpendicular",
        }:
            # Constraint relationships should not be masked by a visible
            # grid. Explicit finite geometry still wins in _pick_better.
            return 90
        if snap_type in {"parallel_equal_length", "perpendicular_equal_length"}:
            return 92
        if snap_type in {"axis_x", "axis_y"}:
            return 75
        return 50

    @staticmethod
    def _is_explicit_finite_geometry(snap_type: str) -> bool:
        """Whether a candidate represents an existing finite geometry target."""
        return (
            snap_type in {"intersection", "center", "midpoint", "edge", "tangent"}
            or snap_type == "vertex"
            or snap_type.startswith(
                ("vertex_", "spline_control_", "arc_start", "arc_end", "circle_", "ellipse_", "quadrant_")
            )
        )

    @staticmethod
    def _is_magnetic_point(snap_type: str) -> bool:
        return snap_type in {"intersection", "vertex", "midpoint"} or snap_type.startswith(
            ("vertex_", "spline_control_", "arc_start", "arc_end")
        )


class ShapeSnapEngine:
    """Shape-aware snapping for precise alignment and positioning."""

    # Match the core CAD snap distance so analytic shapes and polyline
    # geometry feel equally reachable.
    SNAP_RADIUS = 14.0  # Screen pixels

    @staticmethod
    def get_snap_candidates(shape: Shape) -> list[tuple[float, float, str]]:
        """Get snap points for a shape.

        Returns list of (x, y, snap_type) where snap_type describes the point.
        """
        points: list[tuple[float, float, str]] = []

        if isinstance(shape, ArcShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Start point
            start_rad = shape.start_angle * math.pi / 180
            start_x = shape.center[0] + shape.radius * math.cos(start_rad)
            start_y = shape.center[1] + shape.radius * math.sin(start_rad)
            points.append((start_x, start_y, "arc_start"))
            # End point
            end_rad = shape.end_angle * math.pi / 180
            end_x = shape.center[0] + shape.radius * math.cos(end_rad)
            end_y = shape.center[1] + shape.radius * math.sin(end_rad)
            points.append((end_x, end_y, "arc_end"))
            for angle, label in (
                (0.0, "quadrant_east"),
                (90.0, "quadrant_north"),
                (180.0, "quadrant_west"),
                (270.0, "quadrant_south"),
            ):
                if SnapEngine._angle_on_arc(angle, shape):
                    radians = math.radians(angle)
                    points.append(
                        (
                            shape.center[0] + shape.radius * math.cos(radians),
                            shape.center[1] + shape.radius * math.sin(radians),
                            label,
                        )
                    )

        elif isinstance(shape, CircleShape):
            # Center (primary snap point)
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points
            points.append(
                (
                    shape.center[0],
                    shape.center[1] + shape.radius,
                    "circle_north",
                )
            )
            points.append(
                (
                    shape.center[0],
                    shape.center[1] - shape.radius,
                    "circle_south",
                )
            )
            points.append(
                (
                    shape.center[0] + shape.radius,
                    shape.center[1],
                    "circle_east",
                )
            )
            points.append(
                (
                    shape.center[0] - shape.radius,
                    shape.center[1],
                    "circle_west",
                )
            )

        elif isinstance(shape, EllipseShape):
            # Center
            points.append((shape.center[0], shape.center[1], "center"))
            # Cardinal points (approximate for rotation)
            cos_r = math.cos(shape.rotation * math.pi / 180)
            sin_r = math.sin(shape.rotation * math.pi / 180)
            # North
            points.append(
                (
                    shape.center[0] + shape.ry * sin_r,
                    shape.center[1] + shape.ry * cos_r,
                    "ellipse_north",
                )
            )
            # South
            points.append(
                (
                    shape.center[0] - shape.ry * sin_r,
                    shape.center[1] - shape.ry * cos_r,
                    "ellipse_south",
                )
            )
            # East — perpendicular to the north/south axis above, scaled by
            # the other radius, so all four cardinal points sit on the
            # rotated ellipse boundary (circle already has all 4; ellipse
            # was missing this pair).
            points.append(
                (
                    shape.center[0] + shape.rx * cos_r,
                    shape.center[1] - shape.rx * sin_r,
                    "ellipse_east",
                )
            )
            # West
            points.append(
                (
                    shape.center[0] - shape.rx * cos_r,
                    shape.center[1] + shape.rx * sin_r,
                    "ellipse_west",
                )
            )

        elif isinstance(shape, SplineShape):
            # Control points are primary snap targets for splines
            for i, (x, y) in enumerate(shape.control_points):
                points.append((x, y, f"spline_control_{i}"))

        elif isinstance(
            shape,
            (PolygonShape, RectangleShape, RoundedRectangleShape, SlotShape, StarShape),
        ):
            points.append((shape.center[0], shape.center[1], "center"))
            for i, (x, y) in enumerate(shape.points[:-1]):
                points.append((x, y, f"vertex_{i}"))

        # Fallback: tessellation points (lower priority)
        if not points:
            for i, (x, y) in enumerate(shape.points):
                points.append((x, y, f"tessellation_{i}"))

        return points
