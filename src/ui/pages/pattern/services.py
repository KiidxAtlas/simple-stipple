"""Pattern generation and preview business logic (UI-agnostic)."""

from __future__ import annotations

import hashlib
import logging
import math
from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

from shapely import prepared as _shp_prepared  # type: ignore[import-untyped]

from src.backend.dxf.io import (
    analyze_outline_polylines,
    polylines_to_outline,
)
from src.backend.pattern import (
    apply_border_fade,
    apply_interlace,
    apply_invert_fill,
    apply_mirror,
    get_generator,
)
from src.backend.pattern._shared import _collect_lines as _collect_lines_shared
from src.backend.pattern._shared import _extract_polys as _extract_polys_shared
from src.backend.pattern._shared import (
    _polygon_from_polyline as _polygon_from_polyline_shared,
)
from src.backend.pattern._shared import is_open_polyline as _is_open_polyline_shared
from src.backend.pattern._shared import (
    merge_and_classify_outlines as _merge_and_classify_outlines_shared,
)
from src.ui.pages.pattern._spec import PARAM_SPECS
from src.ui.pages.pattern.fill import (
    NULL_PATTERN,
    FillSpec,
    apply_fill,
    build_fill_region,
)

LOGGER = logging.getLogger(__name__)


def _clip_rotated_element(
    poly: list[tuple[float, float]],
    region: Any,
    prep: Any,
    out: list[list[tuple[float, float]]],
) -> None:
    """Clip a rotated polygon or open line string back to *region*.

    Closed polygons are intersected as areas; open polylines (e.g. diagonal
    line segments) are intersected as line strings so the topology
    is preserved correctly.
    """
    if len(poly) < 2:
        return
    # Try closed-polygon path first (requires ≥3 distinct points and real area).
    if len(poly) >= 3:
        from shapely.geometry import Polygon as _P  # type: ignore[import-untyped]

        pts = list(poly)
        if pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        try:
            s = _P(pts)
            if not s.is_valid:
                s = s.buffer(0)
            if not s.is_empty and s.area > 1e-6:
                if prep.contains(s):
                    out.append(poly)
                elif prep.intersects(s):
                    _extract_polys_shared(region.intersection(s), out)
                return
        except (ValueError, TypeError) as exc:
            LOGGER.debug("Treating invalid area candidate as open linework: %s", exc)
    # Open line string path.
    from shapely.geometry import LineString as _LS  # type: ignore[import-untyped]

    try:
        ls = _LS(poly)
        if not ls.is_empty and prep.intersects(ls):
            _collect_lines_shared(region.intersection(ls), out)
    except (ValueError, TypeError):
        out.append(poly)


