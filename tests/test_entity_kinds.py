"""EntityKind is the closed kind set, and it stays wire-compatible with strings."""

from __future__ import annotations

import json
import typing

from simple_stipple.core.cad import primitives
from simple_stipple.core.cad.shape_base import Shape
from simple_stipple.core.document.kinds import (
    CONTROL_POINT_KINDS,
    HANDLELESS_KINDS,
    PARAMETRIC_KINDS,
    PROCEDURAL_KINDS,
    EntityKind,
)
from simple_stipple.core.document.model import EntityRecord


def test_kinds_cover_every_shape_type_and_procedural_recipe() -> None:
    shape_types = set(typing.get_args(typing.get_type_hints(Shape)["shape_type"]))
    procedural = {kind.value for kind in PROCEDURAL_KINDS}
    assert {kind.value for kind in EntityKind} == shape_types | procedural
    assert not shape_types & procedural
    assert procedural <= set(primitives.__all__)


def test_kind_behaves_as_its_plain_string() -> None:
    kind = EntityKind.CIRCLE
    assert kind == "circle"
    assert kind in {"circle"} and "circle" in {kind}
    assert {"circle": 1}[kind] == 1
    assert str(kind) == f"{kind}" == "circle"
    assert json.dumps({"kind": kind}) == '{"kind": "circle"}'


def test_record_kind_is_coerced_on_construction_and_assignment() -> None:
    record = EntityRecord(points=[(0.0, 0.0)], kind="arc")  # type: ignore[arg-type]
    assert record.kind is EntityKind.ARC
    record.kind = "bezier"  # type: ignore[assignment]
    assert record.kind is EntityKind.BEZIER


def test_unknown_kind_degrades_to_a_plain_path() -> None:
    record = EntityRecord(points=[(0.0, 0.0)], kind="hatch")  # type: ignore[arg-type]
    assert record.kind is EntityKind.POLYLINE


def test_kind_groups_partition_as_documented() -> None:
    assert EntityKind.POLYLINE not in PARAMETRIC_KINDS
    assert HANDLELESS_KINDS <= PARAMETRIC_KINDS
    assert not HANDLELESS_KINDS & CONTROL_POINT_KINDS
