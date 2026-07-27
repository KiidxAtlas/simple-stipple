"""Geometry operations extracted from document_service and view.

Wraps backend.cad.geometry, backend.cad.editor_geometry,
backend.editing.transform, backend.editing.offset,
backend.editing.split, backend.editing.boolean,
backend.editing.resample, backend.editing.merge_explode,
backend.cad.shapes.
"""

from __future__ import annotations

from collections.abc import Sequence

from simple_stipple.engine.cad.constraints import GeometricConstraint
from simple_stipple.engine.cad.editor_geometry import CanvasGeometry
from simple_stipple.engine.cad.geometry import (
    EPS,
    ArcSpec,
    PointTuple,
    angle,
    approx_equal,
    arc_from_center_start_end,
    arc_from_three_points,
    arc_spec_from_center_start_end,
    arc_spec_from_three_points,
    build_bezier_poly,
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rounded_rect_poly,
    build_spline_poly,
    build_star_poly,
    diameter,
    distance,
    minimum_clearance,
    points_close,
    shape_circle,
    shape_ellipse,
    shape_polygon,
    shape_rect,
    shape_slot,
)
from simple_stipple.engine.cad.preflight import GeometryPreflight, analyze_geometry
from simple_stipple.engine.cad.shapes import Shape
from simple_stipple.engine.editing.merge_explode import PathInput
from simple_stipple.engine.editing.split import SplitResult


