"""Control-point curve shapes used by the CAD shape factory.

Spline and Bezier retain authored control points, handles, and tessellation
rules. Keeping them together separates curve-specific editing and export
behavior from the parametric primitive families in :mod:`.shapes`.
"""

from __future__ import annotations

from simple_stipple.engine.cad.shapes import Point, Shape, _anisotropic_scale_fn, _finite


class SplineShape(Shape):
    """A B-spline curve defined by control points."""

    def __init__(
        self,
        id: int,
        control_points: list[Point],
        degree: int = 3,
        closed: bool = False,
        segments: int = 24,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="spline", **kwargs)
        self._control_points = list(control_points)
        self.degree = min(degree, len(control_points) - 1)
        self.closed = closed
        self.segments = max(4, segments)

    @property
    def control_points(self) -> list[Point]:
        return self._control_points

    @control_points.setter
    def control_points(self, value: list[Point]):
        self._control_points = list(value)
        self.degree = min(self.degree, len(value) - 1)
        self.invalidate_cache()

    def _compute_tessellation(self) -> list[Point]:
        """Tessellate spline using B-spline interpolation."""
        if len(self._control_points) < 2:
            return []
        if len(self._control_points) == 2:
            return list(self._control_points)

        from simple_stipple.engine.cad.geometry import build_spline_poly

        try:
            return build_spline_poly(
                self._control_points,
                segments=self.segments,
                closed=self.closed,
            )
        except (ValueError, TypeError, IndexError, ZeroDivisionError):
            return list(self._control_points)

    def _map_points(self, fn) -> None:
        self.control_points = [fn(p) for p in self._control_points]

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        self._map_points(_anisotropic_scale_fn(center, factor_x, factor_y))
        return self

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "spline",
            {
                "control_points": [tuple(p) for p in self._control_points],
                "degree": self.degree,
                "closed": self.closed,
                "segments": self.segments,
            },
        )

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        cps = [
            (float(p[0]), float(p[1]))
            for p in self._control_points
            if len(p) >= 2 and _finite(p[0], p[1])
        ]
        if len(cps) < 2:
            return False
        try:
            degree = int(self.degree)
        except (TypeError, ValueError):
            degree = 3
        degree = max(1, min(degree, len(cps) - 1))
        msp.add_spline(cps, degree=degree, dxfattribs=dxfattribs)
        return True


class BezierShape(Shape):
    """Bezier path with independent incoming/outgoing anchor handles."""

    def __init__(
        self,
        id: int,
        control_points: list[Point],
        tangents: list[Point] | None = None,
        handles_in: list[Point] | None = None,
        handles_out: list[Point] | None = None,
        node_types: list[str] | None = None,
        closed: bool = False,
        segments: int = 16,
        **kwargs,
    ):
        super().__init__(id=id, shape_type="bezier", **kwargs)
        self._control_points = list(control_points)
        self.tangents = list(tangents or [])
        self.handles_out = list(handles_out if handles_out is not None else self.tangents)
        self.handles_in = list(
            handles_in if handles_in is not None else [(-x, -y) for x, y in self.tangents]
        )
        self.node_types = list(node_types or [])
        self.node_types.extend(["symmetric"] * (len(control_points) - len(self.node_types)))
        self.closed = closed
        self.segments = max(4, int(segments))

    @property
    def control_points(self) -> list[Point]:
        return self._control_points

    @control_points.setter
    def control_points(self, value: list[Point]) -> None:
        self._control_points = list(value)
        self.invalidate_cache()

    def _compute_tessellation(self) -> list[Point]:
        from simple_stipple.engine.cad.geometry import build_bezier_poly

        return build_bezier_poly(
            self._control_points,
            self.tangents,
            segments=self.segments,
            closed=self.closed,
            handles_in=self.handles_in,
            handles_out=self.handles_out,
        )

    def _map_points(self, fn) -> None:
        old_points = list(self._control_points)
        mapped_points = [fn(point) for point in old_points]

        def map_vectors(vectors: list[Point]) -> list[Point]:
            mapped: list[Point] = []
            for index, vector in enumerate(vectors):
                if index >= len(old_points):
                    break
                anchor = old_points[index]
                tip = fn((anchor[0] + vector[0], anchor[1] + vector[1]))
                mapped.append((tip[0] - mapped_points[index][0], tip[1] - mapped_points[index][1]))
            return mapped

        mapped_tangents: list[Point] = []
        for index, tangent in enumerate(self.tangents):
            if index >= len(old_points):
                break
            anchor = old_points[index]
            tip = fn((anchor[0] + tangent[0], anchor[1] + tangent[1]))
            mapped_tangents.append(
                (tip[0] - mapped_points[index][0], tip[1] - mapped_points[index][1])
            )
        self._control_points = mapped_points
        self.tangents = mapped_tangents
        self.handles_in = map_vectors(self.handles_in)
        self.handles_out = map_vectors(self.handles_out)
        self.invalidate_cache()

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        self._map_points(_anisotropic_scale_fn(center, factor_x, factor_y))
        return self

    def move_control_point(self, index: int, point: Point) -> bool:
        if not 0 <= index < len(self._control_points):
            return False
        points = list(self._control_points)
        points[index] = point
        self.control_points = points
        return True

    def to_meta_dict(self) -> tuple[str, dict | None]:
        return (
            "bezier",
            {
                "tangents": [tuple(tangent) for tangent in self.tangents],
                "handles_in": [tuple(handle) for handle in self.handles_in],
                "handles_out": [tuple(handle) for handle in self.handles_out],
                "node_types": list(self.node_types),
                "closed": self.closed,
                "segments": self.segments,
            },
        )


__all__ = ["BezierShape", "SplineShape"]
