"""Fill (laser-infill) data model + core renderer.

This module replaces the old ``_generate_fill_via_polygonize`` chamber-cutting
algorithm. The new model is intentionally simple and reliable:

  * The fill region is the **input outline** (minus exclusions / cutouts),
    NOT the chambers carved by pattern strokes.
  * Pattern strokes are rendered on top as a pure overlay; they no longer
    influence fill geometry.
  * Per-shape / per-zone fill is opt-in via a :class:`FillSpec` attached to
    each pattern zone.

A pattern named :data:`NULL_PATTERN` (the existing UI label "— None —")
means "outline only, no pattern". Combined with a ``FillSpec`` this gives
the user "outline + fill" with no pattern overlay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# The pattern combo entry that means "no pattern" — outline only.
# Reuses the existing UI label so we don't have to migrate state.
NULL_PATTERN = "— None —"

FillMode = Literal["none", "lines", "crosshatch"]
_VALID_MODES: frozenset[str] = frozenset({"none", "lines", "crosshatch"})


@dataclass(frozen=True)
class FillSpec:
    """Declarative description of how to fill a region with laser strokes.

    Supported modes: ``"lines"`` (parallel hatch) and ``"crosshatch"``
    (two sets of parallel lines at different angles).
    """

    mode: FillMode = "none"
    spacing: float = 1.0
    angle_deg: float = 0.0
    keep_pattern: bool = True  # if False, drop pattern strokes from output
    target_outline: bool = False  # fill the input outline region
    target_pattern: bool = True  # fill the closed pattern strokes
    inset: float = 0.0  # shrink fill region by this many mm before hatching

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"FillSpec.mode must be one of {sorted(_VALID_MODES)!r}, "
                f"got {self.mode!r}"
            )
        if self.spacing <= 0:
            raise ValueError(f"FillSpec.spacing must be > 0 (got {self.spacing!r})")
        if self.inset < 0:
            raise ValueError(f"FillSpec.inset must be >= 0 (got {self.inset!r})")

    @classmethod
    def disabled(cls) -> FillSpec:
        return cls(mode="none")

    @property
    def enabled(self) -> bool:
        return self.mode != "none"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FillSpec:
        """Build a FillSpec from a plain dict.

        Accepts both the new schema and the legacy ``fill_options`` dict
        (``angle`` instead of ``angle_deg``, ``keep_outline`` instead of
        ``keep_pattern``). A None/empty dict yields a disabled spec.
        """
        if not data:
            return cls.disabled()
        mode = str(data.get("mode", "none") or "none")
        # Legacy modes we no longer support → fall back to lines so the user
        # still gets *some* fill, with a deprecation in the project log.
        if mode in {"racecar", "concentric"}:
            mode = "lines"
        if mode not in _VALID_MODES:
            mode = "none"
        spacing = float(data.get("spacing", 1.0) or 1.0)
        if spacing <= 0:
            spacing = 1.0
        angle_deg = float(data.get("angle_deg", data.get("angle", 0.0)) or 0.0)
        keep_pattern = bool(data.get("keep_pattern", data.get("keep_outline", True)))
        # Default targets: fill the pattern cells, not the outline — most
        # users pattern-fill wanting the individual repeated cells engraved,
        # not a single solid region.
        target_outline = bool(data.get("target_outline", False))
        target_pattern = bool(data.get("target_pattern", True))
        inset = float(data.get("inset", 0.0) or 0.0)
        if inset < 0:
            inset = 0.0
        return cls(
            mode=mode,  # type: ignore[arg-type]
            spacing=spacing,
            angle_deg=angle_deg,
            keep_pattern=keep_pattern,
            target_outline=target_outline,
            target_pattern=target_pattern,
            inset=inset,
        )


# ────────────────────────────────────────────────────────────────────────────
# Core fill renderer
# ────────────────────────────────────────────────────────────────────────────


def build_fill_region(polylines: list[list[tuple[float, float]]]) -> Any:
    """Build a fill region that respects nested outlines as holes.

    Given a list of closed polylines, returns a Shapely (Multi)Polygon
    where polylines fully contained inside another polyline become holes
    (even-odd nesting, the standard SVG/DXF convention).

    A plain ``unary_union`` would merge nested rings into a single solid
    region — that's why a donut used to fill solid through the hole.

    Returns ``None`` if there are no usable closed rings.

    Delegates to ``src.backend.generators._shared.nested_polygon_region`` —
    the SAME even-odd nesting logic is also needed by the custom-tile
    generator, so it lives in the shared backend module; this wrapper is
    kept for existing call sites here.
    """
    from src.backend.generators._shared import (
        nested_polygon_region as _nested_polygon_region_shared,
    )

    return _nested_polygon_region_shared(polylines)


def apply_fill(
    region_geom: Any,
    spec: FillSpec,
) -> list[list[tuple[float, float]]]:
    """Generate fill strokes for ``region_geom`` according to ``spec``.

    ``region_geom`` is a Shapely (Multi)Polygon — typically the input outline
    minus any exclusion regions. The caller is responsible for subtracting
    cutouts / exclusion polygons before calling.

    Returns a flat list of polylines (each a list of ``(x, y)`` tuples).
    Returns ``[]`` for disabled specs, empty regions, or unsupported modes.
    """
    if not spec.enabled:
        return []
    if region_geom is None:
        return []
    try:
        if region_geom.is_empty:
            return []
    except AttributeError:
        return []

    # Optional inset — shrink the fill region. ``buffer(-d)`` may collapse
    # narrow shapes to empty; that's the user's signal to lower the inset.
    if spec.inset > 0:
        region_geom = region_geom.buffer(-spec.inset)
        if region_geom is None or region_geom.is_empty:
            return []

    if spec.mode == "lines":
        return _fill_lines(region_geom, spec.spacing, spec.angle_deg)
    if spec.mode == "crosshatch":
        return _fill_crosshatch(region_geom, spec.spacing, spec.angle_deg)
    return []


def _fill_lines(
    region_geom: Any,
    spacing: float,
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Parallel-hatch fill: clip a sweep of straight lines to the region.

    The lines are generated in a frame rotated by ``-angle_deg`` (so a
    horizontal sweep at angle 0 becomes vertical at angle 90, etc.), then
    rotated back into world space.
    """
    import math

    from shapely.affinity import rotate as _shp_rotate  # type: ignore[import-untyped]
    from shapely.geometry import (  # type: ignore[import-untyped]
        LineString as _LS,
    )
    from shapely.geometry import (
        MultiLineString as _MLS,
    )
    from shapely.geometry import (
        MultiPolygon as _MP,
    )
    from shapely.geometry import (
        Polygon as _Poly,
    )

    # Rotate the region into a frame where hatch is horizontal. This avoids
    # having to rotate every emitted line.
    pivot = region_geom.centroid
    px, py = float(pivot.x), float(pivot.y)
    rotated = (
        _shp_rotate(region_geom, -angle_deg, origin=(px, py))
        if abs(angle_deg) > 1e-9
        else region_geom
    )

    minx, miny, maxx, maxy = rotated.bounds
    if not math.isfinite(minx + miny + maxx + maxy):
        return []

    # Pad a touch so the very-edge lines clip cleanly.
    pad = max(spacing, 1e-3)
    y0 = miny + spacing * 0.5
    if y0 > maxy:
        return []

    out_rotated: list[list[tuple[float, float]]] = []
    y = y0
    while y <= maxy + 1e-9:
        sweep = _LS([(minx - pad, y), (maxx + pad, y)])
        try:
            clipped = rotated.intersection(sweep)
        except (ValueError, TypeError):
            y += spacing
            continue
        if clipped.is_empty:
            y += spacing
            continue
        if isinstance(clipped, _LS):
            coords = list(clipped.coords)
            if len(coords) >= 2:
                out_rotated.append([(float(x), float(yy)) for x, yy in coords])
        elif isinstance(clipped, _MLS):
            for seg in clipped.geoms:
                coords = list(seg.coords)
                if len(coords) >= 2:
                    out_rotated.append([(float(x), float(yy)) for x, yy in coords])
        # GeometryCollection fallback (rare — degenerate intersection)
        elif hasattr(clipped, "geoms"):
            for g in clipped.geoms:
                if isinstance(g, _LS):
                    coords = list(g.coords)
                    if len(coords) >= 2:
                        out_rotated.append([(float(x), float(yy)) for x, yy in coords])
        y += spacing

    if not out_rotated:
        return []

    # Silence "polygon used but never imported" lint by referencing types
    _ = (_Poly, _MP)

    if abs(angle_deg) > 1e-9:
        rad = math.radians(angle_deg)
        ca, sa = math.cos(rad), math.sin(rad)
        return [
            [
                (
                    px + (x - px) * ca - (yy - py) * sa,
                    py + (x - px) * sa + (yy - py) * ca,
                )
                for x, yy in line
            ]
            for line in out_rotated
        ]
    return out_rotated


