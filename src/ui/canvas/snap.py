"""Unified snap engine for the canvas.

One entry point (``query``) merges every snap source — polyline vertices,
midpoints, edges, intersections (via the pure candidate functions in
src/backend/cad/snapping.py), parametric-shape points (circle centers, arc
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

from src.backend.cad.shapes import (
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
from src.backend.cad.snapping import angle_snap, resolve_drag_snap, resolve_snap

if TYPE_CHECKING:
    from src.backend.cad.shapes import Shape
    from src.ui.canvas.view import CanvasView

SnapResult = tuple[float, float, str]


class SnapEngine:
    """Snap resolution bound to one canvas view."""

    GUIDE_SNAP_PX = 8.0

    def __init__(self, view: CanvasView) -> None:
        self.v = view

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
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        exclude_polys: set[int] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_master_enabled", True):
            return None
        polylines = [e.points for e in v._entities]
        # Locked and non-active-layer entities remain useful references, but
        # explicitly hidden geometry must not create invisible snap targets.
        hidden_polys: set[int] = set(v._flagged("hidden"))
        vertex_enabled = allow_vertex and getattr(v, "_snap_vertex_enabled", True)
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
                mode=v._mode,
                reference_point=reference_point,
                draw_points=v._draw_pts,
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
                mode=v._mode,
                reference_point=reference_point,
                draw_points=v._draw_pts,
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
                getattr(v, "_snap_angle_enabled", True)
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
        if allow_polyline and vertex_enabled and getattr(v, "_snap_axis_alignment_enabled", True):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._axis_alignment_candidate(cx, cy, wx, wy, exclude=exclude_polys),
            )
        if (
            allow_polyline
            and edge_enabled
            and getattr(v, "_snap_extension_enabled", True)
        ):
            best = self._pick_better(
                cx,
                cy,
                best,
                self._extension_candidate(cx, cy, exclude=exclude_polys),
            )
        best = self._pick_better(cx, cy, best, self._guide_candidate(cx, cy, wx, wy))
        return best

    def _relationship_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        reference: tuple[float, float],
        *,
        exclude: set[int] | None = None,
    ) -> SnapResult | None:
        """Infer parallel, perpendicular, and equal-length line endpoints.

        Candidates must be genuinely close to the pointer in screen space.
        This gives CAD-style intent hints without forcing the cursor onto a
        remote construction line.
        """
        ax, ay = reference
        radius = ShapeSnapEngine.SNAP_RADIUS
        pointer_angle = math.atan2(wy - ay, wx - ax)
        pointer_length = math.hypot(wx - ax, wy - ay)
        if pointer_length <= 1e-12:
            return None
        hidden = self.v._flagged("hidden")
        relationship_candidates: list[SnapResult] = []
        lengths: set[float] = set()
        angles: set[float] = set()
        for index, entity in enumerate(self.v._entities):
            if index in (exclude or ()) or index in hidden:
                continue
            for start, end in zip(entity.points, entity.points[1:]):
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                if length <= 1e-9:
                    continue
                lengths.add(round(length, 9))
                angles.add(round(math.atan2(dy, dx) % math.pi, 9))
        if getattr(self.v, "_snap_angle_enabled", True):
            for angle in angles:
                for target_angle, role in (
                    (angle, "parallel"),
                    (angle + math.pi / 2, "perpendicular"),
                ):
                    # Use the sign closest to the pointer direction.
                    if math.cos(pointer_angle - target_angle) < 0:
                        target_angle += math.pi
                    relationship_candidates.append(
                        (
                            ax + pointer_length * math.cos(target_angle),
                            ay + pointer_length * math.sin(target_angle),
                            role,
                        )
                    )
        ux, uy = (wx - ax) / pointer_length, (wy - ay) / pointer_length
        equal_candidates = (
            [(ax + length * ux, ay + length * uy, "equal_length") for length in lengths]
            if getattr(self.v, "_snap_equal_length_enabled", True)
            else []
        )

        def nearest(candidates: list[SnapResult]) -> SnapResult | None:
            best: SnapResult | None = None
            best_distance = radius
            for candidate in candidates:
                pcx, pcy = self.v._w2c(candidate[0], candidate[1])
                distance = math.hypot(cx - pcx, cy - pcy)
                if distance < best_distance:
                    best, best_distance = candidate, distance
            return best

        # When the cursor is within acquisition tolerance of an existing
        # segment length, length is the more specific constraint. A parallel
        # candidate lies exactly under the raw pointer and otherwise always
        # masks the tiny radial correction needed to make lengths equal.
        equal = nearest(equal_candidates)
        if equal is not None:
            return equal
        return nearest(relationship_candidates)

    def _axis_alignment_candidate(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        exclude: set[int] | None = None,
    ) -> SnapResult | None:
        """Align the moving endpoint's X or Y coordinate to visible endpoints."""
        hidden = self.v._flagged("hidden")
        best: SnapResult | None = None
        best_distance = ShapeSnapEngine.SNAP_RADIUS
        for index, entity in enumerate(self.v._entities):
            if index in (exclude or ()) or index in hidden or not entity.points:
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
                pcx, _ = self.v._w2c(px, wy)
                x_distance = abs(cx - pcx)
                if x_distance < best_distance:
                    best_distance = x_distance
                    best = (px, wy, "axis_x")
                _, pcy = self.v._w2c(wx, py)
                y_distance = abs(cy - pcy)
                if y_distance < best_distance:
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
        self, cx: float, cy: float, exclude: set[int] | None = None
    ) -> SnapResult | None:
        v = self.v
        if not getattr(v, "_snap_vertex_enabled", True):
            return None  # shape center/start/end points are "vertex family"
        best: SnapResult | None = None
        best_dist = float("inf")
        excluded = exclude or ()
        hidden = self.v._flagged("hidden")
        # Shape snapping works across visible layers — shapes on non-active
        # layers remain valid targets even when not selectable/editable.
        # ``exclude`` skips the entity being dragged itself — otherwise its
        # OWN (stale, pre-drag) cached shape stays a valid snap target and
        # the drag can stick to a "ghost" of where it started.
        for idx, shape in enumerate(v._snap_shapes()):
            if idx in excluded or idx in hidden:
                continue
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = v._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= ShapeSnapEngine.SNAP_RADIUS and dist < best_dist:
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
        best_dist = self.GUIDE_SNAP_PX
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
        self, cx: float, cy: float, *, exclude: set[int] | None = None
    ) -> SnapResult | None:
        """Nearest analytic point on circles, arcs, and rotated ellipses."""
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_distance = ShapeSnapEngine.SNAP_RADIUS
        hidden = self.v._flagged("hidden")
        for index, shape in enumerate(self.v._snap_shapes()):
            if index in (exclude or ()) or index in hidden:
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
        exclude: set[int] | None = None,
    ) -> SnapResult | None:
        """Tangency points from the active draw/reference point to circles."""
        if reference is None or not getattr(self.v, "_snap_tangent_enabled", True):
            return None
        ax, ay = reference
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS
        hidden = self.v._flagged("hidden")
        for index, shape in enumerate(self.v._snap_shapes()):
            if index in (exclude or ()) or index in hidden or not isinstance(shape, CircleShape):
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
        exclude: set[int] | None = None,
    ) -> SnapResult | None:
        """Project onto the infinite extension of visible straight segments."""
        if not getattr(self.v, "_snap_extension_enabled", True):
            return None
        wx, wy = self.v._c2w(cx, cy)
        best: SnapResult | None = None
        best_dist = ShapeSnapEngine.SNAP_RADIUS
        hidden = self.v._flagged("hidden")
        for index, entity in enumerate(self.v._entities):
            if index in (exclude or ()) or index in hidden:
                continue
            points = entity.points
            for start, end in zip(points, points[1:]):
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
        first_explicit = first_priority >= 90
        second_explicit = second_priority >= 90
        if first_explicit != second_explicit:
            # Explicit finite geometry always beats inferred construction
            # when both candidates are inside their acquisition radii.
            return first if first_explicit else second
        if not first_explicit and first_priority != second_priority:
            return first if first_priority > second_priority else second
        # A point has a small magnetic core over an edge, but outside that
        # core competing explicit targets resolve by proximity. This avoids a
        # distant circle quadrant stealing an exact tangent/curve hit.
        if self._is_magnetic_point(first[2]) and second_priority < 105 and fd <= 6.0:
            return first
        if self._is_magnetic_point(second[2]) and first_priority < 105 and sd <= 6.0:
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
        if snap_type == "center" or snap_type.startswith(
            ("circle_", "ellipse_", "quadrant_")
        ):
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
            "axis_x",
            "axis_y",
            "parallel",
            "perpendicular",
        }:
            return 40
        return 50

    @staticmethod
    def _is_magnetic_point(snap_type: str) -> bool:
        return (
            snap_type in {"intersection", "vertex", "midpoint"}
            or snap_type.startswith(("vertex_", "spline_control_", "arc_start", "arc_end"))
        )


class ShapeSnapEngine:
    """Shape-aware snapping for precise alignment and positioning."""

    SNAP_RADIUS = 10.0  # Screen pixels

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
