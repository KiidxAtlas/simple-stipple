"""DXF I/O helpers — read and write LWPOLYLINE entities."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

import ezdxf  # type: ignore[attr-defined]
from ezdxf import path as ezdxf_path  # type: ignore[attr-defined]
from ezdxf import units  # type: ignore[attr-defined]
from ezdxf.disassemble import recursive_decompose  # type: ignore[attr-defined]
from ezdxf.math import ConstructionArc  # type: ignore[attr-defined]
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

from simple_stipple.core.cad.constants import (
    DXF_CLOSURE_EPS,
    DXF_DEDUP_EPS,
    DXF_PLANAR_Z_TOLERANCE,
    OUTLINE_CLOSE_TOLERANCE_MM,
    OUTLINE_MIN_AREA_MM2,
)
from simple_stipple.core.cad.shape_factory import shape_from_meta

_LOG = logging.getLogger(__name__)
MAX_DXF_FILE_BYTES = 64 * 1024 * 1024
MAX_DXF_ENTITIES = 500_000


def validate_dxf_document(document: Any) -> None:
    """Raise when ezdxf reports structural errors in ``document``.

    Auditing is best-effort because supported ezdxf versions do not all expose
    the same audit surface. Reported errors are never ignored.
    """
    try:
        auditor = document.audit()
    except (AttributeError, RuntimeError) as exc:
        _LOG.debug("ezdxf audit unavailable: %s", exc)
        return
    if not auditor.has_errors:
        return
    details = "; ".join(str(error.message) for error in auditor.errors[:5])
    raise ValueError(
        f"DXF export failed validation ({len(auditor.errors)} error(s)): "
        f"{details}. The file was not written."
    )


def _require_finite_unit_scale(unit_code: int) -> float:
    """Return the source-to-mm scale, rejecting unusable DXF unit metadata."""
    if not unit_code:
        return 1.0
    try:
        source_units = cast(units.InsertUnits, unit_code)
        mm_units = cast(units.InsertUnits, 4)
        scale = float(units.conversion_factor(source_units, mm_units))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"DXF unit code {unit_code} cannot be converted to millimeters.") from exc
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(
            f"DXF unit code {unit_code} produced an invalid millimeter scale ({scale!r})."
        )
    return scale


def _z_is_planar(value: Any) -> bool:
    """Return whether a DXF vector/point lies on the supported XY plane."""
    try:
        if hasattr(value, "z"):
            z = float(value.z)
        elif isinstance(value, (int, float)):
            z = float(value)
        elif not isinstance(value, (str, bytes)) and len(value) >= 3:
            z = float(value[2])
        else:
            z = float(value)
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
    return math.isfinite(z) and abs(z) <= DXF_PLANAR_Z_TOLERANCE


def _entity_is_planar(entity: _DxfEntity, dxftype: str) -> bool:
    """Reject geometry that the 2D importer would otherwise silently project."""
    dxf = entity.dxf
    try:
        if dxftype == "LINE":
            return _z_is_planar(dxf.start) and _z_is_planar(dxf.end)
        if dxftype == "LWPOLYLINE":
            elevation = dxf.get("elevation", 0.0)
            if hasattr(elevation, "z"):
                elevation = elevation.z
            return _z_is_planar(elevation)
        if dxftype in {"ARC", "CIRCLE", "ELLIPSE"}:
            if not _z_is_planar(dxf.center):
                return False
        if dxftype == "SPLINE":
            spline = cast(Any, entity)
            for point in (*tuple(spline.control_points), *tuple(spline.fit_points)):
                if not _z_is_planar(point):
                    return False
            if dxftype == "ELLIPSE" and not _z_is_planar(dxf.major_axis):
                return False
        extrusion = dxf.get("extrusion", None)
        if extrusion is not None:
            ex = float(getattr(extrusion, "x", 0.0))
            ey = float(getattr(extrusion, "y", 0.0))
            ez = float(getattr(extrusion, "z", 1.0))
            if not all(math.isfinite(v) for v in (ex, ey, ez)):
                return False
            if abs(ex) > DXF_PLANAR_Z_TOLERANCE or abs(ey) > DXF_PLANAR_Z_TOLERANCE:
                return False
            if abs(abs(ez) - 1.0) > DXF_PLANAR_Z_TOLERANCE:
                return False
    except (AttributeError, TypeError, ValueError):
        return False
    return True


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
    units: str = "Unitless"
    # Simple Stipple document structure recovered from "SSTP" XDATA (empty
    # for foreign files): dimension metadata dicts, group memberships as
    # {"layer", "index", "group"} entries, and group id → label.
    dimensions: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    group_labels: dict[int, str] = {}
    # {"layer", "index", "name"} — object (shape) names from "SSTPN" XDATA.
    object_names: list[dict[str, Any]] = []

    @property
    def ignored_entities(self) -> int:
        return self.invalid_polylines + sum(self.unsupported_entities.values())

    @property
    def has_issues(self) -> bool:
        return self.ignored_entities > 0


class _DxfEntity(Protocol):
    """Narrow adapter boundary for the dynamically typed ezdxf entities."""

    dxf: Any
    vertices: Iterable[Any]
    is_closed: bool
    is_2d_polyline: bool
    closed: bool
    CLOSED: int

    def dxftype(self) -> str: ...
    def get_points(self) -> Iterable[Any]: ...
    def flattening(self, distance: float) -> Iterable[Any]: ...


def _dxf_entity(value: object) -> _DxfEntity:
    """Contain ezdxf's missing static types at one audited boundary."""
    return cast(_DxfEntity, value)


