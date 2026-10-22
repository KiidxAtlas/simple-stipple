"""FMP/FMB layout semantics: circle radius, image height/justify, text anchors.

Expected values come from the FiberStar command reference and from SFX entities
paired with the sample jobs (see ``core/formats/starfx.py`` docstring).
"""
import math
from pathlib import Path

import pytest

from simple_stipple.core.formats.starfx import read_starfx

SAMPLE = Path("/Users/atlasp./dev/projects/work/aa-laser/starfx-jobs/AR RECEIVER ENGRAVINGS/"
              "accurate arms receiver logo NEW/accurate arms receiver logo NEW.Fmp")


def _bounds(paths):
    xs = [x for p in paths for x, _ in p]
    ys = [y for p in paths for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def _box_renderer(width=10.0, height=4.0):
    # Outline box with bottom-left at the origin: the TextRenderer contract.
    def render(text, family, h):
        return [[(0, 0), (width, 0), (width, h), (0, h), (0, 0)]]
    return render


def _job(tmp_path, body, fvi=None):
    source = tmp_path / "job.fmp"
    source.write_text('DEFTTF 1,"Tahoma"\n' + body + "\nRETURN\n")
    if fvi is not None:
        (tmp_path / "1.Fvi").write_text(fvi)
    return source


# 20 x 10 raw FVI units (rectangle from (-8,-3) to (12,7)), 0.01 inch each.
_FVI = "MOVEDIST -8,-3\nDRAWLINE 20,0\nDRAWLINE 0,10\nDRAWLINE -20,0\nDRAWLINE 0,-10\n"


def test_circle_argument_is_radius(tmp_path):
    result = read_starfx(_job(tmp_path, ";CIRCLE1\nMOVEABS 1,2\nDRAWCIRCLE 0.598"),
                         _box_renderer())
    obj = result.objects[0]
    assert obj.meta["radius"] == pytest.approx(0.598)
    x0, y0, x1, y1 = _bounds(obj.paths)
    assert (x1 - x0) == pytest.approx(1.196, abs=1e-3)  # SFX bbox 1.1964 for .598


def test_image_height_sets_bbox_height_and_centre_justify_anchors_at_moveabs(tmp_path):
    body = (";VECTOR1\nMOVEABS 10,20\nROTATEABS 0\nIMAGEJUSTIFY 0\nIMAGEHEIGHT 30\n"
            'DRAWFVI "C:\\\\x\\\\1.Fvi"')
    result = read_starfx(_job(tmp_path, body, _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    assert (y1 - y0) == pytest.approx(30)
    assert (x1 - x0) == pytest.approx(60)  # aspect ratio preserved
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((10, 20))


def test_image_height_is_independent_of_native_fvi_units(tmp_path):
    body = ';VECTOR1\nIMAGEHEIGHT 7\nDRAWFVI "1.Fvi"'
    result = read_starfx(_job(tmp_path, body, _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    assert (y1 - y0) == pytest.approx(7)


def test_image_without_height_keeps_native_size_and_warns(tmp_path):
    result = read_starfx(_job(tmp_path, ';VECTOR1\nDRAWFVI "1.Fvi"', _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    assert (x1 - x0, y1 - y0) == pytest.approx((20 * 0.254, 10 * 0.254))
    assert any("no IMAGEHEIGHT" in w for w in result.warnings)


@pytest.mark.parametrize("code,anchor", [
    (1, (0, 0)), (2, (0.5, 0)), (3, (1, 0)),  # anchor as fraction of bbox, from bottom-left
    (4, (0, 0.5)), (5, (0.5, 0.5)), (6, (1, 0.5)),
    (7, (0, 1)), (8, (0.5, 1)), (9, (1, 1)),
])
def test_image_justify_grid_puts_chosen_bbox_point_on_moveabs(tmp_path, code, anchor):
    # Reference: Top 1,2,3  Middle 4,5,6  Bottom 7,8,9 (column order assumed).
    body = f';VECTOR1\nMOVEABS 5,5\nIMAGEJUSTIFY {code}\nIMAGEHEIGHT 10\nDRAWFVI "1.Fvi"'
    result = read_starfx(_job(tmp_path, body, _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    # Top-anchored (rows 1-3) means the bbox hangs *below* MOVEABS: y1 == 5.
    col, row = anchor[0], {0: 1.0, 0.5: 0.5, 1: 0.0}[anchor[1]]
    assert x0 + col * (x1 - x0) == pytest.approx(5)
    assert y0 + row * (y1 - y0) == pytest.approx(5)
    assert any("column order" in w for w in result.warnings)


def test_image_rotation_pivots_on_moveabs(tmp_path):
    body = (';VECTOR1\nMOVEABS 3,4\nROTATEABS 180\nIMAGEJUSTIFY 1\nIMAGEHEIGHT 10\n'
            'DRAWFVI "1.Fvi"')
    result = read_starfx(_job(tmp_path, body, _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    # Unrotated top-left anchor hangs right/down; 180 deg flips it left/up.
    assert (x1, y0) == pytest.approx((3, 4))
    assert (x0, y1) == pytest.approx((3 - 20, 14))


def test_image_scale_adjusts_after_height(tmp_path):
    body = ';VECTOR1\nIMAGEHEIGHT 10\nIMAGESCALEX 50\nDRAWFVI "1.Fvi"'
    result = read_starfx(_job(tmp_path, body, _FVI), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    assert (x1 - x0, y1 - y0) == pytest.approx((10, 10))
    assert any("percentage" in w for w in result.warnings)


@pytest.mark.parametrize("command,expected_x", [
    ("TEXTJUSTIFYL", (0, 10)), ("TEXTJUSTIFYC", (-5, 5)), ("TEXTJUSTIFYR", (-10, 0)),
])
def test_text_justification_anchors_edge_and_bottom_at_moveabs(tmp_path, command, expected_x):
    body = (f";MULTILINETEXT1\nTEXTLINE\nUSETTF 1\nTTFREGULAR\n{command} 200\n"
            "MOVEABS 0,0\nDRAWTEXT \"AB\"")
    result = read_starfx(_job(tmp_path, body), _box_renderer())
    x0, y0, x1, y1 = _bounds(result.objects[0].paths)
    assert (x0, x1) == pytest.approx(expected_x)
    assert (y0, y1) == pytest.approx((0, 4))
    info = result.objects[0].meta["starfx_text"]
    assert info["justify_arg"] == 200
    assert not any("no TEXTJUSTIFY" in w for w in result.warnings)


def test_receiver_sample_text_matches_sfx_box_vertical_extent():
    # SFX FIRE box is y [0.1004, 4.1004] for MOVEABS -0.515,0.100 height 4.
    result = read_starfx(SAMPLE, _box_renderer(11.2422, 4.0)) if SAMPLE.exists() else None
    if result is None:
        pytest.skip("sample job not available")
    fire = next(o for o in result.objects if o.name == "MULTILINETEXT8")
    x0, y0, x1, y1 = _bounds(fire.paths)
    assert (y0, y1) == pytest.approx((0.1, 4.1))
    assert (x0, x1) == pytest.approx((-0.515 - 5.6211, -0.515 + 5.6211))  # SFX [-6.136, 5.106]


def test_receiver_sample_image_matches_sfx_entity_box():
    if not SAMPLE.exists():
        pytest.skip("sample job not available")
    result = read_starfx(SAMPLE, _box_renderer())
    x0, y0, x1, y1 = _bounds(next(o for o in result.objects if o.name == "VECTOR1").paths)
    # SFX _location (lower-left) -34.470808,-20.9124146; size 58.1646 x 45.3375.
    assert (x0, y0) == pytest.approx((-34.4708, -20.9124), abs=2e-3)
    assert (x1 - x0, y1 - y0) == pytest.approx((58.1646, 45.3375), abs=2e-3)
    circle = next(o for o in result.objects if o.name == "CIRCLE5")
    assert circle.meta["radius"] == pytest.approx(0.598)


def test_text_scale_and_rotation_apply_about_anchor_and_are_recorded(tmp_path):
    body = (";MULTILINETEXT1\nUSETTF 1\nTEXTJUSTIFYC 200\nTEXTSCALEX 50\nTEXTSCALEY 200\n"
            "MOVEABS 10,10\nROTATEABS 180\nDRAWTEXT \"AB\"")
    result = read_starfx(_job(tmp_path, body), _box_renderer())
    obj = result.objects[0]
    x0, y0, x1, y1 = _bounds(obj.paths)
    # width 10*0.5 centred on x=10; height 4*2 hangs below y=10 after a half turn.
    assert (x0, x1) == pytest.approx((7.5, 12.5))
    assert (y0, y1) == pytest.approx((2, 10))
    info = obj.meta["starfx_text"]
    assert (info["rotation_deg"], info["scale_x_percent"], info["scale_y_percent"]) == (180, 50, 200)
    assert obj.meta["text_params"]["height_mm"] == 4.0  # renderer callback height unscaled


def test_renderer_receives_text_family_and_height_only(tmp_path):
    calls = []

    def render(text, family, height):
        calls.append((text, family, height))
        return [[(0, 0), (1, 0), (1, 1), (0, 0)]]

    read_starfx(_job(tmp_path, ";MULTILINETEXT1\nUSETTF 1\nTEXTHEIGHT 3.6\nDRAWTEXT \"X\""), render)
    assert calls == [("X", "Tahoma", 3.6)]


def test_unitsinch_scales_positions_sizes_and_text_height(tmp_path):
    calls = []

    def render(text, family, height):
        calls.append(height)
        return _box_renderer()(text, family, height)

    body = (";CIRCLE1\nUNITSINCH\nMOVEABS 1,1\nDRAWCIRCLE 0.5\n"
            ";MULTILINETEXT2\nUSETTF 1\nTEXTHEIGHT 0.1\nDRAWTEXT \"A\"")
    result = read_starfx(_job(tmp_path, body), render)
    assert result.objects[0].meta["center"] == pytest.approx((25.4, 25.4))
    assert result.objects[0].meta["radius"] == pytest.approx(12.7)
    assert calls == [pytest.approx(2.54)]  # inch persists into later blocks


def test_default_text_justification_is_reported_not_silent(tmp_path):
    result = read_starfx(_job(tmp_path, ';MULTILINETEXT1\nUSETTF 1\nDRAWTEXT "A"'),
                         _box_renderer())
    assert any("no TEXTJUSTIFY" in w for w in result.warnings)


def test_spacing_is_reported_when_not_applied(tmp_path):
    body = ';MULTILINETEXT1\nUSETTF 1\nTEXTJUSTIFYC 200\nSPACING 0.5\nDRAWTEXT "A"'
    result = read_starfx(_job(tmp_path, body), _box_renderer())
    assert any("SPACING" in w for w in result.warnings)


@pytest.mark.parametrize("command", [
    "MOVEREL 1,1", "MOVEDIST 1,1", "ROTATEREL 45", "FIELDSCALE 200", "FIELDROTATION 90",
    "MIRRORHORIZ", "TEXTCIRCLE", "TEXTVERTICAL", "TTFBOLD", "IMAGEJUSTIFY 10",
    "IMAGEHEIGHT 0",
])
def test_unsupported_layout_commands_fail_with_line_number(tmp_path, command):
    source = _job(tmp_path, f";VECTOR1\n{command}\nDRAWRECT 1,1")
    with pytest.raises(ValueError, match="line 3"):
        read_starfx(source, _box_renderer())


def test_unsupported_drawing_command_is_warned_and_skipped(tmp_path):
    result = read_starfx(_job(tmp_path, ";VECTOR1\nDRAWLINE 1,1\nDRAWRECT 1,1"),
                         _box_renderer())
    assert result.object_count == 1
    assert any("DRAWLINE" in w for w in result.warnings)


def test_inline_comments_and_space_separated_numbers(tmp_path):
    result = read_starfx(_job(tmp_path, ";RECTANGLE1\nMOVEABS 1 2 ;home\nDRAWRECT 4 2 ;box"),
                         _box_renderer())
    assert result.objects[0].meta["center"] == (1, 2)
    assert result.objects[0].meta["width"] == 4


def test_rotation_of_rectangle_keeps_centre(tmp_path):
    result = read_starfx(_job(tmp_path, ";RECTANGLE1\nMOVEABS 10,20\nROTATEABS 90\nDRAWRECT 4,2"),
                         _box_renderer())
    path = result.objects[0].paths[0]
    assert path[0] == pytest.approx((11, 18))
    assert math.isclose(sum(x for x, _ in path[:4]) / 4, 10)
