"""Pattern parameter collection helpers for PatternTab."""

from __future__ import annotations

from typing import Any


def collect_pattern_params(tab: Any, pattern: str) -> dict:
    """Collect validated generator parameters for the selected pattern.

    The tab argument is expected to expose the same field attributes used by
    PatternTab (line edits / checkboxes and parse helpers).
    """

    params: dict
    if pattern == "Honeycomb":
        params = {
            "r": tab._parse_float_field(
                tab._hex_r,
                "Hex size",
                minimum=0.001,
                maximum=1000,
            ),
            "gap": tab._parse_float_field(
                tab._hex_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
        }
    elif pattern == "Gradient Honeycomb":
        params = {
            "r_min": tab._parse_float_field(
                tab._grad_r_min,
                "Min size",
                minimum=0.0,
                maximum=1000,
            ),
            "r_max": tab._parse_float_field(
                tab._grad_r_max,
                "Max size",
                minimum=0.001,
                maximum=1000,
            ),
            "gap": tab._parse_float_field(
                tab._grad_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
            "angle": tab._parse_float_field(tab._grad_angle, "Direction"),
        }
    elif pattern == "Basketweave":
        params = {
            "strip_w": tab._parse_float_field(
                tab._basket_strip_w,
                "Strip width",
                minimum=0.001,
                maximum=1000,
            ),
            "strip_l": tab._parse_float_field(
                tab._basket_strip_l,
                "Strip length",
                minimum=0.001,
                maximum=1000,
            ),
            "gap": tab._parse_float_field(
                tab._basket_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
        }
    elif pattern == "Fish Scale":
        params = {
            "sw": tab._parse_float_field(
                tab._fish_w,
                "Scale width",
                minimum=0.001,
                maximum=1000,
            ),
            "sh": tab._parse_float_field(
                tab._fish_h,
                "Scale height",
                minimum=0.001,
                maximum=1000,
            ),
        }
    elif pattern == "Stipple Dots":
        params = {
            "r": tab._parse_float_field(
                tab._stip_r,
                "Dot radius",
                minimum=0.001,
                maximum=100,
            ),
            "spacing": tab._parse_float_field(
                tab._stip_spacing,
                "Spacing",
                minimum=0.001,
                maximum=1000,
            ),
            "interlaced": tab._stip_layout.isChecked(),
        }
    elif pattern == "Brick":
        params = {
            "brick_w": tab._parse_float_field(
                tab._brick_w_e,
                "Brick width",
                minimum=0.001,
                maximum=1000,
            ),
            "brick_h": tab._parse_float_field(
                tab._brick_h_e,
                "Brick height",
                minimum=0.001,
                maximum=1000,
            ),
            "gap": tab._parse_float_field(
                tab._brick_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
        }
    elif pattern == "Diagonal Lines":
        params = {
            "spacing": tab._parse_float_field(
                tab._diag_spacing,
                "Line spacing",
                minimum=0.001,
                maximum=1000,
            ),
            "angle": tab._parse_float_field(tab._diag_angle, "Angle"),
        }
    elif pattern == "Square Grid":
        params = {
            "spacing": tab._parse_float_field(
                tab._sq_spacing,
                "Grid spacing",
                minimum=0.001,
                maximum=1000,
            )
        }
    elif pattern == "Concentric Rings":
        params = {
            "spacing": tab._parse_float_field(
                tab._conc_spacing,
                "Ring spacing",
                minimum=0.1,
                maximum=500,
            )
        }
    elif pattern == "Wave Fill":
        params = {
            "spacing": tab._parse_float_field(
                tab._wave_spacing,
                "Row spacing",
                minimum=0.001,
                maximum=1000,
            ),
            "amplitude": tab._parse_float_field(
                tab._wave_amplitude,
                "Amplitude",
                maximum=500,
            ),
            "wavelength": tab._parse_float_field(
                tab._wave_wavelength,
                "Wavelength",
                minimum=0.1,
                maximum=1000,
            ),
        }
    elif pattern == "Sunburst":
        params = {
            "spacing_deg": tab._parse_float_field(
                tab._sunburst_spacing,
                "Spoke spacing",
                minimum=0.5,
                maximum=180,
            ),
        }
    elif pattern == "Voronoi":
        params = {
            "n_cells": tab._parse_int_field(
                tab._vor_cells,
                "Cell count",
                minimum=2,
                maximum=10000,
            ),
            "gap": tab._parse_float_field(
                tab._vor_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
            "seed": tab._parse_int_field(tab._vor_seed, "Seed"),
        }
    elif pattern == "Penrose Tiling":
        params = {
            "scale": tab._parse_float_field(
                tab._penrose_scale,
                "Tile size",
                minimum=0.1,
                maximum=1000,
            ),
            "gap": tab._parse_float_field(
                tab._penrose_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
        }
    elif pattern == "Topographic":
        params = {
            "spacing": tab._parse_float_field(
                tab._topo_spacing,
                "Contour spacing",
                minimum=0.1,
                maximum=500,
            ),
        }
    elif pattern == "Hilbert Curve":
        params = {
            "order": tab._parse_int_field(
                tab._hilbert_order,
                "Order",
                minimum=1,
                maximum=8,
            ),
            "margin": tab._parse_float_field(
                tab._hilbert_margin,
                "Margin",
                minimum=0.0,
                maximum=1000,
            ),
        }
    elif pattern == "Reaction Diffuse":
        params = {
            "cell": tab._parse_float_field(
                tab._rd_cell,
                "Cell",
                minimum=0.1,
                maximum=10000,
            ),
            "iters": tab._parse_int_field(
                tab._rd_iters,
                "Iterations",
                minimum=10,
                maximum=8000,
            ),
            "threshold": tab._parse_float_field(
                tab._rd_threshold,
                "Threshold",
                minimum=0.01,
                maximum=0.99,
            ),
            "seed": tab._parse_int_field(tab._rd_seed, "Seed"),
        }
    elif tab._is_tile_pattern(pattern):
        tile_path = tab._library_patterns.get(pattern, "")
        if not tile_path:
            raise ValueError("Selected tile pattern is unavailable.")
        params = {
            "tile_path": tile_path,
            "gap": tab._parse_float_field(
                tab._tile_gap,
                "Gap",
                minimum=0.0,
                maximum=1000,
            ),
            "angle": tab._parse_float_field(tab._tile_angle, "Tile rotation"),
        }
    elif pattern == "Image Halftone":
        img_path = tab._parse_path_field(tab._htone_img_edit, "Halftone image")
        params = {
            "img_path": img_path,
            "r_min": tab._parse_float_field(
                tab._htone_r_min,
                "Cell min",
                minimum=0.0,
                maximum=100,
            ),
            "r_max": tab._parse_float_field(
                tab._htone_r_max,
                "Cell max",
                minimum=0.001,
                maximum=100,
            ),
            "spacing": tab._parse_float_field(
                tab._htone_spacing,
                "Grid spacing",
                minimum=0.001,
                maximum=1000,
            ),
            "invert": tab._htone_invert.isChecked(),
        }
    else:
        raise ValueError(f"Pattern '{pattern}' is no longer available.")

    params["rotation"] = tab._parse_float_field(
        tab._pattern_rotation,
        "Pattern rotation",
    )
    return params
