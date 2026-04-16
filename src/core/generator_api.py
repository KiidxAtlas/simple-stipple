"""Lazy generator access layer for pattern generation."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache, lru_cache
from importlib import import_module

GeneratorFn = Callable[..., list[list[tuple[float, float]]]]


@lru_cache(maxsize=1)
def _generators_module():
    return import_module("src.core.generators")


@cache
def get_generator(name: str) -> GeneratorFn:
    """Return a named generator function from ``src.core.generators``."""
    mod = _generators_module()
    fn = getattr(mod, name)
    return fn


def apply_interlace(
    polylines: list[list[tuple[float, float]]],
    spacing: float = 1.0,
) -> list[list[tuple[float, float]]]:
    """Apply line interlacing to generated polylines using configured spacing."""
    return get_generator("apply_interlace")(polylines, spacing)
