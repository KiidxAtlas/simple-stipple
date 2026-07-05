"""Display-unit conversion helpers (src/ui/units.py) — pure functions, no Qt."""

from __future__ import annotations

import pytest

from src.ui.units import format_length, from_display, suffix, to_display


def test_mm_is_identity():
    assert to_display(25.4, "mm") == 25.4
    assert from_display(25.4, "mm") == 25.4


def test_mm_to_inches_round_trip():
    assert to_display(25.4, "in") == pytest.approx(1.0)
    assert from_display(1.0, "in") == pytest.approx(25.4)


def test_suffix():
    assert suffix("mm") == "mm"
    assert suffix("in") == "in"


def test_format_length():
    assert format_length(25.4, "mm") == "25.40 mm"
    assert format_length(25.4, "in") == "1.00 in"
    assert format_length(12.7, "in", decimals=3) == "0.500 in"
