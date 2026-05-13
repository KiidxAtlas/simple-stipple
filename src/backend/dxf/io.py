"""DXF I/O helpers — read and write LWPOLYLINE entities."""

from __future__ import annotations

import logging
import math
import re
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

# Tolerance (in drawing units) for detecting that a polyline's first and
# last points coincide.  Tight enough to avoid false positives on real
# open chains, loose enough to absorb float round-trips through shapely.
_DXF_CLOSURE_EPS = 1e-4
_DXF_DEDUP_EPS = 1e-9


def _normalize_polyline_for_dxf(
    pts: list[tuple[float, float]],
    *,
    closure_eps: float = _DXF_CLOSURE_EPS,
    force_close: bool = False,
) -> tuple[list[tuple[float, float]], bool]:
    """Clean a polyline for DXF emission.

    * Drops consecutive duplicate points (zero-length segments) which break
      some downstream CAD tools and inflate file size.
    * Detects whether the polyline is closed (first \u2248 last within
      ``closure_eps``) and strips the trailing duplicate so callers can
      hand the points to ezdxf with ``close=True`` cleanly.

    Returns ``(coords, is_closed)``.  ``is_closed`` is True when either
    the input was naturally closed or ``force_close`` is requested AND
    the result has \u2265 3 distinct points.
    """
    if not pts:
        return [], False

    # Pass 0: drop NaN / inf coordinates that would corrupt the DXF.
    finite: list[tuple[float, float]] = []
    for p in pts:
        try:
            x = float(p[0])
            y = float(p[1])
        except Exception:
            continue
        if math.isfinite(x) and math.isfinite(y):
            finite.append((x, y))
    if not finite:
        return [], False

    # Pass 1: drop runs of identical points.
    cleaned: list[tuple[float, float]] = [finite[0]]
    for p in finite[1:]:
        last = cleaned[-1]
        if abs(p[0] - last[0]) > _DXF_DEDUP_EPS or abs(p[1] - last[1]) > _DXF_DEDUP_EPS:
            cleaned.append(p)

    if len(cleaned) < 2:
        return cleaned, False

    # Pass 2: detect closure (first ~ last) and strip the trailing copy.
    first, last = cleaned[0], cleaned[-1]
    naturally_closed = (
        abs(first[0] - last[0]) <= closure_eps
        and abs(first[1] - last[1]) <= closure_eps
    )
    if naturally_closed and len(cleaned) >= 3:
        cleaned = cleaned[:-1]
        is_closed = True
    else:
        is_closed = bool(force_close) and len(cleaned) >= 3

    return cleaned, is_closed


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
    by_layer, report = _load_dxf_polylines_by_layer_with_report(path)
    flat: list[list[tuple[float, float]]] = []
    for polys in by_layer.values():
        flat.extend(polys)
    return flat, report


