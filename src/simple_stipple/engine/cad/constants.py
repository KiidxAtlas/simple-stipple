"""Centralized geometry and CAD constants.

All geometry tolerances, snap distances, and scale bounds live here as a
single source of truth so that the canvas UI, the pure-Python snapping
logic, and the DXF/FVI/trace pipelines cannot silently drift apart.

Tolerance naming convention
───────────────────────────
* ``EPS`` — general-purpose "two world coordinates are equal" tolerance
  (mm or pixel-space depending on caller).  Used for closure detection,
  near-equality, and duplicate-point culling in the *canvas* coordinate
  space.
* ``*_EPS`` — variant tolerances for specific algorithms (e.g. DXF I/O
  uses tighter/looser values because float round-trips through shapely
  and DXF unit conversion introduce different error budgets).
* ``*_TOL`` — algorithm-specific tolerances that are *not* general-purpose
  equality checks (e.g. trace closure, polyline collinearity).  These may
  differ from ``EPS`` by design.
* ``SNAP_DIST`` — interactive snap radius in screen pixels.
* ``MIN_SCALE`` — minimum zoom scale factor for the canvas view.
"""

from __future__ import annotations

# ── General-purpose geometry tolerances (canvas coordinate space, mm) ────────

#: Two world coordinates are "equal" when their component-wise difference
#: is ≤ this value.  Used for closure detection, near-equality comparisons,
#: and duplicate-point culling in canvas/mm space.
EPS = 1e-6

#: Squared-degenerate-segment threshold.  Segments with length² below this
#: are treated as zero-length (avoids division-by-zero in angle calculations).
EPS_SQ_DEGENERATE = 1e-12

# ── Interactive canvas constants ─────────────────────────────────────────────

#: Snap distance in screen pixels — distance within which the cursor snaps
#: to geometry vertices, edge midpoints, or intersection points.
SNAP_DIST = 14

#: Minimum zoom scale factor.  The canvas will not zoom beyond this factor
#: to prevent numerical instability in rendering and hit-testing.
MIN_SCALE = 1e-6

# ── DXF I/O tolerances (drawing units, after scale conversion) ───────────────

#: Tolerance for detecting that a DXF polyline's first and last points
#: coincide (closure detection).  Tighter than trace tolerance because DXF
#: coordinates are already in drawing units (mm-scaled), but looser than
#: ``EPS`` to absorb float round-trips through shapely.
DXF_CLOSURE_EPS = 1e-4

#: Tolerance for deduplicating consecutive points in DXF polylines.
#: Very tight — only removes true duplicates introduced by shapely
#: coordinate quantization.
DXF_DEDUP_EPS = 1e-9

#: Maximum allowed Z-component for a vector/point to be treated as planar
#: (on the XY plane).  Vectors with |Z| > this are rejected.
DXF_PLANAR_Z_TOLERANCE = 1e-9

#: Minimum outline area in mm² for trace/DXF outline validation.
#: Regions smaller than this are silently ignored rather than reported
#: as "not closed".
OUTLINE_MIN_AREA_MM2 = 0.001

#: Outline close tolerance in mm — distance within which trace/DXF outline
#: endpoints are considered to form a closed loop.
OUTLINE_CLOSE_TOLERANCE_MM = 2.0

# ── Trace pipeline tolerances (pixel space) ──────────────────────────────────

#: Closure tolerance for image trace polylines (pixel space).  Endpoints
#: closer than this are merged to close the polyline.
TRACE_CLOSE_TOL = 0.01

# ── DXF fix pipeline tolerances (mm) ─────────────────────────────────────────

#: Closure tolerance for DXF polyline fixing (mm).  Endpoints closer than
#: this are merged to close the polyline.
DXF_FIX_CLOSE_TOL = 0.01

#: Collinearity tolerance for DXF polyline simplification (mm).  Maximum
#: cross-product deviation to treat a segment as collinear with its neighbor.
DXF_FIX_COLINEAR_TOL = 0.001

# ── FVI (FlexiCam/SignCut) tolerances (mm) ──────────────────────────────────

#: Closure tolerance for FVI path processing (mm).
FVI_CLOSE_TOL_MM = 0.01

# ── Exported symbols ─────────────────────────────────────────────────────────

__all__ = [
    # General-purpose
    "EPS",
    "EPS_SQ_DEGENERATE",
    # Interactive canvas
    "MIN_SCALE",
    "SNAP_DIST",
    # DXF I/O
    "DXF_CLOSURE_EPS",
    "DXF_DEDUP_EPS",
    "DXF_PLANAR_Z_TOLERANCE",
    "OUTLINE_MIN_AREA_MM2",
    "OUTLINE_CLOSE_TOLERANCE_MM",
    # Trace
    "TRACE_CLOSE_TOL",
    # DXF fix
    "DXF_FIX_CLOSE_TOL",
    "DXF_FIX_COLINEAR_TOL",
    # FVI
    "FVI_CLOSE_TOL_MM",
]
