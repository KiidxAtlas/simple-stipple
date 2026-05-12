"""_SubtitlesMixin — section subtitle refresh, pattern shortcuts, palette, scale callbacks."""

from __future__ import annotations

import platform
from pathlib import Path


class _SubtitlesMixin:
    """Mixin providing subtitle refresh, shortcuts, command palette, and scale dim callbacks."""

    def _switch_pattern(self, value: str) -> None:
        for w in self._pattern_widgets.values():
            w.hide()
        self._tile_library_w.hide()
        self._update_tile_library_panel()
        if self._is_tile_pattern(value):
            self._tile_library_w.show()
            self._schedule_preview()
        elif value in self._pattern_widgets:
            self._pattern_widgets[value].show()
            self._schedule_preview()
        self._refresh_section_subtitles()

    def _refresh_section_subtitles(self) -> None:
        """Update collapsed-section subtitles and the export summary chip.

        Cheap to call — only touches QLabel text. Invoked from selection,
        pattern-switch, fill-mode, and preview-scheduling callbacks so each
        section header reflects the current configuration at a glance.
        """
        from src.ui.widgets.collapsible import CollapsibleSection

        # All sections are built lazily; bail out until _build_left has run.
        if not getattr(self, "_pattern_section", None):
            return

        # SHAPE: filename + dimensions
        path = self._dxf_edit.text().strip() if hasattr(self, "_dxf_edit") else ""
        if path:
            try:
                w = float(self._scale_w.text() or "0")
                h = float(self._scale_h.text() or "0")
            except ValueError:
                w = h = 0.0
            dims = f"{w:.1f} × {h:.1f} mm" if w and h else "—"
            self._shape_section.set_subtitle(f"{Path(path).name} · {dims}")
        else:
            self._shape_section.set_subtitle("No file loaded", dim=True)

        # PATTERN: name + key dimension where available.
        # _PATTERN_KEY_DIMS maps pattern display name → (widget_attr, unit)
        _PATTERN_KEY_DIMS: dict[str, tuple[str, str]] = {
            "Honeycomb": ("_hex_r", "mm"),
            "Gradient Honeycomb": ("_grad_r_max", "mm"),
            "Square Grid": ("_sq_spacing", "mm"),
            "Diagonal Lines": ("_diag_spacing", "mm"),
            "Concentric Rings": ("_conc_spacing", "mm"),
            "Wave Fill": ("_wave_wavelength", "mm"),
            "Sunburst": ("_sunburst_spacing", "mm"),
            "Stipple Dots": ("_stip_spacing", "mm"),
            "Brick": ("_brick_w", "mm"),
            "Mesh": ("_mesh_spacing", "mm"),
            "Basketweave": ("_basket_gap", "mm"),
            "Braid": ("_braid_spacing", "mm"),
            "Fish Scale": ("_fish_w", "mm"),
            "Voronoi": ("_vor_cells", "cells"),
            "Penrose Tiling": ("_penrose_scale", "mm"),
            "Topographic": ("_topo_spacing", "mm"),
            "Hilbert Curve": ("_hilbert_order", ""),
            "Celtic Knot": ("_celtic_cell", "mm"),
            "Lissajous": ("_liss_spacing", "mm"),
            "Golden Spiral": ("_golden_spacing", "mm"),
            "Rose Curve": ("_rose_petals", ""),
        }
        pname = (
            self._pattern_combo.currentText() if hasattr(self, "_pattern_combo") else ""
        )
        if pname and pname != "— None —":
            key_dim = ""
            if pname in _PATTERN_KEY_DIMS:
                attr, unit = _PATTERN_KEY_DIMS[pname]
                widget = getattr(self, attr, None)
                if widget is not None and hasattr(widget, "text"):
                    val = widget.text().strip()
                    if val:
                        key_dim = f" · {val} {unit}".rstrip()
            # Active modifier flags appended to pattern subtitle
            mod_parts: list[str] = []
            if getattr(self, "_interlace_cb", None) and self._interlace_cb.isChecked():
                mod_parts.append("Interlaced")
            if (
                getattr(self, "_invert_fill_cb", None)
                and self._invert_fill_cb.isChecked()
            ):
                mod_parts.append("Inverted")
            if getattr(self, "_mirror_v_cb", None) and self._mirror_v_cb.isChecked():
                mod_parts.append("↔")
            if getattr(self, "_mirror_h_cb", None) and self._mirror_h_cb.isChecked():
                mod_parts.append("↕")
            try:
                fade = (
                    float(self._border_fade.text() or "0")
                    if hasattr(self, "_border_fade")
                    else 0.0
                )
                if fade > 0:
                    mod_parts.append(f"Fade {fade:.1f}mm")
            except ValueError:
                pass
            mod_str = " · " + " · ".join(mod_parts) if mod_parts else ""
            self._pattern_section.set_subtitle(f"{pname}{key_dim}{mod_str}")
        else:
            self._pattern_section.set_subtitle("None", dim=True)

        # FILL: mode + spacing + targets + fill line count from last preview
        if hasattr(self, "_fill_mode_combo"):
            mode = self._fill_mode_combo.currentData() or "none"
            if mode == "none":
                self._fill_section.set_subtitle("None", dim=True)
            else:
                spacing = self._fill_spacing.text().strip() or "?"
                fill_targets: list[str] = []
                if (
                    getattr(self, "_fill_target_outline_cb", None)
                    and self._fill_target_outline_cb.isChecked()
                ):
                    fill_targets.append("Outline")
                if (
                    getattr(self, "_fill_target_pattern_cb", None)
                    and self._fill_target_pattern_cb.isChecked()
                ):
                    fill_targets.append("Pattern")
                target_str = " + ".join(fill_targets) if fill_targets else "No target"
                fill_line_count = len(
                    (
                        self._preview_categories
                        if hasattr(self, "_preview_categories")
                        else {}
                    ).get("fill", [])
                )
                count_str = f" · {fill_line_count} lines" if fill_line_count else ""
                self._fill_section.set_subtitle(
                    f"{self._fill_mode_combo.currentText()} · {spacing} mm · {target_str}{count_str}"
                )

        # ZONES: count
        if hasattr(self, "_zones_section") and isinstance(
            self._zones_section, CollapsibleSection
        ):
            n = len(self._zones) if hasattr(self, "_zones") else 0
            if n == 0:
                self._zones_section.set_subtitle("No zones assigned", dim=True)
            else:
                self._zones_section.set_subtitle(
                    f"{n} zone{'s' if n != 1 else ''} assigned"
                )

        # EXPORT summary chip — one-line "what will be written"
        if hasattr(self, "_summary_chip"):
            parts: list[str] = []
            if pname and pname != "— None —":
                parts.append(pname)
            if (
                hasattr(self, "_fill_mode_combo")
                and (self._fill_mode_combo.currentData() or "none") != "none"
            ):
                parts.append(f"fill {self._fill_spacing.text().strip()} mm")
            if (
                hasattr(self, "_include_border_cb")
                and self._include_border_cb.isChecked()
            ):
                parts.append("border layer")
            self._summary_chip.setText(" · ".join(parts) if parts else "Empty output")

    def _install_pattern_shortcuts(self) -> None:
        """Install ⌘E / ⌘R / ⌘K shortcuts on the Pattern tab."""
        from PySide6.QtGui import QKeySequence, QShortcut

        # Use the platform-appropriate modifier: Meta (⌘) on macOS, Ctrl elsewhere.
        modifier = "Meta" if platform.system() == "Darwin" else "Ctrl"

        QShortcut(QKeySequence(f"{modifier}+E"), self, self._generate)
        QShortcut(QKeySequence(f"{modifier}+R"), self, self._reload_dxf)
        QShortcut(QKeySequence(f"{modifier}+K"), self, self._open_command_palette)
        QShortcut(QKeySequence(f"{modifier}+P"), self, self._apply_selected_preset)

    def _open_command_palette(self) -> None:
        """Show a ⌘K palette of common Pattern-tab actions."""
        from src.ui.widgets.command_palette import CommandPaletteDialog

        commands: list[dict] = [
            {
                "title": "Export DXF",
                "shortcut": "⌘E",
                "subtitle": "Generate & save the current pattern + fill",
                "run": self._generate,
            },
            {
                "title": "Reload source DXF",
                "shortcut": "⌘R",
                "subtitle": "Re-read the outline file from disk",
                "run": self._reload_dxf,
            },
            {
                "title": "Browse for DXF…",
                "subtitle": "Pick a different outline file",
                "run": self._browse_dxf,
            },
            {
                "title": "Save preset…",
                "subtitle": "Capture current pattern parameters",
                "run": self._save_preset,
            },
            {
                "title": "Apply selected preset",
                "shortcut": "⌘P",
                "subtitle": "Load the highlighted preset parameters",
                "run": self._apply_selected_preset,
            },
            {
                "title": "Mark selected shapes as cutout",
                "subtitle": "Exclude selected outlines from laser fill",
                "run": self._mark_selection_as_cutout,
            },
            {
                "title": "Manage presets…",
                "subtitle": "Rename, duplicate, import, export",
                "run": self._open_preset_manager,
            },
            {
                "title": "Assign zone to selection",
                "subtitle": "Save current pattern as a named zone",
                "run": self._assign_zone,
            },
            {
                "title": "Clear all zones",
                "run": self._clear_zones,
            },
            {
                "title": "Clear all cutouts",
                "run": self._clear_exclusions,
            },
            {
                "title": "Toggle invert fill",
                "run": lambda: self._invert_fill_cb.setChecked(
                    not self._invert_fill_cb.isChecked()
                ),
            },
            {
                "title": "Toggle border on separate layer",
                "run": lambda: self._include_border_cb.setChecked(
                    not self._include_border_cb.isChecked()
                ),
            },
        ]
        dlg = CommandPaletteDialog(commands, parent=self)
        dlg.exec()

    # ── Dimension callbacks ───────────────────────────────────────────────────

    def _on_scale_w_changed(self, *_) -> None:
        if (
            self._updating_dims
            or not self._ar_lock_btn.isChecked()
            or self._orig_w <= 0
        ):
            return
        try:
            w = float(self._scale_w.text())
            h = w * self._orig_h / self._orig_w
            self._updating_dims = True
            self._scale_h.setText(f"{h:.3f}")
        except ValueError:
            pass
        finally:
            self._updating_dims = False

    def _on_scale_h_changed(self, *_) -> None:
        if (
            self._updating_dims
            or not self._ar_lock_btn.isChecked()
            or self._orig_h <= 0
        ):
            return
        try:
            h = float(self._scale_h.text())
            w = h * self._orig_w / self._orig_h
            self._updating_dims = True
            self._scale_w.setText(f"{w:.3f}")
        except ValueError:
            pass
        finally:
            self._updating_dims = False

    def _on_ar_toggle(self, state: int) -> None:
        self._ar_locked = bool(state)
