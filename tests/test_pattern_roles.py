from __future__ import annotations


def _page(qapp):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    return PatternPage(settings=validate_settings({}))


def test_pattern_shutdown_ignores_late_preview_result(qapp):
    page = _page(qapp)
    page._preview_polys_cache = [[(1.0, 1.0)]]
    page.shutdown()

    page._handle_preview_done((page._preview_revision, [[(9.0, 9.0)]], 1))

    assert page._preview_polys_cache == [[(1.0, 1.0)]]
    page.deleteLater()
    qapp.processEvents()


def test_pattern_outline_roles_default_by_topology_and_drive_cutouts(qapp):
    page = _page(qapp)
    outer = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    inner = [(5, 5), (10, 5), (10, 10), (5, 10), (5, 5)]
    open_path = [(2, 2), (18, 2)]
    try:
        page.load_outline_polys([outer, inner, open_path], source_label="roles")
        roles = [page._outline_roles[oid] for oid in page._outline_ids]
        assert roles == ["boundary", "boundary", "open_path"]
        assert page._exclusion_ids == []

        page._on_canvas_outline_role_change(1, "cutout")
        inner_id = page._outline_ids[1]
        assert page._outline_roles[inner_id] == "cutout"
        assert page._exclusion_ids == [inner_id]
        assert page._generation_polys() == [outer, open_path]

        page._on_canvas_outline_role_change(2, "ignore")
        assert page._generation_polys() == [outer]
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_pattern_roles_persist_and_migrate_legacy_exclusions(qapp):
    from src.ui.pages.pattern.session import (
        apply_pattern_workspace_state,
        get_pattern_workspace_state,
    )

    page = _page(qapp)
    restored = _page(qapp)
    outer = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    inner = [(5, 5), (10, 5), (10, 10), (5, 10), (5, 5)]
    try:
        page.load_outline_polys([outer, inner], source_label="roles")
        page._on_canvas_outline_role_change(1, "cutout")
        page._pattern_cell_cutouts = [inner]
        payload = get_pattern_workspace_state(page)
        apply_pattern_workspace_state(restored, payload)
        assert restored._outline_roles == page._outline_roles
        assert restored._pattern_cell_cutouts == [inner]

        legacy = dict(payload)
        legacy.pop("outline_roles")
        apply_pattern_workspace_state(restored, legacy)
        assert restored._outline_roles[restored._outline_ids[1]] == "cutout"
    finally:
        page.shutdown()
        restored.shutdown()
        page.deleteLater()
        restored.deleteLater()
        qapp.processEvents()


