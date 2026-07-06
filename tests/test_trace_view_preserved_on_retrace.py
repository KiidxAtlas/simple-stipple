"""Regression: retracing after a settings tweak used to always re-fit/re-
center the canvas view (DxfCanvas.load() unconditionally re-fits), so a user
zoomed in to check detail got kicked back out to a fit-to-window view after
every slider drag or checkbox toggle. Only the very first trace of a newly
chosen image should auto-fit; every later retrace of that same image must
preserve the user's current zoom/pan.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.trace.tab import TracePage


def _poly() -> list[tuple[float, float]]:
    return [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]


def _payload(page: TracePage, polys) -> tuple:
    return (page._trace_revision, None, polys, 100, 100, 50.0)


def test_first_trace_of_a_new_image_fits_the_view(qapp):
    page = TracePage(None, None)
    assert page._needs_view_fit is True
    page._handle_trace_done(_payload(page, [_poly()]))
    assert page._needs_view_fit is False


def test_retrace_after_a_settings_tweak_preserves_the_current_zoom(qapp):
    page = TracePage(None, None)
    page._handle_trace_done(_payload(page, [_poly()]))  # initial trace, fits

    page._canvas.set_zoom_percent(250)
    scale_before = page._canvas.get_view_state()["scale"]

    page._handle_trace_done(_payload(page, [_poly()]))  # simulated retrace

    scale_after = page._canvas.get_view_state()["scale"]
    assert scale_after == scale_before


def test_choosing_a_new_image_requests_a_fresh_fit_again(qapp):
    page = TracePage(None, None)
    page._handle_trace_done(_payload(page, [_poly()]))
    assert page._needs_view_fit is False

    page._img_path = "/tmp/some_other_image.png"
    page._needs_view_fit = True  # set by _browse_image/_load_image_from_recent/dropEvent

    page._handle_trace_done(_payload(page, [_poly()]))
    assert page._needs_view_fit is False
