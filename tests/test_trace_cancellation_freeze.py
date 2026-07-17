"""Regression: "when i change settings too much eventually it will freeze
and not reflect changes." Root cause: _run_trace() bailed out on a set
cancel_event with a bare `return` and never emitted any signal — so
_handle_trace_done/_handle_trace_error (the only places that reset
self._running back to False) never ran. Once a single retrace got
cancelled mid-flight, self._running stuck True forever, and every future
_start_trace_thread() call just silently set _trace_pending and returned,
permanently no-op'ing the live preview.

The fix: _run_trace() now emits a dedicated _trace_cancelled signal on
every early-cancellation path, whose handler resets _running (and drains
any pending retrace) exactly like the done/error handlers already did.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.trace.tab import TracePage


def test_a_cancelled_trace_resets_running_via_the_cancelled_signal(qapp, tmp_path):
    page = TracePage(None, None)
    img_path = tmp_path / "dummy.png"
    img_path.write_bytes(b"")

    cancel_event = threading.Event()
    cancel_event.set()  # already superseded before the "thread" even starts

    page._running = True
    received: list[int] = []
    page._trace_cancelled.connect(received.append)

    # Call synchronously (this is what the background thread would do) --
    # must not raise, and must emit _trace_cancelled rather than silently
    # returning with self._running left dangling True.
    page._run_trace(str(img_path), {"width_mm": 50.0}, 7, cancel_event)

    assert received == [7]
    # Signal delivery for a same-thread emit with a direct-connection slot
    # is synchronous, so _running should already be reset.
    assert page._running is False


def test_repeated_rapid_setting_changes_do_not_permanently_freeze_the_preview(qapp, tmp_path):
    """Simulates the exact failure sequence: a trace is "running", the user
    tweaks another setting (schedule_trace cancels it and marks pending),
    the in-flight worker notices the cancellation and reports back. After
    that, a subsequent _start_trace_thread() call must be able to actually
    start a new trace -- not silently no-op forever."""
    page = TracePage(None, None)
    img_path = tmp_path / "dummy.png"
    img_path.write_bytes(b"")
    page._img_path = str(img_path)

    # Pretend a trace is already in flight.
    page._running = True

    # User tweaks another field while it's running: this is exactly what
    # _schedule_trace does when self._running is True.
    page._trace_pending = True
    page._cancel_event.set()

    # The (pretend) in-flight worker thread notices the cancellation and
    # reports back -- this used to be a bare `return` with no signal.
    page._run_trace(str(img_path), {"width_mm": 50.0}, page._trace_revision, page._cancel_event)

    assert page._running is False, (
        "self._running must be reset after a cancelled trace reports back, "
        "or every future retrace silently no-ops forever"
    )
    assert page._trace_pending is False  # drained by the cancellation handler


def test_reload_button_forces_a_fresh_retrace_even_if_running_is_stuck(qapp, tmp_path):
    page = TracePage(None, None)
    img_path = tmp_path / "dummy.png"
    img_path.write_bytes(b"")
    page._img_path = str(img_path)

    # Simulate a stuck state as a defense-in-depth check for the manual
    # "Reload Preview" button, regardless of what caused the stall.
    page._running = True
    page._trace_pending = True

    page._force_reload_trace()

    # _start_trace_thread should have actually progressed far enough to set
    # _running = True again (fields are all populated with valid defaults),
    # proving it didn't just hit the "already running" short-circuit.
    assert page._running is True
    assert page._trace_pending is False
    page.shutdown()
