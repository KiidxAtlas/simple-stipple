"""Pattern generation and preview business logic (UI-agnostic)."""

from __future__ import annotations

import logging
import math
from typing import Any, cast
from uuid import uuid4

from shapely import prepared as _shp_prepared  # type: ignore[import-untyped]

from src.backend.dxf.io import (
    analyze_outline_polylines,
    load_dxf_polylines,
    polylines_to_outline,
)
from src.backend.generators import (
    apply_border_fade,
    apply_interlace,
    apply_invert_fill,
    apply_mirror,
    get_generator,
)
from src.backend.generators._shared import _collect_lines as _collect_lines_shared
from src.backend.generators._shared import _extract_polys as _extract_polys_shared
from src.backend.generators._shared import (
    _polygon_from_polyline as _polygon_from_polyline_shared,
)
from src.backend.generators._shared import is_open_polyline as _is_open_polyline_shared
from src.backend.generators._shared import (
    merge_and_classify_outlines as _merge_and_classify_outlines_shared,
)
from src.backend.generators._shared import (
    nested_polygon_region as _nested_polygon_region_shared,
)
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

    Closed polygons are intersected as areas; open polylines (e.g. Hilbert Curve
    segments, diagonal lines) are intersected as line strings so the topology
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
        except Exception:
            pass
    # Open line string path.
    from shapely.geometry import LineString as _LS  # type: ignore[import-untyped]

    try:
        ls = _LS(poly)
        if not ls.is_empty and prep.intersects(ls):
            _collect_lines_shared(region.intersection(ls), out)
    except Exception:
        out.append(poly)


