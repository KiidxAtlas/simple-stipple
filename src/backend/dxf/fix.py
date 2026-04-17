"""DXF auto-fixer — repairs common issues in DXF files.

Fixes applied
-------------
- Closes near-open polylines (endpoints within tolerance)
- Merges collinear consecutive segments (removes redundant colinear vertices)
- Removes zero-length duplicate vertices
- Removes degenerate polylines with fewer than 2 distinct points
- Normalises entity layer to "0"
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, cast

import ezdxf  # type: ignore[attr-defined]
from shapely.geometry import LineString  # type: ignore[import-untyped]

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
)

_CLOSE_TOL = 0.01  # mm — endpoints closer than this are merged to close polyline
_COLINEAR_TOL = 0.001  # mm — max cross-product deviation to treat segment as collinear
_LOG = logging.getLogger(__name__)


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


def _simplify_collinear(
    pts: list[tuple[float, float]], tol: float
) -> list[tuple[float, float]]:
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
) -> list[tuple[float, float]] | None:
    """Apply all fixes to a single polyline.  Returns None to discard."""
    pts = _remove_duplicates(pts)
    if len(pts) < 2:
        return None
    # Close near-open polylines
    if len(pts) >= 3 and pts[0] != pts[-1]:
        if _dist(pts[0], pts[-1]) < _CLOSE_TOL:
            pts = pts + [pts[0]]
    pts = _simplify_collinear(pts, _COLINEAR_TOL)
    if len(pts) < 2:
        return None
    return pts


def fix_dxf(input_path: str | Path, output_path: str | Path) -> dict:
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
    raw, import_report = load_dxf_polylines_with_report(str(input_path))

    stats = {
        "polylines_in": import_report.supported_polylines,
        "polylines_out": 0,
        "closed": 0,
        "simplified": 0,
        "discarded": 0,
        "flattened_entities": sum(import_report.flattened_entities.values()),
        "flattened_entity_summary": None,
        "ignored_entities": import_report.ignored_entities,
        "ignored_entity_summary": summarize_dxf_import_report(import_report),
    }

    if import_report.flattened_entities:
        stats["flattened_entity_summary"] = ", ".join(
            f"{name} × {count}"
            for name, count in import_report.flattened_entities.items()
        )

    fixed: list[list[tuple[float, float]]] = []
    for pts in raw:
        was_closed = len(pts) >= 3 and pts[0] == pts[-1]
        # Honour existing closed flag
        if was_closed and len(pts) >= 2 and pts[0] != pts[-1]:
            pts = pts + [pts[0]]

        orig_len = len(pts)  # captured after optional close-append
        result = _fix_polyline(pts)
        if result is None:
            stats["discarded"] += 1
            continue

        if len(result) < orig_len:
            stats["simplified"] += 1

        closed_now = len(result) >= 3 and result[0] == result[-1]
        if closed_now and not was_closed and len(pts) >= 3 and pts[0] != pts[-1]:
            stats["closed"] += 1

        fixed.append(result)

    # Write output DXF

    out_doc = cast(Any, ezdxf).new("R2010")
    out_msp = out_doc.modelspace()
    for pts in fixed:
        closed = len(pts) >= 3 and pts[0] == pts[-1]
        coords = pts[:-1] if closed else pts
        entity = out_msp.add_lwpolyline(coords)
        entity.closed = closed

    out_doc.saveas(str(output_path))
    stats["polylines_out"] = len(fixed)
    return stats
