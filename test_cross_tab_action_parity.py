from src.ui.action_maps import (
    IMAGE_ACTION_MAP,
    PATTERN_ACTION_MAP,
    SHAPE_ACTION_MAP,
    SKETCH_ACTION_MAP,
)


def _semantic(action_map: dict, key: str) -> str:
    return str(action_map[key]["semantic"])


def test_equivalent_action_semantics_across_tabs() -> None:
    maps = [SHAPE_ACTION_MAP, SKETCH_ACTION_MAP, IMAGE_ACTION_MAP, PATTERN_ACTION_MAP]

    for action in ("select", "delete", "fit"):
        semantics = {_semantic(m, action) for m in maps}
        assert len(semantics) == 1, (
            f"Mismatched semantic for action '{action}': {semantics}"
        )


def test_export_semantic_consistency_for_exporting_tabs() -> None:
    maps = [SHAPE_ACTION_MAP, IMAGE_ACTION_MAP, PATTERN_ACTION_MAP]
    semantics = {_semantic(m, "export") for m in maps}
    assert len(semantics) == 1
