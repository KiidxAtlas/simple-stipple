from __future__ import annotations

from shapely.geometry import box

from src.ui.pages.pattern.services import PatternProcessingService


def test_custom_tile_service_repeats_selected_geometry():
    tile = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]]
    result = PatternProcessingService()._gen_pattern(
        box(0, 0, 10, 10),
        "Custom Tile",
        {"tile_polys": tile, "gap": 1.0, "rotation": 0.0, "interlock": False},
    )
    assert len(result) > 1
    assert all(len(poly) >= 2 for poly in result)


def test_custom_tile_preserves_internal_closed_and_open_linework():
    motif = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
        [(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0), (2.0, 2.0)],
        [(6.0, 2.0), (8.0, 2.0), (8.0, 4.0), (6.0, 4.0), (6.0, 2.0)],
        [(2.0, 7.0), (8.0, 7.0)],
    ]
    result = PatternProcessingService()._gen_pattern(
        box(-20, -20, 30, 30),
        "Custom Tile",
        {
            "tile_polys": motif,
            "gap": 2.0,
            "rotation": 0.0,
            "interlock": False,
            "repeat_mode": "Straight",
        },
    )
    widths = [round(max(x for x, _ in path) - min(x for x, _ in path), 3) for path in result]
    closed_count = sum(path[0] == path[-1] for path in result)
    open_count = sum(path[0] != path[-1] for path in result)
    assert 2.0 in widths  # both internal square outlines survive
    assert 6.0 in widths  # the internal open decorative stroke survives
    assert closed_count > 2
    assert open_count > 0


def test_custom_tile_requires_geometry():
    result = PatternProcessingService()._gen_pattern(
        box(0, 0, 10, 10),
        "Custom Tile",
        {"tile_polys": [], "gap": 1.0, "rotation": 0.0, "interlock": False},
    )
    assert result == []


def test_custom_tile_repeat_modes_and_phase_change_output():
    tile = [[(0.0, 0.0), (3.0, 0.0), (2.0, 2.0), (0.0, 1.0), (0.0, 0.0)]]
    service = PatternProcessingService()
    base = {
        "tile_polys": tile,
        "gap": 1.0,
        "rotation": 0.0,
        "interlock": False,
        "origin_x": 0.0,
        "origin_y": 0.0,
    }
    straight = service._gen_pattern(box(0, 0, 15, 15), "Custom Tile", base)
    half_drop = service._gen_pattern(
        box(0, 0, 15, 15), "Custom Tile", {**base, "repeat_mode": "Half drop"}
    )
    phased = service._gen_pattern(
        box(0, 0, 15, 15), "Custom Tile", {**base, "origin_x": 1.25, "origin_y": 0.75}
    )
    assert straight != half_drop
    assert straight != phased


def test_pattern_page_builds_custom_tile_parameters(qapp):
    from src.infra.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    page = PatternPage(settings=validate_settings({}))
    try:
        page._custom_tile_polys = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]]
        page._pattern_combo.setCurrentText("Custom Tile")
        params = page._collect_pattern_params("Custom Tile")
        assert params["gap"] == 0.5
        assert params["tile_polys"] == page._custom_tile_polys
        assert params["interlock"] is False
        assert not hasattr(page, "_interlace_cb")
        assert not hasattr(page, "_invert_fill_cb")
        assert not hasattr(page, "_mirror_v_cb")
        assert not hasattr(page, "_mirror_h_cb")
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_custom_tile_preset_state_includes_motif(qapp):
    from src.infra.settings import validate_settings
    from src.ui.pages.pattern.params import collect_form_state, restore_form_state
    from src.ui.pages.pattern.tab import PatternPage

    page = PatternPage(settings=validate_settings({}))
    motif = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)]]
    try:
        page._custom_tile_polys = motif
        page._pattern_combo.setCurrentText("Custom Tile")
        payload = collect_form_state(page)
        page._custom_tile_polys = []
        restore_form_state(page, payload)
        assert page._custom_tile_polys == motif
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_custom_tile_geometry_survives_preset_serialization():
    from src.ui.pages.pattern.presets import deserialize_presets, serialize_presets

    motif = [[[0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]]
    restored = deserialize_presets(
        serialize_presets({"Triangle": {"pattern": "Custom Tile", "custom_tile_polys": motif}})
    )
    assert restored["Triangle"]["custom_tile_polys"] == motif