class GeometryService:
    """Pure geometry operations with no document state dependence.

    All methods accept plain data and return plain data. No CanvasDocument,
    no EntityRecord, no command objects. This makes the service trivial to
    unit-test and easy to reason about.
    """

    # -- Constants (re-exported for convenience) --

    EPS = EPS
    SNAP_DIST = 0.5
    MIN_SCALE = 1e-6

    # -- Shape factories (raw geometry.py) --

    @staticmethod
    def shape_circle(radius: float, *, n: int = 64) -> list[PointTuple]:
        return shape_circle(radius, n)

    @staticmethod
    def shape_ellipse(rx: float, ry: float, *, n: int = 64) -> list[PointTuple]:
        return shape_ellipse(rx, ry, n)

    @staticmethod
    def shape_rect(width: float, height: float) -> list[PointTuple]:
        return shape_rect(width, height)

    @staticmethod
    def shape_slot(length: float, width: float, *, n_end: int = 24) -> list[PointTuple]:
        return shape_slot(length, width, n_end)

    @staticmethod
    def shape_polygon(sides: int, radius: float) -> list[PointTuple]:
        return shape_polygon(sides, radius)

    @staticmethod
    def shape_star(
        cx: float,
        cy: float,
        outer_radius: float,
        points: int = 5,
        inner_ratio: float = 0.45,
        rotation: float = -90.0,
    ) -> list[PointTuple]:
        return build_star_poly(cx, cy, outer_radius, points, inner_ratio, rotation)

    @staticmethod
    def shape_rounded_rect(
        cx: float,
        cy: float,
        width: float,
        height: float,
        radius: float,
    ) -> list[PointTuple]:
        return build_rounded_rect_poly(cx, cy, width, height, radius)

    @staticmethod
    def shape_polygon_poly(cx: float, cy: float, radius: float, sides: int = 6) -> list[PointTuple]:
        return build_polygon_poly(cx, cy, radius, sides)

    @staticmethod
    def shape_circle_poly(
        cx: float, cy: float, radius: float, *, segments: int = 64
    ) -> list[PointTuple]:
        return build_circle_poly(cx, cy, radius, segments)

    @staticmethod
    def shape_ellipse_poly(
        cx: float, cy: float, rx: float, ry: float, *, segments: int = 64
    ) -> list[PointTuple]:
        return build_ellipse_poly(cx, cy, rx, ry, segments)

    @staticmethod
    def shape_spline_poly(
        points: Sequence[PointTuple],
        *,
        segments: int = 24,
        closed: bool = False,
    ) -> list[PointTuple]:
        return build_spline_poly(list(points), segments, closed=closed)

    @staticmethod
    def build_bezier_poly(
        anchors: Sequence[PointTuple],
        tangents: Sequence[PointTuple],
        *,
        segments: int = 32,
    ) -> list[PointTuple]:
        return build_bezier_poly(list(anchors), list(tangents), segments)

    # -- Arc helpers --

    @staticmethod
    def arc_from_center_start_end(
        center: PointTuple,
        start: PointTuple,
        end: PointTuple,
        *,
        segments: int = 32,
    ) -> list[PointTuple]:
        return arc_from_center_start_end(center, start, end, segments)

    @staticmethod
    def arc_from_three_points(
        p1: PointTuple, p2: PointTuple, p3: PointTuple, *, segments: int = 32
    ) -> list[PointTuple]:
        return arc_from_three_points(p1, p2, p3, segments)

    @staticmethod
    def arc_spec_from_center_start_end(
        center: PointTuple,
        start: PointTuple,
        end: PointTuple,
    ) -> ArcSpec | None:
        return arc_spec_from_center_start_end(center, start, end)

    @staticmethod
    def arc_spec_from_three_points(
        p0: PointTuple, p1: PointTuple, p2: PointTuple
    ) -> ArcSpec | None:
        return arc_spec_from_three_points(p0, p1, p2)

    # -- Distance / proximity --

    @staticmethod
    def distance(p1: PointTuple, p2: PointTuple) -> float:
        return distance(p1, p2)

    @staticmethod
    def approx_equal(a: float, b: float, *, eps: float = EPS) -> bool:
        return approx_equal(a, b, eps=eps)

    @staticmethod
    def points_close(p1: PointTuple, p2: PointTuple) -> bool:
        return points_close(p1, p2)

    @staticmethod
    def angle(first: PointTuple, vertex: PointTuple, third: PointTuple) -> float:
        return angle(first, vertex, third)

    @staticmethod
    def diameter(radius: float) -> float:
        return diameter(radius)

    @staticmethod
    def minimum_clearance(paths: list[list[PointTuple]]) -> float | None:
        return minimum_clearance(paths)

    # -- Clipping (Shapely-based, returns BaseGeometry) --
    # Note: clip_line_to_outline and clip_polygon_to_outline work with
    # Shapely Polygon objects, not plain point lists. They are not included
    # here as pure wrappers; callers needing clipping should import directly
    # from backend.cad.geometry.

    # -- Editor geometry (entity-aware transforms) --

    @staticmethod
    def transform_entity_metadata(
        entity,
        *,
        transform: str,
        center: PointTuple = (0.0, 0.0),
        factor: float | None = None,
        angle_degrees: float = 0.0,
        axis: str | None = None,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> bool:
        from simple_stipple.engine.cad.editor_geometry import (
            transform_entity_metadata as _tem,
        )

        return _tem(
            entity,
            transform=transform,
            center=center,
            factor=factor,
            angle_degrees=angle_degrees,
            axis=axis,
            dx=dx,
            dy=dy,
        )

    @staticmethod
    def geometry_for_entity(entity) -> CanvasGeometry:
        from simple_stipple.engine.cad.editor_geometry import CanvasGeometry as _CG
        from simple_stipple.engine.cad.editor_geometry import (
            geometry_for_entity as _gfe,
        )

        result = _gfe(entity)
        if not isinstance(result, _CG):
            raise TypeError(f"geometry_for_entity returned {type(result)}")
        return result

    @staticmethod
    def shape_for_entity(entity) -> Shape:
        from simple_stipple.engine.cad.editor_geometry import (
            shape_for_entity as _sfe,
        )
        from simple_stipple.engine.cad.shapes import Shape as _Shape

        result = _sfe(entity)
        if not isinstance(result, _Shape):
            raise TypeError(f"shape_for_entity returned {type(result)}")
        return result

    @staticmethod
    def control_points(entity) -> list[PointTuple] | None:
        shape = GeometryService.shape_for_entity(entity)
        if shape is None:
            return None
        return shape.control_points

    @staticmethod
    def move_entity_control_point(
        entity,
        index: int,
        point: PointTuple,
        *,
        displayed_point_count: int | None = None,
    ) -> bool:
        from simple_stipple.engine.cad.editor_geometry import (
            move_entity_control_point as _mecp,
        )

        return _mecp(entity, index, point, displayed_point_count=displayed_point_count)

    @staticmethod
    def synchronize_entity_control_points(entity) -> None:
        from simple_stipple.engine.cad.editor_geometry import (
            synchronize_entity_control_points as _secp,
        )

        _secp(entity)

    @staticmethod
    def entity_shows_point_handles(entity) -> bool:
        from simple_stipple.engine.cad.editor_geometry import (
            entity_shows_point_handles as _esh,
        )

        return _esh(entity)

    @staticmethod
    def update_entity_parameter(
        entity,
        key: str,
        value: float,
    ) -> bool:
        from simple_stipple.engine.cad.editor_geometry import (
            update_entity_parameter as _uep,
        )

        return _uep(entity, key, value)

    # -- Editing operations --

    @staticmethod
    def offset_polyline(
        points: Sequence[PointTuple],
        amount: float,
    ) -> list[PointTuple] | None:
        from simple_stipple.engine.editing.offset import offset_polyline as _offset

        return _offset(list(points), amount)

    @staticmethod
    def is_closed(points: Sequence[PointTuple], *, tolerance: float = 0.01) -> bool:
        from simple_stipple.engine.editing.offset import is_closed as _is_closed

        return _is_closed(list(points), tolerance)

    @staticmethod
    def mirror_points(
        points: Sequence[PointTuple],
        origin: PointTuple,
        axis: str = "x",
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.transform import mirror as _mirror

        return _mirror(list(points), origin, axis)

    @staticmethod
    def rotate_points(
        points: Sequence[PointTuple],
        center: PointTuple,
        angle_deg: float,
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.transform import rotate as _rotate

        return _rotate(list(points), center, angle_deg)

    @staticmethod
    def scale_points(
        points: Sequence[PointTuple],
        center: PointTuple,
        factor: float,
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.transform import scale as _scale

        return _scale(list(points), center, factor)

    @staticmethod
    def translate_points(
        points: Sequence[PointTuple],
        dx: float,
        dy: float,
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.transform import translate as _translate

        return _translate(list(points), dx, dy)

    @staticmethod
    def translate_entities(
        entities: Sequence,
        dx: float,
        dy: float,
    ) -> list:
        from simple_stipple.engine.editing.transform import (
            translate_entities as _te,
        )

        return list(_te(entities, dx, dy))

    @staticmethod
    def split_paths(
        sources: Sequence[Sequence[PointTuple]],
        cutter: Sequence[PointTuple],
        entity_ids: Sequence[str] | None = None,
    ) -> SplitResult:
        from simple_stipple.engine.editing.split import (
            split_paths as _split,
        )

        ids = list(entity_ids) if entity_ids is not None else None
        return _split([list(s) for s in sources], list(cutter), ids)

    @staticmethod
    def boolean_polylines(
        polylines: Sequence[Sequence[PointTuple]],
        operation: str,
    ) -> list:
        from simple_stipple.engine.editing.boolean import (
            boolean_polylines as _bp,
        )

        return _bp([list(p) for p in polylines], operation)

    @staticmethod
    def resample_by_count(
        points: Sequence[PointTuple],
        count: int,
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.resample import (
            resample_by_count as _rbc,
        )

        return _rbc(list(points), count)

    @staticmethod
    def resample_by_spacing(
        points: Sequence[PointTuple],
        spacing: float,
    ) -> list[PointTuple]:
        from simple_stipple.engine.editing.resample import (
            resample_by_spacing as _rbs,
        )

        return _rbs(list(points), spacing)

    @staticmethod
    def merge_paths(
        paths: Sequence[Sequence[PointTuple]],
        *,
        tolerance: float = 0.01,
    ) -> list:
        from simple_stipple.engine.editing.merge_explode import (
            PathInput,
        )
        from simple_stipple.engine.editing.merge_explode import (
            merge_paths as _mp,
        )

        return _mp([PathInput(list(p), False) for p in paths], tolerance)

    @staticmethod
    def merge_paths_with_construction(
        path_inputs: Sequence[PathInput],
        *,
        tolerance: float = 0.01,
    ) -> list:
        from simple_stipple.engine.editing.merge_explode import (
            merge_paths as _mp,
        )

        return _mp(list(path_inputs), tolerance)

    @staticmethod
    def explode_path(points: Sequence[PointTuple]) -> list[list[PointTuple]]:
        from simple_stipple.engine.editing.merge_explode import (
            explode_path as _ep,
        )

        return _ep(list(points))

    # -- Shapes --

    @staticmethod
    def constraints_from_dicts(
        dicts: Sequence[dict],
    ) -> list[GeometricConstraint]:
        constraints = []
        for item in dicts:
            if (c := GeometricConstraint.from_dict(item)) is not None:
                constraints.append(c)
        return constraints

    @staticmethod
    def analyze_geometry(polys: list[list[PointTuple]]) -> GeometryPreflight:
        """Return structured preflight diagnostics for a geometry workflow."""
        return analyze_geometry(polys)


# Module-level aliases for test compatibility and convenience.
offset_polyline = GeometryService.offset_polyline
mirror_polyline = GeometryService.mirror_points
rotate_polyline = GeometryService.rotate_points
scale_polyline = GeometryService.scale_points
