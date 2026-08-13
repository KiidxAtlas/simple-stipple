"""Phase 1 acceptance — the reference scenario, nesting, and migration.

The reference scenario the plan is judged against: an outer boundary, a
circle in the middle, a logo engraved inside the circle, honeycomb filling
the ring between them. Before Phase 1 this dead-ended, because the circle had
to be a Cutout to make honeycomb flow around it and a zone to clip the
engraving, and the model forbade both at once.

These are model-level checks, so they run against a plain stand-in for the
page rather than a real ``PatternPage`` — building the widget costs enough
memory that a per-test page pushes the whole suite over its budget. The
widget-level workspace round-trip lives in ``test_ui_audit_remediations``
alongside the other page tests.
"""

from __future__ import annotations

from types import SimpleNamespace

from simple_stipple.core.patterns.processing import PatternProcessor
from simple_stipple.features.pattern.regions.treatments import (
    engraving_mask_polys,
    migrate_workspace_zones,
    region_ids,
    region_tree,
    treatment_kind,
    zones,
)


def ring(size: float, offset: float = 0.0) -> list[tuple[float, float]]:
    return [
        (offset, offset),
        (offset + size, offset),
        (offset + size, offset + size),
        (offset, offset + size),
        (offset, offset),
    ]


OUTER = ring(100.0)
CIRCLE = ring(40.0, 30.0)
INNER = ring(10.0, 45.0)


def make_page(polys: list[list[tuple[float, float]]], treatments: dict) -> SimpleNamespace:
    """The subset of PatternPage the treatment model actually reads."""
    ids = [f"o{index}" for index in range(len(polys))]
    return SimpleNamespace(
        _outline_ids=ids,
        _edit_polys=[list(poly) for poly in polys],
        _treatments={ids[index]: treatment for index, treatment in treatments.items()},
        _orig_w=100.0,
        _orig_h=100.0,
        _pattern_service=PatternProcessor(),
        _pattern_cell_cutouts=[],
        _pattern_cell_instance_cutouts=[],
    )


def jobs_for(page: SimpleNamespace) -> list[dict]:
    built, _warnings = page._pattern_service.snapshot_zone_jobs(
        zones(page), page._outline_ids, page._edit_polys
    )
    return built


def test_reference_scenario_needs_no_duplicated_geometry() -> None:
    # Ring → Honeycomb. Circle → Engrave. Two picks, no cutout role, and the
    # circle is never duplicated to serve as both mask and hole.
    page = make_page(
        [OUTER, CIRCLE],
        {
            0: {"kind": "pattern", "pattern": "Honeycomb", "params": {}},
            1: {"kind": "engrave", "pattern": "— None —", "params": {}},
        },
    )

    assert engraving_mask_polys(page) == [list(CIRCLE)]
    assert len(page._edit_polys) == 2  # no duplicate copy of the circle

    projected = zones(page)
    assert [zone["outline_ids"] for zone in projected] == [["o0"], ["o1"]]
    # The engrave region still emits its own outline so the circle gets cut.
    assert projected[1]["output_mode"] == "outline"

    # The circle subtracts itself from the ring by geometry alone.
    nested = page._pattern_service._zone_nested_exclusions(jobs_for(page), 0)
    assert [list(poly) for poly in nested] == [list(CIRCLE)]


def test_moving_a_region_re_solves_without_reassignment() -> None:
    page = make_page([OUTER, CIRCLE], {1: {"kind": "cut", "pattern": "— None —", "params": {}}})
    moved = [(x + 5.0, y + 5.0) for x, y in CIRCLE]
    page._edit_polys[1] = moved

    # Same treatment, same ids — only the geometry moved, and the exclusion
    # follows it because it was never a stored assignment in the first place.
    assert treatment_kind(page, "o1") == "cut"
    assert [list(poly) for poly in jobs_for(page)[-1]["polys"]] == [moved]


def test_three_deep_nesting_subtracts_at_every_level() -> None:
    treatment = {"kind": "pattern", "pattern": "Honeycomb", "params": {}}
    page = make_page([OUTER, CIRCLE, INNER], {0: treatment, 1: treatment, 2: treatment})

    tree = region_tree(page)
    assert tree["o1"].parent_id == "o0"
    assert tree["o2"].parent_id == "o1"
    assert tree["o2"].depth == 2

    built = jobs_for(page)
    outer_excl = page._pattern_service._zone_nested_exclusions(built, 0)
    circle_excl = page._pattern_service._zone_nested_exclusions(built, 1)
    inner_excl = page._pattern_service._zone_nested_exclusions(built, 2)
    assert [list(poly) for poly in outer_excl] == [list(CIRCLE), list(INNER)]
    assert [list(poly) for poly in circle_excl] == [list(INNER)]
    assert inner_excl == []


def test_open_paths_cannot_carry_a_treatment() -> None:
    page = make_page([OUTER, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]], {})
    assert region_ids(page) == ["o0"]


def test_engraving_falls_back_to_the_whole_document() -> None:
    page = make_page([OUTER, CIRCLE], {})
    assert engraving_mask_polys(page) == [list(OUTER), list(CIRCLE)]


def test_untreated_outlines_still_reach_the_cut_layer() -> None:
    """Treating one region must not drop the rest of the parts from export.

    The moment any region carries a treatment the whole document routes
    through the zone path, whose border layer was built only from zone
    geometry. On a multi-part sheet — an engraved grip beside plain panels —
    that silently exported the grip and nothing else.
    """
    grip = ring(50.0)
    slide = ring(30.0, 60.0)
    spacer = ring(10.0, 60.0)
    service = PatternProcessor()
    treated = [
        {
            "outline_ids": ["grip"],
            "polys": [grip],
            "pattern": "— None —",
            "params": {},
            "scale": (100.0, 100.0),
            "fill": None,
            "output_mode": "outline",
        }
    ]

    _pattern, border = service.build_zone_pattern_polys(
        treated,
        include_border=True,
        orig_w=100.0,
        orig_h=100.0,
        all_polys=[grip, slide, spacer],
    )
    assert len(border) == 3


# ── Workspace migration ───────────────────────────────────────────────────


def test_legacy_zones_and_cutouts_migrate_to_treatments() -> None:
    treatments = migrate_workspace_zones(
        outline_ids=["outer", "circle", "gone"],
        raw_zones=[
            {
                "outline_ids": ["outer", "missing"],
                "pattern": "Honeycomb",
                "params": {"r": 4.0},
                "output_mode": "pattern_fill",
                "scale": (100.0, 100.0),
            },
            {"outline_ids": ["gone"], "pattern": "Lines", "output_mode": "none"},
        ],
        exclusion_ids=["circle", "not-an-outline"],
    )

    assert treatments["outer"]["kind"] == "pattern_fill"
    assert treatments["outer"]["params"] == {"r": 4.0}
    # A cutout always meant "subtract this area but do not fill it" — that is
    # exactly Cut only.
    assert treatments["circle"]["kind"] == "cut"
    # Disabled zones and ids that no longer exist bring nothing across.
    assert "gone" not in treatments
    assert "missing" not in treatments
    assert "not-an-outline" not in treatments
