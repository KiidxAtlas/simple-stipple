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

from PySide6.QtCore import QTimer, Qt, Signal, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QDoubleValidator,
    QIcon,
    QIntValidator,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.backend.dxf.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    svg_to_dxf,
    write_polylines_dxf,
)
from src.app.services.presets_service import (
    PATTERNS,
    PatternProcessor,
    SETTINGS_KEY as PRESET_SETTINGS_KEY,
    ensure_builtins_seeded,
    export_laserstar_package,
    export_raster_job,
)
from src.ui.canvas.constants import DIM
from src.ui.pages.base import BasePage
from src.ui.components import (
    CollapsibleSection,
    clear_line_edit_error,
    content_splitter,
    EscapeBlurFilter,
    parse_float_field_with_feedback,
    set_line_edit_error,
    set_status_label,
    sidebar_panel,
    surface_frame,
    workflow_strip,
)
from src.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN
from src.ui.pages.pattern.domain.session import (
    apply_pattern_workspace_state,
    clear_pattern_workspace_state,
    get_pattern_workspace_state,
)
from src.ui.widgets.dialogs.laserstar_export_dialog import LaserStarExportDialog
from src.ui.pages.pattern.ui.form_spec import PARAM_SPECS
from src.ui.pages.pattern.ui.params import (
    collect_form_state,
    collect_pattern_params,
    restore_form_state,
)
from src.ui.pages.pattern.domain.defaults import (
    DEFAULT_BORDER_FADE,
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_SPACING,
    DEFAULT_MIN_ISLAND_AREA,
    DEFAULT_MIN_SEGMENT,
    DEFAULT_PATTERN_ROTATION,
    DEFAULT_PREVIEW_QUALITY,
    FILL_SPACING_FLOOR_MM,
    PREVIEW_DEBOUNCE_MS,
    SCALE_MIN_MM,
)
from src.ui.pages.pattern.ui.presets_dialog import PresetManagerDialog
from src.ui.pages.pattern.ui.layout import build_left, build_right, refresh_pattern_properties_panel
from src.ui.pages.pattern.domain.workers import CancellableTaskState
from src.core.paths import custom_tiles_dir
from src.ui.util import (
    KIND_DXF,
    KIND_IMAGE,
    pick_open_file,
    pick_save_file,
    record_recent,
)
from src.core.settings import save_settings

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
                from src.core.settings import save_settings

                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        self._base_patterns: list[str] = list(PATTERNS)
        self._custom_tile_polys: list[list[tuple[float, float]]] = []
        self._tile_motifs: dict[str, list[list[tuple[float, float]]]] = {}
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
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)

        right_w = surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        self._left_panel = sidebar_panel(left_w, min_width=320, max_width=370)
        self._splitter = content_splitter(
            self._left_panel,
            right_w,
            sizes=(320, 950),
        )
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
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)
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
        """Select only a zone's boundary entities, not all generated output."""
        outline_count = len(self._preview_categories.get("outline", []))
        return [
            index
            for index, owner in enumerate(self._preview_zone_owners[:outline_count])
            if owner == zone_row
        ]

    def _highlight_zone_on_canvas(self, zone_row: int) -> None:
        """Highlight a zone without changing canvas/layer-tree selection."""
        if not 0 <= zone_row < len(self._zones):
            self._canvas.set_accent_polys({})
            return
        if self._showing_preview:
            indices = [
                index for index, owner in enumerate(self._preview_zone_owners) if owner == zone_row
            ]
        else:
            zone_ids = set(self._zones[zone_row].get("outline_ids", []))
            indices = [
                index
                for index, outline_id in enumerate(self._outline_ids)
                if outline_id in zone_ids
            ]
        self._canvas.set_accent_polys({index: "#f5a623" for index in indices})

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
        entity_ids = self._canvas.get_selected_ids()
        if not entity_ids:
            return
        zone_rows: list[int] = []
        if preview:
            zone_rows = [
                owner
                for idx, owner in enumerate(self._preview_zone_owners[: len(entity_ids)])
                if idx < len(entity_ids) and owner is not None
            ]
        else:
            selected_ids = set(entity_ids)
            zone_rows = [
                row
                for row, zone in enumerate(self._zones)
                if selected_ids.intersection(zone.get("outline_ids", []))
            ]
        if zone_rows:
            self._zone_list.setCurrentRow(zone_rows[0])

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
        valid_ids = set(self._outline_ids)
        self._outline_roles = {
            key: value
            for key, value in self._outline_roles.items()
            if key in valid_ids and value in {"boundary", "cutout", "open_path", "ignore"}
        }
        for index, outline_id in enumerate(self._outline_ids):
            if outline_id not in self._outline_roles:
                is_open = self._pattern_service._is_open_polyline(self._edit_polys[index])
                self._outline_roles[outline_id] = "open_path" if is_open else "boundary"
        self._exclusion_ids = [
            outline_id
            for outline_id in self._outline_ids
            if self._outline_roles.get(outline_id) == "cutout"
        ]

    def _generation_polys(self) -> list[list[tuple[float, float]]]:
        self._ensure_outline_roles()
        return [
            list(poly)
            for outline_id, poly in zip(self._outline_ids, self._edit_polys)
            if self._outline_roles.get(outline_id) in {"boundary", "open_path"}
        ]

    def _validate_outline_inputs(self, polys: list[list[tuple[float, float]]]) -> None:
        warning = self._pattern_service.validate_outline_inputs(polys)
        if warning:
            self._set_status(
                warning,
                STATUS_WARN,
            )

    def _snapshot_zone_jobs(self) -> list[dict]:
        # Pattern-cell cutouts are document-wide motif assignments. Inject the
        # current list at snapshot time so zones created before a cutout was
        # marked cannot retain a stale empty list and fill that cell anyway.
        for zone in self._zones:
            fill = zone.get("fill")
            if isinstance(fill, dict):
                fill["cell_cutouts"] = [list(poly) for poly in self._pattern_cell_cutouts]
                fill["cell_instance_cutouts"] = [
                    list(poly) for poly in self._pattern_cell_instance_cutouts
                ]
        jobs, warnings = self._pattern_service.snapshot_zone_jobs(
            self._zones,
            self._outline_ids,
            self._edit_polys,
        )
        if warnings:
            self._set_status(warnings[-1], STATUS_WARN)
        return jobs

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

    def _update_custom_pattern_actions(self, value: str) -> None:
        if not hasattr(self, "_save_tile_btn"):
            return
        name = self._custom_pattern_name(value)
        self._save_tile_btn.setVisible(value == "Custom Tile" or name is not None)
        self._save_tile_btn.setText("Update custom tile" if name else "Save custom tile")
        self._save_tile_btn.setToolTip(
            "Overwrite this custom tile's saved settings"
            if name
            else "Save the current Custom Tile geometry and settings into the Pattern list"
        )
        self._tile_name_edit.setVisible(value == "Custom Tile")
        self._delete_tile_btn.setVisible(name is not None)
        asset = self._tile_assets.get(name or "", {})
        status = asset.get("status", "embedded" if name else "")
        self._tile_asset_status.setVisible(bool(name))
        self._locate_tile_btn.setVisible(bool(name) and status in {"missing", "invalid"})
        self._repair_tile_btn.setVisible(bool(name) and status == "invalid")
        if not name:
            self._tile_asset_status.clear()
        elif status == "valid":
            self._tile_asset_status.setText(f"Valid · {Path(asset.get('path', '')).name}")
        elif status == "missing":
            self._tile_asset_status.setText("Missing source · embedded fallback remains available")
        elif status == "invalid":
            self._tile_asset_status.setText(
                f"Invalid source · {asset.get('error', 'could not read geometry')}"
            )
        else:
            self._tile_asset_status.setText("Embedded custom tile")

    def use_custom_tile(self, polys: list[list[tuple[float, float]]]) -> None:
        """Use selected canvas geometry as the repeated pattern source."""
        normalized = [[(float(x), float(y)) for x, y in poly] for poly in polys if poly]
        if not normalized:
            return
        self._custom_tile_polys = normalized
        self._pattern_combo.setCurrentText("Custom Tile")
        self._schedule_preview()
        self._canvas._show_flash(f"Custom tile: {len(normalized)} shape(s)", 1200)

    def _refresh_tile_motif_combo(self, current: str | None = None) -> None:
        self._load_custom_tiles_from_disk()
        selected = f"Custom · {current}" if current else None
        self._refresh_pattern_choices(current=selected or self._pattern_combo.currentText())

    def _load_custom_tiles_from_disk(self) -> None:
        """Discover supported user-managed vector tiles without an app restart."""
        folder = custom_tiles_dir(self._settings.get("custom_tiles_dir"))
        for asset in self._tile_assets.values():
            path_text = asset.get("path", "")
            if path_text and not Path(path_text).exists():
                asset["status"] = "missing"
        paths = (
            [
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in {".dxf", ".svg", ".fvi"}
            ]
            if folder.exists()
            else []
        )
        for path in sorted(paths, key=lambda item: item.name.lower()):
            try:
                if path.suffix.lower() == ".dxf":
                    polys, _report = load_dxf_polylines_with_report(str(path))
                elif path.suffix.lower() == ".fvi":
                    polys = [list(poly) for poly in read_fvi(path).paths]
                else:
                    with tempfile.TemporaryDirectory(
                        prefix="simple-stipple-tile-svg-"
                    ) as temp_folder:
                        converted = Path(temp_folder) / "tile.dxf"
                        svg_to_dxf(path, converted)
                        polys, _report = load_dxf_polylines_with_report(str(converted))
            except (OSError, ValueError, RuntimeError) as exc:
                LOGGER.warning("Skipping unreadable custom tile: %s", path)
                self._tile_assets[path.stem] = {
                    "path": str(path),
                    "status": "invalid",
                    "error": str(exc),
                }
                continue
            if polys:
                self._tile_motifs[path.stem] = [list(poly) for poly in polys]
                self._tile_assets[path.stem] = {
                    "path": str(path),
                    "status": "valid",
                    "format": path.suffix.lower(),
                }
            else:
                self._tile_assets[path.stem] = {
                    "path": str(path),
                    "status": "invalid",
                    "error": "No drawable geometry",
                }

    def _persist_tile_motifs(self) -> None:
        self._settings["custom_tile_motifs"] = self._tile_motifs
        self._settings["custom_tile_assets"] = self._tile_assets
        self._settings["custom_tile_settings"] = self._tile_settings
        save_settings(self._settings)

    def _open_custom_tiles_folder(self) -> None:
        folder = custom_tiles_dir(self._settings.get("custom_tiles_dir"))
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _save_tile_motif(self) -> None:
        current = self._pattern_combo.currentText()
        existing_name = self._custom_pattern_name(current)
        if current != "Custom Tile" and existing_name is None:
            return
        if not self._custom_tile_polys:
            self._set_status("Send geometry to Custom Tile before saving a motif.", STATUS_WARN)
            return
        name = existing_name or self._tile_name_edit.text().strip()
        if not name:
            self._set_status("Enter a custom tile name beside Save.", STATUS_WARN)
            self._tile_name_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
        self._tile_motifs[name] = [list(poly) for poly in self._custom_tile_polys]
        saved_state = collect_form_state(self)
        saved_state.pop("custom_tile_polys", None)
        saved_state["pattern"] = "Custom Tile"
        self._tile_settings[name] = saved_state
        if existing_name is not None:
            self._persist_tile_motifs()
            self._set_status(f"Updated custom tile settings: {name}", STATUS_OK)
            return
        safe_name = "".join(
            character for character in name if character not in '<>:"/\\|?*'
        ).strip()
        if not safe_name:
            self._set_status("Choose a name that can be used as a file name.", STATUS_ERR)
            return
        tile_path = custom_tiles_dir(self._settings.get("custom_tiles_dir")) / f"{safe_name}.dxf"
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        write_polylines_dxf(self._custom_tile_polys, str(tile_path), close=False)
        self._tile_assets[name] = {"path": str(tile_path), "status": "valid", "format": ".dxf"}
        self._persist_tile_motifs()
        self._refresh_tile_motif_combo(name)
        self._pattern_combo.setCurrentText(f"Custom · {name}")
        self._tile_name_edit.clear()
        self._set_status(f"Saved custom tile: {tile_path.name} · Custom Tiles", STATUS_OK)

    def _load_tile_motif(self) -> None:
        name = self._custom_pattern_name(self._pattern_combo.currentText()) or ""
        motif = self._tile_motifs.get(name)
        if not motif:
            return
        self._custom_tile_polys = [list(poly) for poly in motif]
        self._pattern_combo.setCurrentText("Custom Tile")
        self._schedule_preview()
        self._set_status(f"Loaded Custom Tile motif: {name}", STATUS_OK)

    def _delete_tile_motif(self) -> None:
        name = self._custom_pattern_name(self._pattern_combo.currentText()) or ""
        if not name or name not in self._tile_motifs:
            return
        answer = QMessageBox.question(
            self,
            "Delete custom pattern?",
            f'Delete the custom pattern "{name}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._tile_motifs[name]
        self._tile_settings.pop(name, None)
        asset = self._tile_assets.pop(name, {})
        safe_name = "".join(
            character for character in name if character not in '<>:"/\\|?*'
        ).strip()
        tile_folder = custom_tiles_dir(self._settings.get("custom_tiles_dir"))
        asset_path = Path(asset.get("path", "")) if asset.get("path") else None
        if asset_path is not None and asset_path.parent == tile_folder:
            asset_path.unlink(missing_ok=True)
        else:
            (tile_folder / f"{safe_name}.dxf").unlink(missing_ok=True)
        self._persist_tile_motifs()
        self._refresh_tile_motif_combo()
        self._pattern_combo.setCurrentText("— None —")
        self._set_status(f"Deleted custom pattern: {name}")

    def _locate_tile_asset(self) -> None:
        name = self._custom_pattern_name(self._pattern_combo.currentText()) or ""
        if not name:
            return
        path = pick_open_file(
            self,
            self._settings,
            "custom_tile_locate",
            "Locate custom tile",
            "Vector tiles (*.dxf *.DXF *.svg *.SVG *.fvi *.FVI);;All files (*)",
            fallback_dir=str(custom_tiles_dir(self._settings.get("custom_tiles_dir"))),
        )
        if not path:
            return
        source = Path(path)
        self._tile_assets[name] = {"path": str(source), "status": "missing"}
        try:
            if source.suffix.lower() == ".dxf":
                polys, _report = load_dxf_polylines_with_report(str(source))
            elif source.suffix.lower() == ".fvi":
                polys = [list(poly) for poly in read_fvi(source).paths]
            elif source.suffix.lower() == ".svg":
                with tempfile.TemporaryDirectory(
                    prefix="simple-stipple-locate-tile-"
                ) as temp_folder:
                    converted = Path(temp_folder) / "tile.dxf"
                    svg_to_dxf(source, converted)
                    polys, _report = load_dxf_polylines_with_report(str(converted))
            else:
                raise ValueError("Choose a DXF, SVG, or FVI tile.")
            if not polys:
                raise ValueError("No drawable geometry was found.")
            self._tile_motifs[name] = [list(poly) for poly in polys]
            self._tile_assets[name] = {
                "path": str(source),
                "status": "valid",
                "format": source.suffix.lower(),
            }
            self._persist_tile_motifs()
            self._refresh_pattern_choices(current=f"Custom · {name}")
            self._set_status(f"Located custom tile: {source.name}", STATUS_OK)
        except (OSError, ValueError, RuntimeError) as exc:
            self._tile_assets[name] = {"path": str(source), "status": "invalid", "error": str(exc)}
            self._persist_tile_motifs()
            self._update_custom_pattern_actions(f"Custom · {name}")

    def _repair_tile_asset(self) -> None:
        name = self._custom_pattern_name(self._pattern_combo.currentText()) or ""
        path = self._tile_assets.get(name, {}).get("path", "")
        if path:
            self.repairTileRequested.emit(path)
            self._set_status("Opened the invalid tile in Convert for repair.", STATUS_WARN)

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
                self._summary_chip.style().unpolish(self._summary_chip)
                self._summary_chip.style().polish(self._summary_chip)

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

    def _refresh_preset_combo(self) -> None:
        current = self._preset_combo.currentText() if hasattr(self, "_preset_combo") else ""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)
        if current and self._preset_combo.findText(current) >= 0:
            self._preset_combo.setCurrentText(current)
        else:
            self._preset_combo.setCurrentIndex(-1)
            preset_editor = self._preset_combo.lineEdit()
            if preset_editor is not None:
                preset_editor.clear()
        self._preset_combo.blockSignals(False)

    def _save_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            self._set_status("Enter a preset name in the combo box.", STATUS_ERR)
            return
        is_update = name in self._presets
        if is_update:
            reply = QMessageBox.question(
                self,
                "Overwrite Preset",
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
        self._set_status(f"{verb} preset: {name}", STATUS_OK)
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
        self._set_status(f"Loaded preset: {name}", STATUS_OK)
        self._schedule_preview()
        self._emit_state_changed()

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name or name not in self._presets:
            return
        answer = QMessageBox.question(
            self,
            "Delete preset?",
            f'Delete the preset "{name}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
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
            self._presets, self._settings, current_preset=current or None, parent=self
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
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)
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
        return {
            "pattern_fill": "Pattern + Fill",
            "pattern": "Pattern",
            "fill": "Fill",
            "outline": "Outline",
            "none": "Disabled",
        }.get(mode, "Pattern + Fill")

    def _zone_label(self, zone: dict, index: int) -> str:
        count = len(zone.get("outline_ids", []))
        mode = str(zone.get("output_mode", "pattern_fill"))
        detail = self._zone_output_label(mode)
        if mode in {"pattern", "pattern_fill"}:
            detail = f"{detail}: {zone.get('pattern', '— None —')}"
        return f"Zone {index + 1} · {detail} · {count} outline{'s' if count != 1 else ''}"

    def _sync_selected_zone_from_controls(self) -> None:
        if self._loading_zone or not hasattr(self, "_zone_list"):
            return
        row = self._zone_list.currentRow()
        if not (0 <= row < len(self._zones)):
            return
        pattern = self._current_pattern_key()
        try:
            params = self._collect_pattern_params(pattern) if pattern != "— None —" else {}
            scale = self._collect_scale()
        except ValueError:
            # Keep the last valid zone state while a numeric field is midway
            # through an edit; the next valid change will commit it.
            return
        zone = self._zones[row]
        zone.update(
            {
                "pattern": pattern,
                "params": params,
                "scale": scale,
                "fill": self._collect_fill_options(),
                "output_mode": self._zone_output_combo.currentData() or "pattern_fill",
                "form_state": collect_form_state(self),
            }
        )
        zone["label"] = self._zone_label(zone, row)
        item = self._zone_list.item(row)
        if item is not None:
            item.setText(zone["label"])
        refresh_pattern_properties_panel(self)

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
        if not hasattr(self, "_zone_params_grid"):
            return
        while self._zone_params_grid.count():
            item = self._zone_params_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._zone_param_inputs = {}
        pattern = self._pattern_key(self._zone_pattern_combo.currentText())
        values = params or {}
        row = 0
        for spec in PARAM_SPECS.get(pattern, []):
            field: QWidget
            key = spec.param_key or spec.attr[1:]
            if spec.kind == "checkbox":
                checkbox = QCheckBox(spec.label)
                checkbox.setChecked(bool(values.get(key, spec.default.lower() == "true")))
                self._zone_params_grid.addWidget(checkbox, row, 0, 1, 2)
                field = checkbox
            elif spec.kind == "combobox":
                combo = QComboBox()
                combo.addItems(spec.items)
                combo.setCurrentText(str(values.get(key, spec.default)))
                self._zone_params_grid.addWidget(QLabel(spec.label), row, 0)
                self._zone_params_grid.addWidget(combo, row, 1)
                field = combo
            else:
                line_edit = QLineEdit(str(values.get(key, spec.default)))
                if spec.kind == "int":
                    line_edit.setValidator(
                        QIntValidator(
                            int(spec.minimum or -2_147_483_648),
                            int(spec.maximum or 2_147_483_647),
                            line_edit,
                        )
                    )
                else:
                    line_edit.setValidator(
                        QDoubleValidator(
                            float(spec.minimum or -1e12),
                            float(spec.maximum or 1e12),
                            6,
                            line_edit,
                        )
                    )
                self._zone_params_grid.addWidget(QLabel(spec.label), row, 0)
                self._zone_params_grid.addWidget(line_edit, row, 1)
                field = line_edit
            field.setToolTip(spec.tooltip)
            if isinstance(field, QLineEdit):
                field.textChanged.connect(self._live_update_selected_zone)
            elif isinstance(field, QComboBox):
                field.currentIndexChanged.connect(self._live_update_selected_zone)
            elif isinstance(field, QCheckBox):
                field.toggled.connect(self._live_update_selected_zone)
            self._zone_param_inputs[key] = field
            row += 1
        self._zone_rotation = QLineEdit(str(values.get("rotation", DEFAULT_PATTERN_ROTATION)))
        self._zone_rotation.setValidator(QDoubleValidator(-36000, 36000, 4, self._zone_rotation))
        self._zone_rotation.textChanged.connect(self._live_update_selected_zone)
        self._zone_params_grid.addWidget(QLabel("Rotation (°)"), row, 0)
        self._zone_params_grid.addWidget(self._zone_rotation, row, 1)
        row += 1
        self._zone_size_percent = QLineEdit(str(values.get("size_percent", 100)))
        self._zone_size_percent.setValidator(QDoubleValidator(1, 10000, 3, self._zone_size_percent))
        self._zone_size_percent.textChanged.connect(self._live_update_selected_zone)
        self._zone_params_grid.addWidget(QLabel("Pattern size (%)"), row, 0)
        self._zone_params_grid.addWidget(self._zone_size_percent, row, 1)

    def _collect_zone_editor(self) -> tuple[str, dict, dict | None]:
        label = self._zone_pattern_combo.currentText()
        pattern = self._pattern_key(label)
        params: dict = {}
        for spec in PARAM_SPECS.get(pattern, []):
            key = spec.param_key or spec.attr[1:]
            field = self._zone_param_inputs[key]
            if spec.kind == "checkbox":
                params[key] = field.isChecked()
            elif spec.kind == "combobox":
                params[key] = field.currentText()
            elif spec.kind == "int":
                value = self._parse_float_field(
                    field,
                    spec.label,
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                )
                assert value is not None
                params[key] = int(value)
            else:
                params[key] = self._parse_float_field(
                    field,
                    spec.label,
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                )
        params["rotation"] = self._parse_float_field(self._zone_rotation, "Rotation")
        params["size_percent"] = self._parse_float_field(
            self._zone_size_percent,
            "Pattern size",
            minimum=1.0,
            maximum=10000.0,
        )
        params.update(
            {
                "density_mode": "Uniform",
                "density_strength": 0.0,
                "density_angle": 0.0,
                "density_reverse": False,
            }
        )
        custom_name = self._custom_pattern_name(label)
        if pattern == "Custom Tile":
            motif = self._tile_motifs.get(custom_name or "", self._custom_tile_polys)
            if not motif:
                raise ValueError("Choose or save custom pattern geometry first.")
            params["tile_polys"] = [list(poly) for poly in motif]
            params["interlock"] = False
        mode = str(self._zone_fill_mode.currentData() or "none")
        fill = None
        if mode != "none":
            fill = {
                "mode": mode,
                "spacing": max(
                    FILL_SPACING_FLOOR_MM,
                    self._parse_float_field(
                        self._zone_fill_spacing,
                        "Fill spacing",
                        minimum=FILL_SPACING_FLOOR_MM,
                    ),
                ),
                "angle_deg": self._parse_float_field(self._zone_fill_angle, "Fill angle"),
                "inset": self._parse_float_field(self._zone_fill_inset, "Fill inset", minimum=0.0),
                "keep_pattern": True,
                "target_outline": self._zone_fill_target_outline.isChecked(),
                "target_pattern": self._zone_fill_target_pattern.isChecked(),
                "cell_cutouts": [list(poly) for poly in self._pattern_cell_cutouts],
                "cell_instance_cutouts": [
                    list(poly) for poly in self._pattern_cell_instance_cutouts
                ],
            }
        return pattern, params, fill

    def _apply_selected_zone_edits(self) -> None:
        """Compatibility entry point; zone controls now commit live."""
        self._live_update_selected_zone()

    def _live_update_selected_zone(self, *_args) -> None:
        if self._loading_zone or self._suspend_state:
            return
        row = self._zone_list.currentRow()
        if not (0 <= row < len(self._zones)):
            return
        try:
            pattern, params, fill = self._collect_zone_editor()
        except (KeyError, TypeError, ValueError):
            # A line edit can briefly contain an incomplete number while the
            # user types. Keep the last valid zone state until it is complete.
            return
        zone = self._zones[row]
        self._preview_user_opt_out = False
        zone.update(
            {
                "pattern": pattern,
                "pattern_label": self._zone_pattern_combo.currentText(),
                "params": params,
                "fill": fill,
                "output_mode": self._zone_output_combo.currentData() or "pattern_fill",
            }
        )
        zone["label"] = self._zone_label(zone, row)
        item = self._zone_list.item(row)
        if item is not None:
            item.setText(zone["label"])
        self._schedule_preview()
        self._emit_state_changed()

    def _show_zone_context_menu(self, pos) -> None:
        item = self._zone_list.itemAt(pos)
        if item is None or not self._zones:
            return
        self._zone_list.setCurrentItem(item)
        menu = QMenu(self._zone_list)
        delete_action = menu.addAction("Delete Zone")
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        chosen = menu.exec(self._zone_list.viewport().mapToGlobal(pos))
        if chosen is delete_action:
            self._remove_selected_zone()

    def _on_zone_selected(self, row: int) -> None:
        valid = 0 <= row < len(self._zones)
        if not valid:
            self._canvas.set_accent_polys({})
            refresh_pattern_properties_panel(self)
            return
        zone = self._zones[row]
        self._highlight_zone_on_canvas(row)
        self._loading_zone = True
        self._suspend_state = True
        try:
            pattern_label = str(zone.get("pattern_label") or zone.get("pattern", "— None —"))
            self._populate_pattern_combo(self._zone_pattern_combo, pattern_label)
            self._rebuild_zone_parameter_editor(params=dict(zone.get("params", {})))
            fill = zone.get("fill")
            fill_mode = str(fill.get("mode", "none")) if isinstance(fill, dict) else "none"
            self._zone_fill_mode.setCurrentIndex(max(0, self._zone_fill_mode.findData(fill_mode)))
            if isinstance(fill, dict):
                self._zone_fill_spacing.setText(str(fill.get("spacing", DEFAULT_FILL_SPACING)))
                self._zone_fill_angle.setText(str(fill.get("angle_deg", DEFAULT_FILL_ANGLE)))
                self._zone_fill_inset.setText(str(fill.get("inset", DEFAULT_FILL_INSET)))
                self._zone_fill_target_outline.setChecked(bool(fill.get("target_outline", True)))
                self._zone_fill_target_pattern.setChecked(bool(fill.get("target_pattern", False)))
            else:
                self._zone_fill_target_outline.setChecked(True)
                self._zone_fill_target_pattern.setChecked(False)
            mode = str(zone.get("output_mode", "pattern_fill"))
            self._zone_output_combo.setCurrentIndex(max(0, self._zone_output_combo.findData(mode)))
        finally:
            self._suspend_state = False
            self._loading_zone = False
        self._refresh_section_subtitles()
        refresh_pattern_properties_panel(self)

    def _assign_zone(self) -> None:
        sel_polys = self._canvas.get_selected()
        promoted: list[list[tuple[float, float]]] = []
        if self._showing_preview:
            # A generated cell can itself become a zone boundary. Promote the
            # selected preview geometry to durable source outlines first.
            promoted = [[(float(x), float(y)) for x, y in poly] for poly in sel_polys]
            sel_ids = self._fresh_outline_ids(len(promoted))
        else:
            sel_ids = [
                eid for eid in self._canvas.get_selected_ids() if eid in self._outline_ids
            ]
        if not sel_polys:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more outlines on the canvas first, then click 'Assign'.",
            )
            return
        try:
            scale = self._collect_scale()
            pattern, params, fill_snapshot = self._collect_zone_editor()
            self._validate_outline_inputs(sel_polys)
        except ValueError as exc:
            self._set_status(str(exc), STATUS_ERR)
            return
        if promoted:
            self._edit_polys.extend(promoted)
            self._outline_ids.extend(sel_ids)
        if set(sel_ids).intersection(self._exclusion_ids):
            self._set_status(
                "Remove Cutout from the selected shape before assigning a zone.",
                STATUS_WARN,
            )
            return
        if any(
            zone.get("outline_ids", []) == sel_ids
            and zone["pattern"] == pattern
            and zone["params"] == params
            and zone["scale"] == scale
            and zone.get("fill") == fill_snapshot
            for zone in self._zones
        ):
            self._set_status("Matching zone already exists.", STATUS_WARN)
            return
        requested_mode = self._zone_output_combo.currentData()
        if requested_mode in {"pattern_fill", "pattern", "fill", "outline", "none"}:
            output_mode = str(requested_mode)
        elif pattern == "— None —" and fill_snapshot:
            output_mode = "fill"
        elif pattern == "— None —":
            output_mode = "outline"
        elif fill_snapshot:
            output_mode = "pattern_fill"
        else:
            output_mode = "pattern"
        # An outline belongs to at most one zone. Reassignment moves selected
        # outlines out of older zones instead of producing overlapping output.
        selected_ids = set(sel_ids)
        retained_zones: list[dict] = []
        for existing in self._zones:
            remaining = [oid for oid in existing.get("outline_ids", []) if oid not in selected_ids]
            if remaining:
                retained_zones.append({**existing, "outline_ids": remaining})
        self._zones = retained_zones
        zone = {
            "outline_ids": list(sel_ids),
            "pattern": pattern,
            "pattern_label": self._zone_pattern_combo.currentText(),
            "params": params,
            "scale": scale,
            "fill": fill_snapshot,
            "output_mode": output_mode,
            "form_state": collect_form_state(self),
        }
        zone["label"] = self._zone_label(zone, len(self._zones))
        self._zones.append(zone)
        self._preview_user_opt_out = False
        self._refresh_zone_list()
        self._zone_list.setCurrentRow(len(self._zones) - 1)
        self._schedule_preview()
        self._emit_state_changed()

    def _remove_selected_zone(self) -> None:
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
            "Clear All Zones?",
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
            for index, zone in enumerate(self._zones):
                zone["label"] = self._zone_label(zone, index)
                self._zone_list.addItem(zone["label"])
        else:
            self._zone_list.addItem("No zones assigned yet")
        self._zone_list.blockSignals(False)
        row_height = self._zone_list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self._zone_list.fontMetrics().height() + 8
        self._zone_list.setFixedHeight(max(44, row_height * max(1, self._zone_list.count()) + 6))
        if not self._zones and self._zone_list.count() > 0:
            item = self._zone_list.item(0)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._update_zone_actions()
        self._refresh_section_subtitles()
        refresh_pattern_properties_panel(self)

    # ── Exclusions (cutouts) ──────────────────────────────────────────────────

    def _on_canvas_cutout_toggle(self, idx: int) -> None:
        if self._showing_preview:
            self._canvas._show_flash("Exit preview mode to assign cutouts", 1200)
            return
        if not (0 <= idx < len(self._outline_ids)):
            return
        oid = self._outline_ids[idx]
        current = self._outline_roles.get(oid, "boundary")
        fallback = (
            "open_path"
            if self._pattern_service._is_open_polyline(self._edit_polys[idx])
            else "boundary"
        )
        self._on_canvas_outline_role_change(idx, fallback if current == "cutout" else "cutout")

    def _on_canvas_outline_role_change(self, idx: int, role: str) -> None:
        if self._showing_preview:
            self._canvas._show_flash("Exit preview mode to assign outline roles", 1200)
            return
        if not (0 <= idx < len(self._outline_ids)) or role not in {
            "boundary",
            "cutout",
            "open_path",
            "ignore",
        }:
            return
        oid = self._outline_ids[idx]
        if role == "boundary" and self._pattern_service._is_open_polyline(self._edit_polys[idx]):
            self._set_status("Close this path before assigning it as a fill boundary.", STATUS_WARN)
            return
        if role == "cutout":
            closed, _open_paths = self._pattern_service._merge_and_classify_outlines(
                self._generation_polys()
            )
            target_is_closed = not self._pattern_service._is_open_polyline(self._edit_polys[idx])
            if target_is_closed and len(closed) <= 1:
                self._set_status("The only closed outline cannot be a cutout.", STATUS_WARN)
                return
            # A cutout cannot also own a zone; that combination subtracts the
            # zone from itself and silently generates no result.
            retained_zones: list[dict] = []
            for zone in self._zones:
                remaining = [zid for zid in zone.get("outline_ids", []) if zid != oid]
                if remaining:
                    retained_zones.append({**zone, "outline_ids": remaining})
            self._zones = retained_zones
            self._refresh_zone_list()
        self._outline_roles[oid] = role
        self._ensure_outline_roles()
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._set_status(
            f"Outline role: {role.replace('_', ' ').title()}",
            STATUS_OK,
        )
        self._schedule_preview()
        self._emit_state_changed()

    def _explain_outline_role(self, idx: int) -> None:
        if not (0 <= idx < len(self._outline_ids)):
            return
        self._ensure_outline_roles()
        role = self._outline_roles.get(self._outline_ids[idx], "boundary")
        closed = not self._pattern_service._is_open_polyline(self._edit_polys[idx])
        explanations = {
            "boundary": "Boundary: closed region contributes to the fillable pattern area.",
            "cutout": "Cutout: this shape is subtracted from every overlapping fill region.",
            "open_path": "Open path: exported as linework but never auto-closed, filled, or subtracted.",
            "ignore": "Ignore: excluded from preview and generated output.",
        }
        detail = explanations[role]
        if role == "boundary" and not closed:
            detail += " Close the path before it can be filled."
        self._set_status(detail, "#79c0ff")
        self._canvas._show_flash(detail, 3500)

    def _mark_selection_as_cutout(self) -> None:
        entity_ids = self._canvas.get_selected_ids()
        if not entity_ids:
            self._set_status("Select one or more shapes on canvas first.", STATUS_WARN)
            return
        if self._showing_preview:
            outline_count = len(self._preview_categories.get("outline", []))
            pattern_polys = self._preview_categories.get("pattern", [])
            # Convert entity IDs to preview cell indices
            cell_indices = set()
            for eid in entity_ids:
                if eid.startswith("preview_"):
                    try:
                        cell_idx = int(eid.split("_")[1])
                        cell_indices.add(cell_idx)
                    except (ValueError, IndexError):
                        pass
            selected_cells = [
                list(pattern_polys[idx - outline_count])
                for idx in cell_indices
                if idx in self._canvas._pattern_cell_indices
                and 0 <= idx - outline_count < len(pattern_polys)
            ]
            unique_cells: dict[tuple, list[tuple[float, float]]] = {}
            for poly in selected_cells:
                unique_cells.setdefault(self._pattern_service._poly_repeat_signature(poly), poly)
            for poly in unique_cells.values():
                self._toggle_pattern_cell_cutout_poly(poly)
            if selected_cells:
                self._configure_pattern_cell_context()
                self._refresh_cutout_status()
                self._schedule_preview()
            return
        for eid in entity_ids:
            if eid in self._outline_ids:
                self._on_canvas_cutout_toggle(self._outline_ids.index(eid))

    def _clear_exclusions(self) -> None:
        if (
            not self._exclusion_ids
            and not self._pattern_cell_cutouts
            and not self._pattern_cell_instance_cutouts
        ):
            return
        for outline_id in self._exclusion_ids:
            index = self._outline_ids.index(outline_id) if outline_id in self._outline_ids else -1
            if index >= 0:
                self._outline_roles[outline_id] = (
                    "open_path"
                    if self._pattern_service._is_open_polyline(self._edit_polys[index])
                    else "boundary"
                )
        self._exclusion_ids.clear()
        self._pattern_cell_cutouts.clear()
        self._pattern_cell_instance_cutouts.clear()
        self._sync_canvas_cutout_highlight()
        self._refresh_cutout_status()
        self._schedule_preview()
        self._emit_state_changed()

    def _sync_canvas_cutout_highlight(self) -> None:
        if not hasattr(self, "_canvas"):
            return
        id_to_idx = {oid: i for i, oid in enumerate(self._outline_ids)}
        self._ensure_outline_roles()
        roles = {
            id_to_idx[outline_id]: role
            for outline_id, role in self._outline_roles.items()
            if outline_id in id_to_idx
        }
        self._canvas.set_outline_roles(roles)

    def _apply_cutout_callout_style(self, *, active: bool) -> None:
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
        if not hasattr(self, "_cutout_status_label"):
            return
        outline_count = len(self._exclusion_ids)
        cell_count = len(self._pattern_cell_cutouts) + len(self._pattern_cell_instance_cutouts)
        n = outline_count + cell_count
        if n == 0:
            self._cutout_icon.setPixmap(
                QIcon(str(Path(__file__).parents[2] / "style" / "icons" / "info.svg")).pixmap(
                    16, 16
                )
            )
            self._cutout_status_label.setText("Right-click a shape on canvas to mark as cutout")
            self._cutout_clear_btn.setVisible(False)
            self._apply_cutout_callout_style(active=False)
        else:
            self._cutout_icon.setPixmap(
                QIcon(str(Path(__file__).parents[2] / "style" / "icons" / "check.svg")).pixmap(
                    16, 16
                )
            )
            parts = []
            if outline_count:
                parts.append(f"{outline_count} outline")
            if cell_count:
                parts.append(f"{cell_count} pattern cell")
            self._cutout_status_label.setText(
                f"{' + '.join(parts)} cutout{'s' if n != 1 else ''} active — shown orange"
            )
            self._cutout_clear_btn.setVisible(True)
            self._apply_cutout_callout_style(active=True)

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
        from src.ui.pages.pattern.domain.workers import compute_preview, compute_preview_zones

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
        from src.app.services.presets_service import diagnose_output

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
        from src.ui.pages.pattern.domain.workers import CANCELLED_MESSAGE

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
        self._preview_status.style().unpolish(self._preview_status)
        self._preview_status.style().polish(self._preview_status)

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
            self._preview_btn.style().unpolish(self._preview_btn)
            self._preview_btn.style().polish(self._preview_btn)
            self._canvas.setToolTip(
                "Refreshing preview — geometry shown is the last completed result."
            )
        if had_cache or was_showing:
            self._set_preview_status("Refreshing preview…")
        self._update_preview_controls()

    def _invalidate_zones_for_geometry_change(self, valid_outline_ids: set[str]) -> None:
        if not self._zones:
            return
        retained: list[dict] = []
        removed_assignments = 0
        for zone in self._zones:
            previous_ids = list(zone.get("outline_ids", []))
            remaining_ids = [oid for oid in previous_ids if oid in valid_outline_ids]
            removed_assignments += len(previous_ids) - len(remaining_ids)
            if remaining_ids:
                retained.append({**zone, "outline_ids": remaining_ids})
        if not removed_assignments:
            return
        self._zones = retained
        self._refresh_zone_list()
        self._set_status(
            f"Outline changed — removed {removed_assignments} affected zone "
            f"assignment{'s' if removed_assignments != 1 else ''}; unaffected zones were kept.",
            STATUS_WARN,
        )

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
            self._summary_chip.style().unpolish(self._summary_chip)
            self._summary_chip.style().polish(self._summary_chip)
        # Keep the core Pattern controls discoverable in the empty state.
        # They serve as editable defaults before an outline or zone exists.
        if hasattr(self, "_zones_section"):
            self._zones_section.setVisible(True)
        if hasattr(self, "_pattern_properties_scroll"):
            self._pattern_properties_scroll.setVisible(True)

    def _update_zone_actions(self) -> None:
        has_selection = bool(getattr(self._canvas, "sel_count", 0))
        zone_pattern = (
            self._pattern_key(self._zone_pattern_combo.currentText())
            if hasattr(self, "_zone_pattern_combo")
            else "— None —"
        )
        can_assign = has_selection and zone_pattern != "— None —"
        self._assign_zone_btn.setEnabled(can_assign)
        self._assign_zone_btn.setToolTip(
            "Select one or more outlines to assign this pattern"
            if not can_assign
            else "Save the current pattern and parameters for the selected outlines"
        )
        if hasattr(self, "_mark_cutout_btn"):
            self._mark_cutout_btn.setEnabled(
                (not self._showing_preview) or bool(getattr(self._canvas, "sel_count", 0))
            )

    # ── Generation ────────────────────────────────────────────────────────────

    def _generate(self) -> None:
        from src.ui.pages.pattern.domain.workers import run_generate, run_generate_zones

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
            self._summary_chip.style().unpolish(self._summary_chip)
            self._summary_chip.style().polish(self._summary_chip)
        self._refresh_canvas_panels()

    def _handle_gen_error(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        from src.ui.pages.pattern.domain.workers import CANCELLED_MESSAGE

        generation_token, msg = payload
        self._generate_task.finish_run()
        if generation_token != self._generation_revision:
            return
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._gen_btn.setEnabled(True)
        self._cancel_generate_btn.setVisible(False)
        if msg == CANCELLED_MESSAGE:
            self._set_status("Generation cancelled", STATUS_WARN)
        else:
            self._set_status(f"Error: {msg}", STATUS_ERR)

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
