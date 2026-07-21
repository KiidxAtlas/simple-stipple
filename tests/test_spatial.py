from src.backend.spatial import build_snap_tree, find_nearest


def test_find_nearest_honors_distance_bound():
    tree = build_snap_tree([(0.0, 0.0), (5.0, 5.0), (10.0, 10.0)])
    assert find_nearest(tree, (5.2, 4.9), 0.5) == (5.0, 5.0)
    assert find_nearest(tree, (5.2, 4.9), 0.1) is None


def test_empty_snap_tree_has_no_match():
    assert find_nearest(build_snap_tree([]), (0.0, 0.0), 1.0) is None


def test_stable_points_reuse_cached_tree():
    assert build_snap_tree([(0.0, 0.0), (1.0, 1.0)]) is build_snap_tree(
        [(0.0, 0.0), (1.0, 1.0)]
    )
