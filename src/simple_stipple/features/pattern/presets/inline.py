"""Preset CRUD (the inline combo box on the Pattern page) — save, apply,
delete, and open the full preset manager. Extracted from ``PatternPage``
(see plan.md Section 9.1); follows the same ``page: Any``-first free-function
convention already used by ``domain/session.py``. This is a lighter-weight
sibling to the full ``ui/presets_dialog.py`` manager dialog — the two
preset workflows are not unified (see plan.md's LP-1 update history).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from simple_stipple.engine.patterns.presets import SETTINGS_KEY as PRESET_SETTINGS_KEY
from simple_stipple.features.pattern.params import collect_form_state, restore_form_state
from simple_stipple.features.pattern.presets_dialog import PresetManagerDialog
from simple_stipple.platform.config import save_settings
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK


def refresh_preset_combo(page: Any) -> None:
    current = page._preset_combo.currentText() if hasattr(page, "_preset_combo") else ""
    page._preset_combo.blockSignals(True)
    page._preset_combo.clear()
    for name in sorted(page._presets):
        page._preset_combo.addItem(name)
    if current and page._preset_combo.findText(current) >= 0:
        page._preset_combo.setCurrentText(current)
    else:
        page._preset_combo.setCurrentIndex(-1)
        preset_editor = page._preset_combo.lineEdit()
        if preset_editor is not None:
            preset_editor.clear()
    page._preset_combo.blockSignals(False)


def save_preset(page: Any) -> None:
    name = page._preset_combo.currentText().strip()
    if not name:
        page._set_status("Enter a preset name in the combo box.", STATUS_ERR)
        return
    is_update = name in page._presets
    if is_update:
        reply = QMessageBox.question(
            page,
            "Overwrite Preset",
            f"A preset called {name!r} already exists.\nReplace it with the current parameters?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    page._presets[name] = collect_form_state(page)
    page._settings[PRESET_SETTINGS_KEY] = dict(page._presets)
    save_settings(page._settings)
    refresh_preset_combo(page)
    page._preset_combo.setCurrentText(name)
    verb = "Updated" if is_update else "Saved"
    page._set_status(f"{verb} preset: {name}", STATUS_OK)
    page._emit_state_changed()


def apply_selected_preset(page: Any) -> None:
    name = page._preset_combo.currentText().strip()
    if not name or name not in page._presets:
        return
    payload = page._presets.get(name)
    if not payload:
        return
    page._suspend_state = True
    restore_form_state(page, payload)
    page._suspend_state = False
    page._set_status(f"Loaded preset: {name}", STATUS_OK)
    page._schedule_preview()
    page._emit_state_changed()


def delete_selected_preset(page: Any) -> None:
    name = page._preset_combo.currentText().strip()
    if not name or name not in page._presets:
        return
    answer = QMessageBox.question(
        page,
        "Delete Preset",
        f'Delete the preset "{name}"? This cannot be undone.',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    page._presets.pop(name, None)
    page._settings[PRESET_SETTINGS_KEY] = dict(page._presets)
    save_settings(page._settings)
    refresh_preset_combo(page)
    page._set_status(f"Deleted preset: {name}")
    page._emit_state_changed()


def open_preset_manager(page: Any) -> None:
    current = page._preset_combo.currentText().strip()
    if current == "Presets":
        current = ""
    dlg = PresetManagerDialog(
        page._presets, page._settings, current_preset=current or None, parent=page
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    if not dlg.is_dirty:
        return
    page._presets = dlg.result_presets
    page._settings[PRESET_SETTINGS_KEY] = dict(page._presets)
    save_settings(page._settings)
    refresh_preset_combo(page)
    if current and current in page._presets:
        page._preset_combo.setCurrentText(current)
    page._set_status(f"Pattern presets updated ({len(page._presets)} total)")
    page._emit_state_changed()
