"""Manage Pattern Presets dialog.

Provides import / export / rename / duplicate / delete actions for the saved
pattern presets.  Operates on a local copy of the presets dict; the caller
reads :attr:`PresetManagerDialog.result_presets` after :meth:`exec` returns
``QDialog.Accepted``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.pages.pattern.presets import (
    export_to_file,
    import_from_file,
    merge_presets,
    reset_to_builtins,
)
from src.ui.util.dialog_paths import pick_open_file, pick_save_file


class PresetManagerDialog(QDialog):
    """Modal dialog that owns and edits a copy of the presets mapping."""

    def __init__(
        self,
        presets: dict[str, dict],
        settings: dict,
        *,
        current_preset: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._presets: dict[str, dict] = {
            name: dict(payload) for name, payload in presets.items()
        }
        self._dirty = False

        self.setWindowTitle("Manage Pattern Presets")
        self.setMinimumSize(540, 380)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(10)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        body.addWidget(self._list, stretch=1)

        sidebar = QVBoxLayout()
        sidebar.setSpacing(6)

        self._rename_btn = self._make_button(
            "Rename…", self._rename, "Rename the selected preset"
        )
        self._duplicate_btn = self._make_button(
            "Duplicate", self._duplicate, "Copy the selected preset under a new name"
        )
        self._delete_btn = self._make_button(
            "Delete", self._delete, "Remove the selected preset"
        )
        for btn in (self._rename_btn, self._duplicate_btn, self._delete_btn):
            sidebar.addWidget(btn)
        sidebar.addSpacing(12)

        self._export_sel_btn = self._make_button(
            "Export selected…",
            self._export_selected,
            "Save the selected preset to a JSON file",
        )
        self._export_all_btn = self._make_button(
            "Export all…",
            self._export_all,
            "Save every preset to a JSON file",
        )
        self._import_btn = self._make_button(
            "Import…",
            self._import,
            "Load presets from a JSON file (collisions get auto-renamed)",
        )
        for btn in (self._export_sel_btn, self._export_all_btn, self._import_btn):
            sidebar.addWidget(btn)
        sidebar.addSpacing(12)

        self._restore_btn = self._make_button(
            "Restore built-ins",
            self._restore_builtins,
            "Re-add any missing factory starter presets",
        )
        sidebar.addWidget(self._restore_btn)
        sidebar.addStretch()

        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setFixedWidth(170)
        body.addWidget(sidebar_widget)

        root.addLayout(body)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #8b949e; font-size: 11px;")
        root.addWidget(self._status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Done")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_list(select=current_preset)

    # ------------------------------------------------------------------
    # Public results
    # ------------------------------------------------------------------

    @property
    def result_presets(self) -> dict[str, dict]:
        """The (possibly mutated) presets the caller should commit."""
        return {name: dict(payload) for name, payload in self._presets.items()}

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_button(self, label: str, slot, tooltip: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    def _refresh_list(self, *, select: str | None = None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for name in sorted(self._presets, key=str.lower):
            payload = self._presets[name]
            pattern = payload.get("pattern", "?")
            item = QListWidgetItem(f"{name}    —  {pattern}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            tooltip = "\n".join(
                f"{k}: {v}" for k, v in sorted(payload.items()) if k != "pattern"
            )
            item.setToolTip(tooltip)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if select:
            self._select(select)
        elif self._list.count():
            self._list.setCurrentRow(0)
        self._update_buttons()

    def _select(self, name: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self._list.setCurrentRow(row)
                return

    def _selected_name(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        has_sel = self._selected_name() is not None
        self._rename_btn.setEnabled(has_sel)
        self._duplicate_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)
        self._export_sel_btn.setEnabled(has_sel)
        self._export_all_btn.setEnabled(bool(self._presets))

    def _set_status(self, message: str) -> None:
        self._status.setText(message)

    def _mark_dirty(self) -> None:
        self._dirty = True

    # ------------------------------------------------------------------
    # Actions: edit
    # ------------------------------------------------------------------

    def _rename(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename preset", "New name:", text=name
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == name:
            return
        if new_name in self._presets:
            QMessageBox.warning(
                self, "Rename preset", f"A preset called {new_name!r} already exists."
            )
            return
        self._presets[new_name] = self._presets.pop(name)
        self._mark_dirty()
        self._refresh_list(select=new_name)
        self._set_status(f"Renamed {name!r} → {new_name!r}")

    def _duplicate(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        base = f"{name} (copy)"
        candidate = base
        n = 2
        while candidate in self._presets:
            candidate = f"{base} {n}"
            n += 1
        self._presets[candidate] = dict(self._presets[name])
        self._mark_dirty()
        self._refresh_list(select=candidate)
        self._set_status(f"Duplicated {name!r} → {candidate!r}")

    def _delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        result = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete preset {name!r}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._mark_dirty()
        self._refresh_list()
        self._set_status(f"Deleted {name!r}")

    # ------------------------------------------------------------------
    # Actions: import / export
    # ------------------------------------------------------------------

    def _export_selected(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
        path = pick_save_file(
            self,
            self._settings,
            "pattern_preset_io",
            "Export preset",
            f"{safe or 'preset'}.json",
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            export_to_file({name: self._presets[name]}, path)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._set_status(f"Exported 1 preset → {Path(path).name}")

    def _export_all(self) -> None:
        if not self._presets:
            return
        path = pick_save_file(
            self,
            self._settings,
            "pattern_preset_io",
            "Export all presets",
            "pattern_presets.json",
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            export_to_file(self._presets, path)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._set_status(f"Exported {len(self._presets)} preset(s) → {Path(path).name}")

    def _import(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "pattern_preset_io",
            "Import presets",
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            incoming = import_from_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        if not incoming:
            QMessageBox.warning(
                self, "Import presets", "No recognizable presets found in the file."
            )
            return
        # Ask user how to handle collisions if any names overlap.
        collisions = sorted(set(self._presets) & set(incoming))
        strategy = "rename"
        if collisions:
            preview = ", ".join(collisions[:3])
            if len(collisions) > 3:
                preview += f", … (+{len(collisions) - 3} more)"
            box = QMessageBox(self)
            box.setWindowTitle("Resolve preset name collisions")
            box.setText(f"{len(collisions)} preset name(s) already exist:\n\n{preview}")
            keep_btn = box.addButton(
                "Keep both (rename)", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
            skip_btn = box.addButton(
                "Skip duplicates", QMessageBox.ButtonRole.RejectRole
            )
            box.setDefaultButton(keep_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is keep_btn:
                strategy = "rename"
            elif clicked is skip_btn:
                strategy = "skip"
            else:
                strategy = "overwrite"
        merged, summary = merge_presets(self._presets, incoming, strategy=strategy)
        self._presets = merged
        self._mark_dirty()
        self._refresh_list()
        bits = [f"{v} {k}" for k, v in summary.items() if v]
        suffix = ", ".join(bits) if bits else "no changes"
        self._set_status(f"Imported {Path(path).name}: {suffix}")

    # ------------------------------------------------------------------

    def _restore_builtins(self) -> None:
        before = set(self._presets)
        self._presets = reset_to_builtins(self._presets)
        added = sorted(set(self._presets) - before)
        if not added:
            self._set_status("All built-ins are already present.")
            return
        self._mark_dirty()
        self._refresh_list(select=added[0])
        self._set_status(f"Restored {len(added)} built-in preset(s).")

    # ------------------------------------------------------------------

__all__ = ["PresetManagerDialog"]
