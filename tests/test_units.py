"""Display-unit conversion helpers in src/ui/util.py."""

from __future__ import annotations

import pytest

from src.ui.util import format_length, from_display, suffix, to_display


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


def test_numeric_expressions_support_arithmetic_and_mixed_units():
    from src.ui.util import parse_numeric_expression

    assert parse_numeric_expression("25 / 2") == pytest.approx(12.5)
    assert parse_numeric_expression("1in + 3mm") == pytest.approx(28.4)
    assert parse_numeric_expression("1 / 2", "in") == pytest.approx(12.7)
    assert parse_numeric_expression("45 + 45", is_length=False) == pytest.approx(90.0)
