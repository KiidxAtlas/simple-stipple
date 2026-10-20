"""Background solve for the Pattern page's preview.

The solve is a plain function of a frozen :class:`PreviewRequest`, so it runs
on a :class:`~simple_stipple.platform.jobs.JobRunner` worker without touching
page or Qt state. It returns the categorized preview or raises
:class:`~simple_stipple.platform.jobs.JobCancelled` once its cancel event is
set; any other exception is a solve failure.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from simple_stipple.core.patterns.cancellation import cancellation_scope
from simple_stipple.platform.jobs import JobCancelled

LOGGER = logging.getLogger(__name__)
Polyline = list[tuple[float, float]]


@dataclass(frozen=True)
class PreviewRequest:
    """Snapshot of everything one preview solve reads from the page."""

    all_polys: list[Polyline]
    pattern: str
    params: dict[str, Any]
    scale: tuple[float, float]
    border_fade: float
    orig_w: float
    orig_h: float
    fill_options: dict[str, Any] | None = None
    #: Per-region treatments; when present the outline pattern is ignored.
    zones: tuple[dict[str, Any], ...] = ()


def solve_preview(
    request: PreviewRequest,
    pattern_service: Any,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Solve ``request`` and return the preview dict (``display``, ``count``, …)."""
    check = cancel_event.is_set if cancel_event is not None else None

    def cancelled() -> bool:
        return check is not None and check()

    if cancelled():
        raise JobCancelled
    try:
        with cancellation_scope(check):
            if request.zones:
                preview = pattern_service.build_preview_zone_polys(
                    list(request.zones),
                    request.all_polys,
                    orig_w=request.orig_w,
                    orig_h=request.orig_h,
                    border_fade=request.border_fade,
                    fill_options=request.fill_options,
                )
            else:
                preview = pattern_service.build_preview_polys(
                    request.all_polys,
                    pattern=request.pattern,
                    params=request.params,
                    scale=request.scale,
                    orig_w=request.orig_w,
                    orig_h=request.orig_h,
                    # The service supplies the scaled outline itself.
                    border_polys=None,
                    border_fade=request.border_fade,
                    fill_options=request.fill_options,
                )
    except Exception:
        if cancelled():
            raise JobCancelled from None
        LOGGER.warning("Preview generation failed", exc_info=True)
        raise
    if cancelled():
        raise JobCancelled
    return preview