# ── App-private XDATA ("SSTP") ───────────────────────────────────────────
# DXF has no group entity, and DIMENSION geometry alone cannot recover our
# measurement metadata, so both ride as XDATA under our own appid. Foreign
# tools ignore XDATA, and we ignore files that do not carry it — such files
# import exactly as before (DIMENSIONs land in unsupported_entities).
_SSTP_APPID = "SSTP"
# Object (shape) names ride under a second appid so they can never be
# mistaken for the group's label chunk or the dimension's JSON payload.
_SSTP_NAME_APPID = "SSTPN"
# XDATA string (group code 1000) payloads are capped at 255 bytes by the DXF
# format. json.dumps emits ASCII-only, so 250 characters always fit.
_XDATA_TEXT_CHUNK = 250


def _register_sstp_appid(doc: Any) -> None:
    if _SSTP_APPID not in doc.appids:
        doc.appids.add(_SSTP_APPID)


def _attach_group_xdata(entity: Any, group_id: int, label: str | None) -> None:
    """Record group membership (and the group's label) on a DXF entity."""
    _register_sstp_appid(entity.doc)
    tags: list[tuple[int, Any]] = [(1071, int(group_id))]
    if label:
        tags.append((1000, str(label)))
    entity.set_xdata(_SSTP_APPID, tags)


