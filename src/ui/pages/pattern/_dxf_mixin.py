"""_DxfMixin — DXF loading, outline manipulation, pattern library for PatternPage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)
from src.settings import save_settings
from src.ui.util.dialog_paths import pick_directory, pick_open_file
from src.ui.util.recent_files import KIND_DXF, KIND_IMAGE, record_recent


class _DxfMixin:
    """Mixin providing DXF file, outline, and pattern-library methods for PatternPage."""

    def _browse_dxf(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "pattern_outline_dxf",
            "Select outline DXF",
            "DXF files (*.dxf *.Dxf *.DXF);;All files (*)",
            fallback_dir=self._settings.get("outline_dxf_dir", ""),
        )
        if path:
            self._dxf_edit.setText(path)
            self._load_dxf(path)

    def load_outline_polys(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft selection",
    ) -> None:
        """Load outline polylines from another tab and prepare Pattern Fill."""
        if not polys:
            return

        incoming = [[(x, y) for x, y in poly] for poly in polys]
        self._suspend_state = True

        self._showing_preview = False
        self._preview_btn.setChecked(False)
        self._preview_btn.setProperty("active", False)
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)

        self._orig_polys = [list(poly) for poly in incoming]
        self._edit_polys = [list(poly) for poly in incoming]
        self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
        self._exclusion_ids.clear()
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        self._zones.clear()
        self._refresh_zone_list()

        self._canvas.set_polylines_state(self._edit_polys, fit=True)
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._canvas.set_mode("select")
        self._canvas.deselect_all()

        self._update_dims_from_polys(self._orig_polys)

        self._dxf_edit.setText(f"[{source_label}]")
        self._set_status(
            f"Loaded {len(self._edit_polys)} outline(s) from {source_label}", "#3fb950"
        )

        self._suspend_state = False
        self._update_preview_controls()
        self._update_zone_actions()
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _update_dims_from_polys(self, polys: list[list[tuple[float, float]]]) -> None:
        """Recompute orig_w/h and sync the scale fields from the polyline bounding box."""
        all_pts = [pt for p in polys for pt in p]
        if all_pts:
            xs, ys = zip(*all_pts)
            self._orig_w = max(xs) - min(xs)
            self._orig_h = max(ys) - min(ys)
            self._orig_dims_label.setText(f"{self._orig_w:.2f} × {self._orig_h:.2f} mm")
            self._scale_w.blockSignals(True)
            self._scale_h.blockSignals(True)
            self._scale_w.setText(f"{self._orig_w:.3f}")
            self._scale_h.setText(f"{self._orig_h:.3f}")
            self._scale_w.blockSignals(False)
            self._scale_h.blockSignals(False)
        else:
            self._orig_w = self._orig_h = 0.0
            self._orig_dims_label.setText("—")

    def _reload_dxf(self) -> None:
        path = self._dxf_edit.text().strip()
        if path:
            self._load_dxf(path)

    def _load_dxf(self, path: str) -> None:
        try:
            polys, report = load_dxf_polylines_with_report(path)
            self._orig_polys = polys
            self._edit_polys = list(polys)
            self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
            self._exclusion_ids.clear()
            self._imported_dxf_layers = [
                (name, count, False, False)
                for name, count in report.layer_counts.items()
            ]
            self._zones.clear()
            self._refresh_zone_list()
            self._canvas.load(polys)
            self._sync_canvas_cutout_highlight()
            self._refresh_cutout_status()

            self._update_dims_from_polys(polys)

            self._set_status(f"Loaded {len(polys)} polylines from {Path(path).name}")
            record_recent(self._settings, KIND_DXF, path)
            if report.has_issues:
                detail = summarize_dxf_import_report(report)
                if detail:
                    QMessageBox.warning(
                        self,
                        "DXF Import Notice",
                        f"{Path(path).name} loaded, but some DXF content could not be preserved.\n\n{detail}",
                    )
            self._update_preview_controls()
            self._update_zone_actions()
            self._schedule_preview()
            self._emit_state_changed()
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _delete_selected(self) -> None:
        if self._showing_preview:
            return
        n = self._canvas.delete_selected()
        if n:
            self._edit_polys = list(self._canvas.get_active())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Deleted {n} polyline(s). Use ↩ Undo to restore.")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _close_selected_outlines(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        changed = self._canvas.close_selected_polylines()
        if changed:
            self._edit_polys = list(self._canvas.get_polylines_state())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Closed {changed} outline(s).", "#3fb950")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()
        else:
            self._set_status("No open outlines selected.")

    def _open_selected_outlines(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        changed = self._canvas.open_selected_polylines()
        if changed:
            self._edit_polys = list(self._canvas.get_polylines_state())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status(f"Opened {changed} outline(s).", "#3fb950")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()
        else:
            self._set_status("No closed outlines selected.")

    def _undo_delete(self) -> None:
        if self._showing_preview:
            return
        if not self._canvas.undo_delete():
            self._set_status("Nothing to undo.")
        else:
            self._edit_polys = list(self._canvas.get_active())
            self._outline_ids = self._sync_outline_ids(self._edit_polys)
            self._zones.clear()
            self._refresh_zone_list()
            self._set_status("Undo: polylines restored.")
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _quick_load(self, path: str) -> None:
        self._dxf_edit.setText(path)
        self._load_dxf(path)

    def _clear_recent(self) -> None:  # pragma: no cover - kept for back-compat
        from src.ui.util.recent_files import clear_recent

        clear_recent(self._settings, KIND_DXF)

    def _reveal_in_finder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self._last_out_path:
            p = Path(self._last_out_path)
            if not p.exists():
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"The file no longer exists:\n{self._last_out_path}",
                )
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def _show_recent_menu(
        self,
    ) -> None:  # pragma: no cover - retained for API stability
        # Recent menu is now driven by RecentFilesButton; this shim keeps any
        # external callers working.
        self._recent_btn._open_menu()

    def _choose_pattern_library_dir(self) -> None:
        path = pick_directory(
            self,
            self._settings,
            "pattern_library",
            "Select pattern library folder",
            fallback_dir=self._settings.get("pattern_library_dir", ""),
        )
        if not path:
            return
        self._settings["pattern_library_dir"] = path
        save_settings(self._settings)
        self._refresh_pattern_library()

    def _refresh_pattern_library(self) -> None:
        current = self._pattern_combo.currentText()
        self._refresh_pattern_choices(current=current)
        self._update_tile_library_panel()
        if self._is_tile_pattern(self._pattern_combo.currentText()):
            self._schedule_preview()

    def _tile_pattern_label(self, path: Path, used: set[str]) -> str:
        base = f"Tile: {path.stem.replace('_', ' ').strip() or path.stem}"
        if base not in used:
            used.add(base)
            return base
        label = f"{base} ({path.parent.name})"
        used.add(label)
        return label

    def _refresh_pattern_choices(
        self,
        current: str | None = None,
        extra_tile_path: str | None = None,
    ) -> None:
        if not hasattr(self, "_pattern_combo"):
            return
        current = self._pattern_combo.currentText() if current is None else current
        library_dir = self._settings.get("pattern_library_dir", "")
        used_labels = set(self._base_patterns)
        library_patterns: dict[str, str] = {}
        paths: list[Path] = []
        if library_dir and Path(library_dir).is_dir():
            paths.extend(sorted(Path(library_dir).glob("*.dxf")))
            paths.extend(sorted(Path(library_dir).glob("*.DXF")))
        if extra_tile_path:
            extra = Path(extra_tile_path)
            if extra.exists() and extra not in paths:
                paths.append(extra)
        self._pattern_combo.blockSignals(True)
        self._pattern_combo.clear()
        self._pattern_combo.addItems(self._base_patterns)
        for path in paths:
            label = self._tile_pattern_label(path, used_labels)
            library_patterns[label] = str(path)
            self._pattern_combo.addItem(label)
        self._library_patterns = library_patterns
        target = current if self._pattern_combo.findText(current) >= 0 else "— None —"
        self._pattern_combo.setCurrentText(target)
        self._pattern_combo.blockSignals(False)

    def _is_tile_pattern(self, pattern: str) -> bool:
        return pattern in self._library_patterns

    def _update_tile_library_panel(self) -> None:
        if not hasattr(self, "_tile_library_folder_lbl"):
            return
        folder = self._settings.get("pattern_library_dir", "")
        self._tile_library_folder_lbl.setText(folder or "No pattern folder selected")
        pattern = (
            self._pattern_combo.currentText() if hasattr(self, "_pattern_combo") else ""
        )
        if self._is_tile_pattern(pattern):
            tile_path = self._library_patterns.get(pattern, "")
            self._tile_name_lbl.setText(f"{pattern}\n{tile_path}")
        else:
            self._tile_name_lbl.setText("Choose a tile pattern from the list")

    def _browse_halftone_image(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "halftone_image",
            "Select image for halftone",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*)",
            recent_kind=KIND_IMAGE,
        )
        if path:
            self._htone_img_edit.setText(path)
            self._schedule_preview()

    def use_polys_as_fill_pattern(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft selection",
    ) -> bool:
        """Persist selected geometry as a temporary tile and activate it as pattern."""
        if not polys:
            return False
        try:
            out_dir = Path(self._settings.get("pattern_output_dir", "") or "")
            if not out_dir:
                out_dir = Path.cwd() / "job"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            tile_path = out_dir / f"tile_from_selection_{stamp}.dxf"
            write_polylines_dxf(polys, str(tile_path), close=False)

            self._refresh_pattern_choices(extra_tile_path=str(tile_path))
            match_label = next(
                (
                    label
                    for label, path in self._library_patterns.items()
                    if path == str(tile_path)
                ),
                "",
            )
            if match_label:
                self._pattern_combo.setCurrentText(match_label)
            self._switch_pattern(self._pattern_combo.currentText())
            self._set_status(
                f"Using selected geometry as fill pattern ({source_label})",
                "#3fb950",
            )
            self._schedule_preview()
            self._emit_state_changed()
            return True
        except (OSError, ValueError) as exc:
            self._set_status(f"Failed to create fill pattern: {exc}", "#f85149")
            return False
