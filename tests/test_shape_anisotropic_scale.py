"""Non-uniform (anisotropic) resize used to turn curves into straight lines.

Resizing a bezier/spline entity by dragging a single corner or edge handle
almost always produces unequal x/y scale factors. Shape.scale() only ever
took one factor, so gizmo._apply_handle_scale's non-uniform branch fell back
to entity.kind = "polyline" with meta = None — and because a curve entity's
`points` are its sparse control points, not a tessellation, redrawing that
"polyline" connected the few control points with straight segments instead
of a curve. Shape.scale_xy (and the transform_meta/transform_entity_metadata
plumbing to it) lets curve-like shapes — line, polyline, spline, bezier —
stay parametric under a non-uniform scale, since their geometry is a linear
function of their control points. A circular arc can't stay a circular arc
under a non-uniform scale, but it isn't just dropped either: scale_xy hands
back a fresh EllipticalArcShape, the shape family the scale actually traces.
Only circle/ellipse/rectangle/... — kinds whose non-uniform image genuinely
has no representation this schema supports — still decline outright.
"""

from __future__ import annotations

import math

from simple_stipple.core.cad.primitives import BezierShape, SplineShape
from simple_stipple.core.cad.shape_factory import ShapeFactory, transform_meta
from simple_stipple.core.cad.shapes import EllipticalArcShape


def test_factory_preserves_curve_shape_types_after_curve_family_extraction() -> None:
    spline = ShapeFactory.spline(control_points=[(0.0, 0.0), (2.0, 1.0)])
    bezier = ShapeFactory.bezier(control_points=[(0.0, 0.0), (2.0, 1.0)])

    assert isinstance(spline, SplineShape)
    assert isinstance(bezier, BezierShape)
    assert (
        ShapeFactory.from_dict(
            {"type": "spline", "control_points": [(0.0, 0.0), (2.0, 1.0)]}
        ).to_meta_dict()[0]
        == "spline"
    )


def test_bezier_scale_xy_scales_control_points_and_tangents_independently() -> None:
    shape = ShapeFactory.bezier(
        control_points=[(0.0, 0.0), (10.0, 0.0)],
        tangents=[(2.0, 3.0), (-2.0, -3.0)],
    )
    result = shape.scale_xy((0.0, 0.0), 2.0, 5.0)
    assert result is shape
    assert shape.control_points == [(0.0, 0.0), (20.0, 0.0)]
    assert shape.tangents[0] == (4.0, 15.0)


def test_spline_scale_xy_scales_control_points_independently() -> None:
    shape = ShapeFactory.spline(control_points=[(1.0, 1.0), (2.0, 2.0), (3.0, 1.0)])
    result = shape.scale_xy((0.0, 0.0), 3.0, 10.0)
    assert result is shape
    assert shape.control_points == [(3.0, 10.0), (6.0, 20.0), (9.0, 10.0)]


def test_line_and_polyline_scale_xy_supported() -> None:
    line = ShapeFactory.line(start=(0.0, 0.0), end=(4.0, 4.0))
    assert line.scale_xy((0.0, 0.0), 2.0, 0.5) is line
    assert line.end == (8.0, 2.0)

    poly = ShapeFactory.polyline(points=[(1.0, 1.0), (2.0, 2.0)])
    assert poly.scale_xy((0.0, 0.0), 2.0, 0.5) is poly
    assert poly.control_points == [(2.0, 0.5), (4.0, 1.0)]


def test_circle_declines_non_uniform_scale() -> None:
    circle = ShapeFactory.circle(center=(0.0, 0.0), radius=5.0)
    assert circle.scale_xy((0.0, 0.0), 2.0, 3.0) is None


def test_arc_scale_xy_returns_an_elliptical_arc() -> None:
    arc = ShapeFactory.arc(center=(2.0, 3.0), radius=5.0, start_angle=20.0, end_angle=200.0)
    result = arc.scale_xy((0.0, 0.0), 2.0, 3.0)
    assert isinstance(result, EllipticalArcShape)
    assert result.rx == 10.0
    assert result.ry == 15.0
    assert result.rotation == 0.0
    # The arc's own start/end angle transfers unchanged: a world-axis scale
    # applies the same way regardless of where on the circle they sit.
    assert result.start_angle == 20.0
    assert result.end_angle == 200.0
    assert result.center == (4.0, 9.0)


def test_arc_scale_xy_declines_for_non_positive_factors() -> None:
    arc = ShapeFactory.arc(center=(0.0, 0.0), radius=5.0, start_angle=0.0, end_angle=90.0)
    assert arc.scale_xy((0.0, 0.0), -2.0, 3.0) is None
    assert arc.scale_xy((0.0, 0.0), 2.0, 0.0) is None


