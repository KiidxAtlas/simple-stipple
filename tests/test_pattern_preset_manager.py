from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _dialog(qapp, settings=None):
    from src.ui.pages.pattern.presets_dialog import PresetManagerDialog

    dialog = PresetManagerDialog(
        {
            "A very long preset name that must remain discoverable": {
                "pattern": "Honeycomb",
                "cell_size": 4,
            },
            "Other": {"pattern": "Brick"},
        },
        settings if settings is not None else {},
    )
    dialog.show()
    qapp.processEvents()
    return dialog


def test_preset_manager_uses_remembered_resizable_sidebar(qapp):
    settings = {"pattern_preset_manager_splitter": [210, 330]}
    dialog = _dialog(qapp, settings)

    assert dialog._list.minimumWidth() == 160
    sizes = dialog._splitter.sizes()
    assert sizes[0] >= 160
    dialog._splitter.setSizes([260, 280])
    dialog._remember_splitter()
    assert settings["pattern_preset_manager_splitter"][0] >= 160

    dialog.close()


def test_preset_manager_renames_inline_and_keeps_full_name_in_tooltip(qapp):
    dialog = _dialog(qapp)
    original = dialog._selected_name()
    item = dialog._list.currentItem()
    assert original is not None and item is not None
    assert original in item.toolTip()

    item.setText("Renamed inline")
    qapp.processEvents()

    assert original not in dialog.result_presets
    assert "Renamed inline" in dialog.result_presets
    assert dialog.is_dirty
    dialog.close()


def test_preset_manager_rejects_duplicate_inline_name_without_modal(qapp):
    dialog = _dialog(qapp)
    item = dialog._list.currentItem()
    assert item is not None
    original = dialog._selected_name()

    item.setText("Other")
    qapp.processEvents()

    assert item.text() == original
    assert "already exists" in dialog._status.text()
    dialog.close()
