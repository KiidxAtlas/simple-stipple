"""Spacing and motion tokens for widget code.

The values live in :mod:`simple_stipple.ui.style.tokens` alongside the colors
and type scale the stylesheet is built from — one source for the whole visual
system. This module re-exports the subset Python layout code needs so widgets
do not reach across into the style package just to get a margin.
"""

from simple_stipple.ui.style.tokens import (
    MOTION_DURATION_MS,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
)

__all__ = [
    "MOTION_DURATION_MS",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
]
