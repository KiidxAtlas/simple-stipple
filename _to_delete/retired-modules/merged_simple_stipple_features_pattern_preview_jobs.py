"""Immutable worker-call assembly for Pattern previews.

The page owns timers, cancellation state, signals, and user-facing recovery.
This module only maps a completed snapshot to the existing pure worker API.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreviewWorkerCall:
    """One fully prepared invocation of a Pattern preview worker."""

    target: Callable[..., None]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


def build_preview_worker_call(
    *,
    zones: list[dict[str, Any]],
    all_polys: list[list[tuple[float, float]]],
    pattern: str,
    params: dict[str, Any],
    scale: tuple[float, float],
    border_polys: list[list[tuple[float, float]]] | None,
    border_fade: float,
    preview_token: int,
    cancel_event: threading.Event,
    pattern_service: Any,
    orig_w: float,
    orig_h: float,
    on_done: Callable,
    on_error: Callable,
    fill_options: dict[str, Any] | None,
    compute_preview: Callable[..., None],
    compute_preview_zones: Callable[..., None],
) -> PreviewWorkerCall:
    """Choose the existing zone or outline worker and preserve its contract."""
    common_kwargs = {
        "pattern_service": pattern_service,
        "orig_w": orig_w,
        "orig_h": orig_h,
        "on_done": on_done,
        "on_error": on_error,
        "fill_options": fill_options,
    }
    if zones:
        return PreviewWorkerCall(
            target=compute_preview_zones,
            args=(
                zones,
                all_polys,
                False,  # invert_fill
                False,  # mirror_v
                False,  # mirror_h
                border_fade,
                None,  # exclusion_polys
                preview_token,
                cancel_event,
            ),
            kwargs=common_kwargs,
        )
    return PreviewWorkerCall(
        target=compute_preview,
        args=(
            all_polys,
            pattern,
            params,
            scale,
            border_polys,
            False,  # interlace
            False,  # invert_fill
            False,  # mirror_v
            False,  # mirror_h
            border_fade,
            None,  # exclusion_polys
            preview_token,
            cancel_event,
        ),
        kwargs=common_kwargs,
    )


__all__ = ["PreviewWorkerCall", "build_preview_worker_call"]
