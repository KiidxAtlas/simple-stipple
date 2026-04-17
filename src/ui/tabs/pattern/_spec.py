"""Declarative parameter specifications for pattern generator widgets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParamField:
    attr: str              # instance attribute name set on the tab (e.g. "_hex_r")
    label: str             # display label in the grid
    default: str           # default value text
    tooltip: str = ""
    kind: str = "float"    # "float" | "int" | "checkbox" | "combobox"
    items: list[str] = field(default_factory=list)  # choices for "combobox"
    hint: str | None = None  # optional hint label appended after this field


# ── Parameter specs for each named pattern ────────────────────────────────────
# Each list entry maps directly to a row in the param grid widget.
# Fields marked hint="..." render a small muted label below them.

PARAM_SPECS: dict[str, list[ParamField]] = {
    "Honeycomb": [
        ParamField("_hex_r", "Hex size (mm)", "1.75", "Radius of each hexagonal cell"),
        ParamField("_hex_gap", "Gap (mm)", "0.5", "Spacing between adjacent hexagons"),
    ],
    "Gradient Honeycomb": [
        ParamField("_grad_r_min", "Min size (mm)", "0.8", "Smallest hex cell size at one end of the gradient"),
        ParamField("_grad_r_max", "Max size (mm)", "2.5", "Largest hex cell size at the other end"),
        ParamField("_grad_gap", "Gap (mm)", "0.5", "Spacing between hexagons"),
        ParamField("_grad_angle", "Direction (°)", "0", "Gradient direction in degrees (0 = left to right)", hint="0° = left→right  ·  90° = vertical"),
    ],
    "Basketweave": [
        ParamField("_basket_strip_w", "Strip width (mm)", "2.0", "Width of each woven strip"),
        ParamField("_basket_strip_l", "Strip length (mm)", "8.0", "Length of each woven strip"),
        ParamField("_basket_gap", "Gap (mm)", "0.2", "Gap between woven strips"),
    ],
    "Fish Scale": [
        ParamField("_fish_w", "Scale width (mm)", "3.0", "Horizontal span of each fish-scale arc"),
        ParamField("_fish_h", "Scale height (mm)", "2.0", "Vertical height of each fish-scale arc"),
    ],
    "Stipple Dots": [
        ParamField("_stip_r", "Dot radius (mm)", "0.4", "Radius of each stipple dot"),
        ParamField("_stip_spacing", "Spacing (mm)", "1.2", "Centre-to-centre distance between dots"),
        ParamField("_stip_layout", "Interlaced (offset grid)", "", "Use interlaced offset grid instead of Poisson-disk distribution", kind="checkbox"),
    ],
    "Brick": [
        ParamField("_brick_w_e", "Brick width (mm)", "4.0", "Width of each brick"),
        ParamField("_brick_h_e", "Brick height (mm)", "2.0", "Height of each brick"),
        ParamField("_brick_gap", "Gap (mm)", "0.5", "Mortar gap between bricks"),
    ],
    "Diagonal Lines": [
        ParamField("_diag_spacing", "Line spacing (mm)", "1.0", "Distance between parallel diagonal lines"),
        ParamField("_diag_angle", "Angle (°)", "45", "Angle of the diagonal lines in degrees"),
    ],
    "Square Grid": [
        ParamField("_sq_spacing", "Grid spacing (mm)", "1.0", "Distance between grid lines"),
    ],
    "Concentric Rings": [
        ParamField("_conc_spacing", "Ring spacing (mm)", "1.5", "Distance between concentric rings"),
    ],
    "Wave Fill": [
        ParamField("_wave_spacing", "Row spacing (mm)", "1.5", "Vertical distance between wave rows"),
        ParamField("_wave_amplitude", "Amplitude (mm)", "0.5", "Peak-to-centre height of each wave"),
        ParamField("_wave_wavelength", "Wavelength (mm)", "3.0", "Horizontal length of one full wave cycle"),
    ],
    "Sunburst": [
        ParamField("_sunburst_spacing", "Spoke spacing (°)", "5.0", "Angular spacing between spokes (smaller = more spokes)", hint="5° → 36 spokes  ·  10° → 18 spokes"),
    ],
    "Voronoi": [
        ParamField("_vor_cells", "Cell count", "60", "Number of random Voronoi cells to generate", kind="int"),
        ParamField("_vor_gap", "Gap (mm)", "0.15", "Inset distance between Voronoi cells"),
        ParamField("_vor_seed", "Seed", "42", "Random seed for reproducible cell placement", kind="int"),
    ],
    "Penrose Tiling": [
        ParamField("_penrose_scale", "Tile size (mm)", "3.0", "Approximate size of each Penrose tile"),
        ParamField("_penrose_gap", "Gap (mm)", "0.1", "Spacing between adjacent tiles", hint="Aperiodic kite-and-dart tiling (P2)"),
    ],
    "Topographic": [
        ParamField("_topo_spacing", "Contour spacing (mm)", "1.5", "Distance between successive contour lines", hint="Inward offset contours from the outline edge"),
    ],
    "Hilbert Curve": [
        ParamField("_hilbert_order", "Order", "5", "Curve recursion depth (1-8)", kind="int"),
        ParamField("_hilbert_margin", "Margin (mm)", "1.0", "Inset from outline bounds", hint="Higher order = denser path"),
    ],
    "Reaction Diffuse": [
        ParamField("_rd_pattern", "Preset", "labyrinth", "Named Gray-Scott reaction-diffusion preset", kind="combobox", items=["labyrinth", "spots", "stripes", "maze"]),
        ParamField("_rd_cell", "Cell (mm)", "0.8", "Simulation grid cell size"),
        ParamField("_rd_iters", "Iterations", "1200", "Simulation steps", kind="int"),
        ParamField("_rd_threshold", "Threshold", "0.22", "Contour extraction threshold (0-1)"),
        ParamField("_rd_seed", "Seed", "42", "Random seed for deterministic output", kind="int"),
    ],
    "Celtic Knot": [
        ParamField("_celtic_cell", "Cell size (mm)", "5.0", "Grid cell size"),
        ParamField("_celtic_line_w", "Line width (mm)", "1.0", "Width of the knot band"),
        ParamField("_celtic_gap", "Gap (mm)", "0.2", "Gap at crossings for the over-under illusion"),
    ],
    "Lissajous": [
        ParamField("_liss_freq_x", "Freq X", "3", "Horizontal frequency", kind="int"),
        ParamField("_liss_freq_y", "Freq Y", "2", "Vertical frequency", kind="int"),
        ParamField("_liss_spacing", "Row spacing (mm)", "2.0", "Vertical offset between repeated curves"),
        ParamField("_liss_amplitude", "Amplitude (mm)", "5.0", "Peak amplitude of each figure"),
    ],
}
