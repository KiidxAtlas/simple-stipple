"""Pattern generation and preview business logic (UI-agnostic)."""

from __future__ import annotations

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
    apply_mirror,
    get_generator,
)
from src.backend.generators._shared import _collect_lines as _collect_lines_shared
from src.backend.generators._shared import _extract_polys as _extract_polys_shared
from src.ui.pages.pattern.fill import (
    NULL_PATTERN,
    FillSpec,
    apply_fill,
    build_fill_region,
)


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
        if len(new_polys) == len(old_ids):
            return list(old_ids)
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
        return [list(id_map[oid]) for oid in ids if oid in id_map]

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
        scaled = self.apply_scale(
            outline_polys,
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
        if exclusion_polys:
            excl_scaled = self.apply_scale(
                exclusion_polys,
                scale[0],
                scale[1],
                orig_w=orig_w,
                orig_h=orig_h,
            )
            excl_outline = polylines_to_outline(excl_scaled)
            fill_outline = fill_outline.difference(excl_outline)

        # invert_fill: generate the pattern in the area OUTSIDE the outline so
        # the result looks like a background / frame around the shape.
        # The outer region is the bounding box (+ 50 % padding) minus the outline.
        # Normal fill (invert_fill=False) generates inside the outline as usual.
        if invert_fill:
            from shapely.geometry import box as _shp_box  # type: ignore[import-untyped]

            minx, miny, maxx, maxy = fill_outline.bounds
            pad = max(maxx - minx, maxy - miny) * 0.5
            outer_box = _shp_box(minx - pad, miny - pad, maxx + pad, maxy + pad)
            active_region: Any = outer_box.difference(fill_outline)
        else:
            active_region = fill_outline

        # Rotation: rotate active_region inversely around the original outline's
        # centroid, generate in that frame, rotate output forward and clip back.
        rot_deg = float(params.get("rotation", 0.0) or 0.0)
        cx: float = fill_outline.centroid.x  # always pivot around original outline
        cy: float = fill_outline.centroid.y
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

        # NOTE: apply_invert_fill is intentionally NOT called here — the
        # inversion is achieved above by generating in the outer region when
        # invert_fill=True.
        if interlace and pattern in self._INTERLACE_PATTERNS:
            polys = apply_interlace(
                polys, active_region, spacing=params.get("spacing", 1.0)
            )
        if mirror_v or mirror_h:
            polys = apply_mirror(polys, outline_poly, mirror_v, mirror_h)
        if border_fade > 0:
            polys = apply_border_fade(polys, outline_poly, border_fade)

        # ── Fill (laser infill) ────────────────────────────────────────────
        # The fill region is the input outline minus exclusions — pattern
        # strokes are a pure overlay and do NOT subdivide the fill region.
        # This is the deliberate redesign that replaced the old chamber-
        # cutting algorithm: simpler, predictable, always covers the shape.
        spec = FillSpec.from_dict(fill_options) if fill_options else FillSpec.disabled()
        if spec.enabled:
            fill_strokes = apply_fill(fill_outline, spec)
            if fill_polys_out is not None:
                fill_polys_out.extend(fill_strokes)
                if not spec.keep_pattern:
                    polys = []
            else:
                base = polys if spec.keep_pattern else []
                polys = base + fill_strokes
        return polys


    def build_zone_pattern_polys(
        self,
        zones: list[dict],
        *,
        include_border: bool,
        orig_w: float,
        orig_h: float,
        invert_fill: bool = False,
        mirror_v: bool = False,
        mirror_h: bool = False,
        border_fade: float = 0.0,
        exclusion_polys: list[list[tuple[float, float]]] | None = None,
        fill_options: dict | None = None,
        fill_polys_out: list | None = None,
    ) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
        all_polys: list[list[tuple[float, float]]] = []
        border_polys: list[list[tuple[float, float]]] = []
        for zone in zones:
            zone_fill: list[list[tuple[float, float]]] = []
            # Per-zone fill override: if the zone carries its own "fill"
            # entry, use it; otherwise fall back to the global fill_options.
            zone_fill_options = zone.get("fill") or fill_options
            zone_generated = self.build_pattern_polys(
                zone["polys"],
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
            all_polys.extend(zone_generated)
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
        return all_polys, border_polys

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
        outline_scaled = (
            border_polys
            if border_polys is not None
            else self.apply_scale(
                outline_polys, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h
            )
        )
        # Display layering matches what the user expects to see:
        # outline (background) → pattern → fill (on top).
        display_polys = (border_polys or []) + pattern_polys + fill_buf
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

        for zone in zones:
            fill_buf: list[list[tuple[float, float]]] = []
            zone_fill_options = zone.get("fill") or fill_options
            zone_generated = self.build_pattern_polys(
                zone["polys"],
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
            for zp in zone["polys"]:
                for idx, cp in enumerate(all_polys):
                    if cp == zp:
                        zone_poly_ids.add(idx)

        context_polys = [p for i, p in enumerate(all_polys) if i not in zone_poly_ids]
        display_polys = context_polys + zone_pattern_polys + zone_fill_polys
        return {
            "outline": context_polys,
            "pattern": zone_pattern_polys,
            "fill": zone_fill_polys,
            "display": display_polys,
            "count": len(zone_pattern_polys) + len(zone_fill_polys),
        }
