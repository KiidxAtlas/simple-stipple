from __future__ import annotations


def _page(qapp):
    from src.infra.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    return PatternPage(settings=validate_settings({}))


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
