"""DXF auto-fixer — repairs common issues in DXF files.

Fixes applied
-------------
- Closes near-open polylines (endpoints within tolerance)
- Merges collinear consecutive segments (removes redundant colinear vertices)
- Removes zero-length duplicate vertices
- Removes degenerate polylines with fewer than 2 distinct points
- Flatten mode normalises supported output geometry to layer "0"
"""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import Any, Literal, cast

import ezdxf  # type: ignore[attr-defined]
from ezdxf import units  # type: ignore[attr-defined]
from shapely.geometry import LineString  # type: ignore[import-untyped]

from src.backend.dxf.io import (
    _normalize_polyline_for_dxf,
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
)
from src.backend.persistence import atomic_write_via

_CLOSE_TOL = 0.01  # mm — endpoints closer than this are merged to close polyline
_COLINEAR_TOL = 0.001  # mm — max cross-product deviation to treat segment as collinear
_LOG = logging.getLogger(__name__)
FixMode = Literal["safe", "flatten"]


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _remove_duplicates(
    pts: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Remove consecutive duplicate vertices."""
    if not pts:
        return pts
    result = [pts[0]]
    for p in pts[1:]:
        if _dist(result[-1], p) > 1e-9:
            result.append(p)
    return result


def _simplify_collinear(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Remove near-collinear interior vertices using Ramer–Douglas–Peucker.

    The tolerance is capped at 1 % of the shape's own extent so that small
    shapes (fine text, micro-details) are not visibly distorted.
    """
    if len(pts) < 3:
        return pts
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    effective_tol = min(tol, extent * 0.01)
    simplified = LineString(pts).simplify(effective_tol, preserve_topology=False)
    return [(float(x), float(y)) for x, y, *_ in simplified.coords]


def _fix_polyline(
    pts: list[tuple[float, float]],
    *,
    close_tol: float = _CLOSE_TOL,
    simplify: bool = True,
) -> list[tuple[float, float]] | None:
    """Apply all fixes to a single polyline.  Returns None to discard."""
    pts = _remove_duplicates(pts)
    if len(pts) < 2:
        return None
    # Close near-open polylines
    if len(pts) >= 3 and pts[0] != pts[-1] and _dist(pts[0], pts[-1]) < close_tol:
        pts = pts + [pts[0]]
    if simplify:
        pts = _simplify_collinear(pts, _COLINEAR_TOL)
    if len(pts) < 2:
        return None
    return pts


def _base_stats() -> dict[str, Any]:
    return {
        "polylines_in": 0,
        "polylines_out": 0,
        "closed": 0,
        "simplified": 0,
        "vertices_removed": 0,
        "discarded": 0,
        "flattened_entities": 0,
        "flattened_entity_summary": None,
        "ignored_entities": 0,
        "ignored_entity_summary": None,
        "protected_polylines": 0,
        "changed": False,
        "written": False,
        "copied_unchanged": False,
        "mode": "safe",
    }


def _audit_or_raise(doc: Any) -> None:
    auditor = doc.audit()
    if not auditor.has_errors:
        return
    details = "; ".join(str(error) for error in auditor.errors[:5])
    raise ValueError(
        f"DXF repair produced {len(auditor.errors)} audit error(s); output was not written"
        + (f": {details}" if details else ".")
    )


def _safe_repair_dxf(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Repair supported polylines in-place while preserving the DXF document."""
    doc = cast(Any, ezdxf).readfile(str(input_path))
    stats = _base_stats()
    modelspace = doc.modelspace()
    unit_code = int(doc.header.get("$INSUNITS", 0) or 0)
    try:
        mm_per_drawing_unit = float(units.conversion_factor(unit_code, 4)) if unit_code else 1.0
    except (ValueError, TypeError):
        mm_per_drawing_unit = 1.0
    close_tol = _CLOSE_TOL / mm_per_drawing_unit
    to_delete: list[Any] = []

    for entity in modelspace:
        entity_type = entity.dxftype()
        if entity_type == "LWPOLYLINE":
            vertex_data = list(entity.get_points(format="xyseb"))
            # Rebuilding an LWPOLYLINE from XY coordinates discards bulges and
            # per-vertex widths.  Those values carry real curve/cut geometry,
            # so safe mode must leave the entire entity untouched.
            if any(
                abs(float(start_width)) > 1e-12
                or abs(float(end_width)) > 1e-12
                or abs(float(bulge)) > 1e-12
                for _x, _y, start_width, end_width, bulge in vertex_data
            ):
                stats["polylines_in"] += 1
                stats["polylines_out"] += 1
                stats["protected_polylines"] += 1
                continue
            points = [(float(point[0]), float(point[1])) for point in vertex_data]
            was_closed = bool(entity.is_closed)
        elif entity_type == "POLYLINE" and bool(entity.is_2d_polyline):
            # Classic POLYLINE vertices can carry widths, bulges, flags, and
            # elevation semantics.  Recreating them is not lossless, so safe
            # mode preserves these legacy entities as-is.
            stats["polylines_in"] += 1
            stats["polylines_out"] += 1
            stats["protected_polylines"] += 1
            continue
        else:
            continue

        stats["polylines_in"] += 1
        if was_closed and points and points[0] != points[-1]:
            points.append(points[0])
        original = list(points)
        # Safe mode never performs tolerance-based RDP simplification: even a
        # tiny deviation can be intentional manufacturing geometry.
        result = _fix_polyline(points, close_tol=close_tol, simplify=False)
        if result is None:
            to_delete.append(entity)
            stats["discarded"] += 1
            stats["changed"] = True
            continue

        closed_now = len(result) >= 3 and result[0] == result[-1]
        if closed_now and not was_closed:
            stats["closed"] += 1
        removed = max(0, len(original) - len(result))
        if removed:
            stats["simplified"] += 1
            stats["vertices_removed"] += removed
        if result != original or closed_now != was_closed:
            stats["changed"] = True
            coords, closed_flag = _normalize_polyline_for_dxf(result)
            if entity_type == "LWPOLYLINE":
                entity.set_points(coords, format="xy")
                entity.closed = closed_flag
            else:
                entity.delete_all_vertices()
                entity.append_vertices(coords)
                entity.close(closed_flag)
        stats["polylines_out"] += 1

    for entity in to_delete:
        modelspace.delete_entity(entity)

    if not stats["changed"]:
        if input_path.resolve() != output_path.resolve():
            def copy_unchanged(temporary: Path) -> None:
                shutil.copyfile(input_path, temporary)

            atomic_write_via(output_path, copy_unchanged)
            stats["copied_unchanged"] = True
        return stats

    _audit_or_raise(doc)
    atomic_write_via(output_path, lambda temporary: doc.saveas(str(temporary)))
    stats["written"] = True
    return stats


def fix_dxf(
    input_path: str | Path,
    output_path: str | Path,
    *,
    mode: FixMode = "safe",
) -> dict:
    """
    Read *input_path*, apply all fixes, write to *output_path*.

    Returns a stats dict::

        {
            "polylines_in":  int,
            "polylines_out": int,
            "closed":        int,   # polylines that were closed
            "simplified":    int,   # polylines where collinear verts removed
            "discarded":     int,   # degenerate polylines removed
        }
    """
    source = Path(input_path)
    destination = Path(output_path)
    if mode == "safe":
        return _safe_repair_dxf(source, destination)
    if mode != "flatten":
        raise ValueError(f"Unknown DXF repair mode: {mode}")

    raw, import_report = load_dxf_polylines_with_report(str(source))
    source_doc = cast(Any, ezdxf).readfile(str(source))
    source_unit = int(source_doc.header.get("$INSUNITS", 0) or 0)
    try:
        mm_factor = float(units.conversion_factor(source_unit, 4)) if source_unit else 1.0
    except (ValueError, TypeError):
        mm_factor = 1.0
    if mm_factor != 1.0:
        raw = [[(x * mm_factor, y * mm_factor) for x, y in points] for points in raw]

    stats = {
        **_base_stats(),
        "mode": "flatten",
        "polylines_in": import_report.supported_polylines,
        "flattened_entities": sum(import_report.flattened_entities.values()),
        "ignored_entities": import_report.ignored_entities,
        "ignored_entity_summary": summarize_dxf_import_report(import_report),
        "changed": True,
    }

    if import_report.flattened_entities:
        stats["flattened_entity_summary"] = ", ".join(
            f"{name} × {count}" for name, count in import_report.flattened_entities.items()
        )

    fixed: list[list[tuple[float, float]]] = []
    for pts in raw:
        was_closed = len(pts) >= 3 and pts[0] == pts[-1]
        orig_len = len(pts)
        result = _fix_polyline(pts)
        if result is None:
            stats["discarded"] += 1
            continue

        if len(result) < orig_len:
            stats["simplified"] += 1
            stats["vertices_removed"] += orig_len - len(result)

        closed_now = len(result) >= 3 and result[0] == result[-1]
        if closed_now and not was_closed and len(pts) >= 3 and pts[0] != pts[-1]:
            stats["closed"] += 1

        fixed.append(result)

    # Write output DXF

    out_doc = cast(Any, ezdxf).new("R2010")
    out_doc.header["$INSUNITS"] = 4  # millimetres — match other exporters
    out_msp = out_doc.modelspace()
    for pts in fixed:
        coords, closed = _normalize_polyline_for_dxf(pts)
        if len(coords) < 2:
            continue
        out_msp.add_lwpolyline(coords, close=closed)

    # Audit the document before saving so we surface any structural
    # issues introduced by the fix pipeline instead of writing a broken file.
    _audit_or_raise(out_doc)
    atomic_write_via(destination, lambda p: out_doc.saveas(str(p)))
    stats["written"] = True
    stats["polylines_out"] = len(fixed)
    return stats
