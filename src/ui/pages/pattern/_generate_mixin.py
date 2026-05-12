"""_GenerateMixin — DXF export/generation threading and result handling."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtWidgets import QMessageBox


class _GenerateMixin:
    """Mixin providing DXF generation methods for PatternPage."""

    def _generate(self) -> None:
        from src.ui.pages.pattern.workers import run_generate, run_generate_zones
        from src.ui.util.dialog_paths import pick_save_file

        if not self._edit_polys and not self._zones:
            QMessageBox.critical(self, "Error", "No polylines available for outline.")
            return

        out_path = pick_save_file(
            self,
            self._settings,
            "pattern_output",
            "Save pattern DXF",
            "pattern.dxf",
            "DXF files (*.dxf);;All files (*)",
            fallback_dir=self._settings.get("pattern_output_dir", ""),
        )
        if not out_path:
            return

        # Read widget values on the GUI thread (thread-safe)
        pattern = self._pattern_combo.currentText()
        include_border = self._include_border_cb.isChecked()
        open_paths = self._export_open_paths_cb.isChecked()
        invert_fill = self._invert_fill_cb.isChecked()
        mirror_v = self._mirror_v_cb.isChecked()
        mirror_h = self._mirror_h_cb.isChecked()
        try:
            border_fade = max(0.0, float(self._border_fade.text() or "0"))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None
        gen_fill_options = self._collect_fill_options()

        self._gen_btn.setEnabled(False)
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status("Generating…")

        self._generation_revision += 1
        generation_token = self._generation_revision
        _, cancel_event = self._generate_task.request_start()
        if self._zones:
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._generate_task.finish_run()
                self._gen_btn.setEnabled(True)
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
                self._set_status(str(exc), "#f85149")
                return
            threading.Thread(
                target=run_generate_zones,
                args=(
                    zones_snap,
                    out_path,
                    include_border,
                    open_paths,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    generation_token,
                    cancel_event,
                ),
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._gen_done.emit,
                    "on_error": self._gen_error.emit,
                    "fill_options": gen_fill_options,
                },
                daemon=True,
            ).start()
        else:
            polys_snap = list(self._edit_polys)
            try:
                scale = self._collect_scale()
                params = (
                    self._collect_pattern_params(pattern)
                    if pattern != "— None —"
                    else {}
                )
            except ValueError:
                self._generate_task.finish_run()
                self._gen_btn.setEnabled(True)
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
                return
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            interlace = self._interlace_cb.isChecked()
            threading.Thread(
                target=run_generate,
                args=(
                    polys_snap,
                    out_path,
                    pattern,
                    params,
                    scale,
                    border_polys,
                    open_paths,
                    interlace,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    generation_token,
                    cancel_event,
                ),
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._gen_done.emit,
                    "on_error": self._gen_error.emit,
                    "fill_options": gen_fill_options,
                },
                daemon=True,
            ).start()

    def _handle_gen_done(self, payload: tuple) -> None:
        generation_token, count, name, out_path, polys = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._gen_btn.setEnabled(True)
        self._set_status(f"Done — {count} shapes → {name}", "#3fb950")
        self._last_out_path = out_path
        self._reveal_btn.setEnabled(True)
        self._preview_polys_cache = list(polys)
        # Update canvas if preview is already showing; otherwise just cache
        if self._showing_preview:
            self._canvas.load(polys)
        self._set_preview_status(f"{count} shapes exported", "success")
        self._update_preview_controls()
        # Update export summary chip to show the actual export result
        if hasattr(self, "_summary_chip"):
            fname = Path(out_path).name
            self._summary_chip.setText(f"✓ {count} shapes exported → {fname}")
            self._summary_chip.setStyleSheet(
                "background: #0f2a17; color: #3fb950; border: 1px solid #1f6f3a;"
                "border-radius: 4px; padding: 4px 6px; font-size: 10px;"
            )
        self._refresh_canvas_panels()

    def _handle_gen_error(self, payload: tuple) -> None:
        generation_token, msg = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._gen_btn.setEnabled(True)
        self._set_status(f"Error: {msg}", "#f85149")
        if self._preview_task.has_pending() and (self._edit_polys or self._zones):
            self._preview_task.pending = False
            self._preview_timer.start(0)
