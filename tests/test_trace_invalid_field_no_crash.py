"""Regression: clearing a numeric trace field (e.g. selecting all text in
"Simplify" or "Min area" and retyping) transiently leaves it empty. If the
220ms live-preview debounce timer fires during that instant, _start_trace_
thread() used to let build_trace_kwargs() raise ValueError straight out of
a QTimer callback — an unhandled exception observed to reach the app's
top-level crash reporter. Every other page (Pattern) wraps this same
raising _parse_float_field contract in try/except; the Trace tab's
_start_trace_thread() was the one caller that didn't.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.pages.trace.tab import TracePage


def test_clearing_a_required_numeric_field_does_not_crash_the_retrace(qapp, tmp_path):
    page = TracePage(None, None)
    img_path = tmp_path / "dummy.png"
    img_path.write_bytes(b"")  # existence is all _start_trace_thread checks
    page._img_path = str(img_path)

    for field in (page._simplify, page._min_area, page._blur, page._close_r, page._width_mm):
        field.setText("")
        page._start_trace_thread()  # must not raise
        assert page._running is False
        field.setText("1.0")  # restore for the next field in the loop


def test_out_of_range_threshold_does_not_crash_the_retrace(qapp, tmp_path):
    page = TracePage(None, None)
    img_path = tmp_path / "dummy.png"
    img_path.write_bytes(b"")
    page._img_path = str(img_path)

    page._auto_thresh_cb.setChecked(False)
    page._thresh_entry.setText("999")  # out of the 0-255 range
    page._start_trace_thread()  # must not raise
    assert page._running is False
