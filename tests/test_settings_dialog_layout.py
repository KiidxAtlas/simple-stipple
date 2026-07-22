"""Settings dialog interaction + structure guards.

The dialog was restructured into aligned form cards. These pin the two things
that restructure must not regress: Enter-to-save works, and every control that
``_save()`` reads is still constructed.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QPushButton

from src.ui.widgets.dialogs.settings_dialog import SettingsDialog


def _buttons(dlg):
    return {b.text(): b for b in dlg.findChildren(QPushButton)}


def test_save_is_the_default_button_so_enter_saves(qapp):
    dlg = SettingsDialog(None, {"unit_system": "mm"})
    buttons = _buttons(dlg)
    assert buttons["Save"].isDefault()  # Enter commits the dialog
    assert not buttons["Cancel"].autoDefault()  # ...and never Cancel
    # A stray Browse/customize button must not steal Enter either.
    assert not buttons["Browse"].autoDefault()


def test_restructure_keeps_every_saved_control(qapp):
    dlg = SettingsDialog(None, {"unit_system": "mm"})

    folder_keys = {k for k, _ in SettingsDialog._FOLDER_FIELDS + SettingsDialog._REPO_FIELDS}
    assert set(dlg._entries) == folder_keys

    toggle_keys = {
        k
        for k, _, _ in (
            SettingsDialog._UPDATE_TOGGLES
            + SettingsDialog._ACCESSIBILITY_TOGGLES
            + SettingsDialog._SNAP_TOGGLES
        )
    } | {"draw_sidebar_always_visible"}
    assert set(dlg._toggles) == toggle_keys

    for attr in (
        "_unit_combo",
        "_smoothing_combo",
        "_ui_scale_combo",
        "_appearance_combo",
        "_rotation_snap_edit",
        "_grid_spacing_edit",
        "_fetch_interval_edit",
        "_smooth_iterations_edit",
        "_simplify_tolerance_edit",
    ):
        assert getattr(dlg, attr) is not None, attr


def test_settings_has_searchable_category_navigation(qapp):
    dlg = SettingsDialog(None, {"unit_system": "mm"})

    categories = {
        dlg._category_combo.itemText(index)
        for index in range(dlg._category_combo.count())
    }
    assert {
        "General",
        "Files & Folders",
        "Canvas & Snapping",
        "Drawing",
        "Pattern",
        "Trace",
        "Export & Machines",
        "Interface",
        "Shortcuts & Menus",
        "Updates",
    }.issubset(categories)

    dlg._search_edit.setText("rotation snap")
    assert dlg._settings_cards["Canvas & Snapping"].isVisibleTo(dlg)
    assert not dlg._settings_cards["Repository"].isVisibleTo(dlg)

    dlg._search_edit.clear()
    assert dlg._settings_cards["Appearance & Units"].isVisibleTo(dlg)
    assert not dlg._settings_cards["Canvas & Snapping"].isVisibleTo(dlg)

    dlg._category_combo.setCurrentText("Shortcuts & Menus")
    assert dlg._settings_cards["Customization"].isVisibleTo(dlg)
    assert not dlg._settings_cards["Appearance & Units"].isVisibleTo(dlg)


def test_settings_apply_is_sticky_and_reset_restores_defaults(qapp, monkeypatch):
    monkeypatch.setattr("src.ui.widgets.dialogs.settings_dialog.save_settings", lambda _s: None)
    dlg = SettingsDialog(
        None,
        {"unit_system": "in", "interface_density": "comfortable", "grid_visible": False},
    )
    dlg._save(close=False)
    assert dlg.result() == 0

    dlg._reset_fields()
    assert dlg._unit_combo.currentData() == "mm"
    assert dlg._density_combo.currentData() == "compact"
    assert dlg._toggles["grid_visible"].isChecked()
