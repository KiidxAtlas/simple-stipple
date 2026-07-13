"""Thread-worker functions for pattern generation and preview.

All functions are pure (no Qt / self references) and designed to be
passed as ``threading.Thread(target=…)`` targets.  Signal callbacks
(``on_done`` / ``on_error``) are provided by the caller so the workers
remain independent of the page class.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.backend.dxf.io import write_polylines_dxf

LOGGER = logging.getLogger(__name__)


def _pattern_layer_plan(
    groups: list[list[list[tuple[float, float]]]],
) -> tuple[str | None, dict[str, list[list[tuple[float, float]]]]]:
    """Decide DXF layer names for generated pattern shapes.

    One outline keeps the classic shared "pattern" layer. Multiple outlines
    each get their own "pattern_N" layer (mirroring "outline_N") so laser/
    CAM software still treats each outline's whole fill as a single job —
    not one job per individual shape — while still separating outlines.
    """
    non_empty = [g for g in groups if g]
    if len(non_empty) <= 1:
        return "pattern", {}
    return None, {f"pattern_{i + 1}": g for i, g in enumerate(groups) if g}


def run_generate(
    active: list[list[tuple[float, float]]],
    out_path: str,
    pattern: str,
    params: dict,
    scale: tuple[float, float],
    border_polys: list[list[tuple[float, float]]] | None,
    open_paths: bool = False,
    interlace: bool = False,
    invert_fill: bool = False,
    mirror_v: bool = False,
    mirror_h: bool = False,
    border_fade: float = 0.0,
    exclusion_polys: list[list[tuple[float, float]]] | None = None,
    generation_token: int = 0,
    cancel_event: threading.Event | None = None,
    *,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict | None = None,
) -> None:
    try:
        if cancel_event and cancel_event.is_set():
            return
        fill_polys: list[list[tuple[float, float]]] = []
        outline_groups: list[list[list[tuple[float, float]]]] = []
        polys = pattern_service.build_pattern_polys(
            active,
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
            fill_polys_out=fill_polys,
            outline_groups_out=outline_groups,
        )
        if cancel_event and cancel_event.is_set():
            return
        close = pattern_service.should_close_pattern(pattern)
        pattern_layer, pattern_extra = _pattern_layer_plan(outline_groups)
        extra: dict[str, list] = dict(pattern_extra)
        if fill_polys:
            extra["fill"] = fill_polys
        # When the pattern was split per-outline (pattern_extra non-empty),
        # every shape already went into extra_layers — the main polylines
        # list must stay empty or they'd be written twice (once bare, once
        # under pattern_N).
        main_polys = [] if pattern_extra else polys
        # Always emit the outline as its own layer so the DXF reliably ships
        # with the documented three-layer split (outline / pattern / fill).
        # This holds regardless of the include_border checkbox or whether the
        # polygonize step produced any fill strokes.
        effective_border = border_polys
        if not effective_border:
            effective_border = pattern_service.apply_scale(
                active, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h
            )
        write_polylines_dxf(
            main_polys,
            out_path,
            close=close,
            open_paths=open_paths,
            border_polys=effective_border,
            pattern_layer=pattern_layer,
            border_layer_prefix="outline",
            extra_layers=extra or None,
        )
        count = len(polys) + len(fill_polys)
        name = Path(out_path).name
        on_done((generation_token, count, name, out_path, polys + fill_polys))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        if cancel_event and cancel_event.is_set():
            return
        LOGGER.debug("Pattern generation failed: %s", exc)
        on_error((generation_token, str(exc)))


def run_generate_zones(
    zones: list[dict],
    out_path: str,
    include_border: bool,
    open_paths: bool = False,
    invert_fill: bool = False,
    mirror_v: bool = False,
    mirror_h: bool = False,
    border_fade: float = 0.0,
    exclusion_polys: list[list[tuple[float, float]]] | None = None,
    generation_token: int = 0,
    cancel_event: threading.Event | None = None,
    *,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict | None = None,
    canvas_polys: list[list[tuple[float, float]]] | None = None,
) -> None:
    """Worker: generate all zone patterns and write to a single DXF.

    ``canvas_polys`` is the FULL set of outlines on the canvas (not just
    those assigned to a zone) — needed so an open shape that was never
    explicitly assigned to any zone can still act as an automatic cutout
    for whichever zone's fill region happens to contain it.
    """
    try:
        fill_polys: list[list[tuple[float, float]]] = []
        zone_groups: list[list[list[tuple[float, float]]]] = []
        all_polys, border_polys = pattern_service.build_zone_pattern_polys(
            zones,
            include_border=include_border,
            orig_w=orig_w,
            orig_h=orig_h,
            all_polys=canvas_polys,
            invert_fill=invert_fill,
            mirror_v=mirror_v,
            mirror_h=mirror_h,
            border_fade=border_fade,
            exclusion_polys=exclusion_polys,
            fill_options=fill_options,
            fill_polys_out=fill_polys,
            zone_groups_out=zone_groups,
        )
        if cancel_event and cancel_event.is_set():
            return
        pattern_layer, pattern_extra = _pattern_layer_plan(zone_groups)
        extra: dict[str, list] = dict(pattern_extra)
        if fill_polys:
            extra["fill"] = fill_polys
        main_polys = [] if pattern_extra else all_polys
        write_polylines_dxf(
            main_polys,
            out_path,
            close=True,
            open_paths=open_paths,
            border_polys=border_polys if border_polys else None,
            pattern_layer=pattern_layer,
            border_layer_prefix="outline",
            extra_layers=extra or None,
        )
        count = len(all_polys)
        name = Path(out_path).name
        on_done((generation_token, count, name, out_path, all_polys))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        if cancel_event and cancel_event.is_set():
            return
        LOGGER.debug("Zone pattern generation failed: %s", exc)
        on_error((generation_token, str(exc)))


def compute_preview(
    outline_polys: list[list[tuple[float, float]]],
    pattern: str,
    params: dict,
    scale: tuple[float, float],
    border_polys: list[list[tuple[float, float]]] | None,
    interlace: bool = False,
    invert_fill: bool = False,
    mirror_v: bool = False,
    mirror_h: bool = False,
    border_fade: float = 0.0,
    exclusion_polys: list[list[tuple[float, float]]] | None = None,
    preview_token: int = 0,
    cancel_event: threading.Event | None = None,
    *,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict | None = None,
) -> None:
    try:
        if cancel_event and cancel_event.is_set():
            return
        preview = pattern_service.build_preview_polys(
            outline_polys,
            pattern=pattern,
            params=params,
            scale=scale,
            orig_w=orig_w,
            orig_h=orig_h,
            border_polys=border_polys,
            interlace=interlace,
            invert_fill=invert_fill,
            mirror_v=mirror_v,
            mirror_h=mirror_h,
            border_fade=border_fade,
            exclusion_polys=exclusion_polys,
            fill_options=fill_options,
        )
        if cancel_event and cancel_event.is_set():
            return
        on_done((preview_token, preview["display"], preview["count"], preview))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        if cancel_event and cancel_event.is_set():
            return
        LOGGER.debug("Preview generation failed: %s", exc)
        on_error((preview_token, str(exc)))


def compute_preview_zones(
    zones: list[dict],
    all_polys: list[list[tuple[float, float]]],
    invert_fill: bool = False,
    mirror_v: bool = False,
    mirror_h: bool = False,
    border_fade: float = 0.0,
    exclusion_polys: list[list[tuple[float, float]]] | None = None,
    preview_token: int = 0,
    cancel_event: threading.Event | None = None,
    *,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict | None = None,
) -> None:
    """Worker: generate each zone's pattern and combine for composite preview."""
    try:
        preview = pattern_service.build_preview_zone_polys(
            zones,
            all_polys,
            orig_w=orig_w,
            orig_h=orig_h,
            invert_fill=invert_fill,
            mirror_v=mirror_v,
            mirror_h=mirror_h,
            border_fade=border_fade,
            exclusion_polys=exclusion_polys,
            fill_options=fill_options,
        )
        if cancel_event and cancel_event.is_set():
            return
        on_done((preview_token, preview["display"], preview["count"], preview))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        if cancel_event and cancel_event.is_set():
            return
        LOGGER.debug("Zone preview generation failed: %s", exc)
        on_error((preview_token, str(exc)))
