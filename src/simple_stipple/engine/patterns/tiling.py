"""Tiling pattern generators (honeycomb, brick, basketweave, etc.)."""

from __future__ import annotations

import math

import numpy as np
import shapely  # type: ignore[import-untyped]
from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from simple_stipple.engine.geometry.jit import tessellate_circles
from simple_stipple.engine.patterns._shared import (
    _clip_to_outline,
    _extract_all_rings,
    _extract_polys,
    _hex_verts,
    lattice_cells,
)
from simple_stipple.engine.patterns.cancellation import cancellation_checkpoint


def gen_honeycomb(
    outline_poly,
    r: float,
    gap: float,
    *,
    repeat_mode: str = "Half drop",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    if r <= 0:
        return []
    col_step = 2.0 * (math.sqrt(3) / 2.0 * r) + gap
    row_step = 1.5 * r + gap * math.sqrt(3) / 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    for cx, cy, _row, _col in lattice_cells(
        outline_poly,
        col_step,
        row_step,
        pad=r * 2.0,
        repeat_mode=repeat_mode,
        origin_x=origin_x,
        origin_y=origin_y,
    ):
        _clip_to_outline(Polygon(_hex_verts(cx, cy, r)), outline_poly, prep, result)
    return result


def gen_brick(
    outline_poly,
    brick_w: float,
    brick_h: float,
    gap: float,
    *,
    repeat_mode: str = "Half drop",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Staggered rectangular bricks clipped to the outline."""
    if brick_w <= 0 or brick_h <= 0:
        return []
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    hw, hh = brick_w / 2.0, brick_h / 2.0
    for x, y, _row, _col in lattice_cells(
        outline_poly,
        brick_w + gap,
        brick_h + gap,
        pad=max(brick_w, brick_h) * 2.0,
        repeat_mode=repeat_mode,
        origin_x=origin_x,
        origin_y=origin_y,
    ):
        verts = [(x - hw, y - hh), (x + hw, y - hh), (x + hw, y + hh), (x - hw, y + hh)]
        _clip_to_outline(Polygon(verts), outline_poly, prep, result)
    return result


def gen_basketweave(
    outline_poly,
    strip_w: float,
    strip_l: float,
    gap: float = 0.1,
    *,
    repeat_mode: str = "Straight",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Classic basketweave strips in a repeating 2×2 module clipped to the outline."""
    if strip_w <= 0 or strip_l <= 0 or gap < 0:
        return []
    module_step = strip_l + strip_w + gap * 2.0
    if module_step <= 0:
        return []
    inset = gap / 2.0
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    for x, y, _row, _col in lattice_cells(
        outline_poly,
        module_step,
        module_step,
        pad=module_step + max(strip_l, strip_w),
        repeat_mode=repeat_mode,
        origin_x=origin_x,
        origin_y=origin_y,
    ):
        if True:
            left = x + inset
            right = x + module_step - inset
            bottom = y + inset
            top = y + module_step - inset
            rects = [
                [
                    (left, top - strip_w),
                    (left + strip_l, top - strip_w),
                    (left + strip_l, top),
                    (left, top),
                ],
                [
                    (right - strip_w, top - strip_l),
                    (right, top - strip_l),
                    (right, top),
                    (right - strip_w, top),
                ],
                [
                    (left, bottom),
                    (left + strip_w, bottom),
                    (left + strip_w, bottom + strip_l),
                    (left, bottom + strip_l),
                ],
                [
                    (right - strip_l, bottom),
                    (right, bottom),
                    (right, bottom + strip_w),
                    (right - strip_l, bottom + strip_w),
                ],
            ]
            for verts in rects:
                rect_poly = Polygon(verts)
                if not prep.intersects(rect_poly):
                    continue
                if prep.contains(rect_poly):
                    result.append(verts + [verts[0]])
                    continue
                _extract_polys(outline_poly.intersection(rect_poly), result)
    return result


def gen_mesh(
    outline_poly,
    r: float,
    spacing: float,
    *,
    quality: str = "high",
    repeat_mode: str = "Straight",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Regular orthogonal grid of small circles clipped to the outline."""
    if r <= 0 or spacing <= 0:
        return []
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    centers = [
        (x, y)
        for x, y, _row, _col in lattice_cells(
            outline_poly,
            spacing,
            spacing,
            pad=r * 2.0,
            repeat_mode=repeat_mode,
            origin_x=origin_x,
            origin_y=origin_y,
        )
    ]
    if not centers:
        return []

    segments = 4 * {"fast": 4, "balanced": 12}.get(quality, 24)
    center_array = np.asarray(centers)
    circles = tessellate_circles(center_array, r, segments)
    center_geometries = shapely.points(center_array)
    fully_inside = np.asarray(shapely.contains(outline_poly, center_geometries)) & (
        np.asarray(shapely.distance(outline_poly.boundary, center_geometries)) >= r
    )
    for points, contained in zip(circles, fully_inside):
        cancellation_checkpoint()
        if contained:
            result.append([(float(x), float(y)) for x, y in points])
        else:
            circle = Polygon(points)
            _clip_to_outline(circle, outline_poly, prep, result)
    return result


def gen_truchet(
    outline_poly,
    tile: float,
    gap: float = 0.3,
    *,
    seed: int | None = None,
    arcs: int = 2,
    repeat_mode: str = "Straight",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Truchet tiles: the region partitioned by quarter-arc pairs.

    The cells are found by *polygonizing* the arcs against the region boundary
    rather than by closing each quarter-disc through its tile corner. Closing
    a sector that way draws two straight radii, and those only disappear when
    a neighbouring tile happens to contribute a matching sector — with random
    orientation it usually does not, which is what left right-angle steps in
    the pattern. Polygonizing means every internal edge is an arc by
    construction, and the cells are still closed, so fill works on them.
    """
    if tile <= 0:
        return []
    # The lattice step is the tile size exactly. Widening it by the gap would
    # pull the arcs apart so they no longer meet at tile-edge midpoints, and
    # then they enclose nothing for polygonize to find — the pattern collapsed
    # to a single cell. ``gap`` only insets the finished cells.
    step = tile
    half = tile / 2.0
    segments = max(4, int(arcs) * 8)
    rng = np.random.default_rng(seed if seed is not None else 0)

    def quarter(cx: float, cy: float, corner: int) -> list[tuple[float, float]]:
        ox, oy = (
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
        )[corner]
        start_angle = math.radians(90.0 * corner)
        return [
            (
                ox + half * math.cos(start_angle + (math.pi / 2.0) * t / segments),
                oy + half * math.sin(start_angle + (math.pi / 2.0) * t / segments),
            )
            for t in range(segments + 1)
        ]

    curves: list[LineString] = []
    for cx, cy, _row, _col in lattice_cells(
        outline_poly,
        step,
        step,
        pad=tile,
        repeat_mode=repeat_mode,
        origin_x=origin_x,
        origin_y=origin_y,
    ):
        corners = (0, 2) if bool(rng.integers(0, 2)) else (1, 3)
        for corner in corners:
            curves.append(LineString(quarter(cx, cy, corner)))
    if not curves:
        return []

    # The region boundary closes the outermost cells; without it the arcs near
    # the edge bound nothing and that area is lost.
    boundary = outline_poly.boundary
    noded = unary_union([*curves, boundary])
    result: list[list[tuple[float, float]]] = []
    prep = prepared.prep(outline_poly)
    # ponytail: every face is emitted, so the cells tile the region and
    # "fill pattern cells" covers all of it. The classic look wants alternate
    # faces only, which needs the arc arrangement properly two-coloured —
    # labelling faces by the quarter-disc union is not sound (polygonize
    # returns far fewer faces than the tiling implies). Unresolved.
    inset = max(0.0, gap) / 2.0
    for cell in polygonize(noded):
        if cell.is_empty or cell.area <= 1e-9:
            continue
        if not prep.intersects(cell.representative_point()):
            continue
        if inset > 0:
            # The cells partition the region, so without a gap there is no
            # space around them for the outline fill to hatch.
            cell = cell.buffer(-inset)
            if cell.is_empty:
                continue
        _clip_to_outline(cell, outline_poly, prep, result)
    return result


def gen_seigaiha(
    outline_poly,
    r: float,
    rings: int = 3,
    ring_gap: float = 0.6,
    gap: float = 0.3,
    *,
    repeat_mode: str = "Half drop",
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Seigaiha: layered fan-shaped scales forming a wave.

    Each scale is a full disc, but only the part not already covered by the
    scales in front of it is emitted — that crescent is what makes the pattern
    read as overlapping waves instead of a mesh of whole circles. The rings
    inside a scale are clipped the same way, so no diameter chords appear.
    """
    if r <= 0 or rings < 1:
        return []
    step = max(ring_gap, 0.01)
    radii = [radius for radius in (r - index * step for index in range(int(rings))) if radius > 0]
    if not radii:
        return []
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []

    centres = [
        (cx, cy)
        for cx, cy, _row, _col in lattice_cells(
            outline_poly,
            r,
            r / 2.0,
            pad=r * 2.0,
            repeat_mode=repeat_mode,
            origin_x=origin_x,
            origin_y=origin_y,
        )
    ]
    if not centres:
        return []

    def disc(cx: float, cy: float, radius: float):
        return Point(cx, cy).buffer(radius, quad_segs=16)

    # Scales lower on the sheet sit in front, so they occlude the ones behind.
    order = sorted(centres, key=lambda c: c[1])
    for index, (cx, cy) in enumerate(order):
        cancellation_checkpoint()
        occluders = [
            disc(ox, oy, r)
            for ox, oy in order[:index]
            if abs(ox - cx) < 2.0 * r and abs(oy - cy) < 2.0 * r
        ]
        front = unary_union(occluders) if occluders else None
        for radius in radii:
            visible = disc(cx, cy, radius)
            if front is not None:
                visible = visible.difference(front)
            if gap > 0:
                # Classic seigaiha tiles the plane completely, which leaves the
                # outline fill nothing to hatch. A small gap separates the
                # scales so both fill targets have somewhere to go.
                visible = visible.buffer(-gap / 2.0)
            if visible.is_empty:
                continue
            parts: list[list[tuple[float, float]]] = []
            _extract_all_rings(visible, parts)
            for part in parts:
                if len(part) >= 4:
                    _clip_to_outline(Polygon(part), outline_poly, prep, result)
    return result


def _shrink(verts: list[tuple[float, float]], groove: float) -> list[tuple[float, float]]:
    """Pull a cell toward its centroid to leave a groove around it."""
    if groove <= 0 or len(verts) < 3:
        return verts
    cx = sum(x for x, _ in verts) / len(verts)
    cy = sum(y for _, y in verts) / len(verts)
    reach = max(math.hypot(x - cx, y - cy) for x, y in verts)
    if reach <= 0:
        return verts
    factor = max(0.05, 1.0 - (groove / 2.0) / reach)
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in verts]


def gen_knurling(
    outline_poly,
    pitch: float,
    angle: float = 30.0,
    cross: bool = True,
    groove: float = 0.3,
    *,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Diamond knurl: the closed cells formed where two groove families cross.

    The knurl is the raised diamonds, not the grooves, so the generator emits
    the diamonds as closed cells. That is what lets fill hatch inside each
    pad, or hatch the region around them, the same way Honeycomb behaves.
    ``cross`` off gives closed straight-knurl strips instead.
    """
    if pitch <= 0:
        return []
    minx, miny, maxx, maxy = outline_poly.bounds
    cx0, cy0 = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    span = math.hypot(maxx - minx, maxy - miny) + pitch * 2.0
    steps = int(span / pitch) + 2
    prep = prepared.prep(outline_poly)
    result: list[list[tuple[float, float]]] = []
    theta = math.radians(angle)
    sin_t, cos_t = math.sin(theta), math.cos(theta)

    if not cross or abs(sin_t) < 1e-9 or abs(cos_t) < 1e-9:
        # Straight knurl: closed strips perpendicular to the groove direction.
        dx, dy = cos_t, sin_t
        nx, ny = -dy, dx
        width = pitch / 2.0
        phase = float(origin_x or 0.0) % pitch
        for index in range(-steps, steps + 1):
            cancellation_checkpoint()
            offset = index * pitch + phase
            mx, my = cx0 + nx * offset, cy0 + ny * offset
            verts = [
                (mx - dx * span - nx * width, my - dy * span - ny * width),
                (mx + dx * span - nx * width, my + dy * span - ny * width),
                (mx + dx * span + nx * width, my + dy * span + ny * width),
                (mx - dx * span + nx * width, my - dy * span + ny * width),
            ]
            _clip_to_outline(Polygon(_shrink(verts, groove)), outline_poly, prep, result)
        return result

    # Diamond cells: intersections of the +angle and -angle groove families.
    det = -math.sin(2.0 * theta)
    phase_a = float(origin_x or 0.0) / pitch
    phase_b = float(origin_y or 0.0) / pitch

    def node(i: float, j: float) -> tuple[float, float]:
        return (
            cx0 - cos_t * (i - j) * pitch / det,
            cy0 + sin_t * (i + j) * pitch / det,
        )

    for i in range(-steps, steps + 1):
        cancellation_checkpoint()
        for j in range(-steps, steps + 1):
            a = i + phase_a
            b = j + phase_b
            verts = [node(a, b), node(a + 1, b), node(a + 1, b + 1), node(a, b + 1)]
            xs = [x for x, _ in verts]
            ys = [y for _, y in verts]
            if max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy:
                continue
            _clip_to_outline(Polygon(_shrink(verts, groove)), outline_poly, prep, result)
    return result
