"""Pattern generation and preview business logic (UI-agnostic)."""

from __future__ import annotations

import math
from typing import Any, cast
from uuid import uuid4

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
            all_pts = [pt for poly in polys for pt in poly]
            if all_pts:
                xs, ys = zip(*all_pts)
                cx = (min(xs) + max(xs)) / 2.0
                cy = (min(ys) + max(ys)) / 2.0
                rad = rot_deg * math.pi / 180.0
                ca, sa = math.cos(rad), math.sin(rad)
                polys = [
                    [
                        (
                            cx + (x - cx) * ca - (y - cy) * sa,
                            cy + (x - cx) * sa + (y - cy) * ca,
                        )
                        for x, y in poly
                    ]
                    for poly in polys
                ]
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
        fill_outline = orig_outline
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
        if invert_fill:
            fill_outline = apply_invert_fill(outline_poly)
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

        polys = self._gen_pattern(fill_outline, pattern, params)
        if interlace:
            polys = apply_interlace(polys, spacing=params.get("spacing", 1.0))
        if mirror_v or mirror_h:
            polys = apply_mirror(polys, outline_poly, mirror_v, mirror_h)
        if border_fade > 0:
            polys = apply_border_fade(polys, outline_poly, border_fade)
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
    ) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
        all_polys: list[list[tuple[float, float]]] = []
        border_polys: list[list[tuple[float, float]]] = []
        for zone in zones:
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
            )
            all_polys.extend(zone_generated)
            if include_border:
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
    ) -> tuple[list[list[tuple[float, float]]], int]:
        polys = self.build_pattern_polys(
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
        )
        display_polys = polys + (border_polys or [])
        return display_polys, len(polys)

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
    ) -> tuple[list[list[tuple[float, float]]], int]:
        zone_poly_ids: set[int] = set()
        zone_results: list[list[tuple[float, float]]] = []

        for zone in zones:
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
            )
            zone_results.extend(zone_generated)
            for zp in zone["polys"]:
                for idx, cp in enumerate(all_polys):
                    if cp == zp:
                        zone_poly_ids.add(idx)

        context_polys = [p for i, p in enumerate(all_polys) if i not in zone_poly_ids]
        display_polys = zone_results + context_polys
        return display_polys, len(zone_results)
