"""_ExclusionMixin — cutout exclusion management for PatternPage."""

from __future__ import annotations


class _ExclusionMixin:
    """Mixin providing cutout exclusion methods for PatternPage."""

    def _on_canvas_cutout_toggle(self, idx: int) -> None:
        """Toggle cutout status for a canvas poly index (called from right-click menu)."""
        if self._showing_preview:
            self._canvas._show_flash("Exit preview mode to assign cutouts", 1200)
            return
        if not (0 <= idx < len(self._outline_ids)):
            return
        oid = self._outline_ids[idx]
        if oid in self._exclusion_ids:
            self._exclusion_ids.remove(oid)
        else:
            self._exclusion_ids.append(oid)
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._schedule_preview()
        self._emit_state_changed()

    def _mark_selection_as_cutout(self) -> None:
        """Mark all currently selected canvas shapes as cutout regions."""
        if self._showing_preview:
            return
        indices = list(self._canvas.get_selection_indices())
        if not indices:
            self._set_status("Select one or more shapes on canvas first.", "#e3b341")
            return
        for idx in indices:
            self._on_canvas_cutout_toggle(idx)

    def _clear_exclusions(self) -> None:
        if not self._exclusion_ids:
            return
        self._exclusion_ids.clear()
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._schedule_preview()
        self._emit_state_changed()

    def _sync_canvas_cutout_highlight(self) -> None:
        """Update canvas accent colors to reflect current cutout assignments."""
        if not hasattr(self, "_canvas"):
            return
        id_to_idx = {oid: i for i, oid in enumerate(self._outline_ids)}
        cutout_idxs = {
            id_to_idx[eid] for eid in self._exclusion_ids if eid in id_to_idx
        }
        self._canvas.set_cutout_indices(cutout_idxs)

    def _apply_cutout_callout_style(self, *, active: bool) -> None:
        """Apply dim or active styling to the cutout callout frame via QSS property."""
        active_val = "true" if active else ""
        self._cutout_callout.setProperty("active", active_val)
        self._cutout_callout.style().unpolish(self._cutout_callout)
        self._cutout_callout.style().polish(self._cutout_callout)
        self._cutout_icon.setProperty("active", active_val)
        self._cutout_icon.style().unpolish(self._cutout_icon)
        self._cutout_icon.style().polish(self._cutout_icon)
        self._cutout_status_label.setProperty("active", active_val)
        self._cutout_status_label.style().unpolish(self._cutout_status_label)
        self._cutout_status_label.style().polish(self._cutout_status_label)

    def _refresh_cutout_status(self) -> None:
        """Update the cutout callout card to reflect current assignments."""
        if not hasattr(self, "_cutout_status_label"):
            return
        n = len(self._exclusion_ids)
        if n == 0:
            self._cutout_icon.setText("ℹ")
            self._cutout_status_label.setText(
                "Right-click a shape on canvas to mark as cutout"
            )
            self._cutout_clear_btn.setVisible(False)
            self._apply_cutout_callout_style(active=False)
        else:
            self._cutout_icon.setText("✓")
            self._cutout_status_label.setText(
                f"{n} cutout{'s' if n != 1 else ''} active — shown orange on canvas"
            )
            self._cutout_clear_btn.setVisible(True)
            self._apply_cutout_callout_style(active=True)

    def _resolve_exclusion_polys(self) -> list[list[tuple[float, float]]]:
        """Return polylines for all current exclusion IDs."""
        return self._resolve_outline_ids(self._exclusion_ids)
