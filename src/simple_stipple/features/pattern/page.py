"""Pattern Generator page."""

# isort: skip_file
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportUndefinedVariable=false

from __future__ import annotations

import logging
import platform
import tempfile
import threading
from datetime import date
from pathlib import Path

from PIL import Image
from typing import Any

from PySide6.QtCore import QSize, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.engine.formats.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    summarize_dxf_import_report,
    svg_to_dxf,
)
from simple_stipple.engine.workflows import (
    PATTERNS,
    PatternProcessor,
    RasterEngravingSpec,
    SETTINGS_KEY as PRESET_SETTINGS_KEY,
    ensure_builtins_seeded,
    export_laserstar_package,
    export_raster_job,
)
from simple_stipple.canvas.constants import DIM
from simple_stipple.features.base import BasePage
from simple_stipple.ui.components.collapsible import CollapsibleSection
from simple_stipple.ui.components.feedback import (
    clear_line_edit_error,
    parse_float_field_with_feedback,
    refresh_style,
    set_line_edit_error,
)
from simple_stipple.ui.components.focus import EscapeBlurFilter
from simple_stipple.ui.components.layout import (
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.workflow import (
    set_status_label,
    workflow_strip,
)
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN
from simple_stipple.features.pattern.session import (
    apply_pattern_workspace_state,
    clear_pattern_workspace_state,
    get_pattern_workspace_state,
)
from simple_stipple.ui.dialogs.laserstar_export_dialog import LaserStarExportDialog
from simple_stipple.features.pattern.params import (
    collect_pattern_params,
    restore_form_state,
)
from simple_stipple.features.pattern.defaults import (
    DEFAULT_BORDER_FADE,
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_SPACING,
    DEFAULT_MIN_ISLAND_AREA,
    DEFAULT_MIN_SEGMENT,
    DEFAULT_PREVIEW_QUALITY,
    FILL_SPACING_FLOOR_MM,
    PREVIEW_DEBOUNCE_MS,
    SCALE_MIN_MM,
)
from simple_stipple.features.pattern.layout import build_left, build_right, refresh_pattern_properties_panel
from simple_stipple.features.pattern.zones import (
    apply_selected_zone_edits,
    assign_zone,
    clear_zones,
    collect_zone_editor,
    highlight_zone_on_canvas,
    invalidate_zones_for_geometry_change,
    live_update_selected_zone,
    on_zone_selected,
    preview_outline_indices_for_zone,
    rebuild_zone_parameter_editor,
    refresh_zone_list,
    remove_selected_zone,
    select_zone_for_canvas_selection,
    show_zone_context_menu,
    snapshot_zone_jobs,
    sync_selected_zone_from_controls,
    update_zone_actions,
    zone_label,
    zone_output_label,
)
from simple_stipple.features.pattern.custom_tiles import (
    apply_custom_tile,
    delete_tile_motif,
    load_custom_tiles_from_disk,
    load_tile_motif,
    locate_tile_asset,
    open_custom_tiles_folder,
    repair_tile_asset,
    save_tile_motif,
    update_custom_pattern_actions,
    refresh_tile_motif_combo,
)
from simple_stipple.features.pattern.presets import (
    apply_selected_preset,
    delete_selected_preset,
    open_preset_manager,
    refresh_preset_combo,
    save_preset,
)
from simple_stipple.features.pattern.workers import CancellableTaskState

# save_settings is otherwise unused at module scope here (the seeded-preset
# path in __init__ re-imports it locally) — kept as a module attribute
# because tests monkeypatch "simple_stipple.features.pattern.page.save_settings" to
# avoid touching disk. See domain/custom_tiles.py and domain/presets.py for
# the module attributes tests must patch for those extracted call sites.
from simple_stipple.platform.config import custom_tiles_dir, save_settings  # noqa: F401
from simple_stipple.features.pattern.outlines import (
    apply_cutout_callout_style,
    clear_exclusions,
    ensure_outline_roles,
    explain_outline_role,
    generation_polys,
    mark_selection_as_cutout,
    on_canvas_cutout_toggle,
    on_canvas_outline_role_change,
    refresh_cutout_status,
    sync_canvas_cutout_highlight,
)
from simple_stipple.ui.files import pick_open_file, pick_save_file
from simple_stipple.ui.recent import KIND_DXF, KIND_IMAGE, record_recent

LOGGER = logging.getLogger(__name__)


class PatternPage(BasePage):
    _gen_done = Signal(object)  # (count, name, polys)
    _gen_error = Signal(object)
    _preview_done = Signal(object)  # (display_polys, count)
    _preview_error = Signal(object)
    sendSelectedToDraftRequested = Signal(object)
    repairTileRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)  # BasePage sets _settings and _suspend_state

        # Runtime state
        self._orig_polys: list[list[tuple[float, float]]] = []
        self._edit_polys: list[list[tuple[float, float]]] = []
        self._orig_w: float = 0.0
        self._orig_h: float = 0.0
        self._updating_dims: bool = False
        self._preview_task = CancellableTaskState()
        self._generate_task = CancellableTaskState()
        self._preview_thread: threading.Thread | None = None
        self._generate_thread: threading.Thread | None = None
        self._shutting_down = False
        self._last_out_path: str | None = None
        self._export_is_current: bool = False
        self._preview_is_stale: bool = False
        self._pending_export_after_preview: Any | None = None
        self._zones_section: CollapsibleSection
        self._zone_output_combo: QComboBox
        self._presets: dict[str, dict] = dict(self._settings.get("pattern_presets", {}))
        # Seed factory starter presets once on first run; respects deletions.
        seeded = ensure_builtins_seeded(self._settings, self._presets)
        if seeded is not self._presets or self._settings.get("pattern_presets") != seeded:
            self._presets = seeded
            self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
            try:
                from simple_stipple.platform.config import save_settings

                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        self._base_patterns: list[str] = list(PATTERNS)
        self._load_state()

        self._showing_preview: bool = False
        self._preview_user_opt_out: bool = False
        self._preview_polys_cache: list[list[tuple[float, float]]] = []
        self._preview_categories: dict[str, list[list[tuple[float, float]]]] = {
            "outline": [],
            "pattern": [],
            "fill": [],
        }
        self._preview_zone_owners: list[int | None] = []
        self._outline_ids: list[str] = []
        self._outline_roles: dict[str, str] = {}
        self._pattern_cell_cutouts: list[list[tuple[float, float]]] = []
        self._pattern_cell_instance_cutouts: list[list[tuple[float, float]]] = []
        self._preview_revision: int = 0
        self._generation_revision: int = 0
        self._pattern_service = PatternProcessor()
        # Zones own editable output settings.  Selecting a zone loads these
        # settings into the inspector; subsequent changes update it live.
        self._zones: list[dict] = []
        self._loading_zone: bool = False
        # Outline IDs marked as exclusion cutouts (pattern fills around them)
        self._exclusion_ids: list[str] = []
        self._engraving_image_path: str = ""

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._start_preview_thread)

        self._gen_done.connect(self._handle_gen_done)
        self._gen_error.connect(self._handle_gen_error)
        self._preview_done.connect(self._handle_preview_done)
        self._preview_error.connect(self._handle_preview_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)
        self._workflow_strip = workflow_strip(
            ("Choose outline", "Define zones", "Choose treatment", "Preview", "Export")
        )
        root.addWidget(self._workflow_strip)

        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 12, 12, 12)
        left.setSpacing(8)

        right_w = surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)

        self._left_panel = sidebar_panel(left_w, min_width=260, max_width=320)
        self._splitter = content_splitter(
            self._left_panel,
            right_w,
            sizes=(300, 950),
        )
        self._splitter.setCollapsible(0, True)
        self._splitter.set_responsive_secondary(0, "Pattern controls")
        root.addWidget(self._splitter, stretch=1)

        build_left(self, left)
        build_right(self, right)
        self._refresh_zone_list()
        refresh_pattern_properties_panel(self)
        self._left_esc_filter = EscapeBlurFilter(self._canvas, within=left_w)
        for edit in left_w.findChildren(QLineEdit):
            edit.installEventFilter(self._left_esc_filter)
        self._update_preview_controls()

        self.setAcceptDrops(True)

    def sizeHint(self) -> QSize:
        """Prefer a width that fits the smallest supported application window."""
        hint = super().sizeHint()
        return QSize(min(900, hint.width()), hint.height())

    def _load_state(self) -> None:
        """Load presets, tile settings, assets, motifs, and tiles from settings."""
        self._custom_tile_polys: list[list[tuple[float, float]]] = []
        self._tile_motifs: dict[str, list[list[tuple[float, float]]]] = {}
        # Seed factory starter presets once on first run; respects deletions.
        seeded = ensure_builtins_seeded(self._settings, self._presets)
        if seeded is not self._presets or self._settings.get("pattern_presets") != seeded:
            self._presets = seeded
            self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
            try:
                from simple_stipple.platform.config import save_settings

                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        raw_tile_settings = self._settings.get("custom_tile_settings", {})
        self._tile_settings: dict[str, dict] = (
            {
                str(name): dict(payload)
                for name, payload in raw_tile_settings.items()
                if isinstance(name, str) and isinstance(payload, dict)
            }
            if isinstance(raw_tile_settings, dict)
            else {}
        )
        self._applying_tile_settings = False
        raw_assets = self._settings.get("custom_tile_assets", {})
        self._tile_assets: dict[str, dict[str, str]] = (
            {
                str(name): {str(key): str(value) for key, value in payload.items()}
                for name, payload in raw_assets.items()
                if isinstance(name, str) and isinstance(payload, dict)
            }
            if isinstance(raw_assets, dict)
            else {}
        )
        raw_motifs = self._settings.get("custom_tile_motifs", {})
        if isinstance(raw_motifs, dict):
            for name, polys in raw_motifs.items():
                if not isinstance(name, str) or not isinstance(polys, list):
                    continue
                try:
                    normalized = [
                        [(float(point[0]), float(point[1])) for point in poly]
                        for poly in polys
                        if isinstance(poly, list) and len(poly) >= 2
                    ]
                except (TypeError, ValueError, IndexError):
                    continue
                if normalized:
                    self._tile_motifs[name] = normalized
        self._load_custom_tiles_from_disk()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith((".dxf", ".fvi", ".svg")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".dxf", ".fvi", ".svg")):
                self._dxf_edit.setText(path)
                self._load_outline_file(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # ── Right panel ───────────────────────────────────────────────────────────

    def _on_zoom_preset(self, value) -> None:
        if value == "fit":
            self._canvas.fit()
        else:
            self._canvas.set_zoom_percent(float(value))
        self._refresh_canvas_panels()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_preview_clicked(self, checked: bool) -> None:
        if self._preview_task.running:
            self._preview_task.cancel()
            self._preview_task.pending = False
            self._preview_btn.setChecked(False)
            self._set_preview_status("Cancelling preview; last completed result retained…")
            return
        # Explicit user action: leaving preview opts out of auto-preview
        # until the user re-engages (new outline or pattern change).
        self._preview_user_opt_out = not checked
        self._on_preview_toggled(checked)

    def _on_preview_toggled(self, checked: bool) -> None:
        """Toggle between outline editing and pattern preview display."""
        if checked and self._preview_polys_cache:
            selected_zone = self._zone_list.currentRow()
            # Switch to preview view
            self._showing_preview = True
            self._canvas.load(self._preview_polys_cache, fit=False)
            # Show the source outline as a faded ghost overlay so the user can
            # see both the outline and the generated pattern at the same time.
            if self._edit_polys:
                self._canvas.set_ghost_polylines(self._edit_polys)
            self._configure_pattern_cell_context()
            if 0 <= selected_zone < len(self._zones):
                self._highlight_zone_on_canvas(selected_zone)
            self._set_preview_status(
                f"{len(self._preview_polys_cache)} shapes — preview", "success"
            )
        elif checked and not self._preview_polys_cache:
            # No preview available yet
            self._preview_btn.setChecked(False)
            self._set_preview_status("No preview available")
            return
        else:
            # Switch back to outline editing
            self._showing_preview = False
            self._canvas.set_pattern_cell_context(set())
            self._canvas.set_ghost_polylines(None)
            if self._edit_polys:
                self._canvas.load(self._edit_polys, fit=False)
            if self._preview_polys_cache:
                self._set_preview_status("Editing outline — preview cached")
            else:
                self._set_preview_status("Adjust settings to build a preview")
            self._sync_canvas_cutout_highlight()
        self._preview_btn.setProperty("active", self._showing_preview)
        refresh_style(self._preview_btn)
        self._update_preview_controls()

    def _configure_pattern_cell_context(self) -> None:
        if not self._showing_preview:
            self._canvas.set_pattern_cell_context(set())
            return
        outline_count = len(self._preview_categories.get("outline", []))
        pattern_polys = self._preview_categories.get("pattern", [])
        indices = set(range(outline_count, outline_count + len(pattern_polys)))
        cutout_signatures = {
            self._pattern_service._poly_repeat_signature(poly)
            for poly in self._pattern_cell_cutouts
        }
        instance_signatures = {
            self._pattern_service._poly_signature(poly)
            for poly in self._pattern_cell_instance_cutouts
        }
        cutout_entity_ids = {
            f"preview_{outline_count + index}"
            for index, poly in enumerate(pattern_polys)
            if self._pattern_service._poly_repeat_signature(poly) in cutout_signatures
            or self._pattern_service._poly_signature(poly) in instance_signatures
        }
        self._canvas.set_pattern_cell_context(indices, cutout_entity_ids)

    def _preview_outline_indices_for_zone(self, zone_row: int) -> list[int]:
        return preview_outline_indices_for_zone(self, zone_row)

    def _highlight_zone_on_canvas(self, zone_row: int) -> None:
        return highlight_zone_on_canvas(self, zone_row)

    def _on_pattern_cell_cutout_toggle(self, canvas_index: int, scope: str = "repeat") -> None:
        if not self._showing_preview:
            return
        outline_count = len(self._preview_categories.get("outline", []))
        pattern_index = canvas_index - outline_count
        pattern_polys = self._preview_categories.get("pattern", [])
        if not 0 <= pattern_index < len(pattern_polys):
            return
        poly = list(pattern_polys[pattern_index])
        if scope == "instance":
            added = self._toggle_pattern_cell_instance_cutout_poly(poly)
        else:
            added = self._toggle_pattern_cell_cutout_poly(poly)
        self._canvas._show_flash(
            ("Only this cell is now a cutout" if added else "This cell is restored")
            if scope == "instance"
            else (
                "This shape is now a cutout in every tile"
                if added
                else "This shape is restored in every tile"
            ),
            1000,
        )
        self._configure_pattern_cell_context()
        self._refresh_cutout_status()
        self._schedule_preview()

    def _toggle_pattern_cell_cutout_poly(self, poly: list[tuple[float, float]]) -> bool:
        signature = self._pattern_service._poly_repeat_signature(poly)
        existing = next(
            (
                index
                for index, cutout in enumerate(self._pattern_cell_cutouts)
                if self._pattern_service._poly_repeat_signature(cutout) == signature
            ),
            None,
        )
        if existing is None:
            self._pattern_cell_cutouts.append(poly)
            return True
        del self._pattern_cell_cutouts[existing]
        return False

    def _toggle_pattern_cell_instance_cutout_poly(self, poly: list[tuple[float, float]]) -> bool:
        signature = self._pattern_service._poly_signature(poly)
        existing = next(
            (
                index
                for index, cutout in enumerate(self._pattern_cell_instance_cutouts)
                if self._pattern_service._poly_signature(cutout) == signature
            ),
            None,
        )
        if existing is None:
            self._pattern_cell_instance_cutouts.append(poly)
            return True
        del self._pattern_cell_instance_cutouts[existing]
        return False

    def _on_sel_change(self, count: int) -> None:
        if self._showing_preview:
            self._select_zone_for_canvas_selection(preview=True)
            self._update_zone_actions()
            return
        self._canvas_runtime.on_selection_change(count)  # updates toolbar
        # `_edit_polys` always mirrors the FULL canvas state — never just the
        # selection subset. Otherwise toggling preview off would only restore
        # the previously-selected shapes (and silently drop all the others).
        # If users want to pattern only specific outlines, they should create
        # zones; selection is purely for selection, not for scoping the fill.
        self._edit_polys = self._canvas.get_polylines_state()
        self._select_zone_for_canvas_selection(preview=False)
        self._update_zone_actions()
        # Update status strip selection count without rebuilding the tree.
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _select_zone_for_canvas_selection(self, *, preview: bool) -> None:
        return select_zone_for_canvas_selection(self, preview=preview)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[str]]],
    ) -> list[dict[str, Any]]:
        return self._canvas_runtime.build_layer_tree_rows(layer_view_state)

    def _on_toolbar_mode(self, value: str) -> None:
        self._canvas_runtime.on_toolbar_mode(value)
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._canvas_runtime.on_canvas_mode_change(mode)
        self._refresh_canvas_panels()

    def _on_canvas_geometry_change(self) -> None:
        if self._showing_preview:
            current = self._canvas.get_polylines_state()
            previous = list(self._preview_polys_cache)
            previous_sigs = [self._pattern_service._poly_signature(poly) for poly in previous]
            current_sigs = [self._pattern_service._poly_signature(poly) for poly in current]

            # Deleting a generated preview cell affects that instance only.
            # The context menu is the explicit route for repeating the cutout
            # across every matching tile.
            missing_sigs = set(previous_sigs) - set(current_sigs)
            removed_cells = [
                poly
                for poly in self._preview_categories.get("pattern", [])
                if self._pattern_service._poly_signature(poly) in missing_sigs
            ]
            for poly in removed_cells:
                if self._pattern_service._poly_signature(poly) not in {
                    self._pattern_service._poly_signature(item)
                    for item in self._pattern_cell_instance_cutouts
                }:
                    self._pattern_cell_instance_cutouts.append(list(poly))

            # Preview outlines are normally the same source coordinates. When
            # one is deleted, remove the matching durable outline and its zone
            # membership as well. Scaled/non-matching display-only outlines
            # are left untouched rather than risking deletion of the wrong one.
            removed_outline_sigs = {
                self._pattern_service._poly_signature(poly)
                for poly in self._preview_categories.get("outline", [])
                if self._pattern_service._poly_signature(poly) in missing_sigs
            }
            kept_polys: list[list[tuple[float, float]]] = []
            kept_ids: list[str] = []
            try:
                preview_scale = self._collect_scale()
                displayed_sources = self._apply_scale(self._edit_polys, *preview_scale)
            except ValueError:
                displayed_sources = self._edit_polys
            for source_index, (outline_id, poly) in enumerate(
                zip(self._outline_ids, self._edit_polys)
            ):
                displayed = displayed_sources[source_index]
                if self._pattern_service._poly_signature(displayed) not in removed_outline_sigs:
                    kept_ids.append(outline_id)
                    kept_polys.append(poly)
            removed_outline_count = len(self._edit_polys) - len(kept_polys)
            if removed_outline_count:
                self._edit_polys = kept_polys
                self._outline_ids = kept_ids
                self._invalidate_zones_for_geometry_change(set(kept_ids))

            # Shapes drawn while previewing have no prior generated signature;
            # promote them to source outlines so they survive regeneration.
            previous_sig_set = set(previous_sigs)
            added = [
                poly for poly, sig in zip(current, current_sigs) if sig not in previous_sig_set
            ]
            if added:
                self._edit_polys.extend([list(poly) for poly in added])
                self._outline_ids.extend(self._fresh_outline_ids(len(added)))
                self._set_status(f"Added {len(added)} outline(s) from preview.", STATUS_OK)

            if removed_cells or removed_outline_count or added:
                self._schedule_preview()
                self._emit_state_changed()
            return
        new_polys = self._canvas.get_polylines_state()
        new_outline_ids = self._sync_outline_ids(new_polys)
        if self._zones:
            self._invalidate_zones_for_geometry_change(set(new_outline_ids))
        self._edit_polys = new_polys
        self._outline_ids = new_outline_ids
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        # Select shapes on canvas without toggling preview mode — the user
        # should be able to highlight shapes in the layer tree while reviewing
        # the generated pattern.
        self._canvas_runtime.on_tree_selection_requested(indices)
        # Update toolbar and status strip without rebuilding the tree —
        # rebuilding would immediately clear the visual selection just made.
        self._canvas_runtime.on_selection_change(len(indices))
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(len(indices))

    def _on_shape_renamed(self, layer_name: str, shape_key: object, new_label: str) -> None:
        """Persist a custom display label for an outline shape."""
        self._canvas_runtime.rename_shape(layer_name, shape_key, new_label)

    def _on_send_selected_to_draft_from_canvas(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self.sendSelectedToDraftRequested.emit(polys)

    def _fit_selection(self) -> None:
        if self._showing_preview:
            self._on_preview_toggled(False)
            self._preview_btn.setChecked(False)
        if self._canvas_runtime.fit_selection():
            self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        self._canvas_runtime.refresh_canvas_panels()

    def get_preset_state(self) -> dict[str, dict]:
        return {name: dict(payload) for name, payload in self._presets.items()}

    def apply_preset_state(self, state: dict[str, dict] | None) -> None:
        presets = state or {}
        self._presets = {name: dict(payload) for name, payload in presets.items()}
        self._refresh_preset_combo()

    def shutdown(self) -> None:
        """Called by ``App.closeEvent`` before the window tears down.

        Signal any in-flight preview/generate worker to stop and give it a
        short window to actually exit, instead of leaving it to run to
        completion (or crash) against a page that's already being destroyed.
        Threads are daemon=True so the process can still exit even if a join
        times out — this is a best-effort head start, not a hard guarantee.
        """
        self._shutting_down = True
        self._preview_timer.stop()
        self._preview_revision += 1
        self._generation_revision += 1
        self._preview_task.cancel()
        self._generate_task.cancel()
        for thread in (self._preview_thread, self._generate_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)

    def get_workspace_state(self) -> dict:
        return get_pattern_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_pattern_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_pattern_workspace_state(self)

    def _set_status(self, text: str, color: str = DIM) -> None:
        set_status_label(
            self._status, text, color, hide_when_empty=False, neutral_role="status-chip"
        )

    def _parse_float_field(
        self,
        entry,
        label: str,
        **kw,
    ):
        return parse_float_field_with_feedback(entry, label, self._set_status, **kw)

    def _parse_int_field(
        self,
        entry,
        label: str,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self._parse_float_field(
            entry,
            label,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        )
        assert value is not None
        return int(value)

    def _parse_path_field(self, entry, label: str) -> str:
        value = entry.text().strip()
        if not value:
            message = f"{label} is required."
            set_line_edit_error(entry, message)
            self._set_status(message, STATUS_ERR)
            raise ValueError(message)
        clear_line_edit_error(entry)
        return value

    def _collect_scale(self) -> tuple[float, float]:
        sw = self._parse_float_field(
            self._scale_w,
            "Scale width",
            minimum=SCALE_MIN_MM,
            allow_empty=True,
        )
        sh = self._parse_float_field(
            self._scale_h,
            "Scale height",
            minimum=SCALE_MIN_MM,
            allow_empty=True,
        )
        sw = self._orig_w if sw is None else sw
        sh = self._orig_h if sh is None else sh
        return sw, sh

    def _collect_pattern_params(self, pattern: str) -> dict:
        return collect_pattern_params(self, self._pattern_key(pattern))

    @staticmethod
    def _pattern_key(label: str) -> str:
        return "Custom Tile" if label.startswith("Custom · ") else label

    @staticmethod
    def _custom_pattern_name(label: str) -> str | None:
        return label.removeprefix("Custom · ") if label.startswith("Custom · ") else None

    def _current_pattern_key(self) -> str:
        return self._pattern_key(self._pattern_combo.currentText())

    def _apply_scale(
        self,
        polys: list[list[tuple[float, float]]],
        sw: float,
        sh: float,
    ) -> list[list[tuple[float, float]]]:
        return self._pattern_service.apply_scale(
            polys,
            sw,
            sh,
            orig_w=self._orig_w,
            orig_h=self._orig_h,
        )

    def _fresh_outline_ids(self, count: int) -> list[str]:
        return self._pattern_service.fresh_outline_ids(count)

    def _sync_outline_ids(self, new_polys: list[list[tuple[float, float]]]) -> list[str]:
        return self._pattern_service.sync_outline_ids(
            new_polys,
            list(self._edit_polys),
            list(self._outline_ids),
        )

    def _resolve_outline_ids(self, ids: list[str]) -> list[list[tuple[float, float]]]:
        return self._pattern_service.resolve_outline_ids(
            ids,
            self._outline_ids,
            self._edit_polys,
        )

    def _ensure_outline_roles(self) -> None:
        return ensure_outline_roles(self)

    def _generation_polys(self) -> list[list[tuple[float, float]]]:
        return generation_polys(self)

    def _validate_outline_inputs(self, polys: list[list[tuple[float, float]]]) -> None:
        warning = self._pattern_service.validate_outline_inputs(polys)
        if warning:
            self._set_status(
                warning,
                STATUS_WARN,
            )

    def _snapshot_zone_jobs(self) -> list[dict]:
        return snapshot_zone_jobs(self)

    # ── Preview / reset ───────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._export_is_current = False
        self._preview_is_stale = False
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        self._preview_zone_owners = []
        if self._showing_preview:
            self._preview_btn.setChecked(False)
            self._on_preview_toggled(False)
        self._set_preview_status("Adjust settings to build a preview")
        self._update_preview_controls()
        self._schedule_preview()
        self._emit_state_changed()

    # ── Build (UI construction) ───────────────────────────────────────────────

    def _update_engraving_section_summaries(self, *_args) -> None:
        self._engraving_placement_section.set_subtitle(
            f"{self._engrave_w.value():.2f} × {self._engrave_h.value():.2f} mm · "
            f"{self._engrave_rotation.value():.0f}°"
        )
        appearance = "Inverted" if self._engrave_invert.isChecked() else "Normal"
        self._engraving_appearance_section.set_subtitle(
            f"Gamma {self._engrave_gamma.value():.2f} · {appearance}"
        )
        self._engraving_process_section.set_subtitle(
            f"{self._engrave_material.currentText()} · "
            f"{self._engrave_max_power.value():.0f}% · {self._engrave_speed.value():.0f} mm/s"
        )
        self._engraving_output_section.set_subtitle(
            f"{self._engrave_target.currentText()} · positioned assets"
        )
        valid = self._engrave_min_power.value() <= self._engrave_max_power.value()
        self._engraving_process_error.setText(
            "Minimum power cannot exceed maximum power. Lower Min power or raise Max power."
            if not valid
            else ""
        )
        self._engraving_process_error.setVisible(not valid)

    def _choose_engraving_image(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "pattern_engraving_image",
            "Choose engraving image",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)",
            recent_kind=KIND_IMAGE,
        )
        if not path:
            return
        self._engraving_image_path = path
        self._engraving_image_label.setText(Path(path).name)
        # Loading an image must not silently fit it to the outline. Respect
        # embedded DPI when present; otherwise retain the user's current width
        # and derive only the height needed to preserve the image aspect ratio.
        try:
            with Image.open(path) as source:
                dpi = source.info.get("dpi")
                if isinstance(dpi, tuple) and len(dpi) >= 2 and dpi[0] and dpi[1]:
                    self._engrave_w.setValue(source.width / float(dpi[0]) * 25.4)
                    self._engrave_h.setValue(source.height / float(dpi[1]) * 25.4)
                elif source.width > 0:
                    self._engrave_h.setValue(self._engrave_w.value() * source.height / source.width)
        except OSError:
            pass
        center_x, center_y = self._canvas._c2w(
            self._canvas.width() / 2.0, self._canvas.height() / 2.0
        )
        self._engrave_x.setValue(center_x - self._engrave_w.value() / 2.0)
        self._engrave_y.setValue(center_y - self._engrave_h.value() / 2.0)
        self._engraving_section.set_subtitle(Path(path).name)
        self._update_engraving_overlay()
        self._canvas.select_background_image(True)
        self._engraving_section.set_expanded(True)
        self._set_status(
            "Engraving image selected — drag inside it or use the corner handles.",
            STATUS_OK,
        )

    def _apply_engraving_material(self, *_args) -> None:
        profiles = {
            "wood": (0.10, 0.0, 60.0, 100.0, 1.05, 1),
            "polymer": (0.10, 0.0, 50.0, 150.0, 1.0, 1),
            "aluminum": (0.08, 0.0, 75.0, 200.0, 0.9, 1),
            "steel": (0.08, 0.0, 80.0, 100.0, 0.9, 1),
        }
        profile = profiles.get(str(self._engrave_material.currentData()))
        if profile is None:
            return
        interval, minimum, maximum, speed, gamma, passes = profile
        self._engrave_interval.setValue(interval)
        self._engrave_min_power.setValue(minimum)
        self._engrave_max_power.setValue(maximum)
        self._engrave_speed.setValue(speed)
        self._engrave_gamma.setValue(gamma)
        self._engrave_passes.setValue(passes)
        self._set_status(
            "Material starting profile applied — calibrate power and passes on scrap.", STATUS_WARN
        )

    def _on_engraving_canvas_transform(
        self, x: float, y: float, w: float, h: float, rotation: float = 0.0
    ) -> None:
        for field, value in (
            (self._engrave_x, x),
            (self._engrave_y, y),
            (self._engrave_w, w),
            (self._engrave_h, h),
            (self._engrave_rotation, rotation),
        ):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        self._emit_state_changed()

    def _on_engraving_selection_changed(self, selected: bool) -> None:
        if selected and hasattr(self, "_engraving_placement_section"):
            self._engraving_section.set_expanded(True)
            self._engraving_placement_section.set_expanded(True)
            self._set_status(
                "Active target: engraving image — drag, resize, or rotate it on canvas.",
                STATUS_OK,
            )
        elif self._engraving_image_path:
            self._set_status(
                "Active target: geometry — click the engraving image to edit its placement.",
                STATUS_OK,
            )

    def _update_engraving_overlay(self, *_args) -> None:
        if not self._engraving_image_path:
            return
        try:
            with Image.open(self._engraving_image_path) as source:
                overlay = source.convert("RGBA")
                overlay.putalpha(125)
                self._canvas.set_background_image(
                    overlay.copy(),
                    self._engrave_w.value(),
                    self._engrave_h.value(),
                    self._engrave_x.value(),
                    self._engrave_y.value(),
                    self._engrave_rotation.value(),
                )
                self._canvas.set_background_image_editable(
                    self._engrave_canvas_edit.isChecked(), self._on_engraving_canvas_transform
                )
        except OSError:
            self._canvas.clear_background_image()

    def _engraving_mask_polys(self) -> list[list[tuple[float, float]]]:
        if self._engrave_target.currentData() != "zone":
            return [list(poly) for poly in self._generation_polys()]
        row = self._zone_list.currentRow()
        if not 0 <= row < len(self._zones):
            raise ValueError("Select a zone before exporting a zone-clipped engraving.")
        ids = set(self._zones[row].get("outline_ids", []))
        return [list(poly) for oid, poly in zip(self._outline_ids, self._edit_polys) if oid in ids]

    def _export_pattern_engraving(self) -> None:
        if not self._engraving_image_path:
            self._set_status("Choose an engraving image first.", STATUS_WARN)
            return
        if self._engrave_min_power.value() > self._engrave_max_power.value():
            self._engraving_process_section.set_expanded(True)
            self._set_status(
                "Engraving output blocked: minimum power exceeds maximum power.", STATUS_ERR
            )
            return
        out = pick_save_file(
            self,
            self._settings,
            "pattern_engraving_output",
            "Export positioned engraving",
            f"{Path(self._engraving_image_path).stem}_pattern_engraving.png",
            "PNG image (*.png)",
        )
        if not out:
            return
        try:
            spec = RasterEngravingSpec(
                x_mm=self._engrave_x.value(),
                y_mm=self._engrave_y.value(),
                width_mm=self._engrave_w.value(),
                height_mm=self._engrave_h.value(),
                line_interval_mm=self._engrave_interval.value(),
                min_power_percent=self._engrave_min_power.value(),
                max_power_percent=self._engrave_max_power.value(),
                speed_mm_s=self._engrave_speed.value(),
                gamma=self._engrave_gamma.value(),
                invert=self._engrave_invert.isChecked(),
                passes=self._engrave_passes.value(),
                rotation_deg=self._engrave_rotation.value(),
            )
            png, _metadata, _svg = export_raster_job(
                self._engraving_image_path, out, spec, self._engraving_mask_polys()
            )
            self._set_status(f"Positioned engraving package exported → {png.name}", STATUS_OK)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Engraving Export", str(exc))

    def _refresh_export_default_label(self) -> None:
        labels = {
            "vector": "Export — Vector DXF",
            "engraving": "Export — Engraving Assets",
            "laserstar": "Export — LaserStar Package",
        }
        self._gen_btn.setText(labels[self._export_default])

    def _select_export_kind(self, kind: str) -> None:
        self._export_default = kind
        self._settings["pattern_export_default"] = kind
        self._refresh_export_default_label()
        self._emit_state_changed()
        self._run_remembered_export()

    def _run_remembered_export(self) -> None:
        if self._export_default == "engraving":
            self._export_pattern_engraving()
        elif self._export_default == "laserstar":
            self._export_laserstar_package()
        else:
            self._generate()

    def _with_current_preview(self, continuation) -> None:
        """Run a preview-dependent export after automatic validation."""
        if self._preview_polys_cache and not self._preview_is_stale:
            continuation()
            return
        has_treatment = bool(self._zones) or (
            bool(self._edit_polys) and self._current_pattern_key() != "— None —"
        )
        if not has_treatment:
            self._set_status(
                "Export needs an outline and treatment before preview validation.", STATUS_WARN
            )
            return
        self._pending_export_after_preview = continuation
        self._set_status("Validating current preview before export…", STATUS_WARN)
        self._schedule_preview()

    def _export_laserstar_package(self) -> None:
        if (
            self._engraving_image_path
            and self._engrave_min_power.value() > self._engrave_max_power.value()
        ):
            self._engraving_section.set_expanded(True)
            self._engraving_process_section.set_expanded(True)
            self._set_status(
                "LaserStar output blocked: minimum power exceeds maximum power.", STATUS_ERR
            )
            return
        self._with_current_preview(self._perform_laserstar_export)

    def _perform_laserstar_export(self) -> None:
        source_name = (
            Path(self._dxf_edit.text()).stem if self._dxf_edit.text().strip() else "stipple-job"
        )
        default_name = str(
            self._settings.get("laserstar_job_name", f"{source_name}-{date.today().isoformat()}")
        )
        dialog = LaserStarExportDialog(
            job_name=default_name,
            destination=str(
                self._settings.get("laserstar_output_dir")
                or self._settings.get("pattern_output_dir", "")
                or Path.home()
            ),
            has_engraving=bool(self._engraving_image_path),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        name = values["job_name"]
        destination = values["destination"]
        self._settings["laserstar_job_name"] = name
        self._settings["laserstar_output_dir"] = destination
        raster_spec = None
        raster_source = None
        raster_mask = None
        if self._engraving_image_path:
            raster_source = self._engraving_image_path
            raster_spec = RasterEngravingSpec(
                x_mm=self._engrave_x.value(),
                y_mm=self._engrave_y.value(),
                width_mm=self._engrave_w.value(),
                height_mm=self._engrave_h.value(),
                line_interval_mm=self._engrave_interval.value(),
                min_power_percent=self._engrave_min_power.value(),
                max_power_percent=self._engrave_max_power.value(),
                speed_mm_s=self._engrave_speed.value(),
                gamma=self._engrave_gamma.value(),
                passes=self._engrave_passes.value(),
                invert=self._engrave_invert.isChecked(),
                rotation_deg=self._engrave_rotation.value(),
            )
            try:
                raster_mask = self._engraving_mask_polys()
            except ValueError as exc:
                QMessageBox.warning(self, "LaserStar Export", str(exc))
                return
        try:
            folder = export_laserstar_package(
                destination,
                name,
                list(self._preview_polys_cache),
                raster_source=raster_source,
                raster_spec=raster_spec,
                raster_mask=raster_mask,
            )
            self._last_out_path = str(folder / "LaserStar-Setup.txt")
            self._reveal_btn.setVisible(True)
            self._operator_notes_btn.setVisible(True)
            self._set_status(f"LaserStar operator package ready → {folder.name}", STATUS_OK)
        except (FileExistsError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "LaserStar Export Failed", str(exc))

    def _copy_operator_notes(self) -> None:
        instance = QApplication.instance()
        if instance is None:
            return
        QApplication.clipboard().setText(
            "1. Open LaserStar-Setup.txt.\n"
            "2. Import the FVI into StarFX using millimeters and the preserved origin.\n"
            "3. Add the positioned engraving image if included.\n"
            "4. Verify material settings, run red trace, and make a material test."
        )
        self._set_status("Operator notes copied", STATUS_OK)

    # ── Subtitles, shortcuts, palette, scale callbacks ────────────────────────

    def _switch_pattern(self, value: str) -> None:
        self._preview_user_opt_out = False
        custom_name = self._custom_pattern_name(value)
        if custom_name and custom_name in self._tile_motifs:
            self._custom_tile_polys = [list(poly) for poly in self._tile_motifs[custom_name]]
            saved_state = self._tile_settings.get(custom_name)
            if saved_state and not self._applying_tile_settings:
                self._applying_tile_settings = True
                try:
                    restore_form_state(
                        self,
                        {
                            **saved_state,
                            "pattern": value,
                            "custom_tile_polys": self._custom_tile_polys,
                        },
                    )
                finally:
                    self._applying_tile_settings = False
        pattern_key = self._pattern_key(value)
        self._update_custom_pattern_actions(value)
        for w in self._pattern_widgets.values():
            w.hide()
        if pattern_key in self._pattern_widgets:
            self._pattern_widgets[pattern_key].show()
            self._schedule_preview()
        has_pattern = pattern_key != "— None —" and pattern_key in self._pattern_widgets
        self._modifiers_label.setVisible(has_pattern)
        self._modifiers_widget.setVisible(has_pattern)
        self._refresh_section_subtitles()

    def _update_custom_pattern_actions(self, value: str):
        return update_custom_pattern_actions(self, value)

    def use_custom_tile(self, polys: list[list[tuple[float, float]]]):
        return apply_custom_tile(self, polys)

    def _refresh_tile_motif_combo(self, current: str | None = None):
        return refresh_tile_motif_combo(self, current)

    def _load_custom_tiles_from_disk(self):
        return load_custom_tiles_from_disk(self)

    def _open_custom_tiles_folder(self):
        return open_custom_tiles_folder(self)

    def _save_tile_motif(self):
        return save_tile_motif(self)

    def _load_tile_motif(self):
        return load_tile_motif(self)

    def _delete_tile_motif(self):
        return delete_tile_motif(self)

    def _locate_tile_asset(self):
        return locate_tile_asset(self)

    def _repair_tile_asset(self):
        return repair_tile_asset(self)

    def apply_settings(self, settings: dict) -> None:
        """Apply folder changes immediately without rebuilding the page."""
        old_folder = custom_tiles_dir(self._settings.get("custom_tiles_dir"))
        self._settings = settings
        new_folder = custom_tiles_dir(settings.get("custom_tiles_dir"))
        if old_folder != new_folder and hasattr(self, "_pattern_combo"):
            self._refresh_tile_motif_combo()
            self._set_status(f"Custom tile library: {new_folder}", STATUS_OK)

    def _refresh_section_subtitles(self) -> None:
        if not getattr(self, "_pattern_section", None):
            return
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
        _PATTERN_KEY_DIMS: dict[str, tuple[str, str]] = {
            "Honeycomb": ("_hex_r", "mm"),
            "Flow Lines": ("_flow_spacing", "mm"),
            "Gradient Honeycomb": ("_grad_r_max", "mm"),
            "Stipple Dots": ("_stip_spacing", "mm"),
            "Brick": ("_brick_w", "mm"),
            "Mesh": ("_mesh_spacing", "mm"),
            "Basketweave": ("_basket_gap", "mm"),
            "Braid": ("_braid_spacing", "mm"),
            "Fish Scale": ("_fish_w", "mm"),
            "Voronoi": ("_vor_cells", "cells"),
            "Topographic": ("_topo_spacing", "mm"),
        }
        pname = self._pattern_combo.currentText() if hasattr(self, "_pattern_combo") else ""
        if pname and pname != "— None —":
            key_dim = ""
            if pname in _PATTERN_KEY_DIMS:
                attr, unit = _PATTERN_KEY_DIMS[pname]
                widget = getattr(self, attr, None)
                if widget is not None and hasattr(widget, "text"):
                    val = widget.text().strip()
                    if val:
                        key_dim = f" · {val} {unit}".rstrip()
            mod_parts: list[str] = []
            try:
                fade = (
                    float(self._border_fade.text() or DEFAULT_BORDER_FADE)
                    if hasattr(self, "_border_fade")
                    else 0.0
                )
                if fade > 0:
                    mod_parts.append(f"Fade {fade:.1f}mm")
            except ValueError:
                # A partially typed optional value is omitted from the subtitle.
                mod_parts.clear()
            mod_str = " · " + " · ".join(mod_parts) if mod_parts else ""
            self._pattern_section.set_subtitle(f"{pname}{key_dim}{mod_str}")
        else:
            self._pattern_section.set_subtitle("None", dim=True)
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
                    (self._preview_categories if hasattr(self, "_preview_categories") else {}).get(
                        "fill", []
                    )
                )
                count_str = f" · {fill_line_count} lines" if fill_line_count else ""
                self._fill_section.set_subtitle(
                    f"{self._fill_mode_combo.currentText()} · {spacing} mm · {target_str}{count_str}"
                )
        if hasattr(self, "_zones_section") and isinstance(self._zones_section, CollapsibleSection):
            n = len(self._zones) if hasattr(self, "_zones") else 0
            if n == 0:
                self._zones_section.set_subtitle("No zones assigned", dim=True)
            else:
                self._zones_section.set_subtitle(f"{n} zone{'s' if n != 1 else ''} assigned")
        if hasattr(self, "_summary_chip"):
            parts: list[str] = []
            if pname and pname != "— None —":
                parts.append(pname)
            if (
                hasattr(self, "_fill_mode_combo")
                and (self._fill_mode_combo.currentData() or "none") != "none"
            ):
                parts.append(f"fill {self._fill_spacing.text().strip()} mm")
            if hasattr(self, "_include_border_cb") and self._include_border_cb.isChecked():
                parts.append("border layer")
            self._summary_chip.setText(" · ".join(parts) if parts else "Empty output")
            # Any settings change supersedes a prior export's success banner.
            if self._summary_chip.property("tone") != "neutral":
                self._summary_chip.setProperty("tone", "neutral")
                refresh_style(self._summary_chip)

    def _install_pattern_shortcuts(self) -> None:
        modifier = "Meta" if platform.system() == "Darwin" else "Ctrl"
        QShortcut(QKeySequence(f"{modifier}+E"), self, self._generate)
        QShortcut(QKeySequence(f"{modifier}+R"), self, self._reload_dxf)
        QShortcut(QKeySequence(f"{modifier}+P"), self, self._apply_selected_preset)

    def command_palette_commands(self) -> list[dict]:
        """Commands contributed to the application's single global palette."""
        modifier = "Meta" if platform.system() == "Darwin" else "Ctrl"

        def shortcut(key: str) -> str:
            return QKeySequence(f"{modifier}+{key}").toString(
                QKeySequence.SequenceFormat.NativeText
            )

        commands: list[dict] = [
            {
                "title": "Export DXF",
                "shortcut": shortcut("E"),
                "subtitle": "Generate & save the current pattern + fill",
                "run": self._generate,
            },
            {
                "title": "Reload source DXF",
                "shortcut": shortcut("R"),
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
                "shortcut": shortcut("P"),
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
            {"title": "Clear all zones", "run": self._clear_zones},
            {"title": "Clear all cutouts", "run": self._clear_exclusions},
            {
                "title": "Toggle border on separate layer",
                "run": lambda: self._include_border_cb.setChecked(
                    not self._include_border_cb.isChecked()
                ),
            },
        ]
        return commands

    def _on_scale_w_changed(self, *_) -> None:
        if self._updating_dims or not self._ar_lock_btn.isChecked() or self._orig_w <= 0:
            return
        try:
            w = float(self._scale_w.text())
            h = w * self._orig_h / self._orig_w
            self._updating_dims = True
            self._scale_h.setText(f"{h:.3f}")
        except ValueError:
            return
        finally:
            self._updating_dims = False

    def _on_scale_h_changed(self, *_) -> None:
        if self._updating_dims or not self._ar_lock_btn.isChecked() or self._orig_h <= 0:
            return
        try:
            h = float(self._scale_h.text())
            w = h * self._orig_w / self._orig_h
            self._updating_dims = True
            self._scale_w.setText(f"{w:.3f}")
        except ValueError:
            return
        finally:
            self._updating_dims = False

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_fill_mode_changed(self, *_) -> None:
        mode = self._fill_mode_combo.currentData()
        active = mode and mode != "none"
        self._fill_params_container.setVisible(bool(active))
        self._fill_spacing.setEnabled(bool(active))
        self._fill_angle.setEnabled(bool(active))
        self._fill_angle.setEnabled(bool(active and mode != "concentric"))
        self._fill_inset.setEnabled(bool(active))
        self._fill_keep_outline_cb.setEnabled(bool(active))
        self._fill_target_outline_cb.setEnabled(bool(active))
        self._fill_target_pattern_cb.setEnabled(bool(active))
        self._refresh_section_subtitles()
        self._schedule_preview()

    def _collect_fill_options(self) -> dict | None:
        mode = self._fill_mode_combo.currentData()
        if not mode or mode == "none":
            return None
        target_outline = self._fill_target_outline_cb.isChecked()
        target_pattern = self._fill_target_pattern_cb.isChecked()
        if not target_outline and not target_pattern:
            return None
        try:
            spacing = max(
                FILL_SPACING_FLOOR_MM, float(self._fill_spacing.text() or DEFAULT_FILL_SPACING)
            )
        except ValueError:
            spacing = 0.5
        try:
            angle = float(self._fill_angle.text() or DEFAULT_FILL_ANGLE)
        except ValueError:
            angle = 0.0
        try:
            inset = max(0.0, float(self._fill_inset.text() or DEFAULT_FILL_INSET))
        except ValueError:
            inset = 0.0
        return {
            "mode": str(mode),
            "spacing": spacing,
            "angle_deg": angle,
            "keep_pattern": self._fill_keep_outline_cb.isChecked(),
            "target_outline": target_outline,
            "target_pattern": target_pattern,
            "inset": inset,
            "cell_cutouts": [list(poly) for poly in self._pattern_cell_cutouts],
            "cell_instance_cutouts": [list(poly) for poly in self._pattern_cell_instance_cutouts],
        }

    def _collect_fabrication_options(self) -> dict:
        try:
            minimum_segment = max(
                0.0, float(self._minimum_segment_edit.text() or DEFAULT_MIN_SEGMENT)
            )
        except ValueError:
            minimum_segment = 0.0
        try:
            minimum_area = max(
                0.0, float(self._minimum_area_edit.text() or DEFAULT_MIN_ISLAND_AREA)
            )
        except ValueError:
            minimum_area = 0.0
        return {
            "minimum_segment": minimum_segment,
            "minimum_area": minimum_area,
            "optimize_order": self._optimize_paths_cb.isChecked(),
        }

    def _refresh_preset_combo(self):
        return refresh_preset_combo(self)

    def _save_preset(self):
        return save_preset(self)

    def _apply_selected_preset(self):
        return apply_selected_preset(self)

    def _delete_selected_preset(self):
        return delete_selected_preset(self)

    def _open_preset_manager(self):
        return open_preset_manager(self)

    # ── DXF loading, outlines, pattern library ────────────────────────────────

    def _browse_dxf(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "pattern_outline_dxf",
            "Select outline vector file",
            "Vector files (*.dxf *.DXF *.fvi *.FVI *.svg *.SVG);;"
            "DXF files (*.dxf *.DXF);;FVI files (*.fvi *.FVI);;"
            "SVG files (*.svg *.SVG);;All files (*)",
            fallback_dir=self._settings.get("outline_dxf_dir", ""),
        )
        if path:
            self._dxf_edit.setText(path)
            self._load_outline_file(path)

    def _load_outline_file(self, path: str) -> None:
        suffix = Path(path).suffix.lower()
        if suffix == ".dxf":
            self._load_dxf(path)
            return
        try:
            if suffix == ".fvi":
                document = read_fvi(path)
                polys = [list(poly) for poly in document.paths]
            elif suffix == ".svg":
                with tempfile.TemporaryDirectory(prefix="simple-stipple-pattern-svg-") as folder:
                    converted = Path(folder) / "outline.dxf"
                    svg_to_dxf(path, converted)
                    polys, _report = load_dxf_polylines_with_report(str(converted))
            else:
                raise ValueError("Choose a DXF, FVI, or SVG vector file.")
            if not polys:
                raise ValueError(f"No supported outline geometry was found in {Path(path).name}.")
            self.load_outline_polys(polys, source_label=Path(path).name)
            self._dxf_edit.setText(path)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))

    def load_outline_polys(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft selection",
        offer_undo: bool = False,
    ) -> None:
        if not polys:
            return
        self._pre_transfer_state = self.get_workspace_state() if offer_undo else None
        incoming = [[(x, y) for x, y in poly] for poly in polys]
        self._suspend_state = True
        self._preview_user_opt_out = False
        self._showing_preview = False
        self._preview_btn.setChecked(False)
        self._preview_btn.setProperty("active", False)
        refresh_style(self._preview_btn)
        self._orig_polys = [list(poly) for poly in incoming]
        self._edit_polys = [list(poly) for poly in incoming]
        self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
        self._exclusion_ids.clear()
        self._export_is_current = False
        self._preview_is_stale = False
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
            f"Loaded {len(self._edit_polys)} outline(s) from {source_label}", STATUS_OK
        )
        self._undo_transfer_btn.setVisible(bool(offer_undo))
        self._suspend_state = False
        self._update_preview_controls()
        self._update_zone_actions()
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _undo_outline_transfer(self) -> None:
        previous = getattr(self, "_pre_transfer_state", None)
        if not isinstance(previous, dict):
            return
        self.apply_workspace_state(previous)
        self._pre_transfer_state = None
        self._undo_transfer_btn.hide()
        self._set_status("Transfer undone; previous Pattern outline restored", STATUS_OK)
        self._emit_state_changed()

    def _update_dims_from_polys(self, polys: list[list[tuple[float, float]]]) -> None:
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
            self._load_outline_file(path)

    def _load_dxf(self, path: str) -> None:
        self._preview_user_opt_out = False
        try:
            polys, report = load_dxf_polylines_with_report(path)
            self._orig_polys = polys
            self._edit_polys = list(polys)
            self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
            self._exclusion_ids.clear()
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
            self._set_status(f"Closed {changed} outline(s).", STATUS_OK)
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
            self._set_status(f"Opened {changed} outline(s).", STATUS_OK)
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()
        else:
            self._set_status("No closed outlines selected.")

    def _quick_load(self, path: str) -> None:
        self._dxf_edit.setText(path)
        self._load_dxf(path)

    def _reveal_in_finder(self) -> None:
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

    def _refresh_pattern_choices(self, current: str | None = None) -> None:
        if not hasattr(self, "_pattern_combo"):
            return
        self._populate_pattern_combo(self._pattern_combo, current)
        if hasattr(self, "_zone_pattern_combo"):
            self._populate_pattern_combo(self._zone_pattern_combo)

    @staticmethod
    def _zone_output_label(mode: str) -> str:
        return zone_output_label(mode)

    def _zone_label(self, zone: dict, index: int) -> str:
        return zone_label(self, zone, index)

    def _sync_selected_zone_from_controls(self) -> None:
        return sync_selected_zone_from_controls(self)

    def _populate_pattern_combo(self, combo: QComboBox, current: str | None = None) -> None:
        current = combo.currentText() if current is None else current
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._base_patterns)
        if self._tile_motifs:
            combo.insertSeparator(combo.count())
            for name in sorted(self._tile_motifs, key=str.casefold):
                combo.addItem(f"Custom · {name}")
        target = current if combo.findText(current) >= 0 else "— None —"
        combo.setCurrentText(target)
        combo.blockSignals(False)
        if combo is getattr(self, "_pattern_combo", None):
            self._update_custom_pattern_actions(target)

    def _rebuild_zone_parameter_editor(
        self, _label: str | None = None, params: dict | None = None
    ) -> None:
        return rebuild_zone_parameter_editor(self, _label, params)

    def _collect_zone_editor(self) -> tuple[str, dict, dict | None]:
        return collect_zone_editor(self)

    def _apply_selected_zone_edits(self) -> None:
        return apply_selected_zone_edits(self)

    def _live_update_selected_zone(self, *_args) -> None:
        return live_update_selected_zone(self, *_args)

    def _show_zone_context_menu(self, pos) -> None:
        return show_zone_context_menu(self, pos)

    def _on_zone_selected(self, row: int) -> None:
        return on_zone_selected(self, row)

    def _assign_zone(self) -> None:
        return assign_zone(self)

    def _remove_selected_zone(self) -> None:
        return remove_selected_zone(self)

    def _clear_zones(self) -> None:
        return clear_zones(self)

    def _refresh_zone_list(self) -> None:
        return refresh_zone_list(self)

    # ── Exclusions (cutouts) ──────────────────────────────────────────────────

    def _on_canvas_cutout_toggle(self, idx: int):
        return on_canvas_cutout_toggle(self, idx)

    def _on_canvas_outline_role_change(self, idx: int, role: str):
        return on_canvas_outline_role_change(self, idx, role)

    def _explain_outline_role(self, idx: int):
        return explain_outline_role(self, idx)

    def _mark_selection_as_cutout(self):
        return mark_selection_as_cutout(self)

    def _clear_exclusions(self):
        return clear_exclusions(self)

    def _sync_canvas_cutout_highlight(self):
        return sync_canvas_cutout_highlight(self)

    def _apply_cutout_callout_style(self, *, active: bool):
        return apply_cutout_callout_style(self, active=active)

    def _refresh_cutout_status(self):
        return refresh_cutout_status(self)

    def _resolve_exclusion_polys(self) -> list[list[tuple[float, float]]]:
        return self._resolve_outline_ids(self._exclusion_ids)

    # ── Presets ───────────────────────────────────────────────────────────────

    def _schedule_preview(self, *_) -> None:
        if self._shutting_down:
            return
        self._refresh_section_subtitles()
        if self._suspend_state:
            return
        # Parameter-only work is still workspace state. Emit before the
        # geometry guards so configuring a future pattern can be saved,
        # recovered, and protected by the close confirmation.
        self._emit_state_changed()
        fill_active = bool(self._collect_fill_options())
        if not self._zones and not fill_active and self._current_pattern_key() == "— None —":
            return
        if not self._zones and not self._edit_polys:
            return
        self._preview_revision += 1
        self._invalidate_preview_cache()
        if self._preview_task.running:
            self._preview_task.pending = True
        self._preview_timer.start(PREVIEW_DEBOUNCE_MS)

    def has_workspace_content(self) -> bool:
        return bool(
            self._edit_polys
            or self._zones
            or self._dxf_edit.text().strip()
            or self._current_pattern_key() != "— None —"
            or self._custom_tile_polys
            or self._pattern_cell_cutouts
            or self._pattern_cell_instance_cutouts
        )

    def _start_preview_thread(self) -> None:
        if self._shutting_down:
            return
        from simple_stipple.features.pattern.workers import compute_preview, compute_preview_zones

        can_start, cancel_event = self._preview_task.request_start()
        if not can_start:
            return
        if not self._zones and not self._edit_polys:
            self._preview_task.finish_run()
            return
        self._update_preview_controls()
        preview_token = self._preview_revision
        pattern = self._current_pattern_key()
        include_border = self._include_border_cb.isChecked()
        try:
            scale = self._collect_scale()
            params = self._collect_pattern_params(pattern) if pattern != "— None —" else {}
            params["quality"] = self._preview_quality_combo.currentData() or DEFAULT_PREVIEW_QUALITY
            if not self._zones:
                self._validate_outline_inputs(self._edit_polys)
        except ValueError as exc:
            self._preview_task.finish_run()
            self._set_preview_status(str(exc), "error")
            self._update_preview_controls()
            return
        interlace = invert_fill = mirror_v = mirror_h = False
        try:
            border_fade = max(0.0, float(self._border_fade.text() or DEFAULT_BORDER_FADE))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None
        fill_options = self._collect_fill_options()
        self._set_preview_status("Previewing…")
        if self._zones:
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._preview_task.finish_run()
                self._set_preview_status(str(exc), "error")
                self._update_preview_controls()
                return
            all_polys_snap = self._generation_polys()
            self._preview_thread = threading.Thread(
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
            )
            self._preview_thread.start()
        else:
            polys_snap = self._generation_polys()
            border_polys = self._apply_scale(polys_snap, *scale) if include_border else None
            self._preview_thread = threading.Thread(
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
            )
            self._preview_thread.start()

    def _handle_preview_done(self, payload: tuple) -> None:
        if self._shutting_down:
            return
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
        self._preview_polys_cache = list(display_polys)
        self._preview_is_stale = False
        self._preview_categories = categories or {
            "outline": [],
            "pattern": list(display_polys),
            "fill": [],
        }
        owners = self._preview_categories.get("zone_owners", [])
        self._preview_zone_owners = (
            [owner if isinstance(owner, int) else None for owner in owners]
            if isinstance(owners, list)
            else []
        )
        generated_signatures = {
            self._pattern_service._poly_signature(poly)
            for poly in self._preview_categories.get("pattern", [])
        }
        self._pattern_cell_cutouts = [
            poly
            for poly in self._pattern_cell_cutouts
            if self._pattern_service._poly_signature(poly) in generated_signatures
        ]
        self._pattern_cell_instance_cutouts = [
            poly
            for poly in self._pattern_cell_instance_cutouts
            if self._pattern_service._poly_signature(poly) in generated_signatures
        ]
        from simple_stipple.engine.workflows import diagnose_output

        diagnostics = diagnose_output(
            self._preview_categories.get("pattern", []) + self._preview_categories.get("fill", [])
        )
        p_count = len(self._preview_categories.get("pattern", []))
        f_count = len(self._preview_categories.get("fill", []))
        detail_parts: list[str] = []
        if p_count:
            detail_parts.append(f"{p_count} pattern")
        if f_count:
            detail_parts.append(f"{f_count} fill")
        detail_str = " + ".join(detail_parts) if detail_parts else str(count)
        status_text = (
            f"{count} shapes ({detail_str}) · {diagnostics.total_length:.1f} mm path"
            f" · {diagnostics.travel_length:.1f} mm travel"
        )
        if self._showing_preview:
            selected_zone = self._zone_list.currentRow()
            self._canvas.load(display_polys, fit=False)
            self._configure_pattern_cell_context()
            if 0 <= selected_zone < len(self._zones):
                self._highlight_zone_on_canvas(selected_zone)
            self._set_preview_status(f"{status_text} — preview", "success")
        elif self._should_auto_preview():
            self._preview_btn.setChecked(True)
            self._on_preview_toggled(True)
        else:
            self._set_preview_status(f"{status_text} ready — click Preview", "success")
        self._refresh_section_subtitles()
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
            self._preview_timer.start(0)
        else:
            pending_export = self._pending_export_after_preview
            self._pending_export_after_preview = None
            if pending_export is not None:
                QTimer.singleShot(0, pending_export)

    def _should_auto_preview(self) -> bool:
        if not self._auto_preview_cb.isChecked():
            return False
        if self._preview_user_opt_out:
            return False
        if getattr(self._canvas, "_mode", "select") != "select":
            return False
        # Zone editing intentionally keeps the zone's source outlines
        # selected. That selection is context, not an in-progress geometry
        # gesture, so it must not suppress the zone result.
        if self._zones:
            return True
        return not getattr(self._canvas, "sel_count", 0)

    def _handle_preview_error(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        from simple_stipple.features.pattern.workers import CANCELLED_MESSAGE

        preview_token, msg = payload
        restart = self._preview_task.finish_run()
        if preview_token != self._preview_revision:
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        if msg == CANCELLED_MESSAGE:
            self._pending_export_after_preview = None
            self._update_preview_controls()
            if restart and (self._edit_polys or self._zones):
                self._preview_timer.start(0)
            return
        self._set_preview_status(f"Preview error: {msg}", "error")
        if self._pending_export_after_preview is not None:
            self._pending_export_after_preview = None
            self._set_status(f"Export blocked — preview validation failed: {msg}", STATUS_ERR)
        if self._showing_preview:
            self._canvas.setToolTip("Preview refresh failed; showing the last completed result.")
        self._update_preview_controls()
        self._refresh_canvas_panels()
        if restart and (self._edit_polys or self._zones):
            self._preview_timer.start(0)

    def _set_preview_status(self, text: str, tone: str = "dim") -> None:
        self._preview_status.setText(text)
        self._preview_status.setAccessibleName("Pattern preview status")
        self._preview_status.setAccessibleDescription(text)
        if tone == "success":
            role = "preview-ok"
        elif tone == "error":
            role = "preview-err"
        else:
            role = "preview-dim"
        self._preview_status.setProperty("role", role)
        refresh_style(self._preview_status)

    def _invalidate_preview_cache(self) -> None:
        self._export_is_current = False
        had_cache = bool(self._preview_polys_cache)
        was_showing = self._showing_preview
        self._preview_is_stale = had_cache or was_showing
        self._preview_polys_cache = []
        # Keep the last-good preview metadata while it remains on canvas so
        # generated geometry can still select its owning zone during a live
        # rebuild.  Replace it atomically when the worker completes.
        if not was_showing:
            self._preview_categories = {"outline": [], "pattern": [], "fill": []}
            self._preview_zone_owners = []
        if was_showing:
            self._preview_btn.blockSignals(True)
            self._preview_btn.setChecked(True)
            self._preview_btn.blockSignals(False)
            self._preview_btn.setProperty("active", True)
            refresh_style(self._preview_btn)
            self._canvas.setToolTip(
                "Refreshing preview — geometry shown is the last completed result."
            )
        if had_cache or was_showing:
            self._set_preview_status("Refreshing preview…")
        self._update_preview_controls()

    def _invalidate_zones_for_geometry_change(self, valid_outline_ids: set[str]) -> None:
        return invalidate_zones_for_geometry_change(self, valid_outline_ids)

    def _update_preview_controls(self) -> None:
        has_preview = bool(self._preview_polys_cache)
        is_computing = self._preview_task.running
        has_outline = bool(self._edit_polys or self._zones)
        has_treatment = bool(self._zones) or (
            has_outline and self._current_pattern_key() != "— None —"
        )
        if self._export_is_current:
            workflow_states = ["complete", "complete", "complete", "complete", "current"]
        elif self._preview_is_stale and has_treatment:
            workflow_states = ["complete", "complete", "complete", "stale", "pending"]
        elif has_preview:
            workflow_states = ["complete", "complete", "complete", "current", "pending"]
        elif has_treatment:
            workflow_states = ["complete", "complete", "current", "pending", "pending"]
        elif has_outline:
            workflow_states = ["complete", "current", "pending", "pending", "pending"]
        else:
            workflow_states = ["current", "pending", "pending", "pending", "pending"]
        reasons = {}
        if self._preview_is_stale:
            reasons[3] = "Preview is stale because an outline or treatment input changed"
            reasons[4] = "Export is unavailable until preview validation completes"
        self._workflow_strip.set_step_states(workflow_states, reasons)
        if self._showing_preview:
            self._preview_btn.setText("Edit Outline")
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip(
                "Checked: showing preview. Click to return to outline editing"
            )
        elif is_computing:
            self._preview_btn.setText("Show Preview")
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip("Preview is computing")
        elif has_preview:
            self._preview_btn.setText("Show Preview")
            self._preview_btn.setEnabled(True)
            self._preview_btn.setToolTip("Show the generated pattern preview")
        else:
            self._preview_btn.setText("Show Preview")
            self._preview_btn.setEnabled(False)
            self._preview_btn.setToolTip(
                "Preview becomes available after the current outline and parameters produce a valid result"
            )
        self._cancel_preview_btn.setVisible(is_computing)
        if hasattr(self, "_gen_btn"):
            pattern_ready = self._current_pattern_key() != "— None —"
            can_export = bool(self._edit_polys) and (pattern_ready or bool(self._zones))
            self._gen_btn.setEnabled(
                can_export and not self._generate_task.running and not self._preview_task.running
            )
            if self._generate_task.running:
                export_tip = "Stop the current pattern export"
            elif self._preview_task.running:
                export_tip = "Preview is still computing; export will be available when it finishes"
            elif can_export:
                export_tip = "Generate the pattern fill and save as a DXF  (⌘E)"
            else:
                export_tip = "Load an outline and choose a pattern before exporting"
            self._gen_btn.setToolTip(export_tip)
            if hasattr(self, "_export_actions"):
                self._export_actions["engraving"].setEnabled(bool(self._engraving_image_path))
            open_outlines = sum(
                1 for poly in self._edit_polys if len(poly) > 1 and poly[0] != poly[-1]
            )
            if not self._edit_polys:
                preflight = "Preflight · Load an outline to begin"
                preflight_tone = "neutral"
            elif self._preview_task.running:
                preflight = "Preflight · Preview is still computing"
                preflight_tone = "neutral"
            elif open_outlines and not self._export_open_paths_cb.isChecked():
                preflight = (
                    f"Preflight · {open_outlines} open outline"
                    f"{'s' if open_outlines != 1 else ''} require attention"
                )
                preflight_tone = "warn"
            elif self._preview_polys_cache:
                preflight = (
                    f"Ready · {len(self._preview_polys_cache)} output paths · "
                    f"{len(self._zones) or 1} zone{'s' if len(self._zones) != 1 else ''}"
                )
                preflight_tone = "success"
            else:
                preflight = "Preflight · Update the preview before export"
                preflight_tone = "warn"
            self._summary_chip.setText(preflight)
            self._summary_chip.setProperty("tone", preflight_tone)
            refresh_style(self._summary_chip)
        # Keep the core Pattern controls discoverable in the empty state.
        # They serve as editable defaults before an outline or zone exists.
        if hasattr(self, "_zones_section"):
            self._zones_section.setVisible(True)
        if hasattr(self, "_pattern_properties_scroll"):
            self._pattern_properties_scroll.setVisible(True)

    def _update_zone_actions(self) -> None:
        return update_zone_actions(self)

    # ── Generation ────────────────────────────────────────────────────────────

    def _generate(self) -> None:
        from simple_stipple.features.pattern.workers import run_generate, run_generate_zones

        if self._generate_task.running:
            self._cancel_generation()
            return
        if not self._edit_polys and not self._zones:
            self._set_status("Load an outline before exporting.", STATUS_WARN)
            return
        pattern = self._current_pattern_key()
        try:
            zones_snap = self._snapshot_zone_jobs() if self._zones else None
            scale = self._collect_scale() if not self._zones else None
            params = (
                self._collect_pattern_params(pattern)
                if not self._zones and pattern != "— None —"
                else {}
            )
            params["quality"] = "high"
        except ValueError as exc:
            self._set_status(str(exc), STATUS_ERR)
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
        include_border = self._include_border_cb.isChecked()
        open_paths = self._export_open_paths_cb.isChecked()
        invert_fill = mirror_v = mirror_h = False
        try:
            border_fade = max(0.0, float(self._border_fade.text() or DEFAULT_BORDER_FADE))
        except ValueError:
            border_fade = 0.0
        excl_polys = self._resolve_exclusion_polys() or None
        gen_fill_options = self._collect_fill_options()
        fabrication_options = self._collect_fabrication_options()
        self._gen_btn.setEnabled(False)
        self._cancel_generate_btn.setEnabled(True)
        self._cancel_generate_btn.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._set_status("Generating…")
        self._generation_revision += 1
        generation_token = self._generation_revision
        _, cancel_event = self._generate_task.request_start()
        if self._zones:
            assert zones_snap is not None
            self._generate_thread = threading.Thread(
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
                    "canvas_polys": self._generation_polys(),
                    "fabrication_options": fabrication_options,
                },
                daemon=True,
            )
            self._generate_thread.start()
        else:
            polys_snap = self._generation_polys()
            assert scale is not None
            border_polys = self._apply_scale(polys_snap, *scale) if include_border else None
            interlace = False
            self._generate_thread = threading.Thread(
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
                    "fabrication_options": fabrication_options,
                },
                daemon=True,
            )
            self._generate_thread.start()

    def _handle_gen_done(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        generation_token, count, name, out_path, polys = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._update_preview_controls()
        self._cancel_generate_btn.setVisible(False)
        self._set_status(f"Done — {count} shapes → {name}", STATUS_OK)
        self._last_out_path = out_path
        self._export_is_current = True
        self._reveal_btn.setVisible(True)
        self._preview_polys_cache = list(polys)
        self._preview_is_stale = False
        if self._showing_preview:
            self._canvas.load(polys)
        self._set_preview_status(f"{count} shapes exported", "success")
        self._update_preview_controls()
        if hasattr(self, "_summary_chip"):
            fname = Path(out_path).name
            self._summary_chip.setText(f"✓ {count} shapes exported → {fname}")
            self._summary_chip.setProperty("tone", "success")
            refresh_style(self._summary_chip)
        self._refresh_canvas_panels()

    def _handle_gen_error(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        from simple_stipple.features.pattern.workers import CANCELLED_MESSAGE

        generation_token, msg = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._gen_btn.setEnabled(True)
        self._cancel_generate_btn.setVisible(False)
        # Refresh the workflow strip / preview controls so a failed run doesn't
        # leave them looking mid-flight, and clear any stale success summary.
        self._update_preview_controls()
        if msg == CANCELLED_MESSAGE:
            self._set_status("Generation cancelled", STATUS_WARN)
            summary_text, summary_tone = "Generation cancelled", "warn"
        else:
            self._set_status(f"Error: {msg}", STATUS_ERR)
            summary_text, summary_tone = f"✗ Generation failed — {msg}", "danger"
        if hasattr(self, "_summary_chip"):
            self._summary_chip.setText(summary_text)
            self._summary_chip.setProperty("tone", summary_tone)
            refresh_style(self._summary_chip)

    def _cancel_generation(self) -> None:
        if not self._generate_task.running:
            return
        self._generate_task.cancel()
        self._cancel_generate_btn.setEnabled(False)
        self._set_status("Cancelling generation…", STATUS_WARN)
        if self._preview_task.has_pending() and (self._edit_polys or self._zones):
            self._preview_task.pending = False
            self._preview_timer.start(0)

    # ── Zones ─────────────────────────────────────────────────────────────────
