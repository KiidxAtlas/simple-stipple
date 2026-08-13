"""Phase 1 — containment tree and the workspace migration that feeds it."""

from __future__ import annotations

from simple_stipple.core.patterns.tiling import build_region_tree


def square(x: float, y: float, size: float) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]


def test_nesting_three_deep_picks_the_nearest_parent():
    ids = ["outer", "mid", "inner"]
    polys = [square(0, 0, 100), square(10, 10, 50), square(20, 20, 10)]
    tree = build_region_tree(ids, polys)

    assert tree["outer"].parent_id is None
    assert tree["outer"].depth == 0
    assert tree["mid"].parent_id == "outer"
    assert tree["mid"].depth == 1
    assert tree["inner"].parent_id == "mid"
    assert tree["inner"].depth == 2
    assert tree["outer"].children == ("mid",)
    assert tree["mid"].children == ("inner",)


def test_disjoint_shapes_are_siblings_at_depth_zero():
    tree = build_region_tree(["a", "b"], [square(0, 0, 10), square(50, 50, 10)])
    assert all(region.depth == 0 and region.parent_id is None for region in tree.values())


def test_partial_overlap_is_not_containment():
    tree = build_region_tree(["a", "b"], [square(0, 0, 10), square(5, 5, 10)])
    assert tree["a"].parent_id is None
    assert tree["b"].parent_id is None


def test_identical_shapes_do_not_parent_each_other():
    tree = build_region_tree(["a", "b"], [square(0, 0, 10), square(0, 0, 10)])
    assert tree["a"].parent_id is None
    assert tree["b"].parent_id is None


def test_open_polylines_get_no_region():
    tree = build_region_tree(
        ["closed", "open"],
        [square(0, 0, 10), [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)]],
    )
    assert set(tree) == {"closed"}


def test_degenerate_geometry_is_skipped():
    tree = build_region_tree(["dot", "ok"], [[(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], square(0, 0, 4)])
    assert set(tree) == {"ok"}