class PatternProcessingService:
    """Pure pattern/preview processing helpers used by PatternPage."""

    _OPEN_PATTERNS = {
        "Fish Scale",
        "Topographic",
    }

    # Patterns whose output is a regular grid of discrete tile-shaped polys
    # where row-binning makes sense. For continuous curves (rings, spirals,
    # waves, etc.) interlacing chops them into incoherent strips, so we no-op.
    _INTERLACE_PATTERNS = {
        "Brick",
        "Honeycomb",
        "Gradient Honeycomb",
        "Basketweave",
        "Mesh",
        "Fish Scale",
        "Stipple Dots",
    }
    MAX_ESTIMATED_ELEMENTS = 100_000

    @classmethod
    def estimate_pattern_elements(cls, outline: Any, pattern: str, params: dict) -> int:
        """Conservative pre-generation estimate used to reject runaway jobs."""
        if pattern == NULL_PATTERN or outline is None or outline.is_empty:
            return 0
        minx, miny, maxx, maxy = outline.bounds
        width = max(0.0, maxx - minx)
        height = max(0.0, maxy - miny)
        area = max(float(getattr(outline, "area", width * height)), 0.0)

        def positive(key: str, default: float = 1.0) -> float:
            value = float(params.get(key, default) or default)
            if not math.isfinite(value):
                return max(float(default), 1e-6)
            return max(value, 1e-6)

        if pattern == "Voronoi":
            return max(0, int(params.get("n_cells", 0) or 0))
        if pattern == "Topographic":
            return int(math.hypot(width, height) / positive("spacing")) + 1
        if pattern == "Flow Lines":
            return (
                int(
                    (math.hypot(width, height) + 2 * abs(float(params.get("amplitude", 0))))
                    / positive("spacing")
                )
                + 2
            )
        if pattern in {"Stipple Dots", "Mesh"}:
            return int(area / positive("spacing") ** 2 * 1.5) + 1
        if pattern in {"Honeycomb", "Gradient Honeycomb"}:
            radius = positive("r", positive("r_min", 1.0))
            step = radius + max(float(params.get("gap", 0) or 0), 0.0)
            return int(area / max(step * step * 2.0, 1e-9)) + 1
        if pattern == "Brick":
            cell = (positive("brick_w") + max(float(params.get("gap", 0) or 0), 0.0)) * (
                positive("brick_h") + max(float(params.get("gap", 0) or 0), 0.0)
            )
            return int(area / max(cell, 1e-9) * 1.5) + 1
        if pattern == "Basketweave":
            cell = positive("strip_w") * positive("strip_l")
            return int(area / max(cell, 1e-9) * 2.0) + 1
        if pattern == "Custom Tile":
            points = [point for poly in params.get("tile_polys", []) for point in poly]
            if points:
                tile_w = max(x for x, _y in points) - min(x for x, _y in points)
                tile_h = max(y for _x, y in points) - min(y for _x, y in points)
                gap = max(float(params.get("gap", 0) or 0), 0.0)
                copies = area / max((tile_w + gap) * (tile_h + gap), 1e-9)
                return int(copies * max(len(params.get("tile_polys", [])), 1) * 1.5) + 1
        return 0

    @classmethod
    def validate_pattern_complexity(cls, outline: Any, pattern: str, params: dict) -> int:
        estimate = cls.estimate_pattern_elements(outline, pattern, params)
        if estimate > cls.MAX_ESTIMATED_ELEMENTS:
            raise ValueError(
                f"Estimated {estimate:,} pattern elements exceeds the {cls.MAX_ESTIMATED_ELEMENTS:,} safety limit. "
                "Increase spacing/size or use a smaller outline."
            )
        return estimate

    @staticmethod
    def should_close_pattern(pattern: str) -> bool:
        return pattern not in PatternProcessingService._OPEN_PATTERNS

    @staticmethod
    def apply_scale(
        polys: list[list[tuple[float, float]]],
        sw: float,
        sh: float,
        *,
        orig_w: float,
        orig_h: float,
    ) -> list[list[tuple[float, float]]]:
        if orig_w <= 0 or orig_h <= 0:
            return polys
        if sw <= 0 or sh <= 0:
            return polys
        sx = sw / orig_w
        sy = sh / orig_h
        if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
            return polys
        all_pts = [pt for p in polys for pt in p]
        if not all_pts:
            return polys
        xs, ys = zip(*all_pts)
        ox, oy = min(xs), min(ys)
        return [[(ox + (x - ox) * sx, oy + (y - oy) * sy) for x, y in poly] for poly in polys]

    @staticmethod
    def fresh_outline_ids(count: int) -> list[str]:
        return [uuid4().hex for _ in range(count)]

    @staticmethod
    def _poly_signature(
        poly: list[tuple[float, float]],
    ) -> tuple[tuple[float, float], ...]:
        return tuple((round(x, 6), round(y, 6)) for x, y in poly)

    @staticmethod
    def sync_outline_ids(
        new_polys: list[list[tuple[float, float]]],
        old_polys: list[list[tuple[float, float]]],
        old_ids: list[str],
    ) -> list[str]:
        # Fast-path: identical length and matching signatures in the same
        # order — reuse the existing ids unchanged.
        if len(new_polys) == len(old_ids):
            same_order = True
            for npoly, opoly in zip(new_polys, old_polys):
                if PatternProcessingService._poly_signature(
                    npoly
                ) != PatternProcessingService._poly_signature(opoly):
                    same_order = False
                    break
            if same_order:
                return list(old_ids)

        # Otherwise reconcile by signature so moved/renamed outlines keep IDs
        sig_to_ids: dict[tuple[tuple[float, float], ...], list[str]] = {}
        for poly, oid in zip(old_polys, old_ids):
            sig = PatternProcessingService._poly_signature(poly)
            sig_to_ids.setdefault(sig, []).append(oid)
        resolved: list[str] = []
        for poly in new_polys:
            sig = PatternProcessingService._poly_signature(poly)
            ids = sig_to_ids.get(sig, [])
            if ids:
                resolved.append(ids.pop(0))
            else:
                resolved.append(uuid4().hex)
        return resolved

    @staticmethod
    def resolve_outline_ids(
        ids: list[str],
        outline_ids: list[str],
        edit_polys: list[list[tuple[float, float]]],
    ) -> list[list[tuple[float, float]]]:
        id_map = {oid: poly for oid, poly in zip(outline_ids, edit_polys)}
        resolved: list[list[tuple[float, float]]] = []
        for oid in ids:
            if oid in id_map:
                poly = id_map[oid]
                resolved.append([(float(x), float(y)) for x, y in poly])
        return resolved

    @staticmethod
    def validate_outline_inputs(polys: list[list[tuple[float, float]]]) -> str | None:
        # Use the exact same weld/merge/closure classifier as generation.
        # The general DXF preflight intentionally accepts gaps up to 2 mm,
        # while Pattern requires explicit roles for genuinely open paths.
        closed, open_paths = PatternProcessingService._merge_and_classify_outlines(polys)
        analysis = analyze_outline_polylines(closed)
        if analysis.usable_closed_count <= 0:
            if not closed:
                detail = f" ({len(open_paths)} open path(s) selected)" if open_paths else ""
                raise ValueError(
                    "The selected geometry has no closed boundary"
                    f"{detail}. Select a closed shape or use Close Outline first."
                )
            if analysis.too_small_count:
                raise ValueError(
                    "The selected boundary is closed, but its filled area is too small to pattern."
                )
            raise ValueError(
                "The selected boundary is closed, but it crosses itself or has invalid geometry. "
                "Repair the shape before assigning a zone."
            )
        if open_paths:
            return (
                f"Using {analysis.usable_closed_count} closed outline(s); "
                f"keeping {len(open_paths)} open path(s) as unfilled linework."
            )
        return None

    def snapshot_zone_jobs(
        self,
        zones: list[dict],
        outline_ids: list[str],
        edit_polys: list[list[tuple[float, float]]],
    ) -> tuple[list[dict], list[str]]:
        jobs: list[dict] = []
        warnings: list[str] = []
        for zone in zones:
            zone_outline_ids = [str(v) for v in zone.get("outline_ids", [])]
            resolved = self.resolve_outline_ids(zone_outline_ids, outline_ids, edit_polys)
            if not resolved:
                continue
            warning = self.validate_outline_inputs(resolved)
            if warning:
                warnings.append(warning)
            normalized = deepcopy(zone)
            normalized["pattern"] = str(normalized.get("pattern") or NULL_PATTERN).strip()
            if not normalized["pattern"]:
                normalized["pattern"] = NULL_PATTERN
            normalized.setdefault("params", {})
            jobs.append({**normalized, "polys": resolved})
        if not jobs:
            raise ValueError(
                "No valid closed zone outlines were found. Reassign zones after repairing the outlines."
            )
        return jobs, warnings

    def _gen_pattern(
        self,
        outline,
        pattern: str,
        params: dict,
    ) -> list[list[tuple[float, float]]]:
        params = deepcopy(params)
        multiplier = max(float(params.pop("size_percent", 100.0) or 100.0) / 100.0, 0.01)
        if abs(multiplier - 1.0) > 1e-9:
            for spec in PARAM_SPECS.get(pattern, []):
                key = spec.param_key or spec.attr[1:]
                if "(mm)" in spec.label and isinstance(params.get(key), (int, float)):
                    params[key] = float(params[key]) * multiplier
            if pattern == "Custom Tile" and params.get("tile_polys"):
                points = [point for poly in params["tile_polys"] for point in poly]
                cx = (min(x for x, _y in points) + max(x for x, _y in points)) / 2.0
                cy = (min(y for _x, y in points) + max(y for _x, y in points)) / 2.0
                params["tile_polys"] = [
                    [(cx + (x - cx) * multiplier, cy + (y - cy) * multiplier) for x, y in poly]
                    for poly in params["tile_polys"]
                ]
        self.validate_pattern_complexity(outline, pattern, params)
        # NULL_PATTERN ("— None —") = outline-only mode: no pattern strokes,
        # only the outline (and optional fill) reach the output.
        if pattern == NULL_PATTERN:
            return []

        if pattern == "Honeycomb":
            polys = get_generator("gen_honeycomb")(outline, params["r"], params["gap"])
        elif pattern == "Custom Tile":
            polys = get_generator("gen_custom_tile")(
                outline,
                params["tile_polys"],
                params["gap"],
                0.0,  # global rotation is applied once by build_pattern_polys
                params.get("interlock", False),
                repeat_mode=params.get("repeat_mode", "Straight"),
                origin_x=params.get("origin_x", 0.0),
                origin_y=params.get("origin_y", 0.0),
            )
        elif pattern == "Gradient Honeycomb":
            polys = get_generator("gen_gradient_honeycomb")(
                outline,
                params["r_min"],
                params["r_max"],
                params["gap"],
                params["angle"],
            )
        elif pattern == "Flow Lines":
            polys = get_generator("gen_flow_lines")(
                outline,
                params["spacing"],
                params["amplitude"],
                params["wavelength"],
                params["angle"],
            )
        elif pattern == "Stipple Dots":
            polys = get_generator("gen_stipple_dots")(
                outline,
                params["r"],
                params["spacing"],
                seed=params.get("seed"),
                quality=params.get("quality", "high"),
            )
        elif pattern == "Brick":
            polys = get_generator("gen_brick")(
                outline, params["brick_w"], params["brick_h"], params["gap"]
            )
        elif pattern == "Basketweave":
            polys = get_generator("gen_basketweave")(
                outline, params["strip_w"], params["strip_l"], params["gap"]
            )
        elif pattern == "Mesh":
            polys = get_generator("gen_mesh")(
                outline, params["r"], params["spacing"], quality=params.get("quality", "high")
            )
        elif pattern == "Voronoi":
            polys = get_generator("gen_voronoi")(
                outline, params["n_cells"], params["gap"], params["seed"]
            )
        elif pattern == "Topographic":
            polys = get_generator("gen_topographic")(
                outline, params["spacing"], quality=params.get("quality", "high")
            )
        else:
            raise ValueError(f"Pattern '{pattern}' is no longer available.")

        return self._apply_density_field(
            polys,
            outline,
            mode=params.get("density_mode", "Uniform"),
            strength=float(params.get("density_strength", 0.0) or 0.0),
            angle=float(params.get("density_angle", 0.0) or 0.0),
            reverse=bool(params.get("density_reverse", False)),
        )

    @staticmethod
    def _apply_density_field(
        polys: list[list[tuple[float, float]]],
        outline: Any,
        *,
        mode: str,
        strength: float,
        angle: float = 0.0,
        reverse: bool = False,
    ) -> list[list[tuple[float, float]]]:
        """Deterministically thin elements according to a spatial density field."""
        mode = str(mode or "Uniform").lower()
        strength = max(0.0, min(1.0, float(strength)))
        if mode == "uniform" or strength <= 0 or not polys:
            return polys
        minx, miny, maxx, maxy = outline.bounds
        width = max(maxx - minx, 1e-9)
        height = max(maxy - miny, 1e-9)
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        max_radius = max(math.hypot(width / 2.0, height / 2.0), 1e-9)
        kept: list[list[tuple[float, float]]] = []
        from shapely.geometry import Point as _Point  # type: ignore[import-untyped]

        for poly in polys:
            if not poly:
                continue
            px = sum(point[0] for point in poly) / len(poly)
            py = sum(point[1] for point in poly) / len(poly)
            if mode == "horizontal":
                radians = math.radians(angle)
                dx, dy = math.cos(radians), math.sin(radians)
                corners = ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))
                projections = [x * dx + y * dy for x, y in corners]
                span = max(max(projections) - min(projections), 1e-9)
                field = (px * dx + py * dy - min(projections)) / span
            elif mode == "radial":
                field = 1.0 - min(1.0, math.hypot(px - cx, py - cy) / max_radius)
            elif mode == "boundary":
                field = min(
                    1.0, float(outline.boundary.distance(_Point(px, py))) / (min(width, height) / 2)
                )
            else:
                field = 1.0
            if reverse:
                field = 1.0 - field
            probability = (1.0 - strength) + strength * max(0.0, min(1.0, field))
            digest = hashlib.blake2b(f"{px:.6f},{py:.6f}".encode(), digest_size=8).digest()
            sample = int.from_bytes(digest, "big") / (2**64 - 1)
            if sample <= probability:
                kept.append(poly)
        return kept

    @staticmethod
    def _is_open_polyline(poly: list[tuple[float, float]], tol: float = 0.01) -> bool:
        """Strict open/closed check matching the canvas's own ground truth
        (``PolylineView._is_poly_closed``) — NOT the lenient few-mm tolerance
        ``analyze_outline_polylines``/``polylines_to_outline`` use elsewhere
        for hand-drawn-shape robustness. A deliberately-opened outline (via
        the canvas "Open Outline" action) always has a real, if small, gap;
        this must not be confused with a merely-imprecise closed sketch.
        """
        return _is_open_polyline_shared(poly, tol)

    @staticmethod
    def _merge_and_classify_outlines(
        outline_polys: list[list[tuple[float, float]]],
    ) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
        """Weld + merge end-to-end-connected outline polylines before
        classifying open vs. closed, then return (closed_polys, open_polys).

        Delegates to ``src.backend.pattern._shared.merge_and_classify_outlines``
        — the SAME logic is also needed by the custom-tile generator
        (``gen_custom_tile``), so it lives in the shared backend module and
        this is just a thin wrapper kept for existing call sites here.
        """
        return _merge_and_classify_outlines_shared(outline_polys)

    def build_pattern_polys(
        self,
        outline_polys: list[list[tuple[float, float]]],
        *,
        pattern: str,
        params: dict,
        scale: tuple[float, float],
        orig_w: float,
        orig_h: float,
        interlace: bool = False,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        fill_options: dict | None = None,
        fill_polys_out: list[list[tuple[float, float]]] | None = None,
    ) -> list[list[tuple[float, float]]]:
        # Only closed boundary shapes contribute to the fillable body. Open
        # paths remain neutral linework unless the page explicitly supplies
        # them through ``exclusion_polys`` with the Cutout role. Connected pieces
        # (e.g. an Exploded circle's individual 2-point segments) are first
        # merged back into continuous paths so they can still be recognized
        # as closed/open as a whole, not lost entirely piece-by-piece.
        closed_outline_polys, _open_outline_polys = self._merge_and_classify_outlines(outline_polys)
        scaled = self.apply_scale(
            closed_outline_polys,
            scale[0],
            scale[1],
            orig_w=orig_w,
            orig_h=orig_h,
        )
        orig_outline = polylines_to_outline(scaled)
        outline_poly = cast(Any, orig_outline)
        # The pattern uses the merged outline (existing behavior). The fill
        # region uses even-odd nesting so a donut's inner ring becomes a
        # hole instead of being silently merged into a solid.
        nested_fill_region = build_fill_region(scaled)
        fill_outline = nested_fill_region if nested_fill_region is not None else orig_outline
        all_exclusion_polys = list(exclusion_polys or [])
        if all_exclusion_polys:
            excl_scaled = self.apply_scale(
                all_exclusion_polys,
                scale[0],
                scale[1],
                orig_w=orig_w,
                orig_h=orig_h,
            )
            # build_fill_region (not polylines_to_outline) handles a cutout
            # of ANY gap size via Shapely's implicit ring-closing — an open
            # cutout shouldn't fail to exclude just because its gap exceeds
            # polylines_to_outline's lenient few-mm "closed enough" check.
            excl_outline = build_fill_region(excl_scaled)
            if excl_outline is not None:
                fill_outline = fill_outline.difference(excl_outline)

        # Always generate pattern elements inside the fill region (never
        # outside the outline). When ``invert_fill`` is requested we compute
        # the inverted shapes from the generated pattern (negative space of
        # the pattern union inside the outline) rather than generating in
        # an outer bounding-frame which would produce geometry outside the
        # outline.
        active_region = fill_outline

        # Rotation: rotate active_region inversely around the original outline's
        # centroid, generate in that frame, rotate output forward and clip back.
        rot_deg = float(params.get("rotation", 0.0) or 0.0)
        # Guard: centroid of an empty geometry is itself empty — fall back to
        # the bounding-box centre so we never call .x/.y on an empty Point.
        _centroid = fill_outline.centroid
        if _centroid.is_empty:
            _b = fill_outline.bounds  # (minx, miny, maxx, maxy)
            cx: float = (_b[0] + _b[2]) / 2 if _b else 0.0
            cy: float = (_b[1] + _b[3]) / 2 if _b else 0.0
        else:
            cx = float(_centroid.x)
            cy = float(_centroid.y)
        if abs(rot_deg) > 1e-9:
            from shapely.affinity import (
                rotate as _shp_rot,  # type: ignore[import-untyped]
            )

            gen_outline = _shp_rot(active_region, -rot_deg, origin=(cx, cy))
        else:
            gen_outline = active_region

        polys = self._gen_pattern(gen_outline, pattern, params)

        if abs(rot_deg) > 1e-9:
            # Rotate polys forward to the real coordinate frame.
            rad = rot_deg * math.pi / 180.0
            ca, sa = math.cos(rad), math.sin(rad)
            rotated: list[list[tuple[float, float]]] = [
                [
                    (
                        cx + (x - cx) * ca - (y - cy) * sa,
                        cy + (x - cx) * sa + (y - cy) * ca,
                    )
                    for x, y in poly
                ]
                for poly in polys
            ]
            # Clip each rotated element back to active_region.
            # Handles both closed polygons and open line strings correctly.
            clipped: list[list[tuple[float, float]]] = []
            prep_ar = _shp_prepared.prep(active_region)
            for poly in rotated:
                _clip_rotated_element(poly, active_region, prep_ar, clipped)
            polys = clipped

        # If invert_fill is True, compute the negative-space shapes inside the
        # outline from the generated pattern union so no geometry is produced
        # outside the outline.
        if interlace and pattern in self._INTERLACE_PATTERNS:
            polys = apply_interlace(polys, active_region, spacing=params.get("spacing", 1.0))
        if mirror_v or mirror_h:
            polys = apply_mirror(polys, outline_poly, mirror_v, mirror_h)
        if border_fade > 0:
            polys = apply_border_fade(polys, outline_poly, border_fade)

        if invert_fill:
            try:
                polys = apply_invert_fill(polys, fill_outline)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Invert fill could not build valid negative-space geometry. "
                    "Repair overlapping or self-intersecting pattern cells and try again."
                ) from exc

        # ── Fill (laser infill) ────────────────────────────────────────────
        # The fill region is the input outline minus exclusions — pattern
        # strokes are a pure overlay and do NOT subdivide the fill region.
        # This is the deliberate redesign that replaced the old chamber-
        # cutting algorithm: simpler, predictable, always covers the shape.
        spec = FillSpec.from_dict(fill_options) if fill_options else FillSpec.disabled()
        if spec.enabled:
            fill_strokes: list[list[tuple[float, float]]] = []
            raw_cell_cutouts = list(fill_options.get("cell_cutouts", [])) if fill_options else []
            generated_signatures = {self._poly_signature(poly) for poly in polys}
            raw_cell_cutouts = [
                poly
                for poly in raw_cell_cutouts
                if self._poly_signature(poly) in generated_signatures
            ]
            cell_cutout_signatures = {
                self._poly_signature(poly) for poly in raw_cell_cutouts if len(poly) >= 3
            }
            # Build the cell geometry once. Outline fill uses its complement;
            # pattern fill uses the cells themselves. This makes the two
            # independent toggles partition the outline instead of engraving
            # overlapping hatch passes over the same area.
            cell_shapes: list[tuple[Any, str | None]] = []
            pending_lines: list[list[tuple[float, float]]] = []
            try:
                for poly in polys:
                    shp = _polygon_from_polyline_shared(poly, force_close=False)
                    if shp is not None:
                        cell_shapes.append((shp, self._poly_signature(poly)))
                    elif len(poly) >= 2:
                        pending_lines.append(poly)

                if pending_lines:
                    from shapely.geometry import (  # type: ignore[import-untyped]
                        LineString as _LS,
                    )
                    from shapely.ops import (  # type: ignore[import-untyped]
                        polygonize,
                        unary_union,
                    )

                    lines = [_LS(poly) for poly in pending_lines]
                    for shp in polygonize(unary_union(lines)):
                        clipped = shp.intersection(fill_outline)
                        if not clipped.is_empty:
                            cell_shapes.append((clipped, None))
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Pattern-cell fill failed because one or more generated cells are invalid."
                ) from exc

            if spec.target_outline:
                try:
                    outline_fill_region = fill_outline
                    if cell_shapes:
                        from shapely.ops import unary_union  # type: ignore[import-untyped]

                        outline_fill_region = outline_fill_region.difference(
                            unary_union([shape for shape, _signature in cell_shapes])
                        )
                    fill_strokes.extend(apply_fill(outline_fill_region, spec))
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        "Outline fill failed because the fill boundary is invalid."
                    ) from exc

            if spec.target_pattern:
                try:
                    for shape, signature in cell_shapes:
                        if signature is not None and signature in cell_cutout_signatures:
                            continue
                        fill_strokes.extend(apply_fill(shape, spec))
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        "Pattern-cell fill failed because one or more generated cells are invalid."
                    ) from exc

            if fill_polys_out is not None:
                fill_polys_out.extend(fill_strokes)
                if not spec.keep_pattern:
                    polys = []
            else:
                base = polys if spec.keep_pattern else []
                polys = base + fill_strokes
        return polys

    @staticmethod
    def _floating_open_cutouts(
        zones: list[dict],
        all_polys: list[list[tuple[float, float]]] | None,
    ) -> list[list[tuple[float, float]]]:
        """Outlines present on the canvas but NOT assigned to any zone,
        that are genuinely OPEN (after merging any exploded pieces back
        together) — these should still act as automatic cutouts for every
        zone, the same "open shape = cutout" rule the non-zone workflow
        already applies, even though Zones otherwise require each outline
        to be explicitly assigned. Safe to hand to every zone unconditionally:
        subtracting a cutout that doesn't spatially overlap a given zone's
        fill region is simply a no-op for that zone.
        """
        if not all_polys:
            return []
        assigned_sigs = {
            PatternProcessingService._poly_signature(p)
            for zone in zones
            for p in zone.get("polys", [])
        }
        unassigned = [
            p for p in all_polys if PatternProcessingService._poly_signature(p) not in assigned_sigs
        ]
        if not unassigned:
            return []
        _closed, open_ = PatternProcessingService._merge_and_classify_outlines(unassigned)
        return open_

    @staticmethod
    def _zone_nested_exclusions(
        zones: list[dict], target_idx: int
    ) -> list[list[tuple[float, float]]]:
        """Other zones' shapes that sit inside THIS zone's own outline.

        Zones are generated independently, so an outer zone has no idea an
        inner zone's shape exists inside it — its pattern fill would cover
        the whole outline, including the differently-patterned inner
        shape's area, and visually overlay it. Treating any other zone's
        outline as an automatic cutout when it's geometrically contained in
        this zone mirrors the existing "open shape = cutout" convention.
        """
        target_polys = zones[target_idx].get("polys") or []
        if not target_polys:
            return []
        try:
            target_region = build_fill_region(target_polys)
        except (ValueError, TypeError):
            return []
        if target_region is None or target_region.is_empty:
            return []
        prep = _shp_prepared.prep(target_region)

        from shapely.geometry import Polygon as _Poly

        nested: list[list[tuple[float, float]]] = []
        for other_idx, other in enumerate(zones):
            if other_idx == target_idx:
                continue
            for poly in other.get("polys") or []:
                if len(poly) < 3:
                    continue
                try:
                    shp = _Poly(poly)
                    if not shp.is_valid:
                        shp = shp.buffer(0)
                    if shp.is_empty:
                        continue
                    # Full containment of the OTHER shape within this
                    # zone's region — not just a point test, which would
                    # false-positive whenever a much larger shape's
                    # representative point happens to land inside a small
                    # nested zone (e.g. the outer outline's own centroid
                    # sitting inside a small inner zone near the middle).
                    if not prep.contains(shp):
                        continue
                except (ValueError, TypeError):
                    continue
                nested.append(poly)
        return nested

    def build_zone_pattern_polys(
        self,
        zones: list[dict],
        *,
        include_border: bool,
        orig_w: float,
        orig_h: float,
        all_polys: list[list[tuple[float, float]]] | None = None,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        fill_options: dict | None = None,
        fill_polys_out: list | None = None,
    ) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
        # Open paths are neutral unless explicitly supplied as exclusions.
        floating_cutouts: list[list[tuple[float, float]]] = []
        all_polys_out: list[list[tuple[float, float]]] = []
        border_polys: list[list[tuple[float, float]]] = []
        for idx, zone in enumerate(zones):
            output_mode = str(zone.get("output_mode", "pattern_fill"))
            if output_mode == "none":
                continue
            zone_fill: list[list[tuple[float, float]]] = []
            # Presence, rather than truthiness, controls inheritance.  An
            # explicit None means "no fill in this zone"; older workspaces
            # without a fill key continue to inherit the document fill.
            zone_fill_options = zone["fill"] if "fill" in zone else fill_options
            zone_pattern = str(zone.get("pattern") or NULL_PATTERN).strip() or NULL_PATTERN
            if output_mode == "pattern":
                zone_fill_options = None
            elif output_mode == "fill":
                zone_pattern = NULL_PATTERN
            elif output_mode == "outline":
                zone_pattern = NULL_PATTERN
                zone_fill_options = None
            nested_exclusions = self._zone_nested_exclusions(zones, idx)
            zone_generated = self.build_pattern_polys(
                zone["polys"] + floating_cutouts,
                pattern=zone_pattern,
                params=zone["params"],
                scale=zone["scale"],
                orig_w=orig_w,
                orig_h=orig_h,
                interlace=zone.get("interlace", False),
                invert_fill=invert_fill,
                mirror_v=mirror_v,
                mirror_h=mirror_h,
                border_fade=border_fade,
                exclusion_polys=list(exclusion_polys or []) + nested_exclusions,
                fill_options=zone_fill_options,
                fill_polys_out=zone_fill if fill_polys_out is not None else None,
            )
            all_polys_out.extend(zone_generated)
            if fill_polys_out is not None and zone_fill:
                fill_polys_out.extend(zone_fill)
            # Always collect the outline so the export can ship it as a
            # dedicated layer; include_border now only controls whether the
            # outline is also baked into the pattern stream for legacy
            # consumers.
            border_polys.extend(
                self.apply_scale(
                    zone["polys"],
                    zone["scale"][0],
                    zone["scale"][1],
                    orig_w=orig_w,
                    orig_h=orig_h,
                )
            )
        # Floating (unassigned) open cutouts aren't scoped to any single
        # zone's scale, so they're included in the border/outline layer
        # as-is (raw coordinates) — they're still visible even though they
        # were used as a cutout mask above, not because they were assigned.
        border_polys.extend(floating_cutouts)
        return all_polys_out, border_polys

    def build_preview_polys(
        self,
        outline_polys: list[list[tuple[float, float]]],
        *,
        pattern: str,
        params: dict,
        scale: tuple[float, float],
        orig_w: float,
        orig_h: float,
        border_polys: list[list[tuple[float, float]]] | None,
        interlace: bool = False,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        fill_options: dict | None = None,
    ) -> dict[str, Any]:
        """Generate a preview payload split by DXF-layer category.

        Returns a dict with three lists matching the export layer split:
          - ``outline``: scaled outline polylines (always present so the
            layer tree can show an Outline row even when the border-export
            checkbox is off).
          - ``pattern``: pattern strokes / closed face polygons.
          - ``fill``: hatch fill strokes (empty if fill mode is "none").
          - ``count``: total renderable shapes (excludes outline if
            ``border_polys`` is None — matches legacy display count).
          - ``display``: flat ordered list for canvas rendering.
        """
        fill_buf: list[list[tuple[float, float]]] = []
        pattern_polys = self.build_pattern_polys(
            outline_polys,
            pattern=pattern,
            params=params,
            scale=scale,
            orig_w=orig_w,
            orig_h=orig_h,
            interlace=interlace,
            invert_fill=invert_fill,
            mirror_v=mirror_v,
            mirror_h=mirror_h,
            border_fade=border_fade,
            exclusion_polys=exclusion_polys,
            fill_options=fill_options,
            fill_polys_out=fill_buf,
        )
        # Always supply a scaled outline so the layer tree has something to
        # show in its Outline row even when the user did not enable the
        # "include border" export option.
        # Always return a scaled outline representation. If `border_polys`
        # were supplied by the caller assume they should be scaled to match
        # the requested preview scale; otherwise scale the source outline.
        outline_scaled = (
            self.apply_scale(border_polys, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h)
            if border_polys is not None
            else self.apply_scale(outline_polys, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h)
        )
        # Display layering matches what the user expects to see:
        # outline (background) → pattern → fill (on top).
        display_polys = (outline_scaled or []) + pattern_polys + fill_buf
        return {
            "outline": outline_scaled,
            "pattern": pattern_polys,
            "fill": fill_buf,
            "display": display_polys,
            "count": len(pattern_polys) + len(fill_buf),
        }

    def build_preview_zone_polys(
        self,
        zones: list[dict],
        all_polys: list[list[tuple[float, float]]],
        *,
        orig_w: float,
        orig_h: float,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        fill_options: dict | None = None,
    ) -> dict[str, Any]:
        zone_poly_ids: set[int] = set()
        zone_pattern_polys: list[list[tuple[float, float]]] = []
        zone_fill_polys: list[list[tuple[float, float]]] = []
        zone_outline_polys: list[list[tuple[float, float]]] = []
        pattern_owners: list[int] = []
        fill_owners: list[int] = []
        outline_owners: list[int] = []
        floating_cutouts: list[list[tuple[float, float]]] = []

        for zone_idx, zone in enumerate(zones):
            output_mode = str(zone.get("output_mode", "pattern_fill"))
            fill_buf: list[list[tuple[float, float]]] = []
            zone_fill_options = zone["fill"] if "fill" in zone else fill_options
            zone_pattern = str(zone.get("pattern") or NULL_PATTERN).strip() or NULL_PATTERN
            if output_mode == "pattern":
                zone_fill_options = None
            elif output_mode == "fill":
                zone_pattern = NULL_PATTERN
            elif output_mode in {"outline", "none"}:
                zone_pattern = NULL_PATTERN
                zone_fill_options = None
            nested_exclusions = self._zone_nested_exclusions(zones, zone_idx)
            zone_generated = [] if output_mode == "none" else self.build_pattern_polys(
                    zone["polys"] + floating_cutouts,
                    pattern=zone_pattern,
                    params=zone["params"],
                    scale=zone["scale"],
                    orig_w=orig_w,
                    orig_h=orig_h,
                    interlace=zone.get("interlace", False),
                    invert_fill=invert_fill,
                    mirror_v=mirror_v,
                    mirror_h=mirror_h,
                    border_fade=border_fade,
                    exclusion_polys=list(exclusion_polys or []) + nested_exclusions,
                    fill_options=zone_fill_options,
                    fill_polys_out=fill_buf,
            )
            zone_pattern_polys.extend(zone_generated)
            zone_fill_polys.extend(fill_buf)
            pattern_owners.extend([zone_idx] * len(zone_generated))
            fill_owners.extend([zone_idx] * len(fill_buf))
            scaled_zone_outlines = self.apply_scale(
                zone["polys"], zone["scale"][0], zone["scale"][1],
                orig_w=orig_w, orig_h=orig_h,
            )
            zone_outline_polys.extend(scaled_zone_outlines)
            outline_owners.extend([zone_idx] * len(scaled_zone_outlines))
            # Match zone polys to the global all_polys list using
            # rounded signatures to tolerate minor numeric differences
            # introduced by transforms. We pop indices as they are used to
            # support duplicated polygons.
            sig_to_indices: dict[tuple[tuple[float, float], ...], list[int]] = {}
            for idx, cp in enumerate(all_polys):
                sig = self._poly_signature(cp)
                sig_to_indices.setdefault(sig, []).append(idx)
            for zp in zone["polys"]:
                sig = self._poly_signature(zp)
                indices = sig_to_indices.get(sig)
                if indices:
                    zone_poly_ids.add(indices.pop(0))

        context_polys = [p for i, p in enumerate(all_polys) if i not in zone_poly_ids]
        outline_polys = context_polys + zone_outline_polys
        display_polys = outline_polys + zone_pattern_polys + zone_fill_polys
        zone_owners: list[int | None] = (
            [None] * len(context_polys)
            + outline_owners
            + pattern_owners
            + fill_owners
        )
        return {
            "outline": outline_polys,
            "pattern": zone_pattern_polys,
            "fill": zone_fill_polys,
            "display": display_polys,
            "count": len(zone_pattern_polys) + len(zone_fill_polys),
            "zone_owners": zone_owners,
        }
