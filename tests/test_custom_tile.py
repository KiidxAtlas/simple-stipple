from __future__ import annotations

from shapely.geometry import LineString, box

from src.backend.pattern.processing import PatternProcessor


def test_custom_tile_service_repeats_selected_geometry():
    tile = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]]
    result = PatternProcessor()._gen_pattern(
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
    result = PatternProcessor()._gen_pattern(
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


def test_clipped_custom_tile_cells_remain_closed_and_receive_pattern_fill():
    outline = [(0.0, 0.0), (9.0, 0.0), (9.0, 9.0), (0.0, 9.0), (0.0, 0.0)]
    motif = [[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)]]
    service = PatternProcessor()
    params = {
        "tile_polys": motif,
        "gap": 0.0,
        "rotation": 0.0,
        "interlock": False,
        "repeat_mode": "Straight",
        "origin_x": 1.0,
        "origin_y": 1.0,
    }

    cells = service.build_pattern_polys(
        [outline],
        pattern="Custom Tile",
        params=params,
        scale=(9.0, 9.0),
        orig_w=9.0,
        orig_h=9.0,
    )
    edge_cells = [
        cell
        for cell in cells
        if min(x for x, _y in cell) == 0.0
        or max(x for x, _y in cell) == 9.0
        or min(y for _x, y in cell) == 0.0
        or max(y for _x, y in cell) == 9.0
    ]
    assert edge_cells
    assert all(cell[0] == cell[-1] for cell in edge_cells)

    fill: list[list[tuple[float, float]]] = []
    service.build_pattern_polys(
        [outline],
        pattern="Custom Tile",
        params=params,
        scale=(9.0, 9.0),
        orig_w=9.0,
        orig_h=9.0,
        fill_options={"mode": "lines", "spacing": 0.5, "target_pattern": True},
        fill_polys_out=fill,
    )
    assert fill
    assert any(LineString(stroke).distance(box(0, 0, 9, 9).boundary) < 1e-9 for stroke in fill)


def test_custom_tile_requires_geometry():
    result = PatternProcessor()._gen_pattern(
        box(0, 0, 10, 10),
        "Custom Tile",
        {"tile_polys": [], "gap": 1.0, "rotation": 0.0, "interlock": False},
    )
    assert result == []


def test_custom_tile_repeat_modes_and_phase_change_output():
    tile = [[(0.0, 0.0), (3.0, 0.0), (2.0, 2.0), (0.0, 1.0), (0.0, 0.0)]]
    service = PatternProcessor()
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
    from src.core.settings import validate_settings
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


def test_saved_custom_patterns_appear_in_main_pattern_picker(qapp):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    motif = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)]]
    page = PatternPage(
        settings=validate_settings({"custom_tile_motifs": {"My Tile": motif}})
    )
    try:
        assert page._pattern_combo.findText("Custom · My Tile") >= 0
        assert not hasattr(page, "_tile_motif_combo")
        page._pattern_combo.setCurrentText("Custom · My Tile")
        assert page._current_pattern_key() == "Custom Tile"
        assert page._custom_tile_polys == motif
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_pattern_size_percent_scales_custom_tile_geometry():
    from shapely.geometry import box

    from src.backend.pattern.processing import PatternProcessor

    service = PatternProcessor()
    params = {
        "tile_polys": [[(0.0, 0.0), (2.0, 0.0)]],
        "gap": 1.0,
        "repeat_mode": "Straight",
        "origin_x": 0.0,
        "origin_y": 0.0,
        "rotation": 0.0,
        "size_percent": 100.0,
    }
    normal = service._gen_pattern(box(0, 0, 20, 20), "Custom Tile", params)
    larger = service._gen_pattern(
        box(0, 0, 20, 20), "Custom Tile", {**params, "size_percent": 200.0}
    )
    assert normal != larger


def test_custom_tile_preset_state_includes_motif(qapp):
    from src.core.settings import validate_settings
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
    from src.backend.pattern.presets import deserialize_presets, serialize_presets

    motif = [[[0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]]
    restored = deserialize_presets(
        serialize_presets({"Triangle": {"pattern": "Custom Tile", "custom_tile_polys": motif}})
    )
    assert restored["Triangle"]["custom_tile_polys"] == motif
