"""DXF I/O helpers — read and write LWPOLYLINE entities."""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any, NamedTuple, cast

import ezdxf  # type: ignore[attr-defined]
from ezdxf.math import ConstructionArc  # type: ignore[attr-defined]
from shapely.geometry import Polygon  # type: ignore[import-untyped]
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

_LOG = logging.getLogger(__name__)
OUTLINE_CLOSE_TOLERANCE_MM = 2.0
OUTLINE_MIN_AREA_MM2 = 1.0


class OutlinePreflight(NamedTuple):
    usable_polygons: list[Polygon]
    usable_closed_count: int
    open_count: int
    too_small_count: int


class DxfImportReport(NamedTuple):
    supported_polylines: int
    flattened_entities: dict[str, int]
    unsupported_entities: dict[str, int]
    invalid_polylines: int
    layer_counts: dict[str, int]

    @property
    def flattened_entity_count(self) -> int:
        return sum(self.flattened_entities.values())

    @property
    def ignored_entities(self) -> int:
        return self.invalid_polylines + sum(self.unsupported_entities.values())

    @property
    def has_issues(self) -> bool:
        return self.ignored_entities > 0


def _ezdxf_readfile(path: str):
    return cast(Any, ezdxf).readfile(path)


def _ezdxf_new(version: str = "R2010"):
    return cast(Any, ezdxf).new(version)


def _polyline_points_closed(
    pts: list[tuple[float, float]],
    *,
    closed: bool,
) -> list[tuple[float, float]]:
    if not pts:
        return []
    result = list(pts)
    if closed and (
        abs(result[-1][0] - result[0][0]) > 1e-6
        or abs(result[-1][1] - result[0][1]) > 1e-6
    ):
        result.append(result[0])
    return result


def _arc_points(
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    closed: bool = False,
    sagitta: float = 0.02,
) -> list[tuple[float, float]]:
    arc = ConstructionArc(
        center=center,
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
    )
    pts = [(float(p.x), float(p.y)) for p in arc.flattening(sagitta)]
    return _polyline_points_closed(pts, closed=closed)


def _ellipse_points(
    center: tuple[float, float],
    major_axis: tuple[float, float],
    ratio: float,
    start_param: float,
    end_param: float,
    *,
    closed: bool = False,
    segments: int = 96,
) -> list[tuple[float, float]]:
    cx, cy = center
    mx, my = major_axis
    major_len = math.hypot(mx, my)
    if major_len < 1e-9 or ratio <= 0:
        return []
    # Per ezdxf, the minor axis is perpendicular to the major axis and scaled by the ratio.
    minor_x = -my * ratio
    minor_y = mx * ratio

    start = float(start_param)
    end = float(end_param)
    if closed or end <= start:
        end += 2.0 * math.pi
        closed = True

    span = max(end - start, 1e-9)
    count = max(16, min(256, int(math.ceil(span / (2.0 * math.pi) * segments))))
    pts: list[tuple[float, float]] = []
    for idx in range(count + 1):
        t = start + (span * idx / count)
        cos_t = math.cos(t)
        sin_t = math.sin(t)
        pts.append((
            cx + mx * cos_t + minor_x * sin_t,
            cy + my * cos_t + minor_y * sin_t,
        ))
    return _polyline_points_closed(pts, closed=closed)


