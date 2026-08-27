"""Qt-free state for the image-tracing workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceModel:
    """Mutable tracing state, separate from TracePage's widget tree.

    Worker coordination, source metadata, and the most recent result are
    workflow state rather than Qt state.  Keeping them here makes the
    lifecycle explicit and lets future non-widget callers reuse it.
    """

    image_path: str | None = None
    running: bool = False
    trace_pending: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    trace_thread: threading.Thread | None = None
    shutting_down: bool = False
    last_output: str | None = None
    last_display_image: Any | None = None
    last_width_mm: float = 0.0
    last_height_mm: float = 0.0
    image_width_px: int = 0
    image_height_px: int = 0
    image_aspect: float = 1.0
    aspect_locked: bool = True
    trace_revision: int = 0
    needs_view_fit: bool = True
    trace_result_stale: bool = False

    def reset_result(self) -> None:
        """Discard result data while retaining the selected source image."""
        self.last_output = None
        self.last_display_image = None
        self.last_width_mm = 0.0
        self.last_height_mm = 0.0
        self.image_width_px = 0
        self.image_height_px = 0
        self.image_aspect = 1.0
        self.trace_result_stale = False