def _attach_dimension_xdata(entity: Any, meta: dict[str, Any]) -> None:
    """Record the full dimension metadata dict as chunked JSON XDATA."""
    try:
        payload = json.dumps(meta, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    _register_sstp_appid(entity.doc)
    chunks = [
        payload[offset : offset + _XDATA_TEXT_CHUNK]
        for offset in range(0, len(payload), _XDATA_TEXT_CHUNK)
    ]
    entity.set_xdata(_SSTP_APPID, [(1000, chunk) for chunk in chunks])


def _attach_name_xdata(entity: Any, name: str) -> None:
    """Record a shape's object name (layer-tree label) on a DXF entity."""
    if not name:
        return
    doc = entity.doc
    if _SSTP_NAME_APPID not in doc.appids:
        doc.appids.add(_SSTP_NAME_APPID)
    payload = json.dumps(str(name), separators=(",", ":"))
    chunks = [
        payload[offset : offset + _XDATA_TEXT_CHUNK]
        for offset in range(0, len(payload), _XDATA_TEXT_CHUNK)
    ]
    entity.set_xdata(_SSTP_NAME_APPID, [(1000, chunk) for chunk in chunks])


def _name_from_xdata(entity: Any) -> str | None:
    try:
        if not entity.has_xdata(_SSTP_NAME_APPID):
            return None
        text = "".join(
            str(tag.value) for tag in entity.get_xdata(_SSTP_NAME_APPID) if tag.code == 1000
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, str) and value else None


def _read_sstp_xdata(entity: Any) -> list[Any]:
    try:
        if not entity.has_xdata(_SSTP_APPID):
            return []
        return list(entity.get_xdata(_SSTP_APPID))
    except (AttributeError, TypeError, ValueError):
        return []


def _group_from_xdata(tags: list[Any]) -> tuple[int | None, str | None]:
    group_id: int | None = None
    label: str | None = None
    for tag in tags:
        if tag.code == 1071:
            try:
                group_id = int(tag.value)
            except (TypeError, ValueError):
                group_id = None
        elif tag.code == 1000 and label is None:
            label = str(tag.value)
    return group_id, label


def _dimension_from_xdata(tags: list[Any]) -> dict[str, Any] | None:
    text = "".join(str(tag.value) for tag in tags if tag.code == 1000)
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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
        abs(result[-1][0] - result[0][0]) > 1e-6 or abs(result[-1][1] - result[0][1]) > 1e-6
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
        pts.append(
            (
                cx + mx * cos_t + minor_x * sin_t,
                cy + my * cos_t + minor_y * sin_t,
            )
        )
    return _polyline_points_closed(pts, closed=closed)


def _expand_insert_entities(
    msp: Any,
    flattened_entities: Counter[str],
    invalid_polylines: int,
    path: str,
) -> list[Any]:
    """Expand INSERT entities and return the flattened list.

    Returns the list of entities to import (expanded INSERTs + non-INSERT entities).
    """
    import_entities: list[Any] = []
    for source_entity in msp:
        if source_entity.dxftype() == "INSERT":
            try:
                expanded = list(recursive_decompose([source_entity]))
                import_entities.extend(expanded)
                flattened_entities["INSERT (block contents)"] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                _LOG.warning("Skipping invalid INSERT in %s: %s", path, exc)
                invalid_polylines += 1
        else:
            import_entities.append(source_entity)
    return import_entities


def _process_lwpolyline(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool, bool] | None:
    """Extract points from an LWPOLYLINE entity, or None on failure.

    Returns ``(pts, is_closed, had_bulges)``.
    """
    try:
        lw = entity
        vertex_data = list(cast(Any, lw).get_points(format="xyb"))
        had_bulges = any(abs(float(point[2])) > 1e-12 for point in vertex_data)
        if had_bulges:
            pts = [
                (float(point.x), float(point.y))
                for point in ezdxf_path.make_path(entity).flattening(flattening_distance)
            ]
        else:
            pts = [(float(p[0]), float(p[1])) for p in vertex_data]
        return pts, bool(lw.is_closed), had_bulges
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid LWPOLYLINE in DXF: %s", exc)
        return None


def _process_polyline(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool, bool] | None:
    """Extract points from a POLYLINE entity, or None on failure.

    Returns ``(pts, is_closed, had_bulges)``. ``had_bulges`` is only meaningful
    when the return is not None (3D polylines still return None).
    """
    try:
        poly = entity
        if not poly.is_2d_polyline:
            return None
        vertices = list(poly.vertices)
        had_bulges = any(abs(float(vertex.dxf.get("bulge", 0.0))) > 1e-12 for vertex in vertices)
        if had_bulges:
            pts = [
                (float(point.x), float(point.y))
                for point in ezdxf_path.make_path(entity).flattening(flattening_distance)
            ]
        else:
            pts = [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in vertices
            ]
        return pts, bool(poly.is_closed), had_bulges
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid POLYLINE in DXF: %s", exc)
        return None


def _process_line(entity: Any) -> tuple[list[tuple[float, float]], bool] | None:
    """Extract points from a LINE entity, or None on failure."""
    try:
        line = entity
        start = (float(line.dxf.start.x), float(line.dxf.start.y))
        end = (float(line.dxf.end.x), float(line.dxf.end.y))
        return [start, end], False
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid LINE in DXF: %s", exc)
        return None


def _process_arc(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool] | None:
    """Extract points from an ARC entity, or None on failure."""
    try:
        arc = entity
        center = (float(arc.dxf.center.x), float(arc.dxf.center.y))
        radius = float(arc.dxf.radius)
        pts = _arc_points(
            center,
            radius,
            float(arc.dxf.start_angle),
            float(arc.dxf.end_angle),
            closed=False,
            sagitta=flattening_distance,
        )
        return pts, False
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid ARC in DXF: %s", exc)
        return None


def _process_circle(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool] | None:
    """Extract points from a CIRCLE entity, or None on failure."""
    try:
        circle = entity
        center = (float(circle.dxf.center.x), float(circle.dxf.center.y))
        radius = float(circle.dxf.radius)
        pts = _arc_points(
            center,
            radius,
            0.0,
            360.0,
            closed=True,
            sagitta=flattening_distance,
        )
        return pts, True
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid CIRCLE in DXF: %s", exc)
        return None


def _process_ellipse(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool] | None:
    """Extract points from an ELLIPSE entity, or None on failure."""
    try:
        ellipse = entity
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
        return pts, bool(getattr(ellipse, "closed", False))
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid ELLIPSE in DXF: %s", exc)
        return None


def _process_spline(
    entity: Any, flattening_distance: float
) -> tuple[list[tuple[float, float]], bool] | None:
    """Extract points from a SPLINE entity, or None on failure."""
    try:
        spline = entity
        pts = [(float(p.x), float(p.y)) for p in spline.flattening(flattening_distance)]
        is_closed = bool(getattr(spline.dxf, "flags", 0) & spline.CLOSED)
        return pts, is_closed
    except (AttributeError, TypeError, ValueError) as exc:
        _LOG.warning("Skipping invalid SPLINE in DXF: %s", exc)
        return None


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
    source = Path(path)
    size = source.stat().st_size
    if size > MAX_DXF_FILE_BYTES:
        raise ValueError(
            f"{source.name} is too large to import safely "
            f"({size / (1024 * 1024):.1f} MB; limit 64 MB)."
        )
    try:
        doc = _ezdxf_readfile(path)
    except (OSError, FileNotFoundError, ValueError) as exc:
        _LOG.error("Failed to open DXF file %s: %s", path, exc)
        raise ValueError(f"Could not open {source.name} as a DXF: {exc}") from exc
    msp = doc.modelspace()
    if len(msp) > MAX_DXF_ENTITIES:
        raise ValueError(
            f"{source.name} contains {len(msp):,} entities; "
            f"the safe import limit is {MAX_DXF_ENTITIES:,}."
        )
    unit_code = int(doc.header.get("$INSUNITS", 0) or 0)
    unit_name = {
        0: "Unitless",
        1: "Inches",
        2: "Feet",
        4: "Millimeters",
        5: "Centimeters",
        6: "Meters",
    }.get(unit_code, f"DXF unit code {unit_code}")
    mm_per_unit = _require_finite_unit_scale(unit_code)
    flattening_distance = 0.02 / mm_per_unit
    by_layer: dict[str, list[list[tuple[float, float]]]] = {}
    flattened_entities: Counter[str] = Counter()
    unsupported_entities: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    invalid_polylines = 0
    total_supported = 0
    unit_range_violation: str | None = None
    dimensions: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    group_labels: dict[int, str] = {}
    object_names: list[dict[str, Any]] = []

    def _append(
        layer: str, pts: list[tuple[float, float]], closed: bool, source: Any = None
    ) -> None:
        nonlocal total_supported, unit_range_violation
        if len(pts) < 2:
            return
        scaled = [(x * mm_per_unit, y * mm_per_unit) for x, y in pts]
        if any(not math.isfinite(value) for point in scaled for value in point):
            unit_range_violation = (
                "DXF coordinates exceed the finite numerical range after conversion "
                f"from {unit_name} to millimeters."
            )
            return
        bucket = by_layer.setdefault(layer, [])
        bucket.append(_polyline_points_closed(scaled, closed=closed))
        total_supported += 1
        if source is not None:
            group_id, group_label = _group_from_xdata(_read_sstp_xdata(source))
            if group_id is not None:
                groups.append({"layer": layer, "index": len(bucket) - 1, "group": group_id})
                if group_label:
                    group_labels.setdefault(group_id, group_label)
            object_name = _name_from_xdata(source)
            if object_name:
                object_names.append(
                    {"layer": layer, "index": len(bucket) - 1, "name": object_name}
                )

    import_entities = _expand_insert_entities(msp, flattened_entities, invalid_polylines, path)

    for ent in import_entities:
        entity = _dxf_entity(ent)
        dxftype = entity.dxftype()
        try:
            layer_name = str(entity.dxf.layer).strip() or "0"
        except (AttributeError, ValueError, TypeError):
            layer_name = "0"
        layer_counts[layer_name] += 1
        if dxftype in {"LWPOLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}:
            if not _entity_is_planar(entity, dxftype):
                unsupported_entities[f"{dxftype} (non-planar)"] += 1
                continue
        if dxftype == "LWPOLYLINE":
            result = _process_lwpolyline(entity, flattening_distance)
            if result is None:
                invalid_polylines += 1
            else:
                pts, closed, had_bulges = result
                if had_bulges:
                    flattened_entities["LWPOLYLINE (bulge arcs)"] += 1
                _append(layer_name, pts, closed, ent)
        elif dxftype == "POLYLINE":
            result = _process_polyline(entity, flattening_distance)
            if result is None:
                try:
                    poly = entity
                    if not poly.is_2d_polyline:
                        unsupported_entities["POLYLINE (3D)"] += 1
                    else:
                        invalid_polylines += 1
                except (AttributeError, TypeError, ValueError):
                    invalid_polylines += 1
                continue
            pts, closed, had_bulges = result
            if had_bulges:
                flattened_entities["POLYLINE (bulge arcs)"] += 1
            _append(layer_name, pts, closed, ent)
        elif dxftype == "LINE":
            line_result = _process_line(entity)
            if line_result is None:
                invalid_polylines += 1
            else:
                pts, closed = line_result
                _append(layer_name, pts, closed, ent)
                flattened_entities[dxftype] += 1
        elif dxftype == "ARC":
            arc_result = _process_arc(entity, flattening_distance)
            if arc_result is None:
                invalid_polylines += 1
            else:
                pts, closed = arc_result
                _append(layer_name, pts, closed, ent)
                flattened_entities[dxftype] += 1
        elif dxftype == "CIRCLE":
            circle_result = _process_circle(entity, flattening_distance)
            if circle_result is None:
                invalid_polylines += 1
            else:
                pts, closed = circle_result
                _append(layer_name, pts, closed, ent)
                flattened_entities[dxftype] += 1
        elif dxftype == "ELLIPSE":
            ellipse_result = _process_ellipse(entity, flattening_distance)
            if ellipse_result is None:
                invalid_polylines += 1
            else:
                pts, closed = ellipse_result
                _append(layer_name, pts, closed, ent)
                flattened_entities[dxftype] += 1
        elif dxftype == "SPLINE":
            spline_result = _process_spline(entity, flattening_distance)
            if spline_result is None:
                invalid_polylines += 1
            else:
                pts, closed = spline_result
                _append(layer_name, pts, closed, ent)
                flattened_entities[dxftype] += 1
        elif dxftype == "DIMENSION":
            dimension_meta = _dimension_from_xdata(_read_sstp_xdata(ent))
            if dimension_meta is None:
                # Foreign dimension — recoverable geometry is not guessed
                # from defpoints; report it as unsupported, as before.
                unsupported_entities[dxftype] += 1
            else:
                dimensions.append(dimension_meta)
        else:
            unsupported_entities[dxftype] += 1

    if unit_range_violation is not None:
        raise ValueError(unit_range_violation)

    return (
        by_layer,
        DxfImportReport(
            supported_polylines=total_supported,
            flattened_entities=dict(sorted(flattened_entities.items())),
            unsupported_entities=dict(sorted(unsupported_entities.items())),
            invalid_polylines=invalid_polylines,
            layer_counts=dict(sorted(layer_counts.items())),
            units=unit_name,
            dimensions=dimensions,
            groups=groups,
            group_labels=dict(sorted(group_labels.items())),
            object_names=object_names,
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
        layers = ", ".join(f"{name} × {count}" for name, count in report.layer_counts.items())
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
    """Analyze candidate outline polylines before pattern generation.

    Self-touching glyph contours are common in text generated by vector-font
    APIs.  They look like ordinary closed letters on canvas but are rejected by
    GEOS as invalid polygons unless they are first normalized into their valid
    polygonal pieces.  Treat that repair as part of import/preflight rather
    than making users manually repair every letter.
    """
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
        repaired = p
        if not repaired.is_valid:
            try:
                from shapely import make_valid  # type: ignore[import-untyped]

                repaired = make_valid(repaired)
            except Exception:
                try:
                    repaired = repaired.buffer(0)
                except Exception:
                    continue
        if isinstance(repaired, Polygon):
            parts = [repaired]
        elif isinstance(repaired, MultiPolygon):
            parts = list(repaired.geoms)
        elif isinstance(repaired, GeometryCollection):
            parts = [part for part in repaired.geoms if isinstance(part, Polygon)]
        else:
            parts = []
        accepted = False
        for part in parts:
            if part.is_empty or part.area < OUTLINE_MIN_AREA_MM2:
                continue
            usable.append(part)
            accepted = True
        if not accepted:
            too_small_count += 1
    return OutlinePreflight(
        usable_polygons=usable,
        usable_closed_count=len(usable),
        open_count=open_count,
        too_small_count=too_small_count,
    )


def polylines_to_outline(polylines: list[list[tuple[float, float]]]) -> BaseGeometry:
    """Build a filled Shapely region from a list of closed polylines.

    Only polylines that form a genuine closed loop (start ≈ end, within 0.5 mm)
    and have meaningful area (> 1 mm²) are used. This prevents open detail lines
    from being auto-closed into sliver polygons that corrupt the region.

    The result follows the non-zero winding rule used by text and most vector
    formats.  Contours nested inside a parent with the *opposite* direction are
    holes (the counters in ``e``, ``o``, ``B``, and similar glyphs); nested
    contours travelling in the same direction remain ordinary filled shapes.
    This preserves text outlines without turning their counters into solid
    blocks, while keeping independently drawn nested squares fillable unless
    the user explicitly marks one as a cutout.
    """
    analysis = analyze_outline_polylines(polylines)
    if not analysis.usable_polygons:
        raise ValueError(
            "No valid closed outline was found. Close or repair the outline before generating a pattern."
        )
    polygons = analysis.usable_polygons

    def _orientation(poly: Polygon) -> int:
        coords = list(poly.exterior.coords)
        signed_area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(coords, coords[1:]))
        return 1 if signed_area >= 0.0 else -1

    # Identify the nearest fully-containing parent for each contour.  Merely
    # testing representative points would incorrectly treat overlapping shapes
    # as nested, so the complete candidate polygon must be contained.  The
    # spatial index keeps imported text/compound artwork from degrading into
    # an all-pairs containment scan.
    from shapely import STRtree  # type: ignore[import-untyped]

    tree = STRtree(polygons)
    parents: list[int | None] = [None] * len(polygons)
    for index, polygon in enumerate(polygons):
        candidates = [
            int(other_index)
            for other_index in tree.query(polygon)
            if other_index != index
            and (other := polygons[int(other_index)])
            and other.area > polygon.area
            and other.contains(polygon)
        ]
        if candidates:
            parents[index] = min(candidates, key=lambda candidate: polygons[candidate].area)

    roots: list[int] = []
    for index in range(len(polygons)):
        root = index
        while parents[root] is not None:
            parent = parents[root]
            assert parent is not None
            root = parent
        roots.append(root)

    # Every top-level contour is a positive region regardless of its drawing
    # direction.  Within a top-level shape, matching its direction adds material
    # and the opposite direction subtracts material.  That is the non-zero
    # winding rule, expressed with robust Shapely overlay operations.  Crucially
    # each compound root is resolved independently before its result is unioned
    # with its neighbours: a counter in one overlapping letter must not punch a
    # hole through the adjacent letter or another overlapping drawing.
    orientations = [_orientation(polygon) for polygon in polygons]
    root_regions: list[BaseGeometry] = []
    for root in dict.fromkeys(roots):
        positive = [
            polygon
            for index, polygon in enumerate(polygons)
            if roots[index] == root and orientations[index] == orientations[root]
        ]
        negative = [
            polygon
            for index, polygon in enumerate(polygons)
            if roots[index] == root and orientations[index] != orientations[root]
        ]
        region = unary_union(positive)
        if negative:
            region = region.difference(unary_union(negative))
        if not region.is_empty:
            root_regions.append(region)
    result = unary_union(root_regions)
    if result.is_empty:
        raise ValueError(
            "The validated outline produced an empty region. Repair or simplify the outline and try again."
        )
    return (
        result
        if not result.is_empty
        else max(analysis.usable_polygons, key=lambda p: p.area).convex_hull
    )


def _normalize_polyline_for_dxf(
    pts: list[tuple[float, float]],
    *,
    closure_eps: float = DXF_CLOSURE_EPS,
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
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            finite.append((x, y))
    if not finite:
        return [], False

    # Pass 1: drop runs of identical points.
    cleaned: list[tuple[float, float]] = [finite[0]]
    for p in finite[1:]:
        last = cleaned[-1]
        if abs(p[0] - last[0]) > DXF_DEDUP_EPS or abs(p[1] - last[1]) > DXF_DEDUP_EPS:
            cleaned.append(p)

    if len(cleaned) < 2:
        return cleaned, False

    # Pass 2: detect closure (first ~ last) and strip the trailing copy.
    first, last = cleaned[0], cleaned[-1]
    naturally_closed = (
        abs(first[0] - last[0]) <= closure_eps and abs(first[1] - last[1]) <= closure_eps
    )
    if naturally_closed and len(cleaned) >= 3:
        cleaned = cleaned[:-1]
        is_closed = True
    else:
        is_closed = bool(force_close) and len(cleaned) >= 3

    return cleaned, is_closed


_LAYER_COLORS = [5, 4, 6, 1, 2, 8]


def _layer_from_meta_name(name: str | None) -> str | None:
    if not name:
        return None
    label = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name).strip())
    label = re.sub(r"_+", "_", label).strip("_")
    return label[:255] or None


def _entity_attributes(
    doc: Any,
    default_attrs: dict[str, str],
    meta: dict[str, Any] | None,
    entity_names: list[str] | None,
    index: int,
) -> dict[str, str]:
    attrs = dict(default_attrs)
    layer_name = _layer_from_meta_name(meta.get("name")) if isinstance(meta, dict) else None
    if not layer_name and entity_names and index < len(entity_names):
        layer_name = _layer_from_meta_name(entity_names[index])
    if not layer_name:
        return attrs
    if layer_name not in doc.layers:
        doc.layers.add(layer_name, color=2)
    attrs["layer"] = layer_name
    return attrs


def _write_dimension(
    msp: Any,
    points: list[tuple[float, float]],
    meta: dict[str, Any],
    attrs: dict[str, str],
) -> Any | None:
    """Emit a DIMENSION entity and return it (None → caller draws a fallback)."""
    try:
        p1 = tuple(meta.get("p1", points[0]))
        p2 = tuple(meta.get("p2", points[1]))
        precision = max(0, min(8, int(meta.get("precision", 2))))
        dim_override = {"dimdec": precision}
        if meta.get("type") == "angle" and "p3" in meta:
            p3 = tuple(meta["p3"])
            override = msp.add_angular_dim_3p(
                base=p3,
                center=p2,
                p1=p1,
                p2=p3,
                override=dim_override,
                dxfattribs=attrs or None,
            )
        elif meta.get("type") == "diameter":
            center = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            override = msp.add_diameter_dim(
                center=center,
                mpoint=p2,
                override=dim_override,
                dxfattribs=attrs or None,
            )
        else:
            offset = float(meta.get("offset", 5.0))
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy)
            if length <= 1e-12:
                return None
            base = (p1[0] - dy * offset / length, p1[1] + dx * offset / length)
            override = msp.add_linear_dim(
                base=base,
                p1=p1,
                p2=p2,
                dimstyle="EZDXF",
                override=dim_override,
                dxfattribs=attrs or None,
            )
        override.render()
        _attach_dimension_xdata(override.dimension, meta)
        return override.dimension
    except (TypeError, ValueError, IndexError):
        _LOG.warning("Invalid dimension metadata; exporting fallback line")
        return None


def _write_native_shape(
    msp: Any,
    kind: str,
    meta: dict[str, Any] | None,
    attrs: dict[str, str],
) -> Any | None:
    """Emit a native DXF entity for ``kind`` and return it (None → fallback)."""
    if kind == "polyline" or not isinstance(meta, dict):
        return None
    if kind == "ellipse" and "rotation" not in meta and "angle" in meta:
        meta = {**meta, "rotation": meta["angle"]}
    shape = shape_from_meta(kind, meta)
    if shape is None:
        return None
    count_before = len(msp)
    if not shape.to_dxf(msp, attrs or None) or len(msp) <= count_before:
        return None
    return msp[count_before]


def _write_polyline(
    msp: Any,
    points: list[tuple[float, float]],
    attrs: dict[str, str],
    *,
    close: bool,
    open_paths: bool,
) -> Any | None:
    coords, is_closed = _normalize_polyline_for_dxf(
        points,
        force_close=close and not open_paths,
    )
    if len(coords) < 2:
        return None
    return msp.add_lwpolyline(
        coords,
        close=False if open_paths else is_closed,
        dxfattribs=attrs or None,
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
    extra_layer_records: dict[str, list[dict[str, Any]]] | None = None,
    entity_groups: list[int | None] | None = None,
    group_labels: dict[int, str] | None = None,
    object_names: list[str | None] | None = None,
) -> None:
    doc = _ezdxf_new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Cycling palette used for both the main layer and any extra layers.
    # We deliberately avoid color 7 here: many CAM/laser tools treat DXF
    # color 7 as "BYBLOCK / no color set" and refuse to fill those
    # entities, which would silently break the user's first layer.
    dxfattrs: dict[str, str] = {}
    if pattern_layer:
        if pattern_layer not in doc.layers:
            doc.layers.add(pattern_layer, color=_LAYER_COLORS[0])
        dxfattrs = {"layer": pattern_layer}

    # Pad the kind/meta lists to the polyline count — zip() truncates at the
    # shortest input, which silently dropped shapes when callers passed
    # shorter lists. Never drop geometry.
    kinds = list(entity_kinds) if entity_kinds is not None else []
    metas = list(entity_meta) if entity_meta is not None else []
    if len(kinds) < len(polylines):
        kinds += ["polyline"] * (len(polylines) - len(kinds))
    if len(metas) < len(polylines):
        metas += [None] * (len(polylines) - len(metas))
    # Optional group membership per main-list entity, written as XDATA so the
    # drawing's grouping survives a save/open round trip through plain DXF.
    groups_list: list[int | None] = list(entity_groups) if entity_groups is not None else []
    if len(groups_list) < len(polylines):
        groups_list += [None] * (len(polylines) - len(groups_list))
    names_list: list[str | None] = list(object_names) if object_names is not None else []
    if len(names_list) < len(polylines):
        names_list += [None] * (len(polylines) - len(names_list))

    for i, (c, kind, meta) in enumerate(zip(polylines, kinds, metas)):
        if len(c) < 2:
            continue
        entity_attrs = _entity_attributes(doc, dxfattrs, meta, entity_names, i)
        if kind == "dimension" and isinstance(meta, dict):
            if _write_dimension(msp, c, meta, entity_attrs) is not None:
                continue
        created = _write_native_shape(msp, kind, meta, entity_attrs)
        if created is None:
            created = _write_polyline(msp, c, entity_attrs, close=close, open_paths=open_paths)
        group_id = groups_list[i]
        if created is not None and group_id is not None:
            _attach_group_xdata(
                created,
                group_id,
                group_labels.get(group_id) if group_labels else None,
            )
        if created is not None and names_list[i]:
            _attach_name_xdata(created, str(names_list[i]))

    if border_polys:
        # Every outline is its own entity, but they all share one layer —
        # laser/CAM software treats a layer as a single job, and splitting
        # outlines across outline_1/outline_2/… made it run each outline
        # as a separate job instead of cutting them together.
        if border_layer_prefix not in doc.layers:
            doc.layers.add(border_layer_prefix, color=3)
        for c in border_polys:
            coords, is_closed = _normalize_polyline_for_dxf(
                c,
                force_close=not bool(open_paths),
            )
            if len(coords) >= 3 and (open_paths or is_closed):
                msp.add_lwpolyline(
                    coords,
                    close=(False if open_paths else True),
                    dxfattribs={"layer": border_layer_prefix},
                )

    if extra_layer_records:
        extra_layers = {
            name: [list(record["polyline"]) for record in records]
            for name, records in extra_layer_records.items()
        }

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
            records = (extra_layer_records or {}).get(layer_name, [])
            for record_index, c in enumerate(layer_polys):
                if len(c) < 2:
                    continue
                record = records[record_index] if record_index < len(records) else None
                created = None
                if record is not None:
                    kind = str(record.get("kind", "polyline"))
                    meta = record.get("meta")
                    if kind != "polyline" and isinstance(meta, dict):
                        shape = shape_from_meta(kind, meta)
                        if shape is not None:
                            count_before = len(msp)
                            if shape.to_dxf(msp, attrs) and len(msp) > count_before:
                                created = msp[count_before]
                if created is None:
                    coords, is_closed = _normalize_polyline_for_dxf(c, force_close=False)
                    if len(coords) < 2:
                        continue
                    created = msp.add_lwpolyline(coords, close=is_closed, dxfattribs=attrs)
                group_id = record.get("group") if record is not None else None
                if group_id is not None:
                    _attach_group_xdata(
                        created,
                        int(group_id),
                        group_labels.get(int(group_id)) if group_labels else None,
                    )
                object_name = record.get("object_name") if record is not None else None
                if object_name:
                    _attach_name_xdata(created, str(object_name))

    # Audit the document before persisting. Never write a malformed DXF —
    # a file that crashes or silently misbehaves in downstream CAD/CAM tools
    # is worse than a visible export error here.
    try:
        validate_dxf_document(doc)
    except ValueError:
        _LOG.error(
            "write_polylines_dxf: refusing to write invalid document to %s",
            out_path,
        )
        raise

    from simple_stipple.platform.storage import atomic_write_via

    atomic_write_via(out_path, lambda p: doc.saveas(str(p)))
