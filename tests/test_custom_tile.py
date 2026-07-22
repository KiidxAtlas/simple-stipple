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


def test_custom_tile_save_uses_inline_name_and_project_library(qapp, tmp_path):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    page = PatternPage(settings=validate_settings({"custom_tiles_dir": str(tmp_path)}))
    try:
        motif = [[(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)]]
        page._custom_tile_polys = motif
        page._pattern_combo.setCurrentText("Custom Tile")
        page._tile_name_edit.setText("Inline Motif")

        page._save_tile_motif()

        assert (tmp_path / "Inline Motif.dxf").exists()
        assert page._tile_motifs["Inline Motif"] == motif
        assert page._pattern_combo.currentText() == "Custom · Inline Motif"
        assert page._tile_name_edit.text() == ""
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_saved_custom_tile_restores_its_pattern_settings(qapp, tmp_path, monkeypatch):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    monkeypatch.setattr("src.ui.pages.pattern.tab.save_settings", lambda _settings: None)
    page = PatternPage(settings=validate_settings({"custom_tiles_dir": str(tmp_path)}))
    try:
        page._custom_tile_polys = [[(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)]]
        page._pattern_combo.setCurrentText("Custom Tile")
        page._custom_tile_repeat.setCurrentText("Half drop")
        page._custom_tile_gap.setText("2.75")
        page._tile_name_edit.setText("Dropped Tile")
        page._save_tile_motif()

        page._custom_tile_repeat.setCurrentText("Straight")
        page._custom_tile_gap.setText("0.5")
        page._pattern_combo.setCurrentText("— None —")
        page._pattern_combo.setCurrentText("Custom · Dropped Tile")

        assert page._custom_tile_repeat.currentText() == "Half drop"
        assert page._custom_tile_gap.text() == "2.75"
        assert page._settings["custom_tile_settings"]["Dropped Tile"]["custom_tile_repeat"] == "Half drop"

        page._custom_tile_repeat.setCurrentText("Brick offset")
        page._custom_tile_gap.setText("4.25")
        assert page._save_tile_btn.text() == "Update custom tile"
        assert not page._save_tile_btn.isHidden()
        page._save_tile_motif()

        page._pattern_combo.setCurrentText("— None —")
        page._pattern_combo.setCurrentText("Custom · Dropped Tile")
        assert page._custom_tile_repeat.currentText() == "Brick offset"
        assert page._custom_tile_gap.text() == "4.25"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_custom_tile_library_loads_verified_svg_and_fvi_formats(qapp, tmp_path):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    (tmp_path / "line.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="M 0 0 L 10 0 L 10 10 Z"/></svg>',
        encoding="utf-8",
    )
    (tmp_path / "mark.fvi").write_text(
        "MOVEDIST 0,0\nDRAWLINE 10,0\nDRAWLINE 0,10\n",
        encoding="utf-8",
    )

    page = PatternPage(settings=validate_settings({"custom_tiles_dir": str(tmp_path)}))
    try:
        assert "line" in page._tile_motifs
        assert "mark" in page._tile_motifs
        assert page._pattern_combo.findText("Custom · line") >= 0
        assert page._pattern_combo.findText("Custom · mark") >= 0
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_missing_custom_tile_keeps_embedded_fallback_and_offers_locate(qapp, tmp_path):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    motif = [[(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)]]
    missing = tmp_path / "moved.dxf"
    page = PatternPage(settings=validate_settings({
        "custom_tiles_dir": str(tmp_path),
        "custom_tile_motifs": {"Moved": motif},
        "custom_tile_assets": {
            "Moved": {"path": str(missing), "status": "valid", "format": ".dxf"}
        },
    }))
    try:
        page._pattern_combo.setCurrentText("Custom · Moved")
        assert page._custom_tile_polys == motif
        assert page._tile_assets["Moved"]["status"] == "missing"
        assert "embedded fallback" in page._tile_asset_status.text()
        assert not page._locate_tile_btn.isHidden()
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_invalid_custom_tile_offers_repair_without_discarding_fallback(qapp, tmp_path):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    broken = tmp_path / "Broken.dxf"
    broken.write_text("not a dxf", encoding="utf-8")
    motif = [[(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)]]
    page = PatternPage(settings=validate_settings({
        "custom_tiles_dir": str(tmp_path),
        "custom_tile_motifs": {"Broken": motif},
    }))
    requested = []
    page.repairTileRequested.connect(requested.append)
    try:
        page._pattern_combo.setCurrentText("Custom · Broken")
        assert page._custom_tile_polys == motif
        assert page._tile_assets["Broken"]["status"] == "invalid"
        assert "Invalid source" in page._tile_asset_status.text()
        assert not page._repair_tile_btn.isHidden()
        page._repair_tile_btn.click()
        assert requested == [str(broken)]
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