def _load_dxf_polylines_with_report(
    path: str,
) -> tuple[list[list[tuple[float, float]]], DxfImportReport]:
    doc = _ezdxf_readfile(path)
    msp = doc.modelspace()
    result: list[list[tuple[float, float]]] = []
    flattened_entities: Counter[str] = Counter()
    unsupported_entities: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    invalid_polylines = 0

    def _append(pts: list[tuple[float, float]], closed: bool) -> None:
        if len(pts) < 2:
            return
        result.append(_polyline_points_closed(pts, closed=closed))

    for ent in msp:
        dxftype = ent.dxftype()
        try:
            layer_name = str(ent.dxf.layer).strip()
        except Exception:
            layer_name = "0"
        if layer_name:
            layer_counts[layer_name] += 1
        if dxftype == "LWPOLYLINE":
            try:
                lw = cast(Any, ent)
                pts = [(float(p[0]), float(p[1])) for p in lw.get_points()]
                _append(pts, bool(lw.is_closed))
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid LWPOLYLINE in %s: %s", path, exc)
                invalid_polylines += 1
        elif dxftype == "POLYLINE":
            try:
                poly = cast(Any, ent)
                if not poly.is_2d_polyline:
                    unsupported_entities["POLYLINE (3D)"] += 1
                    continue
                pts = [
                    (float(v.dxf.location.x), float(v.dxf.location.y))
                    for v in poly.vertices
                ]
                _append(pts, bool(poly.is_closed))
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid POLYLINE in %s: %s", path, exc)
                invalid_polylines += 1
                continue
        elif dxftype == "LINE":
            try:
                line = cast(Any, ent)
                start = (float(line.dxf.start.x), float(line.dxf.start.y))
                end = (float(line.dxf.end.x), float(line.dxf.end.y))
                _append([start, end], False)
                flattened_entities[dxftype] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid LINE in %s: %s", path, exc)
                invalid_polylines += 1
        elif dxftype == "ARC":
            try:
                arc = cast(Any, ent)
                center = (float(arc.dxf.center.x), float(arc.dxf.center.y))
                radius = float(arc.dxf.radius)
                pts = _arc_points(
                    center,
                    radius,
                    float(arc.dxf.start_angle),
                    float(arc.dxf.end_angle),
                    closed=False,
                )
                _append(pts, False)
                flattened_entities[dxftype] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid ARC in %s: %s", path, exc)
                invalid_polylines += 1
        elif dxftype == "CIRCLE":
            try:
                circle = cast(Any, ent)
                center = (float(circle.dxf.center.x), float(circle.dxf.center.y))
                radius = float(circle.dxf.radius)
                pts = _arc_points(
                    center,
                    radius,
                    0.0,
                    360.0,
                    closed=True,
                )
                _append(pts, True)
                flattened_entities[dxftype] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid CIRCLE in %s: %s", path, exc)
                invalid_polylines += 1
        elif dxftype == "ELLIPSE":
            try:
                ellipse = cast(Any, ent)
                center = (float(ellipse.dxf.center.x), float(ellipse.dxf.center.y))
                major_axis = (
                    float(ellipse.dxf.major_axis.x),
                    float(ellipse.dxf.major_axis.y),
                )
                pts = _ellipse_points(
                    center,
                    major_axis,
                    float(ellipse.dxf.ratio),
                    float(ellipse.dxf.start_param),
                    float(ellipse.dxf.end_param),
                    closed=bool(getattr(ellipse, "closed", False)),
                )
                _append(pts, bool(getattr(ellipse, "closed", False)))
                flattened_entities[dxftype] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid ELLIPSE in %s: %s", path, exc)
                invalid_polylines += 1
        else:
            unsupported_entities[dxftype] += 1

    return (
        result,
        DxfImportReport(
            supported_polylines=len(result),
            flattened_entities=dict(sorted(flattened_entities.items())),
            unsupported_entities=dict(sorted(unsupported_entities.items())),
            invalid_polylines=invalid_polylines,
            layer_counts=dict(sorted(layer_counts.items())),
        ),
    )


def load_dxf_polylines(path: str) -> list[list[tuple[float, float]]]:
    """Return all LWPOLYLINE and POLYLINE entities as lists of (x, y) tuples.

    For flag-closed polylines (is_closed=True) the closing point is appended
    so that downstream code can treat start≈end as the closed-loop signal.
    Supports both modern LWPOLYLINE (R14+) and legacy POLYLINE (pre-R14) entities.
    """
    polys, _ = _load_dxf_polylines_with_report(path)
    return polys


def load_dxf_polylines_with_report(
    path: str,
) -> tuple[list[list[tuple[float, float]]], DxfImportReport]:
    """Return polylines plus a report describing skipped DXF content."""
    return _load_dxf_polylines_with_report(path)


def summarize_dxf_import_report(report: DxfImportReport) -> str | None:
    """Format a short human-readable description of skipped DXF content."""
    parts: list[str] = []
    if report.layer_counts:
        layers = ", ".join(
            f"{name} × {count}" for name, count in report.layer_counts.items()
        )
        parts.append(f"layers: {layers}")
    if report.flattened_entities:
        details = ", ".join(
            f"{name} × {count}" for name, count in report.flattened_entities.items()
        )
        parts.append(f"flattened into polylines: {details}")
    if report.invalid_polylines:
        parts.append(f"{report.invalid_polylines} malformed polyline(s)")
    if report.unsupported_entities:
        details = ", ".join(
            f"{name} × {count}" for name, count in report.unsupported_entities.items()
        )
        parts.append(f"unsupported DXF entity types: {details}")
    if not parts:
        return None
    return "; ".join(parts)


