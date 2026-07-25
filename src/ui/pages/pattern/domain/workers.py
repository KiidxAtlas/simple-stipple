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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from src.app.services.presets_service import (
    cancellation_scope,
    prepare_output,
)
from src.backend.dxf.service import DxfService

LOGGER = logging.getLogger(__name__)
CANCELLED_MESSAGE = "__task_cancelled__"


class TaskPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"


def _report_cancel(on_error: Callable, token: int) -> None:
    on_error((token, CANCELLED_MESSAGE))


def _run_cancellable(cancel_event, function, *args, **kwargs):
    check = cancel_event.is_set if cancel_event else None
    with cancellation_scope(check):
        return function(*args, **kwargs)


@dataclass
class CancellableTaskState:
    """Track running/pending state and cancellation token for threaded tasks."""

    running: bool = False
    pending: bool = False
    _cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def phase(self) -> TaskPhase:
        if self.running and self.pending:
            return TaskPhase.CANCELLING
        if self.running:
            return TaskPhase.RUNNING
        return TaskPhase.IDLE

    def request_start(self) -> tuple[bool, threading.Event]:
        """Request a new run.

        Returns (can_start_now, cancel_event_for_run).
        If already running, marks pending and returns (False, current_event).
        """
        if self.running:
            self.pending = True
            self._cancel_event.set()
            return False, self._cancel_event

        # Cancel any prior in-flight token, then mint a fresh event so the
        # caller can pass it into the new worker thread atomically.
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        self.running = True
        self.pending = False
        return True, self._cancel_event

    def finish_run(self) -> bool:
        """Mark run complete and return whether another run is pending."""
        self.running = False
        if self.pending:
            self.pending = False
            return True
        return False

    def has_pending(self) -> bool:
        return self.pending

    def cancel(self) -> None:
        """Signal the in-flight run's cancel event, if any.

        Used on page/app shutdown so an in-flight worker notices at its next
        checkpoint and stops, instead of running to completion against a
        page that's already being torn down.
        """
        self._cancel_event.set()


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
    fabrication_options: dict | None = None,
) -> None:
    try:
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, generation_token)
            return
        fill_polys: list[list[tuple[float, float]]] = []
        polys = _run_cancellable(
            cancel_event,
            pattern_service.build_pattern_polys,
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
        )
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, generation_token)
            return
        polys = prepare_output(polys, fabrication_options)
        fill_polys = prepare_output(fill_polys, fabrication_options)
        close = pattern_service.should_close_pattern(pattern)
        extra: dict[str, list] = {}
        if fill_polys:
            extra["fill"] = fill_polys
        # Always emit the outline as its own layer so the DXF reliably ships
        # with the documented three-layer split (outline / pattern / fill).
        # This holds regardless of the include_border checkbox or whether the
        # polygonize step produced any fill strokes.
        effective_border = border_polys
        if not effective_border:
            effective_border = pattern_service.apply_scale(
                active, scale[0], scale[1], orig_w=orig_w, orig_h=orig_h
            )
        DxfService.write_polylines_dxf(
            polys,
            out_path,
            close=close,
            open_paths=open_paths,
            border_polys=effective_border,
            pattern_layer="pattern",
            border_layer_prefix="outline",
            extra_layers=extra or None,
        )
        count = len(polys) + len(fill_polys)
        name = Path(out_path).name
        on_done((generation_token, count, name, out_path, polys + fill_polys))
    except Exception as exc:  # noqa: BLE001 - any failure must still clear
        # `running` via on_error, or Generate/Preview stay disabled forever.
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, generation_token)
            return
        LOGGER.warning("Pattern generation failed: %s", exc, exc_info=True)
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
    fabrication_options: dict | None = None,
) -> None:
    """Worker: generate all zone patterns and write to a single DXF.

    ``canvas_polys`` is the FULL set of outlines on the canvas (not just
    those assigned to a zone) — needed so an open shape that was never
    explicitly assigned to any zone can still act as an automatic cutout
    for whichever zone's fill region happens to contain it.
    """
    try:
        fill_polys: list[list[tuple[float, float]]] = []
        all_polys, border_polys = _run_cancellable(
            cancel_event,
            pattern_service.build_zone_pattern_polys,
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
        )
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, generation_token)
            return
        all_polys = prepare_output(all_polys, fabrication_options)
        fill_polys = prepare_output(fill_polys, fabrication_options)
        extra: dict[str, list] = {}
        if fill_polys:
            extra["fill"] = fill_polys
        DxfService.write_polylines_dxf(
            all_polys,
            out_path,
            close=True,
            open_paths=open_paths,
            border_polys=border_polys if border_polys else None,
            pattern_layer="pattern",
            border_layer_prefix="outline",
            extra_layers=extra or None,
        )
        count = len(all_polys) + len(fill_polys)
        name = Path(out_path).name
        on_done((generation_token, count, name, out_path, all_polys + fill_polys))
    except Exception as exc:  # noqa: BLE001 - see run_generate
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, generation_token)
            return
        LOGGER.warning("Zone pattern generation failed: %s", exc, exc_info=True)
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
            _report_cancel(on_error, preview_token)
            return
        preview = _run_cancellable(
            cancel_event,
            pattern_service.build_preview_polys,
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
            _report_cancel(on_error, preview_token)
            return
        on_done((preview_token, preview["display"], preview["count"], preview))
    except Exception as exc:  # noqa: BLE001 - see run_generate
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, preview_token)
            return
        LOGGER.warning("Preview generation failed: %s", exc, exc_info=True)
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
        preview = _run_cancellable(
            cancel_event,
            pattern_service.build_preview_zone_polys,
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
            _report_cancel(on_error, preview_token)
            return
        on_done((preview_token, preview["display"], preview["count"], preview))
    except Exception as exc:  # noqa: BLE001 - see run_generate
        if cancel_event and cancel_event.is_set():
            _report_cancel(on_error, preview_token)
            return
        LOGGER.warning("Zone preview generation failed: %s", exc, exc_info=True)
        on_error((preview_token, str(exc)))
