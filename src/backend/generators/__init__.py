"""Pattern generators package — re-exports all public gen_* functions."""

from collections.abc import Callable
from functools import cache

from src.backend.generators._shared import (
    HATCH_MODES,
    apply_border_fade,
    apply_interlace,
    apply_invert_fill,
    apply_mirror,
)
from src.backend.generators.advanced import (
    gen_hilbert_curve,
    gen_penrose_tiling,
    gen_reaction_diffuse,
)
from src.backend.generators.composite import gen_custom_tile, gen_image_halftone
from src.backend.generators.curves import (
    gen_celtic_knot,
    gen_concentric_rings,
    gen_sunburst,
)
from src.backend.generators.organic import (
    gen_stipple_dots,
    gen_stipple_interlaced,
    gen_topographic,
    gen_voronoi,
)
from src.backend.generators.tiling import (
    gen_basketweave,
    gen_brick,
    gen_gradient_honeycomb,
    gen_honeycomb,
    gen_mesh,
    gen_square_grid,
)

__all__ = [
    "HATCH_MODES",
    "apply_border_fade",
    "apply_interlace",
    "apply_invert_fill",
    "apply_mirror",
    "gen_basketweave",
    "gen_brick",
    "gen_celtic_knot",
    "gen_concentric_rings",
    "gen_custom_tile",
    "gen_gradient_honeycomb",
    "gen_hilbert_curve",
    "gen_honeycomb",
    "gen_image_halftone",
    "gen_mesh",
    "gen_penrose_tiling",
    "gen_reaction_diffuse",
    "gen_square_grid",
    "gen_stipple_dots",
    "gen_stipple_interlaced",
    "gen_sunburst",
    "gen_topographic",
    "gen_voronoi",
    "get_generator",
]

GeneratorFn = Callable[..., list[list[tuple[float, float]]]]


@cache
def get_generator(name: str) -> GeneratorFn:
    """Return a named generator function from this package."""
    return globals()[name]
