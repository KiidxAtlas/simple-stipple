from __future__ import annotations

import pytest
from PIL import Image

pytest.importorskip("PySide6")


def _states(page) -> list[str]:
    return [str(label.property("state")) for label in page._workflow_strip._labels]


@pytest.fixture()
def pattern_page(qapp):
    from src.core.settings import validate_settings
    from src.ui.pages.pattern.tab import PatternPage

    page = PatternPage(settings=validate_settings({}))
    yield page
    page.shutdown()
    page.deleteLater()
    qapp.processEvents()


def test_pattern_workflow_represents_all_five_steps(pattern_page):
    page = pattern_page

    page._edit_polys = []
    page._zones = []
    page._preview_polys_cache = []
    page._preview_is_stale = False
    page._export_is_current = False
    page._update_preview_controls()
    assert _states(page) == ["current", "pending", "pending", "pending", "pending"]

    page._edit_polys = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]]
    page._pattern_combo.setCurrentText("— None —")
    page._update_preview_controls()
    assert _states(page)[1] == "current"

    available_pattern = next(
        page._pattern_combo.itemText(index)
        for index in range(page._pattern_combo.count())
        if page._pattern_combo.itemText(index) != "— None —"
    )
    page._pattern_combo.setCurrentText(available_pattern)
    page._update_preview_controls()
    assert _states(page)[2] == "current"

    page._preview_polys_cache = [[(0.0, 0.0), (1.0, 0.0)]]
    page._update_preview_controls()
    assert _states(page)[3] == "current"

    page._export_is_current = True
    page._update_preview_controls()
    assert _states(page) == ["complete", "complete", "complete", "complete", "current"]


def test_pattern_change_invalidates_export_step(pattern_page):
    page = pattern_page
    page._export_is_current = True
    page._edit_polys = [[(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)]]
    available_pattern = next(
        page._pattern_combo.itemText(index)
        for index in range(page._pattern_combo.count())
        if page._pattern_combo.itemText(index) != "— None —"
    )
    page._pattern_combo.setCurrentText(available_pattern)
    page._preview_polys_cache = [[(0.0, 0.0), (1.0, 0.0)]]

    page._invalidate_preview_cache()

    assert not page._export_is_current
    assert page._preview_is_stale
    assert _states(page)[3:] == ["stale", "pending"]
    assert "stale" in page._workflow_strip._labels[3].toolTip().lower()


def test_pattern_has_one_purpose_labeled_remembered_export(pattern_page, monkeypatch):
    page = pattern_page
    actions = {kind: action.text() for kind, action in page._export_actions.items()}
    assert actions == {
        "vector": "Vector-only — Pattern and fill DXF",
        "engraving": "Engraving-only — Positioned image assets",
        "laserstar": "Combined job — LaserStar operator package",
    }

    called: list[str] = []
    monkeypatch.setattr(page, "_export_pattern_engraving", lambda: called.append("engraving"))
    page._select_export_kind("engraving")

    assert page._export_default == "engraving"
    assert page._settings["pattern_export_default"] == "engraving"
    assert page._gen_btn.text() == "Export — Engraving Assets"
    assert called == ["engraving"]


def test_preview_dependent_export_automatically_schedules_validation(
    pattern_page, monkeypatch
):
    page = pattern_page
    page._edit_polys = [[(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)]]
    available_pattern = next(
        page._pattern_combo.itemText(index)
        for index in range(page._pattern_combo.count())
        if page._pattern_combo.itemText(index) != "— None —"
    )
    page._pattern_combo.setCurrentText(available_pattern)
    page._preview_polys_cache = []
    page._preview_is_stale = True
    scheduled: list[bool] = []

    def continuation():
        return None

    monkeypatch.setattr(page, "_schedule_preview", lambda: scheduled.append(True))

    page._with_current_preview(continuation)

    assert page._pending_export_after_preview is continuation
    assert scheduled == [True]
    assert "Validating current preview" in page._status.text()


def test_material_profile_never_overwrites_manual_values_until_applied(pattern_page):
    page = pattern_page
    page._engrave_max_power.setValue(37.0)

    page._engrave_material.setCurrentIndex(
        page._engrave_material.findData("aluminum")
    )

    assert page._engrave_max_power.value() == 37.0
    assert page._apply_material_btn.isEnabled()
    page._apply_material_btn.click()
    assert page._engrave_max_power.value() == 75.0


def test_engraving_import_preserves_dpi_size_centers_and_selects(
    pattern_page, monkeypatch, tmp_path
):
    page = pattern_page
    source = tmp_path / "engraving.png"
    Image.new("L", (100, 50), 128).save(source, dpi=(100, 100))
    monkeypatch.setattr(
        "src.ui.pages.pattern.tab.pick_open_file", lambda *_args, **_kwargs: str(source)
    )
    center_before = page._canvas._c2w(
        page._canvas.width() / 2.0, page._canvas.height() / 2.0
    )

    page._choose_engraving_image()

    assert page._engrave_w.value() == pytest.approx(25.4, abs=0.05)
    assert page._engrave_h.value() == pytest.approx(12.7, abs=0.05)
    assert page._engrave_x.value() + page._engrave_w.value() / 2 == pytest.approx(
        center_before[0], abs=0.02
    )
    assert page._engrave_y.value() + page._engrave_h.value() / 2 == pytest.approx(
        center_before[1], abs=0.02
    )
    assert page._canvas.is_background_image_selected()
    assert page._engraving_section.is_expanded()
    assert page._engraving_placement_section.is_expanded()


def test_engraving_controls_are_grouped_and_invalid_power_blocks_export(pattern_page):
    page = pattern_page
    assert page._engraving_placement_section._title == "Placement"
    assert page._engraving_appearance_section._title == "Appearance"
    assert page._engraving_process_section._title == "Laser Process"
    assert page._engraving_output_section._title == "Output"

    page._engrave_min_power.setValue(90)
    page._engrave_max_power.setValue(20)

    assert not page._engraving_process_error.isHidden()
    assert "Minimum power" in page._engraving_process_error.text()


def test_cross_page_transfer_can_restore_previous_pattern_outline(pattern_page, monkeypatch):
    page = pattern_page
    previous = [[(0.0, 0.0), (8.0, 0.0), (0.0, 0.0)]]
    incoming = [[(20.0, 0.0), (24.0, 0.0), (20.0, 0.0)]]
    monkeypatch.setattr(page, "_schedule_preview", lambda *_args: None)
    monkeypatch.setattr("src.ui.pages.pattern.tab.save_settings", lambda _settings: None)
    page.load_outline_polys(previous, source_label="existing")

    page.load_outline_polys(incoming, source_label="Trace selection", offer_undo=True)
    assert page._edit_polys == incoming
    assert not page._undo_transfer_btn.isHidden()

    page._undo_outline_transfer()
    assert page._edit_polys == previous
    assert page._undo_transfer_btn.isHidden()
    assert "restored" in page._status.text()


def test_pattern_layers_and_export_sidebar_never_collapses(pattern_page, qapp):
    page = pattern_page
    page.resize(900, 600)
    page.show()
    qapp.processEvents()

    assert page._canvas_splitter._responsive_secondary is None
    assert page._canvas_splitter.widget(1).isVisible()
    assert page._canvas_splitter.sizes()[1] > 0
    assert page._gen_btn.isVisibleTo(page)