def analyze_outline_polylines(
    polylines: list[list[tuple[float, float]]],
) -> OutlinePreflight:
    """Analyze candidate outline polylines before pattern generation."""
    usable: list[Polygon] = []
    open_count = 0
    too_small_count = 0
    for c in polylines:
        if len(c) < 3:
            if c:
                open_count += 1
            continue
        dx = c[-1][0] - c[0][0]
        dy = c[-1][1] - c[0][1]
        if math.hypot(dx, dy) > OUTLINE_CLOSE_TOLERANCE_MM:
            open_count += 1
            continue
        try:
            p = Polygon(c)
        except (TypeError, ValueError):
            continue
        if not p.is_valid:
            continue
        if p.area < OUTLINE_MIN_AREA_MM2:
            too_small_count += 1
            continue
        usable.append(p)
    return OutlinePreflight(
        usable_polygons=usable,
        usable_closed_count=len(usable),
        open_count=open_count,
        too_small_count=too_small_count,
    )


def polylines_to_outline(polylines: list[list[tuple[float, float]]]) -> BaseGeometry:
    """Build a Shapely union polygon from a list of closed polylines.

    Only polylines that form a genuine closed loop (start ≈ end, within 0.5 mm)
    and have meaningful area (> 1 mm²) are used. This prevents open detail lines
    from being auto-closed into sliver polygons that corrupt the union.
    """
    analysis = analyze_outline_polylines(polylines)
    if not analysis.usable_polygons:
        raise ValueError(
            "No valid closed outline was found. Close or repair the outline before generating a pattern."
        )
    result = unary_union(analysis.usable_polygons)
    if result.is_empty:
        raise ValueError(
            "The validated outline produced an empty region. Repair or simplify the outline and try again."
        )
    return (
        result
        if not result.is_empty
        else max(analysis.usable_polygons, key=lambda p: p.area).convex_hull
    )


def write_polylines_dxf(
    polylines: list[list[tuple[float, float]]],
    out_path: str,
    close: bool = False,
    border_polys: list[list[tuple[float, float]]] | None = None,
    pattern_layer: str | None = None,
    border_layer_prefix: str = "BORDER",
    entity_kinds: list[str] | None = None,
    entity_meta: list[dict[str, Any] | None] | None = None,
) -> None:
    doc = _ezdxf_new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    dxfattrs: dict[str, str] = {}
    if pattern_layer:
        if pattern_layer not in doc.layers:
            doc.layers.add(pattern_layer, color=7)
        dxfattrs = {"layer": pattern_layer}

    kinds = entity_kinds if entity_kinds is not None else ["polyline"] * len(polylines)
    metas = entity_meta if entity_meta is not None else [None] * len(polylines)

    for c, kind, meta in zip(polylines, kinds, metas):
        if len(c) >= 2:
            if kind == "line" and meta and "start" in meta and "end" in meta:
                msp.add_line(
                    tuple(meta["start"]),
                    tuple(meta["end"]),
                    dxfattribs=dxfattrs or None,
                )
                continue
            if kind == "circle" and meta and "center" in meta and "radius" in meta:
                msp.add_circle(
                    tuple(meta["center"]),
                    float(meta["radius"]),
                    dxfattribs=dxfattrs or None,
                )
                continue
            if (
                kind == "ellipse"
                and meta
                and "center" in meta
                and "rx" in meta
                and "ry" in meta
            ):
                rx = float(meta["rx"])
                ry = float(meta["ry"])
                if rx > 0 and ry > 0:
                    rot = math.radians(
                        float(meta.get("rotation", meta.get("angle", 0.0)))
                    )
                    major_axis = (rx * math.cos(rot), rx * math.sin(rot))
                    msp.add_ellipse(
                        tuple(meta["center"]),
                        major_axis,
                        ratio=ry / rx,
                        dxfattribs=dxfattrs or None,
                    )
                    continue
            if kind == "arc" and meta and "center" in meta and "radius" in meta:
                msp.add_arc(
                    tuple(meta["center"]),
                    float(meta["radius"]),
                    float(meta.get("start_angle", 0.0)),
                    float(meta.get("end_angle", 360.0)),
                    dxfattribs=dxfattrs or None,
                )
                continue

            if (
                close
                and len(c) >= 3
                and math.hypot(c[-1][0] - c[0][0], c[-1][1] - c[0][1]) < 0.5
            ):
                poly_close = True
                coords = c[:-1]
            else:
                poly_close = False
                coords = c
            msp.add_lwpolyline(coords, close=poly_close, dxfattribs=dxfattrs or None)

    if border_polys:
        count = len(border_polys)
        for idx, c in enumerate(border_polys):
            if len(c) < 2:
                continue
            layer_name = border_layer_prefix
            if count > 1:
                layer_name = f"{border_layer_prefix}_{idx + 1}"
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=3)
            msp.add_lwpolyline(c, close=True, dxfattribs={"layer": layer_name})

    doc.saveas(out_path)
