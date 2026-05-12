"""_PreviewMixin — live preview scheduling, threading, and result handling."""

from __future__ import annotations

import threading


class _PreviewMixin:
    """Mixin providing live-preview methods for PatternPage."""

    def _on_fill_mode_changed(self, *_) -> None:
        """Enable/disable infill spacing/angle widgets and refresh preview."""
        mode = self._fill_mode_combo.currentData()
        active = mode and mode != "none"
        self._fill_params_container.setVisible(bool(active))
        self._fill_spacing.setEnabled(bool(active))
        self._fill_angle.setEnabled(bool(active))
        self._fill_keep_outline_cb.setEnabled(bool(active))
        self._fill_target_outline_cb.setEnabled(bool(active))
        self._fill_target_pattern_cb.setEnabled(bool(active))
        self._refresh_section_subtitles()
        self._schedule_preview()

    def _collect_fill_options(self) -> dict | None:
        """Read the laser-fill widget state into a plain dict for the worker."""
        mode = self._fill_mode_combo.currentData()
        if not mode or mode == "none":
            return None
        target_outline = self._fill_target_outline_cb.isChecked()
        target_pattern = self._fill_target_pattern_cb.isChecked()
        if not target_outline and not target_pattern:
            return None
        try:
            spacing = max(0.05, float(self._fill_spacing.text() or "0.5"))
        except ValueError:
            spacing = 0.5
        try:
            angle = float(self._fill_angle.text() or "0")
        except ValueError:
            angle = 0.0
        return {
            "mode": str(mode),
            "spacing": spacing,
            "angle_deg": angle,
            "keep_pattern": self._fill_keep_outline_cb.isChecked(),
            "target_outline": target_outline,
            "target_pattern": target_pattern,
        }

    def _schedule_preview(self, *_) -> None:
        # Subtitles are cheap and reflect what the next preview will use.
        self._refresh_section_subtitles()
        if self._suspend_state:
            return
        # Allow preview if zones exist OR a fill is configured (outline + fill
        # mode), even when pattern is "— None —". Otherwise require normal
        # preconditions.
        fill_active = bool(self._collect_fill_options())
        if (
            not self._zones
            and not fill_active
            and self._pattern_combo.currentText() == "— None —"
        ):
            return
        if not self._zones and not self._edit_polys:
            return
        self._preview_revision += 1
        self._invalidate_preview_cache()
        if self._preview_task.running:
            self._preview_task.pending = True
        self._preview_timer.start(400)
        self._emit_state_changed()

    def _start_preview_thread(self) -> None:
        from src.ui.pages.pattern.workers import compute_preview, compute_preview_zones

        can_start, cancel_event = self._preview_task.request_start()
        if not can_start:
            return
        if not self._zones and not self._edit_polys:
            self._preview_task.finish_run()
            return
        self._update_preview_controls()  # show "Previewing…" on the button
        preview_token = self._preview_revision
        pattern = self._pattern_combo.currentText()
        include_border = self._include_border_cb.isChecked()
        try:
            scale = self._collect_scale()
            params = (
                self._collect_pattern_params(pattern) if pattern != "— None —" else {}
            )
            if not self._zones:
                self._validate_outline_inputs(self._edit_polys)
        except ValueError:
            self._preview_task.finish_run()
            return
        interlace = self._interlace_cb.isChecked()
        invert_fill = self._invert_fill_cb.isChecked()
        mirror_v = self._mirror_v_cb.isChecked()
        mirror_h = self._mirror_h_cb.isChecked()
        try:
            border_fade = max(0.0, float(self._border_fade.text() or "0"))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None
        fill_options = self._collect_fill_options()
        self._set_preview_status("Previewing…")
        if self._zones:
            # Zone mode: snapshot zone data + all polys for context
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._preview_task.finish_run()
                self._set_preview_status(str(exc), "error")
                self._update_preview_controls()
                return
            all_polys_snap = list(self._edit_polys)
            threading.Thread(
                target=compute_preview_zones,
                args=(
                    zones_snap,
                    all_polys_snap,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    preview_token,
                    cancel_event,
                ),
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._preview_done.emit,
                    "on_error": self._preview_error.emit,
                    "fill_options": fill_options,
                },
                daemon=True,
            ).start()
        else:
            polys_snap = list(self._edit_polys)
            border_polys = (
                self._apply_scale(polys_snap, *scale) if include_border else None
            )
            threading.Thread(
                target=compute_preview,
                args=(
                    polys_snap,
                    pattern,
                    params,
                    scale,
                    border_polys,
                    interlace,
                    invert_fill,
                    mirror_v,
                    mirror_h,
                    border_fade,
                    excl_polys,
                    preview_token,
                    cancel_event,
                ),
                kwargs={
                    "pattern_service": self._pattern_service,
                    "orig_w": self._orig_w,
                    "orig_h": self._orig_h,
                    "on_done": self._preview_done.emit,
                    "on_error": self._preview_error.emit,
                    "fill_options": fill_options,
                },
                daemon=True,
            ).start()

    def _handle_preview_done(self, payload: tuple) -> None:
        # Workers emit either (token, display, count) for legacy callers or
        # (token, display, count, categories) for the new three-layer split.
        if len(payload) == 4:
            preview_token, display_polys, count, categories = payload
        else:
            preview_token, display_polys, count = payload
            categories = None
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        was_empty = not bool(self._preview_polys_cache)
        self._preview_polys_cache = list(display_polys)
        self._preview_categories = categories or {
            "outline": [],
            "pattern": list(display_polys),
            "fill": [],
        }
        # Build per-category detail for the status line
        p_count = len(self._preview_categories.get("pattern", []))
        f_count = len(self._preview_categories.get("fill", []))
        detail_parts: list[str] = []
        if p_count:
            detail_parts.append(f"{p_count} pattern")
        if f_count:
            detail_parts.append(f"{f_count} fill")
        detail_str = " + ".join(detail_parts) if detail_parts else str(count)
        status_text = f"{count} shapes ({detail_str})"
        if self._showing_preview:
            self._canvas.load(display_polys)
            self._set_preview_status(f"{status_text} — preview", "success")
        else:
            self._set_preview_status(f"{status_text} ready — click Preview", "success")
        self._refresh_section_subtitles()  # update fill line count in subtitle
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
            self._preview_timer.start(0)

    def _handle_preview_error(self, payload: tuple) -> None:
        preview_token, msg = payload
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        self._set_preview_status(f"Preview error: {msg}", "error")
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
            self._preview_timer.start(0)

    def _set_preview_status(self, text: str, tone: str = "dim") -> None:
        self._preview_status.setText(text)
        if tone == "success":
            role = "preview-ok"
        elif tone == "error":
            role = "preview-err"
        else:
            role = "preview-dim"
        self._preview_status.setProperty("role", role)
        self._preview_status.style().unpolish(self._preview_status)
        self._preview_status.style().polish(self._preview_status)

    def _invalidate_preview_cache(self) -> None:
        had_cache = bool(self._preview_polys_cache)
        was_showing = self._showing_preview
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        if was_showing:
            # Keep preview mode active while parameters/settings refresh.
            # The canvas continues to display the last preview until the new
            # preview result arrives.
            self._preview_btn.blockSignals(True)
            self._preview_btn.setChecked(True)
            self._preview_btn.blockSignals(False)
            self._preview_btn.setProperty("active", True)
            self._preview_btn.style().unpolish(self._preview_btn)
            self._preview_btn.style().polish(self._preview_btn)
        if had_cache or was_showing:
            self._set_preview_status("Refreshing preview…")
        self._update_preview_controls()

    def _invalidate_zones_for_geometry_change(self) -> None:
        if not self._zones:
            return
        self._zones.clear()
        self._refresh_zone_list()
        self._set_status(
            "Outline changed — cleared assigned zones to avoid mismatched pattern results.",
            "#e3b341",
        )

    def _update_preview_controls(self) -> None:
        has_preview = bool(self._preview_polys_cache)
        is_computing = self._preview_task.running
        if self._showing_preview:
            self._preview_btn.setText("← Outline")
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("Return to outline editing")
        elif is_computing:
            self._preview_btn.setText("Previewing…")
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip("Generating preview in background…")
        elif has_preview:
            self._preview_btn.setText("Preview ▶")
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("Show the generated pattern preview")
        else:
            self._preview_btn.setText("Preview")
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip(
                "Preview becomes available after the current outline and parameters produce a valid result"
            )

    def _update_zone_actions(self) -> None:
        has_selection = bool(getattr(self._canvas, "sel_count", 0))
        can_assign = (not self._showing_preview) and has_selection
        self._assign_zone_btn.setEnabled(can_assign)
        self._assign_zone_btn.setToolTip(
            "Select one or more outlines to assign this pattern"
            if not can_assign
            else "Save the current pattern and parameters for the selected outlines"
        )
        self._remove_zone_btn.setEnabled(
            (not self._showing_preview) and bool(self._zones)
        )
        self._clear_zones_btn.setEnabled(
            (not self._showing_preview) and bool(self._zones)
        )
        if hasattr(self, "_mark_cutout_btn"):
            self._mark_cutout_btn.setEnabled(not self._showing_preview)