def _load_dxf_polylines_by_layer_with_report(
    path: str,
) -> tuple[dict[str, list[list[tuple[float, float]]]], DxfImportReport]:
    doc = _ezdxf_readfile(path)
    msp = doc.modelspace()
    by_layer: dict[str, list[list[tuple[float, float]]]] = {}
    flattened_entities: Counter[str] = Counter()
    unsupported_entities: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    invalid_polylines = 0
    total_supported = 0

    def _append(layer: str, pts: list[tuple[float, float]], closed: bool) -> None:
        nonlocal total_supported
        if len(pts) < 2:
            return
        bucket = by_layer.setdefault(layer, [])
        bucket.append(_polyline_points_closed(pts, closed=closed))
        total_supported += 1

    for ent in msp:
        dxftype = ent.dxftype()
        try:
            layer_name = str(ent.dxf.layer).strip() or "0"
        except (AttributeError, ValueError, TypeError):
            layer_name = "0"
        layer_counts[layer_name] += 1
        if dxftype == "LWPOLYLINE":
            try:
                lw = cast(Any, ent)
                pts = [(float(p[0]), float(p[1])) for p in lw.get_points()]
                _append(layer_name, pts, bool(lw.is_closed))
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
                _append(layer_name, pts, bool(poly.is_closed))
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid POLYLINE in %s: %s", path, exc)
                invalid_polylines += 1
                continue
        elif dxftype == "LINE":
            try:
                line = cast(Any, ent)
                start = (float(line.dxf.start.x), float(line.dxf.start.y))
                end = (float(line.dxf.end.x), float(line.dxf.end.y))
                _append(layer_name, [start, end], False)
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
                _append(layer_name, pts, False)
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
                _append(layer_name, pts, True)
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
                _append(layer_name, pts, bool(getattr(ellipse, "closed", False)))
                flattened_entities[dxftype] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid ELLIPSE in %s: %s", path, exc)
                invalid_polylines += 1
        else:
            unsupported_entities[dxftype] += 1

    return (
        by_layer,
        DxfImportReport(
            supported_polylines=total_supported,
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


def load_dxf_polylines_by_layer_with_report(
    path: str,
) -> tuple[dict[str, list[list[tuple[float, float]]]], DxfImportReport]:
    """Return polylines grouped by source layer plus the import report."""
    return _load_dxf_polylines_by_layer_with_report(path)


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
    open_paths: bool = False,
    border_polys: list[list[tuple[float, float]]] | None = None,
    pattern_layer: str | None = None,
    border_layer_prefix: str = "BORDER",
    entity_kinds: list[str] | None = None,
    entity_meta: list[dict[str, Any] | None] | None = None,
    entity_names: list[str] | None = None,
    extra_layers: dict[str, list[list[tuple[float, float]]]] | None = None,
) -> None:
    def _layer_from_meta_name(name: str | None) -> str | None:
        if not name:
            return None
        label = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name).strip())
        label = re.sub(r"_+", "_", label).strip("_")
        if not label:
            return None
        return label[:255]

    doc = _ezdxf_new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Cycling palette used for both the main layer and any extra layers.
    # We deliberately avoid color 7 here: many CAM/laser tools treat DXF
    # color 7 as "BYBLOCK / no color set" and refuse to fill those
    # entities, which would silently break the user's first layer.
    _LAYER_COLORS = [5, 4, 6, 1, 2, 8]  # blue, cyan, magenta, red, yellow, gray

    dxfattrs: dict[str, str] = {}
    if pattern_layer:
        if pattern_layer not in doc.layers:
            doc.layers.add(pattern_layer, color=_LAYER_COLORS[0])
        dxfattrs = {"layer": pattern_layer}

    kinds = entity_kinds if entity_kinds is not None else ["polyline"] * len(polylines)
    metas = entity_meta if entity_meta is not None else [None] * len(polylines)

    for i, (c, kind, meta) in enumerate(zip(polylines, kinds, metas)):
        if len(c) >= 2:
            entity_attrs = dict(dxfattrs)
            layer_from_name = None
            if isinstance(meta, dict):
                layer_from_name = _layer_from_meta_name(meta.get("name"))
            if not layer_from_name and entity_names and i < len(entity_names):
                layer_from_name = _layer_from_meta_name(entity_names[i])
            if layer_from_name:
                if layer_from_name not in doc.layers:
                    doc.layers.add(layer_from_name, color=2)
                entity_attrs["layer"] = layer_from_name

            if kind == "line" and meta and "start" in meta and "end" in meta:
                msp.add_line(
                    tuple(meta["start"]),
                    tuple(meta["end"]),
                    dxfattribs=entity_attrs or None,
                )
                continue
            if kind == "circle" and meta and "center" in meta and "radius" in meta:
                msp.add_circle(
                    tuple(meta["center"]),
                    float(meta["radius"]),
                    dxfattribs=entity_attrs or None,
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
                        dxfattribs=entity_attrs or None,
                    )
                    continue
            if kind == "arc" and meta and "center" in meta and "radius" in meta:
                msp.add_arc(
                    tuple(meta["center"]),
                    float(meta["radius"]),
                    float(meta.get("start_angle", 0.0)),
                    float(meta.get("end_angle", 360.0)),
                    dxfattribs=entity_attrs or None,
                )
                continue
            if kind == "spline" and meta and "control_points" in meta:
                cps = [
                    tuple(pt)
                    for pt in cast(
                        list[tuple[float, float]], meta.get("control_points", [])
                    )
                ]
                if len(cps) >= 2:
                    msp.add_spline(
                        cps,
                        degree=int(meta.get("degree", 3)),
                        dxfattribs=entity_attrs or None,
                    )
                    continue

            force_close = bool(close) and not bool(open_paths)
            coords, is_closed = _normalize_polyline_for_dxf(
                c,
                force_close=force_close,
            )
            if len(coords) < 2:
                continue
            msp.add_lwpolyline(
                coords,
                close=(False if open_paths else is_closed),
                dxfattribs=entity_attrs or None,
            )

    if border_polys:
        # Pre-normalize so layer suffixes (_1, _2…) reflect the *valid*
        # borders only — otherwise a single surviving border could be
        # named "outline_2" with no "outline_1" anywhere in the file.
        valid_borders: list[list[tuple[float, float]]] = []
        for c in border_polys:
            coords, is_closed = _normalize_polyline_for_dxf(
                c,
                force_close=not bool(open_paths),
            )
            if len(coords) >= 3 and (open_paths or is_closed):
                valid_borders.append(coords)
        count = len(valid_borders)
        for idx, coords in enumerate(valid_borders):
            layer_name = border_layer_prefix
            if count > 1:
                layer_name = f"{border_layer_prefix}_{idx + 1}"
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=3)
            msp.add_lwpolyline(
                coords,
                close=(False if open_paths else True),
                dxfattribs={"layer": layer_name},
            )

    if extra_layers:
        # Each entry produces its own DXF layer. Polylines are written as
        # LWPOLYLINE entities; closure is inferred from coordinate equality.
        # Colors cycle so layers are visually distinguishable in CAD viewers.
        # Offset by 1 when a pattern_layer was emitted so the main layer's
        # color (LAYER_COLORS[0]) isn't reused on the first extra.
        color_offset = 1 if pattern_layer else 0
        for color_idx, (layer_name, layer_polys) in enumerate(extra_layers.items()):
            if not layer_polys:
                continue
            color = _LAYER_COLORS[(color_idx + color_offset) % len(_LAYER_COLORS)]
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=color)
            attrs = {"layer": layer_name}
            for c in layer_polys:
                if len(c) < 2:
                    continue
                coords, is_closed = _normalize_polyline_for_dxf(c, force_close=False)
                if len(coords) < 2:
                    continue
                msp.add_lwpolyline(coords, close=is_closed, dxfattribs=attrs)

    # Audit the document before persisting so structural problems surface in
    # the log instead of producing a file that crashes downstream CAD tools.
    try:
        auditor = doc.audit()
        if auditor.has_errors:
            _LOG.warning(
                "write_polylines_dxf: %d audit error(s) in %s; writing anyway",
                len(auditor.errors),
                out_path,
            )
    except (AttributeError, RuntimeError, ValueError) as exc:
        # Audit is best-effort; older ezdxf versions or unusual docs may
        # not expose the same surface.
        _LOG.debug("ezdxf audit unavailable: %s", exc)

    from ..io.persistence import atomic_write_via

    atomic_write_via(out_path, lambda p: doc.saveas(str(p)))
