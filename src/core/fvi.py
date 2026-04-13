"""FVI → DXF conversion."""

from __future__ import annotations

import math
import re
from pathlib import Path

import ezdxf  # type: ignore[attr-defined]
from ezdxf.math import ConstructionArc  # type: ignore[attr-defined]

_FVI_SCALE = 0.254  # FVI units → mm
# Sagitta tolerance for arc flattening (mm after scaling).
# Smaller = smoother arcs.  0.01 mm is imperceptible at laser resolution.
_ARC_SAGITTA = 0.01 / _FVI_SCALE  # keep in FVI units for consistent tolerance


def _arc_pts(
    x: float,
    y: float,
    ex: float,
    ey: float,
    cx: float,
    cy: float,
) -> list[tuple[float, float]]:
    """Tessellate a DRAWARC into line pts (excluding the start point).

    All args are in FVI units (pre-scale).
    ex, ey = endpoint delta from (x, y)
    cx, cy = center offset from (x, y)
    Returns a list of (world_x, world_y) points in mm, start-exclusive.

    Uses ``ezdxf.math.ConstructionArc.flattening`` for accurate, resolution-aware
    tessellation that correctly handles edge cases like full circles, very short
    sweeps, and degenerate zero-radius inputs.
    """
    center_x, center_y = x + cx, y + cy
    endx, endy = x + ex, y + ey

    r = math.hypot(x - center_x, y - center_y)
    if r < 1e-9:
        return [(endx * _FVI_SCALE, endy * _FVI_SCALE)]

    ang_start = math.degrees(math.atan2(y - center_y, x - center_x))
    ang_end = math.degrees(math.atan2(endy - center_y, endx - center_x))

    # Cross product determines winding: >= 0 → CCW, < 0 → CW
    cross = ex * cy - ey * cx

    if cross >= 0:
        # CCW arc: ezdxf always goes CCW from start_angle to end_angle
        arc = ConstructionArc(
            center=(center_x, center_y),
            radius=r,
            start_angle=ang_start,
            end_angle=ang_end,
        )
    else:
        # CW arc: build the reciprocal CCW arc (end→start), flatten, then reverse
        arc = ConstructionArc(
            center=(center_x, center_y),
            radius=r,
            start_angle=ang_end,
            end_angle=ang_start,
        )

    # flattening(sagitta) auto-computes segment count for smooth curves
    pts = list(arc.flattening(_ARC_SAGITTA))
    if cross < 0:
        pts = list(reversed(pts))

    # pts[0] coincides with the current pen position; skip it and scale to mm
    return [(float(p.x) * _FVI_SCALE, float(p.y) * _FVI_SCALE) for p in pts[1:]]


def convert_fvi_to_dxf(src: Path, dst: Path) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    x = y = 0.0
    pts: list[tuple[float, float]] = []

    _CLOSE_TOL_FVI = 1.0  # mm — treat shape as closed if start≈end within this

    def _flush() -> None:
        if len(pts) < 2:
            return
        p0, p1 = pts[0], pts[-1]
        is_closed = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < _CLOSE_TOL_FVI
        if is_closed:
            # Drop the duplicate closing point and set the DXF close flag
            msp.add_lwpolyline(pts[:-1], close=True)
        else:
            msp.add_lwpolyline(pts)

    with src.open() as f:
        for raw in f:
            ln = raw.strip()
            m = re.match(r"MOVEDIST\s+([-\d.]+),([-\d.]+)", ln)
            if m:
                _flush()
                pts = []
                x += float(m.group(1))
                y += float(m.group(2))
                pts.append((x * _FVI_SCALE, y * _FVI_SCALE))
                continue
            m = re.match(r"DRAWLINE\s+([-\d.]+),([-\d.]+)", ln)
            if m:
                x += float(m.group(1))
                y += float(m.group(2))
                pts.append((x * _FVI_SCALE, y * _FVI_SCALE))
                continue
            m = re.match(r"DRAWARC\s+([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)", ln)
            if m:
                ex = float(m.group(1))
                ey = float(m.group(2))
                cx = float(m.group(3))
                cy = float(m.group(4))
                arc_pts = _arc_pts(x, y, ex, ey, cx, cy)
                pts.extend(arc_pts)
                x += ex
                y += ey
    _flush()
    doc.saveas(str(dst))
