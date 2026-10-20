"""Generator parameter schemas: coercion, compatibility and form agreement."""

from __future__ import annotations

import pytest

from simple_stipple.core.patterns.params import (
    InvalidParam,
    ParamSpec,
    normalize_pattern_params,
)
from simple_stipple.features.pattern.form import PARAM_SPECS, PATTERN_PARAM_SCHEMAS
from simple_stipple.features.pattern.regions.treatments import (
    load_stored_treatments,
    migrate_workspace_zones,
)

GAP = ParamSpec.from_text_default("gap", "float", "0.5", minimum=0.0, maximum=20.0)
SEED = ParamSpec.from_text_default("seed", "int", "42", minimum=0)
CROSS = ParamSpec.from_text_default("cross", "checkbox", "true")
REPEAT = ParamSpec.from_text_default("repeat_mode", "combobox", "Straight", items=["Straight"])


def test_specs_coerce_stored_text_to_declared_types():
    assert GAP.default == 0.5
    assert GAP.coerce("1.25") == 1.25
    assert SEED.coerce(7.0) == 7 and isinstance(SEED.coerce(7.0), int)
    assert CROSS.coerce("off") is False
    assert REPEAT.coerce("Straight") == "Straight"
    for spec, bad in ((GAP, "wide"), (GAP, float("nan")), (GAP, True), (CROSS, "maybe")):
        with pytest.raises(InvalidParam):
            spec.coerce(bad)


def test_normalize_keeps_old_workspaces_generating_the_same_output():
    stored = {"gap": "40", "seed": "abc", "repeat_mode": "Legacy", "rotation": 15.0}
    result, issues = normalize_pattern_params((GAP, SEED, CROSS, REPEAT), stored)
    # Out-of-range and retired choices were accepted by older releases: keep them.
    assert result["gap"] == 40.0
    assert result["repeat_mode"] == "Legacy"
    # Uncoercible values could only crash the generator: use the default.
    assert result["seed"] == 42
    # Missing keys stay missing so the generator's own fallback still applies.
    assert "cross" not in result
    # Keys outside the schema pass through untouched.
    assert result["rotation"] == 15.0
    assert len(issues) == 3


@pytest.mark.parametrize("pattern", sorted(PARAM_SPECS))
def test_every_form_default_satisfies_its_schema(pattern):
    specs = PATTERN_PARAM_SCHEMAS[pattern]
    defaults = {spec.key: spec.default for spec in specs}
    result, issues = normalize_pattern_params(specs, defaults)
    assert issues == []
    assert result == defaults


def test_workspace_treatments_are_normalized_on_load():
    issues: list[str] = []
    stored = {
        "a": {"kind": "pattern", "pattern": "Honeycomb", "params": {"r": "2", "gap": None}},
        "b": {"kind": "cut", "pattern": "— None —", "params": {}},
        "c": "not a treatment",
    }
    treatments = load_stored_treatments(stored, issues)
    assert set(treatments) == {"a", "b"}
    assert treatments["a"]["params"] == {"r": 2.0, "gap": 0.5}
    assert len(issues) == 1 and issues[0].startswith("Honeycomb gap")

    migrated = migrate_workspace_zones(
        ["z"],
        [{"pattern": "Honeycomb", "params": {"r": "3"}, "outline_ids": ["z"]}],
        [],
        issues,
    )
    assert migrated["z"]["params"] == {"r": 3.0}
