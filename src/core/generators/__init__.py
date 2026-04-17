"""Pattern generators package — re-exports all public gen_* functions."""

from src.core.generators._shared import apply_interlace
from src.core.generators.advanced import (
    gen_hilbert_curve,
    gen_penrose_tiling,
    gen_reaction_diffuse,
)
from src.core.generators.composite import (
    gen_custom_tile,
    gen_image_halftone,
    gen_moroccan_zellige,
    gen_tri_weave,
)
from src.core.generators.curves import (
    gen_celtic_knot,
    gen_concentric_rings,
    gen_lissajous,
    gen_spiral,
    gen_sunburst,
    gen_wave_fill,
)
from src.core.generators.organic import (
    gen_stipple_dots,
    gen_stipple_interlaced,
    gen_topographic,
    gen_voronoi,
)
from src.core.generators.tiling import (
    gen_basketweave,
    gen_brick,
    gen_diagonal_lines,
    gen_diamond_checkering,
    gen_fish_scale,
    gen_gradient_honeycomb,
    gen_honeycomb,
    gen_square_grid,
    gen_triangle_grid,
)

__all__ = [
    "apply_interlace",
    "gen_basketweave",
    "gen_brick",
    "gen_celtic_knot",
    "gen_concentric_rings",
    "gen_custom_tile",
    "gen_diagonal_lines",
    "gen_diamond_checkering",
    "gen_fish_scale",
    "gen_gradient_honeycomb",
    "gen_hilbert_curve",
    "gen_honeycomb",
    "gen_image_halftone",
    "gen_lissajous",
    "gen_moroccan_zellige",
    "gen_penrose_tiling",
    "gen_reaction_diffuse",
    "gen_spiral",
    "gen_square_grid",
    "gen_stipple_dots",
    "gen_stipple_interlaced",
    "gen_sunburst",
    "gen_topographic",
    "gen_tri_weave",
    "gen_triangle_grid",
    "gen_voronoi",
    "gen_wave_fill",
]
