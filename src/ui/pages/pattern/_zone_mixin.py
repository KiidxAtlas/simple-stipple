"""_ZoneMixin — pattern zone assignment and management."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


class _ZoneMixin:
    """Mixin providing pattern zone management methods for PatternPage."""

    def _assign_zone(self) -> None:
        """Save current pattern+params as a zone for the selected outlines."""
        sel_polys = self._canvas.get_selected()
        sel_ids = [
            self._outline_ids[idx]
            for idx in self._canvas.get_selection_indices()
            if 0 <= idx < len(self._outline_ids)
        ]
        if not sel_polys:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more outlines on the canvas first, then click 'Assign'.",
            )
            return
        pattern = self._pattern_combo.currentText()
        # NOTE: "— None —" is now allowed — it means outline-only / fill-only
        # zone (e.g. a region you want filled but not patterned).
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern)
            self._validate_outline_inputs(sel_polys)
        except ValueError:
            return
        interlace = self._interlace_cb.isChecked()
        # Capture the current fill settings as the per-zone fill override
        # so each zone carries its own fill snapshot. Stored as a dict to
        # match the worker's serialization contract.
        fill_snapshot = self._collect_fill_options()
        if any(
            zone.get("outline_ids", []) == sel_ids
            and zone["pattern"] == pattern
            and zone["params"] == params
            and zone["interlace"] == interlace
            and zone["scale"] == scale
            and zone.get("fill") == fill_snapshot
            for zone in self._zones
        ):
            self._set_status("Matching zone already exists.", "#e3b341")
            return
        label = f"Zone {len(self._zones) + 1}: {pattern} ({len(sel_polys)} outline{'s' if len(sel_polys) != 1 else ''})"
        self._zones.append({
            "outline_ids": list(sel_ids),
            "pattern": pattern,
            "params": params,
            "interlace": interlace,
            "scale": scale,
            "fill": fill_snapshot,
            "label": label,
        })
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()

    def _remove_selected_zone(self) -> None:
        """Remove the currently highlighted zone from the list."""
        row = self._zone_list.currentRow()
        if 0 <= row < len(self._zones):
            del self._zones[row]
            self._refresh_zone_list()
            self._schedule_preview()
            self._emit_state_changed()

    def _clear_zones(self) -> None:
        if not self._zones:
            return
        reply = QMessageBox.question(
            self,
            "Clear all zones?",
            "This removes every assigned pattern zone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._zones.clear()
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()

    def _refresh_zone_list(self) -> None:
        if not hasattr(self, "_zone_list"):
            return
        self._zone_list.blockSignals(True)
        self._zone_list.clear()
        if self._zones:
            for zone in self._zones:
                self._zone_list.addItem(zone["label"])
        else:
            self._zone_list.addItem("No zones assigned yet")
        self._zone_list.blockSignals(False)
        if not self._zones and self._zone_list.count() > 0:
            item = self._zone_list.item(0)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._update_zone_actions()
        self._refresh_section_subtitles()
