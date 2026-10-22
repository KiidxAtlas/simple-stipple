"""StarFX job artwork recovery and safe companion resolution."""
import math

import pytest

from simple_stipple.core.formats.starfx import read_starfx


def test_geometry_text_and_passes(tmp_path):
    source = tmp_path / "job.Fmp"
    source.write_text('''DEFTTF 1,"Tahoma"
;CIRCLE1
FOR I = 1 TO 34
MOVEABS 4,5
DRAWCIRCLE 2
NEXT
;RECTANGLE2
MOVEABS 10,20
ROTATEABS 90
DRAWRECT 4,2
;MULTILINETEXT3
TEXTHEIGHT 4
USETTF 1
MOVEABS 2,3
DRAWTEXT "FIRE"
;MULTILINETEXT4
TEXTHEIGHT 4
USETTF 1
DRAWTEXT "SAFE"
RETURN
DRAWCIRCLE 999
''')
    calls = []

    def renderer(text, family, height):
        calls.append((text, family, height))
        return [[(0, 0), (2, 0), (2, 4), (0, 0)]]

    result = read_starfx(source, renderer)
    assert result.object_count == 4
    assert len(result.paths) == 4
    assert calls == [("FIRE", "Tahoma", 4), ("SAFE", "Tahoma", 4)]
    circle = result.paths[0]
    assert circle[0] == circle[-1]
    assert all(math.hypot(x-4, y-5) == pytest.approx(2) for x, y in circle)  # DRAWCIRCLE takes a radius
    assert result.paths[1][0] == pytest.approx((11, 18))
    assert result.paths[2][0] == (1, 3)


def test_fmb_resolves_case_insensitive_local_companions(tmp_path):
    (tmp_path / "Job.Fmp").write_text(
        ';VECTOR1\nMOVEABS 2,3\nDRAWFVI "C:\\\\old\\\\1.Fvi"\nRETURN\n'
    )
    (tmp_path / "1.FVI").write_text("MOVEDIST 0,0\nDRAWLINE 1,0\n")
    result = read_starfx(tmp_path / "Job.Fmb", lambda *args: [])
    # No IMAGEHEIGHT: native 0.254 mm units, bounding-box centre on MOVEABS.
    assert result.paths[0] == pytest.approx([(1.873, 3), (2.127, 3)])


def test_missing_companion_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="companion"):
        read_starfx(tmp_path / "job.fmb", lambda *args: [])


@pytest.mark.parametrize("value", ["nan", "inf", "radius", "-1"])
def test_bad_circle_is_not_silently_discarded(tmp_path, value):
    source = tmp_path / "job.fmp"
    source.write_text(f";CIRCLE1\nDRAWCIRCLE {value}\nRETURN\n")
    with pytest.raises(ValueError, match="line 2"):
        read_starfx(source, lambda *args: [])


def test_companion_symlink_cannot_escape_folder(tmp_path):
    folder = tmp_path / "job"
    folder.mkdir()
    (tmp_path / "outside.Fvi").write_text("MOVEDIST 0,0\nDRAWLINE 1,0\n")
    (folder / "1.Fvi").symlink_to(tmp_path / "outside.Fvi")
    source = folder / "job.fmp"
    source.write_text(';VECTOR1\nDRAWFVI "1.Fvi"\nRETURN\n')
    with pytest.raises(ValueError, match="inside the job folder"):
        read_starfx(source, lambda *args: [])