def _fill_crosshatch(
    region_geom: Any,
    spacing: float,
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Crosshatch fill: two sets of parallel lines at ±45° to the base angle.

    The crosshatch pattern is created by generating two sets of parallel
    hatch lines — one rotated +45° and one rotated -45° from the base
    ``angle_deg`` — and combining them.  This gives the classic diagonal
    crosshatch look used in many laser-engraving applications.
    """
    lines_1 = _fill_lines(region_geom, spacing, angle_deg + 45.0)
    lines_2 = _fill_lines(region_geom, spacing, angle_deg - 45.0)
    return lines_1 + lines_2


def _fill_crosshatch(
    region_geom: Any,
    spacing: float,
    angle_deg: float,
) -> list[list[tuple[float, float]]]:
    """Crosshatch fill: two sets of parallel lines at ±45° to the base angle.

    The crosshatch pattern is created by generating two sets of parallel
    hatch lines — one rotated +45° and one rotated -45° from the base
    ``angle_deg`` — and combining them.  This gives the classic diagonal
    crosshatch look used in many laser-engraving applications.
    """
    lines_1 = _fill_lines(region_geom, spacing, angle_deg + 45.0)
    lines_2 = _fill_lines(region_geom, spacing, angle_deg - 45.0)
    return lines_1 + lines_2