def test_fill_targets_are_independent_and_preview_cells_can_be_cutouts(qapp):
    page = _page(qapp)
    cell = [(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)]
    try:
        page._fill_target_outline_cb.setChecked(True)
        page._fill_target_pattern_cb.setChecked(True)
        assert page._fill_target_outline_cb.isChecked()
        assert page._fill_target_pattern_cb.isChecked()

        page._preview_categories = {"outline": [], "pattern": [cell], "fill": []}
        page._preview_polys_cache = [cell]
        page._showing_preview = True
        page._canvas.load([cell])
        page._configure_pattern_cell_context()
        page._suspend_state = True
        page._on_pattern_cell_cutout_toggle(0)
        assert page._pattern_cell_cutouts == [cell]
        assert 0 in page._canvas._pattern_cell_cutout_indices
    finally:
        page._suspend_state = False
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_pattern_property_defaults_are_editable_before_a_zone_exists(qapp):
    page = _page(qapp)
    try:
        assert page._zone_list.count() == 1
        assert page._zone_list.item(0).text() == "No zones assigned yet"
        assert page._pattern_props_scope.text().startswith("New zone defaults")
        assert page._zone_output_combo.isEnabled()
        assert page._pattern_combo.isEnabled()
        assert page._pattern_combo.currentText() == "— None —"
        assert page._fill_mode_combo.isEnabled()
        assert not page._zones_section.isHidden()
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_custom_pattern_actions_only_appear_for_their_valid_pattern(qapp):
    page = _page(qapp)
    try:
        page._pattern_combo.setCurrentText("Honeycomb")
        assert page._save_tile_btn.isHidden()
        assert page._delete_tile_btn.isHidden()

        page._pattern_combo.setCurrentText("Custom Tile")
        assert not page._save_tile_btn.isHidden()
        assert page._delete_tile_btn.isHidden()

        page._tile_motifs["Saved"] = [[(0, 0), (1, 0), (0, 0)]]
        page._refresh_pattern_choices(current="Custom · Saved")
        page._pattern_combo.setCurrentText("Custom · Saved")
        assert page._save_tile_btn.isHidden()
        assert not page._delete_tile_btn.isHidden()
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_geometry_change_keeps_unaffected_zone_assignments(qapp):
    page = _page(qapp)
    first = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    second = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
    try:
        page.load_outline_polys([first, second], source_label="zones")
        first_id, second_id = page._outline_ids
        page._zones = [
            {"outline_ids": [first_id], "pattern": "Honeycomb", "params": {}},
            {"outline_ids": [second_id], "pattern": "Grid", "params": {}},
        ]

        page._invalidate_zones_for_geometry_change({second_id})

        assert len(page._zones) == 1
        assert page._zones[0]["outline_ids"] == [second_id]
        assert page._zones[0]["pattern"] == "Grid"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_selecting_outline_or_preview_geometry_selects_owning_zone(qapp):
    page = _page(qapp)
    first = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    second = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
    try:
        page.load_outline_polys([first, second], source_label="zones")
        page._zones = [
            {
                "outline_ids": [page._outline_ids[0]],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (30, 10),
            },
            {
                "outline_ids": [page._outline_ids[1]],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (30, 10),
            },
        ]
        page._refresh_zone_list()

        page._canvas.set_selection([1])
        assert page._zone_list.currentRow() == 1
        assert page._pattern_props_scope.text() == "Editing Zone 2"

        page._showing_preview = True
        page._preview_zone_owners = [0, 1]
        page._canvas.load([first, second])
        page._canvas.set_selection([0])
        assert page._zone_list.currentRow() == 0
        assert page._pattern_props_scope.text() == "Editing Zone 1"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_selecting_zone_in_list_highlights_its_canvas_shapes(qapp):
    page = _page(qapp)
    first = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    second = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
    try:
        page.load_outline_polys([first, second], source_label="zones")
        page._zones = [
            {
                "outline_ids": [page._outline_ids[1]],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (30, 10),
            }
        ]
        page._refresh_zone_list()

        page._zone_list.setCurrentRow(0)

        assert page._canvas.get_selection_indices() == []
        assert page._canvas._accent_polys == {1: "#f5a623"}
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_selecting_zone_in_preview_does_not_select_generated_pattern_vertices(qapp):
    page = _page(qapp)
    boundary = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    cell_a = [(1, 1), (2, 1), (2, 2), (1, 1)]
    cell_b = [(3, 3), (4, 3), (4, 4), (3, 3)]
    try:
        page.load_outline_polys([boundary], source_label="zone preview")
        page._zones = [
            {
                "outline_ids": [page._outline_ids[0]],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (10, 10),
            }
        ]
        page._refresh_zone_list()
        page._showing_preview = True
        page._preview_categories = {
            "outline": [boundary],
            "pattern": [cell_a, cell_b],
            "fill": [],
        }
        page._preview_zone_owners = [0, 0, 0]
        page._canvas.load([boundary, cell_a, cell_b])

        page._zone_list.setCurrentRow(0)

        assert page._canvas.get_selection_indices() == []
        assert page._canvas._accent_polys == {
            0: "#f5a623",
            1: "#f5a623",
            2: "#f5a623",
        }
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_zone_editor_live_updates_pattern_and_fill_without_mutating_left_defaults(qapp):
    page = _page(qapp)
    outline = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    try:
        page.load_outline_polys([outline], source_label="zone editor")
        page._zones = [
            {
                "outline_ids": [page._outline_ids[0]],
                "pattern": "Honeycomb",
                "params": {"r": 1.75, "gap": 0.5, "rotation": 0.0},
                "scale": (20.0, 20.0),
                "fill": None,
                "output_mode": "pattern",
            }
        ]
        page._refresh_zone_list()
        page._zone_list.setCurrentRow(0)
        left_pattern = page._pattern_combo.currentText()
        page._preview_user_opt_out = True

        page._zone_pattern_combo.setCurrentText("Brick")
        page._zone_fill_mode.setCurrentIndex(page._zone_fill_mode.findData("lines"))
        page._zone_fill_spacing.setText("2.5")
        page._zone_fill_target_outline.setChecked(False)
        page._zone_fill_target_pattern.setChecked(True)
        qapp.processEvents()

        assert page._zones[0]["pattern"] == "Brick"
        assert page._zones[0]["fill"]["spacing"] == 2.5
        assert page._zones[0]["fill"]["target_outline"] is False
        assert page._zones[0]["fill"]["target_pattern"] is True
        assert page._pattern_combo.currentText() == left_pattern
        assert page._preview_user_opt_out is False
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_zone_fill_inherits_pattern_cell_cutouts_marked_after_zone_creation(qapp):
    page = _page(qapp)
    outline = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    cutout = [(2, 2), (5, 2), (5, 5), (2, 5), (2, 2)]
    try:
        page.load_outline_polys([outline], source_label="zone cutout")
        page._zones = [{
            "outline_ids": [page._outline_ids[0]],
            "pattern": "Brick", "params": {"brick_w": 4, "brick_h": 4, "gap": 0},
            "scale": (20.0, 20.0),
            "fill": {"mode": "lines", "spacing": 1, "target_pattern": True,
                     "cell_cutouts": []},
            "output_mode": "pattern_fill",
        }]
        page._pattern_cell_cutouts = [cutout]

        jobs = page._snapshot_zone_jobs()
        assert jobs[0]["fill"]["cell_cutouts"] == [cutout]
        assert page._zones[0]["fill"]["cell_cutouts"] == [cutout]
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_zone_editor_rejects_nan_without_replacing_last_valid_parameters(qapp):
    page = _page(qapp)
    outline = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    try:
        page.load_outline_polys([outline], source_label="zone editor")
        page._zones = [
            {
                "outline_ids": [page._outline_ids[0]],
                "pattern": "Topographic",
                "params": {"spacing": 2.0, "quality": "high", "rotation": 0.0},
                "scale": (20.0, 20.0),
                "fill": None,
                "output_mode": "pattern",
            }
        ]
        page._refresh_zone_list()
        page._zone_list.setCurrentRow(0)

        spacing = page._zone_param_inputs["spacing"]
        spacing.setText("nan")
        qapp.processEvents()

        assert page._zones[0]["params"]["spacing"] == 2.0
        assert spacing.property("error") is True
        assert "finite" in spacing.toolTip().lower()
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_zone_pattern_enables_assignment_and_selected_zone_can_auto_preview(qapp):
    page = _page(qapp)
    outline = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    try:
        page.load_outline_polys([outline], source_label="zone preview")
        page._canvas.set_selection([0])
        page._pattern_combo.setCurrentText("— None —")
        page._zone_pattern_combo.setCurrentText("Honeycomb")

        assert page._assign_zone_btn.isEnabled()

        page._zones = [
            {
                "outline_ids": [page._outline_ids[0]],
                "pattern": "Honeycomb",
                "params": {},
                "scale": (20.0, 20.0),
            }
        ]
        assert page._canvas.get_selection_indices() == [0]
        assert page._should_auto_preview()
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_preview_pattern_cell_can_be_promoted_to_a_zone(qapp):
    page = _page(qapp)
    outline = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    cell = [(2, 2), (6, 2), (6, 6), (2, 6), (2, 2)]
    try:
        page.load_outline_polys([outline], source_label="preview zone")
        page._zone_pattern_combo.setCurrentText("Honeycomb")
        page._showing_preview = True
        page._preview_polys_cache = [outline, cell]
        page._preview_categories = {
            "outline": [outline],
            "pattern": [cell],
            "fill": [],
            "zone_owners": [None, None],
        }
        page._canvas.load([outline, cell])
        page._canvas.set_selection([1])

        page._assign_zone()

        assert len(page._edit_polys) == 2
        assert page._edit_polys[-1] == cell
        assert page._zones[-1]["outline_ids"] == [page._outline_ids[-1]]
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()
