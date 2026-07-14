"""Pattern generators package — re-exports all public gen_* functions."""

from collections.abc import Callable
from functools import cache

from src.backend.pattern._shared import (
    HATCH_MODES,
    apply_border_fade,
    apply_interlace,
    apply_invert_fill,
    apply_mirror,
    gen_custom_tile,
)
from src.backend.pattern.organic import (
    gen_flow_lines,
    gen_stipple_dots,
    gen_stipple_interlaced,
    gen_topographic,
    gen_voronoi,
)
from src.backend.pattern.tiling import (
    gen_basketweave,
    gen_brick,
    gen_gradient_honeycomb,
    gen_honeycomb,
    gen_mesh,
)

__all__ = [
    "HATCH_MODES",
    "apply_border_fade",
    "apply_interlace",
    "apply_invert_fill",
    "apply_mirror",
    "gen_basketweave",
    "gen_brick",
    "gen_custom_tile",
    "gen_flow_lines",
    "gen_gradient_honeycomb",
    "gen_honeycomb",
    "gen_mesh",
    "gen_stipple_dots",
    "gen_stipple_interlaced",
    "gen_topographic",
    "gen_voronoi",
    "get_generator",
]

GeneratorFn = Callable[..., list[list[tuple[float, float]]]]


@cache
def get_generator(name: str) -> GeneratorFn:
    """Return a named generator function from this package."""
    return globals()[name]
