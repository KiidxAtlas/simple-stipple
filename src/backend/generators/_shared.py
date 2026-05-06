"""Shared helpers used across all generator sub-modules."""

from __future__ import annotations

import logging
import math

from shapely import prepared  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

try:
    from PIL import Image as _PIL_Image  # type: ignore[import-untyped]

    _PIL_OK = True
except ImportError:
    _PIL_Image = None
    _PIL_OK = False

LOGGER = logging.getLogger(__name__)


def _hex_verts(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.pi / 6 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _coords_to_polyline(coords) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for c in coords:
        try:
            x = float(c[0])
            y = float(c[1])
        except Exception:
            # Skip any malformed coordinate entries
            continue
        out.append((x, y))
    return out


def _extract_polys(geom, out: list[list[tuple[float, float]]]) -> None:
    """Append exterior coords of Polygon(s) from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(_coords_to_polyline(geom.exterior.coords))
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if not g.is_empty and g.area >= 0.001:
                out.append(_coords_to_polyline(g.exterior.coords))


def _collect_lines(geom, out: list) -> None:
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        c = list(geom.coords)
        if len(c) >= 2:
            try:
                out.append([(float(x), float(y)) for x, y in c])
            except Exception:
                # Fallback: append raw coords if conversion fails
                out.append(c)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            _collect_lines(g, out)


def _clip_to_outline(
    shape: Polygon,
    outline_poly,
    prep,
    result: list[list[tuple[float, float]]],
    *,
    shrink: float = 0.0,
) -> None:
    """Clip a polygon to the outline boundary and append valid pieces to result.

    When *shrink* > 0 the shape is inset by that amount before clipping.
    """
    if not prep.intersects(shape):
        return
    if shrink > 0:
        shape = shape.buffer(-shrink)
        if shape is None or shape.is_empty:
            return
    if prep.contains(shape):
        _extract_polys(shape, result)
        return
    _extract_polys(outline_poly.intersection(shape), result)


def _extract_all_rings(geom, out: list[list[tuple[float, float]]]) -> None:
    """Extract exterior AND interior (hole) rings from a Shapely geometry.

    Unlike _extract_polys which only keeps exterior rings, this function also
    appends the boundary of each hole so callers receive all closed loops that
    describe the full topology of the geometry (needed for negative-space fills).
    """
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area >= 0.001:
            out.append(_coords_to_polyline(geom.exterior.coords))
            for interior in geom.interiors:
                pts = _coords_to_polyline(interior.coords)
                if len(pts) >= 3:
                    out.append(pts)
    elif isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        for g in geom.geoms:
            _extract_all_rings(g, out)


def apply_invert_fill(
    polys: list[list[tuple[float, float]]],
    outline_poly: BaseGeometry,
) -> list[list[tuple[float, float]]]:
    """Return the negative space: the outline region NOT covered by *polys*.

    Computes ``outline - union(pattern_shapes)`` and returns all boundary
    rings (exterior + holes) as separate closed polylines. This represents the
    *gaps between* the pattern elements within the outline — effectively
    inverting which parts of the fill area are drawn.
    """
    from shapely.ops import unary_union

    if not polys or outline_poly is None or outline_poly.is_empty:
        return polys

    shapes = []
    for poly in polys:
        if len(poly) < 3:
            continue
        pts = list(poly)
        if pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        try:
            s = Polygon(pts)
            if not s.is_valid:
                s = s.buffer(0)
            if s.is_valid and not s.is_empty:
                shapes.append(s)
        except Exception:
            continue

    if not shapes:
        result: list[list[tuple[float, float]]] = []
        _extract_polys(outline_poly, result)
        return result

    try:
        pattern_union = unary_union(shapes)
        gap_geom = outline_poly.difference(pattern_union)
    except Exception:
        return polys

    result = []
    _extract_all_rings(gap_geom, result)
    return result


def apply_border_fade(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    fade_width: float,
) -> list[list[tuple[float, float]]]:
    """Thin out pattern elements near the outline boundary.

    Elements whose centroids are within fade_width of the boundary are removed
    with probability proportional to their closeness to the edge (deterministic
    based on position hash, so the result is stable across re-generates).
    """
    if fade_width <= 0 or not polys:
        return polys
    from shapely.geometry import Point

    boundary = outline_poly.boundary
    result = []
    for poly in polys:
        if not poly:
            continue
        # Prefer geometric polygon centroid when possible for irregular shapes.
        try:
            shape = _polygon_from_polyline(poly)
        except Exception:
            shape = None
        if shape is not None:
            _c = shape.centroid
            if not _c.is_empty:
                cx = float(_c.x)
                cy = float(_c.y)
            else:
                cx = sum(x for x, y in poly) / len(poly)
                cy = sum(y for x, y in poly) / len(poly)
        else:
            # Fallback: arithmetic mean of vertices.
            cx = sum(x for x, y in poly) / len(poly)
            cy = sum(y for x, y in poly) / len(poly)
        dist = Point(cx, cy).distance(boundary)
        if dist >= fade_width:
            result.append(poly)
            continue
        ratio = dist / fade_width
        h = (hash((round(cx, 2), round(cy, 2))) & 0xFFFF) / 0xFFFF
        if h < ratio:
            result.append(poly)
    return result


def apply_mirror(
    polys: list[list[tuple[float, float]]],
    outline_poly: Polygon,
    mirror_v: bool = False,
    mirror_h: bool = False,
) -> list[list[tuple[float, float]]]:
    """Mirror pattern elements to enforce symmetry across one or both axes.

    ``mirror_v`` reflects across the vertical centre axis (left ↔ right);
    ``mirror_h`` reflects across the horizontal centre axis (top ↔ bottom).
    Every source polyline is preserved and reflected copies are appended for
    each enabled axis (and the diagonal opposite when both are on). The
    output therefore always *contains* the source pattern plus its mirror
    images — guaranteed to be visibly symmetric even when the source already
    fills both halves of the outline.
    """
    if not mirror_v and not mirror_h:
        return polys
    if not polys:
        return polys

    # Centre of the outline bounding box is the mirror axis origin.
    bounds = outline_poly.bounds
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2

    def _reflect(
        poly: list[tuple[float, float]], rv: bool, rh: bool
    ) -> list[tuple[float, float]]:
        return [
            (
                2 * cx - x if rv else x,
                2 * cy - y if rh else y,
            )
            for x, y in poly
        ]

    # Prepare outline for clipping mirrored pieces when an outline is provided.
    prep_outline = None
    try:
        if outline_poly is not None and not outline_poly.is_empty:
            prep_outline = prepared.prep(outline_poly)
    except Exception:
        prep_outline = None

    result: list[list[tuple[float, float]]] = []
    for poly in polys:
        if not poly:
            continue
        # Always keep the source element.
        result.append(poly)
        # For each requested reflection, reflect then clip to outline.
        for rv, rh in ((True, False), (False, True), (True, True)):
            if (rv and not mirror_v) or (rh and not mirror_h):
                continue
            reflected = _reflect(poly, rv, rh)
            if prep_outline is None:
                result.append(reflected)
                continue
            # Closed polygon path handling.
            if len(reflected) >= 3:
                pts = list(reflected)
                if pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                try:
                    shape = Polygon(pts)
                    if not shape.is_valid:
                        shape = shape.buffer(0)
                    if shape.is_empty:
                        continue
                    if prep_outline.contains(shape):
                        result.append(reflected)
                    elif prep_outline.intersects(shape):
                        clipped = outline_poly.intersection(shape)
                        pieces: list[list[tuple[float, float]]] = []
                        _extract_polys(clipped, pieces)
                        result.extend(pieces)
                    # else: entirely outside — drop it
                except Exception:
                    result.append(reflected)
            else:
                # Open polyline path handling.
                try:
                    ls = LineString(reflected)
                    if not ls.is_empty and prep_outline.intersects(ls):
                        clipped = outline_poly.intersection(ls)
                        _collect_lines(clipped, result)
                except Exception:
                    result.append(reflected)
    return result


def apply_interlace(
    polylines: list[list[tuple[float, float]]],
    outline_poly: BaseGeometry | None = None,
    *,
    spacing: float = 1.0,
) -> list[list[tuple[float, float]]]:
    """Apply interlacing offset to pattern polylines.

    Partitions polylines into rows based on Y-coordinate and offsets alternating
    rows horizontally by ``spacing``/2, creating a tessellating interlaced
    effect. Each shifted shape is clipped back to *outline_poly* so no element
    escapes the outline boundary.
    """
    if not polylines:
        return polylines

    # Estimate per-poly height to derive an automatic row pitch when needed.
    heights: list[float] = []
    all_y: list[float] = []
    for poly in polylines:
        if not poly:
            continue
        ys = [y for _, y in poly]
        if not ys:
            continue
        heights.append(max(ys) - min(ys))
        all_y.extend(ys)

    if not all_y:
        return polylines

    auto_pitch = 0.0
    if heights:
        heights.sort()
        median_h = heights[len(heights) // 2]
        auto_pitch = max(median_h * 1.05, 1e-6)

    if spacing <= 0 or (auto_pitch > 0 and spacing < auto_pitch * 0.5):
        spacing = auto_pitch if auto_pitch > 0 else 1.0

    min_y = min(all_y)
    max_y = max(all_y)
    if max_y - min_y < 1e-6:
        return polylines

    row_height = spacing

    # Build a prepared outline for fast clipping, if provided.
    prep_outline = None
    if outline_poly is not None and not outline_poly.is_empty:
        prep_outline = prepared.prep(outline_poly)

    result: list[list[tuple[float, float]]] = []
    for poly in polylines:
        if not poly:
            result.append(poly)
            continue
        poly_y = sum(y for _, y in poly) / len(poly)
        poly_y = round(poly_y, 6)
        row_idx = int((poly_y - min_y) / row_height)

        if row_idx % 2 == 1:
            offset_x = spacing / 2.0
            shifted = [(x + offset_x, y) for x, y in poly]
        else:
            shifted = poly

        # Clip shifted shape to outline to prevent boundary escape.
        if prep_outline is not None and row_idx % 2 == 1:
            if len(shifted) >= 3:
                pts = list(shifted)
                if pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                try:
                    from shapely.geometry import Polygon as _Poly

                    shape = _Poly(pts)
                    if not shape.is_valid:
                        shape = shape.buffer(0)
                    if shape.is_valid and not shape.is_empty:
                        if prep_outline.contains(shape):
                            result.append(shifted)
                        elif prep_outline.intersects(shape):
                            clipped = outline_poly.intersection(shape)  # type: ignore[union-attr]
                            pieces: list[list[tuple[float, float]]] = []
                            _extract_polys(clipped, pieces)
                            result.extend(pieces)
                        # else: entirely outside — drop it
                    else:
                        result.append(shifted)
                except Exception:
                    result.append(shifted)
            else:
                result.append(shifted)
        else:
            result.append(shifted)

    return result


# ─── Hatch infill (laser-fill) ─────────────────────────────────────────────


HATCH_MODES = ("none", "lines", "crosshatch", "racecar", "concentric")


def _polygon_from_polyline(
    poly: list[tuple[float, float]],
) -> Polygon | None:
    if not poly or len(poly) < 3:
        return None
    pts = list(poly)
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    try:
        shape = Polygon(pts)
    except (TypeError, ValueError):
        return None
    if not shape.is_valid:
        try:
            shape = shape.buffer(0)
        except (TypeError, ValueError):
            return None
    if shape.is_empty or shape.area <= 1e-9:
        return None
    return shape


def _hatch_lines_for_polygon(
    shape: Polygon,
    spacing: float,
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Return parallel hatch lines clipped to ``shape`` at the given angle."""
    if spacing <= 0 or shape.is_empty:
        return []
    minx, miny, maxx, maxy = shape.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    diag = math.hypot(maxx - minx, maxy - miny) + spacing * 2.0
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx  # perpendicular direction
    half = diag
    n_lines = int(diag / spacing) + 2
    lines: list[list[tuple[float, float]]] = []
    for i in range(-n_lines, n_lines + 1):
        offset = i * spacing
        ox = cx + offset * nx
        oy = cy + offset * ny
        p1 = (ox - dx * half, oy - dy * half)
        p2 = (ox + dx * half, oy + dy * half)
        clipped = shape.intersection(LineString([p1, p2]))
        _collect_lines(clipped, lines)
    return [[(float(x), float(y)) for x, y in ln] for ln in lines]


def _serpentine_connect(
    lines: list[list[tuple[float, float]]],
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Stitch parallel hatch lines into a continuous racecar/zigzag path.

    Lines are sorted along the perpendicular axis, alternate ones are reversed,
    and successive endpoints are joined into a single polyline.
    """
    if not lines:
        return []
    a = math.radians(angle_deg)
    nx, ny = -math.sin(a), math.cos(a)

    def _key(ln: list[tuple[float, float]]) -> float:
        x = (ln[0][0] + ln[-1][0]) / 2.0
        y = (ln[0][1] + ln[-1][1]) / 2.0
        return x * nx + y * ny

    ordered = sorted(lines, key=_key)
    path: list[tuple[float, float]] = []
    flip = False
    for ln in ordered:
        seg = list(reversed(ln)) if flip else list(ln)
        if not path:
            path.extend(seg)
        else:
            # Connector from end of last segment to start of next.
            path.append(seg[0])
            path.extend(seg[1:])
        flip = not flip
    return [path] if len(path) >= 2 else []


def _concentric_offsets(
    shape: Polygon,
    spacing: float,
    max_loops: int = 200,
) -> list[list[tuple[float, float]]]:
    """Return inset loops of ``shape`` spaced ``spacing`` apart."""
    if spacing <= 0 or shape.is_empty:
        return []
    out: list[list[tuple[float, float]]] = []
    current = shape
    step = 0
    while step < max_loops:
        try:
            current = current.buffer(-spacing, join_style="mitre")
        except (TypeError, ValueError):
            break
        if current is None or current.is_empty:
            break
        _extract_polys(current, out)
        step += 1
    return out


def apply_hatch_fill(
    polys: list[list[tuple[float, float]]],
    *,
    mode: str = "none",
    spacing: float = 1.0,
    angle_deg: float = 0.0,
    keep_outline: bool = False,
) -> list[list[tuple[float, float]]]:
    """Replace each closed pattern polygon with laser-style infill polylines.

    Modes:
      * ``"none"`` — return ``polys`` unchanged.
      * ``"lines"`` — parallel hatch lines at ``angle_deg``.
      * ``"crosshatch"`` — two perpendicular sets of hatch lines.
      * ``"racecar"`` — single continuous serpentine path per shape.
      * ``"concentric"`` — inward inset loops.

    When ``keep_outline`` is True, the original polygon outline is preserved
    alongside the infill so the laser cuts/marks both edge and fill.
    """
    if mode == "none" or not polys or spacing <= 0:
        return polys

    out: list[list[tuple[float, float]]] = []
    for poly in polys:
        shape = _polygon_from_polyline(poly)
        if shape is None:
            # Open polylines (no enclosed area) pass straight through.
            out.append(poly)
            continue
        if keep_outline:
            out.append(poly)
        if mode == "lines":
            out.extend(_hatch_lines_for_polygon(shape, spacing, angle_deg))
        elif mode == "crosshatch":
            out.extend(_hatch_lines_for_polygon(shape, spacing, angle_deg))
            out.extend(_hatch_lines_for_polygon(shape, spacing, angle_deg + 90.0))
        elif mode == "racecar":
            lines = _hatch_lines_for_polygon(shape, spacing, angle_deg)
            out.extend(_serpentine_connect(lines, angle_deg))
        elif mode == "concentric":
            out.extend(_concentric_offsets(shape, spacing))
        else:
            # Unknown mode — fail safe and keep the original outline.
            if not keep_outline:
                out.append(poly)
    return out
