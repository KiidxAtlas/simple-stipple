"""Pattern form controls: slider mapping, numeric commit policy, starter presets."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit

from simple_stipple.core.patterns.presets import BUILTIN_PRESETS
from simple_stipple.core.patterns.processing import RETIRED_PATTERNS, available_patterns
from simple_stipple.features.pattern.form import (
    PARAM_SPECS,
    SliderScale,
    bind_numeric_commit,
    retired_pattern_notice,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _spec(pattern: str, attr: str):
    return next(spec for spec in PARAM_SPECS[pattern] if spec.attr == attr)


def test_wide_ranges_get_a_logarithmic_track_that_reaches_both_ends() -> None:
    scale = SliderScale.for_field(_spec("Honeycomb", "_hex_r"))  # 0.001–20 mm

    assert scale.logarithmic
    assert scale.text(0) == "0.001"
    assert scale.text(scale.steps) == "20"
    # The default sits in the upper quarter of a log track, not pinned near
    # zero as it would be on a linear 0.001–20 mm track.
    assert 0.6 * scale.steps < scale.position(1.75) < 0.85 * scale.steps
    # Slider values round to three significant figures, never finer than the field.
    assert scale.text(scale.position(1.75)) in {"1.74", "1.75", "1.76"}


def test_linear_sliders_round_to_their_step_and_integers_step_by_one() -> None:
    gap = SliderScale.for_field(_spec("Honeycomb", "_hex_gap"))  # 0–20 mm
    assert not gap.logarithmic
    assert gap.text(52) == "1.04"

    angle = SliderScale.for_field(_spec("Knurling", "_knurl_angle"))  # −89–89°
    assert angle.text(123) == "-67.1"

    rings = SliderScale.for_field(_spec("Seigaiha", "_seigaiha_rings"))  # 1–12
    assert rings.steps == 11
    assert [rings.text(p) for p in (0, 4, 11)] == ["1", "5", "12"]

    cells = SliderScale.for_field(_spec("Voronoi", "_vor_cells"))  # 2–2000, log
    assert cells.logarithmic
    assert cells.text(cells.steps // 2).isdigit()


def test_numeric_fields_commit_on_finish_and_reject_bad_entries_in_place(
    app: QApplication,
) -> None:
    field = QLineEdit("1")
    field.setToolTip("Radius")
    commits: list[str] = []
    bind_numeric_commit(field, lambda: commits.append(field.text()), minimum=0.5, length=True)

    field.setText("2")
    assert commits == []  # typing alone never commits

    field.setText("abc")
    field.editingFinished.emit()
    assert commits == []
    assert field.property("error")
    assert field.text() == "abc"  # the typed text is kept for correcting
    assert field.toolTip() == "Enter a number or expression, e.g. 25/2"

    field.setText("0.1")
    field.editingFinished.emit()
    assert commits == []
    assert field.toolTip() == "Must be at least 0.5 mm"

    field.setText("25/2")
    field.editingFinished.emit()
    assert commits == ["12.5"]  # expressions commit as the number they produce
    assert not field.property("error")
    assert field.toolTip() == "Radius"


def test_integer_fields_reject_fractions(app: QApplication) -> None:
    field = QLineEdit("3")
    commits: list[str] = []
    bind_numeric_commit(field, lambda: commits.append(field.text()), integer=True)

    field.setText("2.5")
    field.editingFinished.emit()
    assert commits == []
    assert field.toolTip() == "Enter a whole number"


def test_starter_presets_only_use_available_generators() -> None:
    patterns = set(available_patterns())
    for name, payload in BUILTIN_PRESETS.items():
        assert payload["pattern"] in patterns, name
        assert payload["pattern"] not in RETIRED_PATTERNS, name


def test_retired_generators_are_named_with_their_replacement() -> None:
    assert retired_pattern_notice(["Honeycomb", None]) is None
    assert (
        retired_pattern_notice(["Fish Scale", "Flow Lines", "Fish Scale"])
        == "Retired pattern replaced: Fish Scale → Seigaiha; Flow Lines → Truchet"
    )
