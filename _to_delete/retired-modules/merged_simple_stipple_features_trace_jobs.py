"""Background image-trace job orchestration.

This module owns the non-Qt worker boundary used by :class:`TracePage`:
cancellation checks, image-pipeline invocation, and stable result
classification.  The page remains responsible for delivering the outcome via
Qt signals and changing visible widget state.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image

from simple_stipple.engine.imaging.trace import TraceCancelled, image_to_outlines

LOGGER = logging.getLogger(__name__)

TracePipelineResult = tuple[Image.Image, list[list[tuple[float, float]]], int, int]
TracePipeline = Callable[..., TracePipelineResult]


@dataclass(frozen=True)
class TraceJobOutcome:
    """The terminal state of one background image-trace request."""

    trace_token: int
    result: TracePipelineResult | None = None
    error: str | None = None
    cancelled: bool = False


def run_trace_job(
    image_path: str | None,
    kwargs: dict[str, Any],
    trace_token: int,
    cancel_event: threading.Event | None = None,
    *,
    trace_pipeline: TracePipeline = image_to_outlines,
) -> TraceJobOutcome:
    """Run one trace request without accessing page or Qt state.

    ``trace_pipeline`` is injectable so the page's established patch seam and
    focused characterization tests continue to observe the same pipeline.
    """
    if cancel_event and cancel_event.is_set():
        return TraceJobOutcome(trace_token, cancelled=True)
    if not image_path:
        return TraceJobOutcome(trace_token, error="No image selected.")
    try:
        result = trace_pipeline(
            image_path,
            cancel_check=(cancel_event.is_set if cancel_event else None),
            **kwargs,
        )
        if cancel_event and cancel_event.is_set():
            return TraceJobOutcome(trace_token, cancelled=True)
        return TraceJobOutcome(trace_token, result=result)
    except TraceCancelled:
        return TraceJobOutcome(trace_token, cancelled=True)
    except Exception as exc:  # noqa: BLE001 - worker boundary must always complete
        LOGGER.exception("Trace worker failed")
        if cancel_event and cancel_event.is_set():
            return TraceJobOutcome(trace_token, cancelled=True)
        return TraceJobOutcome(trace_token, error=str(exc))


__all__ = ["TraceJobOutcome", "run_trace_job"]
