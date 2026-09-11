"""Section summaries tolerate partially edited form values without hiding context."""

import pytest

from simple_stipple.features.pattern.form import (
    fill_subtitle,
    outline_subtitle,
    pattern_subtitle,
    zones_subtitle,
)


@pytest.mark.parametrize("width,height", [("-", "12"), ("12", "1e"), ("", "12")])
def test_outline_summary_retains_filename_during_incomplete_dimensions(width, height):
    assert outline_subtitle(" /tmp/outline.dxf ", width, height) == ("outline.dxf · —", False)
    assert outline_subtitle("", width, height) == ("No file loaded", True)


def test_outline_summary_formats_valid_dimensions():
    assert outline_subtitle("outline.dxf", "12.34", "8") == ("outline.dxf · 12.3 × 8.0 mm", False)


@pytest.mark.parametrize("fade", ["-", "1e", "0", "-2"])
def test_pattern_summary_keeps_dimension_when_optional_fade_is_incomplete(fade):
    assert pattern_subtitle("Honeycomb", " 1.75 ", "mm", fade) == ("Honeycomb · 1.75 mm", False)


def test_pattern_summary_preserves_none_custom_and_positive_fade():
    assert pattern_subtitle("— None —", "1.75", "mm", "2") == ("None", True)
    assert pattern_subtitle("Custom · Saved", "", "", "2") == ("Custom · Saved · Fade 2.0mm", False)


@pytest.mark.parametrize(
    "outline,pattern,target",
    [
        (False, False, "No target"),
        (True, False, "Outline"),
        (False, True, "Pattern"),
        (True, True, "Outline + Pattern"),
    ],
)
def test_fill_summary_retains_targets_and_result_count_during_spacing_edits(
    outline, pattern, target
):
    assert fill_subtitle(
        "lines", "Lines", " ", target_outline=outline, target_pattern=pattern, line_count=3
    ) == (f"Lines · ? mm · {target} · 3 lines", False)
    assert fill_subtitle(
        "none", "None", " ", target_outline=outline, target_pattern=pattern, line_count=3
    ) == ("None", True)


@pytest.mark.parametrize(
    "count,text,dim",
    [
        (0, "Optional · different pattern for a selection", True),
        (1, "1 zone assigned", False),
        (3, "3 zones assigned", False),
    ],
)
def test_zone_summary_distinguishes_empty_single_and_multiple(count, text, dim):
    assert zones_subtitle(count) == (text, dim)