def test_elliptical_arc_start_and_end_points_match_arc_before_the_scale() -> None:
    """The scaled ellipse must pass through the anisotropically-scaled
    images of the original arc's start/end points — not just carry the
    right rx/ry numbers."""
    center, radius, start_deg, end_deg = (1.0, -2.0), 4.0, 35.0, 260.0
    arc = ShapeFactory.arc(center=center, radius=radius, start_angle=start_deg, end_angle=end_deg)
    fx, fy = 1.5, 0.4
    pivot = (10.0, -5.0)

    def scale_pt(p: tuple[float, float]) -> tuple[float, float]:
        return (pivot[0] + (p[0] - pivot[0]) * fx, pivot[1] + (p[1] - pivot[1]) * fy)

    original_start = (
        center[0] + radius * math.cos(math.radians(start_deg)),
        center[1] + radius * math.sin(math.radians(start_deg)),
    )
    original_end = (
        center[0] + radius * math.cos(math.radians(end_deg)),
        center[1] + radius * math.sin(math.radians(end_deg)),
    )
    expected_start = scale_pt(original_start)
    expected_end = scale_pt(original_end)

    result = arc.scale_xy(pivot, fx, fy)
    assert isinstance(result, EllipticalArcShape)
    got_start, got_end = result.control_points[1], result.control_points[2]
    assert math.isclose(got_start[0], expected_start[0], abs_tol=1e-9)
    assert math.isclose(got_start[1], expected_start[1], abs_tol=1e-9)
    assert math.isclose(got_end[0], expected_end[0], abs_tol=1e-9)
    assert math.isclose(got_end[1], expected_end[1], abs_tol=1e-9)


def test_elliptical_arc_exports_to_dxf_with_ratio_within_spec() -> None:
    """ry > rx must still export — ezdxf's ELLIPSE requires ratio <= 1.0,
    so this only passes if the shape swaps which axis is major."""
    import ezdxf

    shape = EllipticalArcShape(
        id=1, center=(0.0, 0.0), rx=3.0, ry=7.0, start_angle=20.0, end_angle=200.0
    )
    doc = ezdxf.new()
    msp = doc.modelspace()
    assert shape.to_dxf(msp) is True
    entity = msp[0]
    assert entity.dxftype() == "ELLIPSE"
    assert entity.dxf.ratio <= 1.0


def test_transform_meta_keeps_bezier_kind_under_non_uniform_scale() -> None:
    meta = {
        "tangents": [(1.0, 0.0)],
        "handles_in": [(-1.0, 0.0)],
        "handles_out": [(1.0, 0.0)],
        "node_types": ["symmetric"],
        "closed": False,
        "segments": 16,
    }
    result = transform_meta(
        "bezier",
        meta,
        transform="scale",
        center=(0.0, 0.0),
        factor=2.0,
        factor_y=5.0,
        points=[(0.0, 0.0), (10.0, 0.0)],
    )
    assert result is not None
    new_kind, new_meta = result
    assert new_kind == "bezier"
    assert new_meta["tangents"][0] == (2.0, 0.0)


def test_transform_meta_without_points_would_silently_drop_bezier_tangents() -> None:
    """Regression guard: bezier's control points live on the entity, not in
    meta. Reconstructing from meta alone (the old shape_from_meta path)
    yields a shape with zero control points, and every tangent/handle
    silently maps to an empty list instead of raising — the caller must
    always pass ``points`` for bezier/spline, or curves flatten on resize."""
    meta = {
        "tangents": [(1.0, 0.0)],
        "handles_in": [(-1.0, 0.0)],
        "handles_out": [(1.0, 0.0)],
        "node_types": ["symmetric"],
        "closed": False,
        "segments": 16,
    }
    result = transform_meta("bezier", meta, transform="scale", center=(0.0, 0.0), factor=2.0)
    assert result is not None
    _, new_meta = result
    assert new_meta["tangents"] == []


def test_transform_meta_converts_arc_to_elliptical_arc_under_non_uniform_scale() -> None:
    meta = {"center": (0.0, 0.0), "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0}
    result = transform_meta(
        "arc", meta, transform="scale", center=(0.0, 0.0), factor=2.0, factor_y=3.0
    )
    assert result is not None
    new_kind, new_meta = result
    assert new_kind == "elliptical_arc"
    assert new_meta["rx"] == 10.0
    assert new_meta["ry"] == 15.0
    # Stale arc-only fields don't leak into the new kind's metadata.
    assert "radius" not in new_meta


def test_transform_meta_returns_none_for_circle_under_non_uniform_scale() -> None:
    meta = {"center": (0.0, 0.0), "radius": 5.0}
    result = transform_meta(
        "circle", meta, transform="scale", center=(0.0, 0.0), factor=2.0, factor_y=3.0
    )
    assert result is None


def test_transform_meta_uniform_scale_unaffected_by_factor_y_addition() -> None:
    meta = {"center": (1.0, 1.0), "radius": 5.0}
    result = transform_meta(
        "circle", meta, transform="scale", center=(0.0, 0.0), factor=2.0, factor_y=2.0
    )
    assert result is not None
    new_kind, new_meta = result
    assert new_kind == "circle"
    assert new_meta["radius"] == 10.0