class PatternProcessingService:
    """Pure pattern/preview processing helpers used by PatternPage."""

    _OPEN_PATTERNS = {
        "Fish Scale",
        "Diagonal Lines",
        "Square Grid",
        "Concentric Rings",
        "Wave Fill",
        "Sunburst",
        "Topographic",
        "Hilbert Curve",
        "Reaction Diffuse",
        "Golden Spiral",
        "Rose Curve",
    }

    # Patterns whose output is a regular grid of discrete tile-shaped polys
    # where row-binning makes sense. For continuous curves (rings, spirals,
    # waves, etc.) interlacing chops them into incoherent strips, so we no-op.
    _INTERLACE_PATTERNS = {
        "Brick",
        "Honeycomb",
        "Gradient Honeycomb",
        "Basketweave",
        "Square Grid",
        "Mesh",
        "Fish Scale",
        "Stipple Dots",
    }

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
        return [
            [(ox + (x - ox) * sx, oy + (y - oy) * sy) for x, y in poly]
            for poly in polys
        ]

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
        analysis = analyze_outline_polylines(polys)
        if analysis.usable_closed_count <= 0:
            raise ValueError(
                "No valid closed outline was found. Close or repair the outline before generating a pattern."
            )
        if analysis.open_count > 0:
            return (
                f"Using {analysis.usable_closed_count} closed outline(s); "
                f"ignoring {analysis.open_count} open outline(s)."
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
            resolved = self.resolve_outline_ids(
                zone_outline_ids, outline_ids, edit_polys
            )
            if not resolved:
                continue
            warning = self.validate_outline_inputs(resolved)
            if warning:
                warnings.append(warning)
            jobs.append({**zone, "polys": resolved})
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
        # NULL_PATTERN ("— None —") = outline-only mode: no pattern strokes,
        # only the outline (and optional fill) reach the output.
        if pattern == NULL_PATTERN:
            return []

        rot_deg = float(params.get("rotation", 0.0) or 0.0)

        if pattern == "Honeycomb":
            polys = get_generator("gen_honeycomb")(outline, params["r"], params["gap"])
        elif pattern == "Gradient Honeycomb":
            polys = get_generator("gen_gradient_honeycomb")(
                outline,
                params["r_min"],
                params["r_max"],
                params["gap"],
                params["angle"],
            )
        elif pattern == "Basketweave":
            polys = get_generator("gen_basketweave")(
                outline, params["strip_w"], params["strip_l"], params["gap"]
            )
        elif pattern == "Braid":
            polys = get_generator("gen_braid")(
                outline, params["strip_width"], params["spacing"]
            )
        elif pattern == "Fish Scale":
            polys = get_generator("gen_fish_scale")(outline, params["sw"], params["sh"])
        elif pattern == "Stipple Dots":
            if params.get("interlaced"):
                polys = get_generator("gen_stipple_interlaced")(
                    outline,
                    params["r"],
                    params["spacing"],
                )
            else:
                polys = get_generator("gen_stipple_dots")(
                    outline,
                    params["r"],
                    params["spacing"],
                )
        elif pattern == "Brick":
            polys = get_generator("gen_brick")(
                outline, params["brick_w"], params["brick_h"], params["gap"]
            )
        elif pattern == "Diagonal Lines":
            polys = get_generator("gen_diagonal_lines")(
                outline,
                params["spacing"],
                params["angle"],
            )
        elif pattern == "Square Grid":
            polys = get_generator("gen_square_grid")(outline, params["spacing"])
        elif pattern == "Mesh":
            polys = get_generator("gen_mesh")(outline, params["r"], params["spacing"])
        elif pattern == "Concentric Rings":
            polys = get_generator("gen_concentric_rings")(outline, params["spacing"])
        elif pattern == "Wave Fill":
            polys = get_generator("gen_wave_fill")(
                outline, params["spacing"], params["amplitude"], params["wavelength"]
            )
        elif pattern == "Sunburst":
            polys = get_generator("gen_sunburst")(outline, params["spacing_deg"])
        elif pattern == "Voronoi":
            polys = get_generator("gen_voronoi")(
                outline, params["n_cells"], params["gap"], params["seed"]
            )
        elif pattern == "Penrose Tiling":
            polys = get_generator("gen_penrose_tiling")(
                outline,
                params["scale"],
                params["gap"],
            )
        elif pattern == "Topographic":
            polys = get_generator("gen_topographic")(outline, params["spacing"])
        elif pattern == "Hilbert Curve":
            polys = get_generator("gen_hilbert_curve")(
                outline,
                params["order"],
                params["margin"],
            )
        elif pattern == "Reaction Diffuse":
            polys = get_generator("gen_reaction_diffuse")(
                outline,
                params["cell"],
                params["iters"],
                params["threshold"],
                params["seed"],
                params.get("pattern", "labyrinth"),
            )
        elif pattern == "Celtic Knot":
            polys = get_generator("gen_celtic_knot")(
                outline,
                params["cell_size"],
                params["line_width"],
                params["gap"],
            )
        elif pattern == "Lissajous":
            polys = get_generator("gen_lissajous")(
                outline,
                params["freq_x"],
                params["freq_y"],
                params["spacing"],
                params["amplitude"],
            )
        elif pattern == "Golden Spiral":
            polys = get_generator("gen_golden_spiral")(
                outline,
                params["turns"],
                params["spacing_mm"],
                params["direction"],
            )
        elif pattern == "Rose Curve":
            polys = get_generator("gen_rose_curve")(
                outline,
                params["petals"],
                params["copies"],
                params["margin_mm"],
            )
        elif params.get("tile_path"):
            tile_polys = load_dxf_polylines(params["tile_path"])
            polys = get_generator("gen_custom_tile")(
                outline,
                tile_polys,
                params["gap"],
                params["angle"],
                params.get("interlock", False),
            )
        else:
            polys = get_generator("gen_image_halftone")(
                outline,
                params["img_path"],
                params["r_min"],
                params["r_max"],
                params["spacing"],
                params["invert"],
            )

        if abs(rot_deg) > 1e-9:
            pass  # rotation is applied in build_pattern_polys via outline transform
        return polys

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

        Delegates to ``src.backend.generators._shared.merge_and_classify_outlines``
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
        # An OPEN outline shape acts as an automatic cutout — same treatment
        # as an explicitly marked exclusion/cutout region (user request: "an
        # open shape should work similar to a cutout"). Only genuinely CLOSED
        # shapes contribute to the fillable body/outline. Connected pieces
        # (e.g. an Exploded circle's individual 2-point segments) are first
        # merged back into continuous paths so they can still be recognized
        # as closed/open as a whole, not lost entirely piece-by-piece.
        closed_outline_polys, open_outline_polys = self._merge_and_classify_outlines(
            outline_polys
        )
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
        fill_outline = (
            nested_fill_region if nested_fill_region is not None else orig_outline
        )
        all_exclusion_polys = list(exclusion_polys or []) + open_outline_polys
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
            from shapely.affinity import rotate as _shp_rot  # type: ignore[import-untyped]

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
            polys = apply_interlace(
                polys, active_region, spacing=params.get("spacing", 1.0)
            )
        if mirror_v or mirror_h:
            polys = apply_mirror(polys, outline_poly, mirror_v, mirror_h)
        if border_fade > 0:
            polys = apply_border_fade(polys, outline_poly, border_fade)

        if invert_fill:
            try:
                polys = apply_invert_fill(polys, fill_outline)
            except Exception:
                # Fail safe: if inversion fails, fall back to the original
                # generated polys (but they will still be clipped to the
                # active region from rotation/clip steps above).
                pass

        # ── Fill (laser infill) ────────────────────────────────────────────
        # The fill region is the input outline minus exclusions — pattern
        # strokes are a pure overlay and do NOT subdivide the fill region.
        # This is the deliberate redesign that replaced the old chamber-
        # cutting algorithm: simpler, predictable, always covers the shape.
        spec = FillSpec.from_dict(fill_options) if fill_options else FillSpec.disabled()
        if spec.enabled:
            fill_strokes: list[list[tuple[float, float]]] = []
            # Target the outline region when requested.
            if spec.target_outline:
                try:
                    fill_strokes.extend(apply_fill(fill_outline, spec))
                except Exception:
                    # Fail safe: skip outline fill on errors.
                    LOGGER.exception("Outline fill failed")

            # Target closed pattern polygons when requested. Use the final
            # generated `polys` (which may already reflect invert_fill) as the
            # source geometry for pattern-targeted fills.
            if spec.target_pattern and polys:
                try:
                    # Collect closed cells first. For "Custom Tile" patterns
                    # specifically, combine them via even-odd nesting
                    # (nested_polygon_region) before hatching — filling each
                    # closed cell independently would also hatch a HOLE ring
                    # (the cutout left by an Exploded/open shape inside the
                    # tile, see gen_custom_tile) as if it were its own solid
                    # area, since a hole ring is just another closed polyline
                    # once it reaches this point with no memory of why it's
                    # shaped that way.
                    #
                    # This must NOT be applied to every pattern though: many
                    # patterns (Topographic, Concentric Rings, Golden Spiral,
                    # Sunburst, …) legitimately produce MANY nested closed
                    # rings that are each independently solid (e.g.
                    # progressively-smaller elevation contours) — NOT a hole
                    # relationship. Even-odd nesting there would incorrectly
                    # punch alternating "holes" through them, so an inner
                    # contour would never fill "all the way". Custom Tile is
                    # currently the ONLY generator that produces genuine
                    # holes, so gate the nesting treatment on that.
                    pending_lines: list[list[tuple[float, float]]] = []
                    closed_cells: list[list[tuple[float, float]]] = []
                    for poly in polys:
                        # Only fill genuinely closed pattern cells by default —
                        # open strokes should not be silently auto-closed and
                        # filled as if they bounded a region.
                        shp = _polygon_from_polyline_shared(poly, force_close=False)
                        if shp is not None:
                            closed_cells.append(poly)
                        else:
                            if len(poly) >= 2:
                                pending_lines.append(poly)

                    if closed_cells:
                        if params.get("tile_path"):
                            nested_region = _nested_polygon_region_shared(closed_cells)
                            if nested_region is not None and not nested_region.is_empty:
                                fill_strokes.extend(apply_fill(nested_region, spec))
                        else:
                            for poly in closed_cells:
                                shp = _polygon_from_polyline_shared(
                                    poly, force_close=False
                                )
                                if shp is not None:
                                    fill_strokes.extend(apply_fill(shp, spec))

                    # Polygonize any remaining linework and fill resulting polygons.
                    if pending_lines:
                        try:
                            from shapely.geometry import LineString as _LS  # type: ignore[import-untyped]
                            from shapely.ops import (  # type: ignore[import-untyped]
                                linemerge,
                                polygonize,
                                unary_union,
                            )

                            lines = [_LS(p) for p in pending_lines if len(p) >= 2]
                            merged = linemerge(unary_union(lines))  # type: ignore[arg-type]
                            polys_from_lines = list(polygonize(merged))
                            for shp in polys_from_lines:
                                try:
                                    # Ensure resulting polygons do not escape the
                                    # configured fill outline.
                                    if fill_outline is not None:
                                        shp = shp.intersection(fill_outline)
                                    if shp is None or shp.is_empty:
                                        continue
                                except Exception:
                                    pass
                                fill_strokes.extend(apply_fill(shp, spec))
                        except Exception:
                            # Best-effort fallback: ignore polygonize failure.
                            pass
                except Exception:
                    LOGGER.exception("Pattern-targeted fill failed")

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
            p
            for p in all_polys
            if PatternProcessingService._poly_signature(p) not in assigned_sigs
        ]
        if not unassigned:
            return []
        _closed, open_ = PatternProcessingService._merge_and_classify_outlines(
            unassigned
        )
        return open_

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
        floating_cutouts = self._floating_open_cutouts(zones, all_polys)
        all_polys_out: list[list[tuple[float, float]]] = []
        border_polys: list[list[tuple[float, float]]] = []
        for zone in zones:
            zone_fill: list[list[tuple[float, float]]] = []
            # Per-zone fill override: if the zone carries its own "fill"
            # entry, use it; otherwise fall back to the global fill_options.
            zone_fill_options = zone.get("fill") or fill_options
            zone_generated = self.build_pattern_polys(
                zone["polys"] + floating_cutouts,
                pattern=zone["pattern"],
                params=zone["params"],
                scale=zone["scale"],
                orig_w=orig_w,
                orig_h=orig_h,
                interlace=zone.get("interlace", False),
                invert_fill=invert_fill,
                mirror_v=mirror_v,
                mirror_h=mirror_h,
                border_fade=border_fade,
                exclusion_polys=exclusion_polys,
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
            self.apply_scale(
                border_polys, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h
            )
            if border_polys is not None
            else self.apply_scale(
                outline_polys, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h
            )
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
        floating_cutouts = self._floating_open_cutouts(zones, all_polys)

        for zone in zones:
            fill_buf: list[list[tuple[float, float]]] = []
            zone_fill_options = zone.get("fill") or fill_options
            zone_generated = self.build_pattern_polys(
                zone["polys"] + floating_cutouts,
                pattern=zone["pattern"],
                params=zone["params"],
                scale=zone["scale"],
                orig_w=orig_w,
                orig_h=orig_h,
                interlace=zone.get("interlace", False),
                invert_fill=invert_fill,
                mirror_v=mirror_v,
                mirror_h=mirror_h,
                border_fade=border_fade,
                exclusion_polys=exclusion_polys,
                fill_options=zone_fill_options,
                fill_polys_out=fill_buf,
            )
            zone_pattern_polys.extend(zone_generated)
            zone_fill_polys.extend(fill_buf)
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
        display_polys = context_polys + zone_pattern_polys + zone_fill_polys
        return {
            "outline": context_polys,
            "pattern": zone_pattern_polys,
            "fill": zone_fill_polys,
            "display": display_polys,
            "count": len(zone_pattern_polys) + len(zone_fill_polys),
        }
