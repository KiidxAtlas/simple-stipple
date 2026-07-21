"""The global UI-font scaling must never produce a non-positive point size.

``_apply_accessibility_settings`` scales the shared QApplication font by
``ui_scale``. Two real inputs used to drive it negative — a pixel-defined base
font (``pointSizeF() == -1``) and a corrupt ``ui_scale`` — which Qt accepts and
then renders as broken text. ``resolve_scaled_point_size`` closes that class;
these tests pin every branch.
"""

import pytest

from src.app.window import resolve_scaled_point_size


def test_pixel_defined_base_font_does_not_scale_negative():
    # QFont.pointSizeF() returns -1 for a pixel-sized font.
    base, scaled = resolve_scaled_point_size(-1.0, None, 1.5)
    assert base > 0
    assert scaled > 0


@pytest.mark.parametrize("bad_scale", [0.0, -2.0, None])
def test_corrupt_ui_scale_falls_back_to_unscaled(bad_scale):
    base, scaled = resolve_scaled_point_size(12.0, None, bad_scale)  # type: ignore[arg-type]
    assert base == 12.0
    assert scaled == 12.0


def test_stored_base_prevents_compounding_across_calls():
    # First call establishes the base from the live font.
    base1, scaled1 = resolve_scaled_point_size(10.0, None, 2.0)
    assert (base1, scaled1) == (10.0, 20.0)
    # Second call sees the already-scaled font (20) but a stored base (10);
    # the stored base must win so scaling never compounds to 40.
    base2, scaled2 = resolve_scaled_point_size(20.0, base1, 2.0)
    assert (base2, scaled2) == (10.0, 20.0)


def test_normal_scaling_is_applied():
    assert resolve_scaled_point_size(12.0, None, 1.25) == (12.0, 15.0)
