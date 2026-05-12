"""_PresetMixin — preset save/load/delete/manage for PatternPage."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from src.settings import save_settings
from src.ui.pages.pattern.presets import SETTINGS_KEY as PRESET_SETTINGS_KEY
from src.ui.pages.pattern.presets_dialog import PresetManagerDialog
from src.ui.pages.pattern.params import collect_form_state, restore_form_state


class _PresetMixin:
    """Mixin providing preset management methods for PatternPage."""

    def _refresh_preset_combo(self) -> None:
        current = (
            self._preset_combo.currentText() if hasattr(self, "_preset_combo") else ""
        )
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)
        if current and self._preset_combo.findText(current) >= 0:
            self._preset_combo.setCurrentText(current)
        else:
            self._preset_combo.setCurrentIndex(-1)
            self._preset_combo.lineEdit().clear()
        self._preset_combo.blockSignals(False)

    def _save_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            self._set_status("Enter a preset name in the combo box.", "#f85149")
            return
        is_update = name in self._presets
        if is_update:
            reply = QMessageBox.question(
                self,
                "Overwrite preset",
                f"A preset called {name!r} already exists.\nReplace it with the current parameters?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._presets[name] = collect_form_state(self)
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._preset_combo.setCurrentText(name)
        verb = "Updated" if is_update else "Saved"
        self._set_status(f"{verb} preset: {name}", "#3fb950")
        self._emit_state_changed()

    def _apply_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name not in self._presets:
            return
        payload = self._presets.get(name)
        if not payload:
            return
        self._suspend_state = True
        restore_form_state(self, payload)
        self._suspend_state = False
        self._set_status(f"Loaded preset: {name}", "#3fb950")
        self._schedule_preview()
        self._emit_state_changed()

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name not in self._presets:
            return
        self._presets.pop(name, None)
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        self._set_status(f"Deleted preset: {name}")
        self._emit_state_changed()

    def _open_preset_manager(self) -> None:
        current = self._preset_combo.currentText().strip()
        if current == "Presets":
            current = ""
        dlg = PresetManagerDialog(
            self._presets,
            self._settings,
            current_preset=current or None,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.is_dirty:
            return
        self._presets = dlg.result_presets
        self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
        save_settings(self._settings)
        self._refresh_preset_combo()
        if current and current in self._presets:
            self._preset_combo.setCurrentText(current)
        self._set_status(f"Pattern presets updated ({len(self._presets)} total)")
        self._emit_state_changed()
