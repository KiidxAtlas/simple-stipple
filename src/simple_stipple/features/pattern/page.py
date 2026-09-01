"""Pattern Generator page."""

# isort: skip_file
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportUndefinedVariable=false

from __future__ import annotations

import logging
import platform
import threading
from datetime import date
from pathlib import Path

from PIL import Image
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal, QUrl
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

from simple_stipple.core.formats.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    summarize_dxf_import_report,
    svg_to_dxf,
)
from simple_stipple.core.formats.svg import read_svg_images
from simple_stipple.core.patterns.presets import SETTINGS_KEY as PRESET_SETTINGS_KEY
from simple_stipple.core.patterns.presets import ensure_builtins_seeded
from simple_stipple.core.patterns.processing import PATTERNS, PatternProcessor
from simple_stipple.canvas.constants import DIM
from simple_stipple.canvas.layers.logic import flatten_shape_keys
from simple_stipple.features.base import BasePage
from simple_stipple.features.pattern.export import (
    EXPORT_BUTTON_LABEL,
    EXPORT_FORMAT_KEYS,
    build_engraving_job,
    export_document_file,
    export_format_suffix,
    export_laserstar_job,
)
from simple_stipple.ui.components.layout import CollapsibleSection
from simple_stipple.ui.components.feedback import (
    parse_float_field_with_feedback,
    refresh_style,
)
from simple_stipple.ui.components.focus import EscapeBlurFilter
from simple_stipple.ui.components.layout import (
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.style import STATUS_ERR, STATUS_OK, STATUS_WARN
from simple_stipple.features.pattern.session import (
    apply_pattern_workspace_state,
    clear_pattern_workspace_state,
    get_pattern_workspace_state,
)
from simple_stipple.ui.dialogs.laserstar_export_dialog import LaserStarExportDialog
from simple_stipple.ui.dialogs.export_preflight import export_preflight
from simple_stipple.features.pattern.form import (
    collect_pattern_params,
    restore_form_state,
)
from simple_stipple.features.pattern.defaults import (
    DEFAULT_BORDER_FADE,
    DEFAULT_FILL_ANGLE,
    DEFAULT_FILL_INSET,
    DEFAULT_FILL_SPACING,
    DEFAULT_PREVIEW_QUALITY,
    FILL_SPACING_FLOOR_MM,
    PREVIEW_DEBOUNCE_MS,
    SCALE_MIN_MM,
)
from simple_stipple.features.pattern.layout import (
    build_left,
    build_right,
    refresh_pattern_properties_panel,
)
from simple_stipple.features.pattern.model import PatternModel
from simple_stipple.features.pattern.outline_state import read_outline_vector
from simple_stipple.features.pattern.outline_state import (
    canvas_records,
    normalize_outline_items,
    outline_bounds,
    reconcile_outline_ids,
    smallest_containing_outline,
)
from simple_stipple.features.pattern.session import build_preview_worker_call
from simple_stipple.platform.settings import user_data_dir
from simple_stipple.features.pattern.regions.zones import (
    assign_zone,
    clear_zones,
    highlight_zone_on_canvas,
    invalidate_zones_for_geometry_change,
    live_update_selected_zone,
    on_zone_selected,
    refresh_zone_list,
    remove_selected_zone,
    select_zone_for_canvas_selection,
    show_zone_context_menu,
    snapshot_zone_jobs,
    sync_engraving_visibility,
    update_zone_actions,
)
from simple_stipple.core.patterns.fill import NULL_PATTERN
from simple_stipple.features.pattern.regions.treatments import (
    IMAGE_PATTERN,
    engraving_mask_polys,
    region_engraving,
    set_region_engraving,
    update_region_engraving,
    generation_polys,
    redo_treatments,
    region_tree,
    undo_treatments,
    zones as project_treatment_zones,
)
from simple_stipple.features.pattern.custom_tiles import (
    apply_custom_tile,
    delete_tile_motif,
    load_custom_tiles_from_disk,
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
from simple_stipple.platform.settings import custom_tiles_dir, save_settings  # noqa: F401
from simple_stipple.ui.dialogs.files import pick_open_file, pick_save_file
from simple_stipple.ui.components.recent import KIND_DXF, KIND_IMAGE, record_recent

LOGGER = logging.getLogger(__name__)


class PatternPage(BasePage):
    _gen_done = Signal(object)  # (count, name, polys)
    _gen_error = Signal(object)
    _preview_done = Signal(object)  # (display_polys, count)
    _preview_error = Signal(object)
    sendSelectedToDraftRequested = Signal(object)
    # Emitted by the empty-state buttons; the window routes it to that page.
    openPageRequested = Signal(str)
    repairTileRequested = Signal(str)

    _MODEL_STATE_FIELDS = {
        "_orig_polys": "original_polys",
        "_edit_polys": "editable_polys",
        "_orig_w": "original_width",
        "_orig_h": "original_height",
        "_updating_dims": "updating_dimensions",
        "_preview_task": "preview_task",
        "_generate_task": "generate_task",
        "_preview_thread": "preview_thread",
        "_generate_thread": "generate_thread",
        "_shutting_down": "shutting_down",
        "_last_out_path": "last_output_path",
        "_export_is_current": "export_is_current",
        "_preview_is_stale": "preview_is_stale",
        "_output_order": "output_order",
        "_output_disabled": "output_disabled",
        "_force_export_quality": "force_export_quality",
        "_pending_export_after_preview": "pending_export_after_preview",
        "_presets": "presets",
        "_base_patterns": "base_patterns",
        "_preview_polys_cache": "preview_polys_cache",
        "_preview_categories": "preview_categories",
        "_preview_zone_owners": "preview_zone_owners",
        "_outline_ids": "outline_ids",
        "_outline_layers": "outline_layers",
        "_pattern_cell_cutouts": "pattern_cell_cutouts",
        "_pattern_cell_instance_cutouts": "pattern_cell_instance_cutouts",
        "_preview_revision": "preview_revision",
        "_generation_revision": "generation_revision",
        "_pattern_service": "pattern_service",
        "_treatments": "treatments",
        "_treatment_undo": "treatment_undo",
        "_treatment_redo": "treatment_redo",
        "_loading_zone": "loading_zone",
        "_engraving_image_path": "engraving_image_path",
    }

    def __getattr__(self, name: str):
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            return getattr(model, field)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            setattr(model, field, value)
            return
        super().__setattr__(name, value)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)  # BasePage sets _settings and _suspend_state
        self._model = PatternModel()

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
        # Output-panel state: run order and the rows the user has switched off.
        self._output_order: list[str] = []
        self._output_disabled: set[str] = set()
        # Set for the one solve that feeds an export, so the written geometry
        # is never the fast preview approximation.
        self._force_export_quality: bool = False
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
                from simple_stipple.platform.settings import save_settings

                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        self._base_patterns: list[str] = list(PATTERNS)
        self._load_state()

        self._preview_polys_cache: list[list[tuple[float, float]]] = []
        self._preview_categories: dict[str, list[list[tuple[float, float]]]] = {
            "outline": [],
            "pattern": [],
            "fill": [],
        }
        self._preview_zone_owners: list[int | None] = []
        self._outline_ids: list[str] = []
        self._outline_layers: dict[str, str] = {}
        self._pattern_cell_cutouts: list[list[tuple[float, float]]] = []
        self._pattern_cell_instance_cutouts: list[list[tuple[float, float]]] = []
        self._preview_revision: int = 0
        self._generation_revision: int = 0
        self._pattern_service = PatternProcessor()
        # One treatment per region, keyed by the owning outline's id. Regions
        # themselves are derived from geometry, so nothing here declares
        # containment — see features/pattern/treatments.py.
        self._treatments: dict[str, dict] = {}
        # Treatment history, interleaved with canvas undo by canvas depth.
        self._treatment_undo: list[tuple[int, str | None, dict]] = []
        self._treatment_redo: list[tuple[int, str | None, dict]] = []
        self._loading_zone: bool = False
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
        self._set_advanced_mode(self._advanced_mode_cb.isChecked())
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
                from simple_stipple.platform.settings import save_settings

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

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
    OUTLINE_SUFFIXES = (".dxf", ".fvi", ".svg")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                suffix = url.toLocalFile().lower()
                if suffix.endswith(self.OUTLINE_SUFFIXES) or suffix.endswith(self.IMAGE_SUFFIXES):
                    event.acceptProposedAction()
                    return
        # Qt withholds dropEvent entirely once dragEnterEvent rejects, so
        # this is the only chance to say why.
        if event.mimeData().hasUrls():
            self._set_status(
                "Pattern accepts DXF, FVI or SVG outlines, and images to engrave", STATUS_WARN
            )
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self.IMAGE_SUFFIXES):
                # An image dropped on the canvas is engraved into whichever
                # region it lands in — no import dialog, no target combo.
                if self._drop_image_into_region(path, event):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return
            if path.lower().endswith(self.OUTLINE_SUFFIXES):
                # Loading an outline always replaces the current one (and its
                # zones/cutouts); a stray drop shouldn't do that silently.
                if self._orig_polys:
                    answer = QMessageBox.question(
                        self,
                        "Replace Outline",
                        f"Replace the current outline with {Path(path).name}? "
                        "Zones and cutouts will be reset.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        event.ignore()
                        return
                self._show_outline_path(path)
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

    def _cancel_solve(self) -> None:
        """Stop the solve in flight; the last completed result stays on screen."""
        if not self._preview_task.running:
            return
        self._preview_task.cancel()
        self._preview_task.pending = False
        self._set_preview_status("Cancelled — showing the last completed result.")

    def _set_result_visible(self, visible: bool) -> None:
        """Show or hide the solved pattern.

        Visibility only: the canvas never stops holding the real outlines, so
        there is no mode to leave and every edit is an edit of the document.
        """
        self._canvas.set_result_visible(visible)
        if not self._preview_polys_cache:
            self._set_preview_status("Choose a treatment to solve a pattern")
        elif visible:
            self._set_preview_status(
                f"{len(self._preview_polys_cache)} shapes — pattern shown", "success"
            )
        else:
            self._set_preview_status("Pattern hidden — outlines only")
        self._update_preview_controls()

    def _highlight_zone_on_canvas(self, zone_row: int) -> None:
        return highlight_zone_on_canvas(self, zone_row)

    def _on_pattern_cell_cutout_toggle(self, result_index: int, scope: str = "repeat") -> None:
        """Remove (or restore) a generated cell picked from the result layer."""
        polys = self._canvas._result_polys
        if not 0 <= result_index < len(polys):
            return
        poly = list(polys[result_index])
        if scope == "instance":
            added = self._toggle_pattern_cell_instance_cutout_poly(poly)
        else:
            added = self._toggle_pattern_cell_cutout_poly(poly)
        self._canvas._show_flash(
            ("Only this cell is removed" if added else "This cell is restored")
            if scope == "instance"
            else (
                "This shape is removed from every tile"
                if added
                else "This shape is restored in every tile"
            ),
            1000,
        )
        self._schedule_preview()

    def _on_result_cell_convert(self, result_index: int) -> None:
        """Promote a generated cell to a real, editable outline."""
        polys = self._canvas._result_polys
        if not 0 <= result_index < len(polys):
            return
        poly = [tuple(point) for point in polys[result_index]]
        if len(poly) < 2:
            return
        new_id = self._fresh_outline_ids(1)[0]
        self._edit_polys = list(self._edit_polys) + [poly]
        self._outline_ids = list(self._outline_ids) + [new_id]
        self._outline_layers[new_id] = self._canvas.active_layer or "Outline"
        self._load_outline_canvas(fit=False)
        self._canvas.set_selection([new_id])
        self._set_status("Cell converted to an editable outline.", STATUS_OK)
        self._update_zone_actions()
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

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
        self._canvas_runtime.on_selection_change(count)  # updates toolbar
        # `_edit_polys` mirrors the FULL canvas state, never the selection
        # subset. The id check is belt-and-braces now that the canvas only ever
        # holds outlines: geometry and its parallel id list must stay aligned.
        if self._canvas.get_entity_ids() == self._outline_ids:
            self._edit_polys = self._canvas.get_polylines_state()
        self._select_zone_for_canvas_selection()
        self._update_zone_actions()
        # Update status strip selection count without rebuilding the tree.
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _select_zone_for_canvas_selection(self) -> None:
        return select_zone_for_canvas_selection(self)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[str]]],
    ) -> list[dict[str, Any]]:
        return self._canvas_runtime.build_layer_tree_rows(layer_view_state)

    def _open_pattern_layer_settings(self, layer: str) -> None:
        """Turn the virtual result layer into a useful shortcut, not a dead row."""
        if layer == "pattern_result":
            self._pattern_section.set_expanded(True)
            self._pattern_combo.setFocus()
            self._set_status("Pattern settings are ready to edit.", STATUS_OK)

    def _on_pattern_layer_visibility_changed(self, layer: str, visible: bool) -> None:
        """The result row's eye is the only control over showing the pattern."""
        if layer == "pattern_result":
            self._set_result_visible(visible)

    def _on_toolbar_mode(self, value: str) -> None:
        self._canvas_runtime.on_toolbar_mode(value)
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._canvas_runtime.on_canvas_mode_change(mode)
        self._refresh_canvas_panels()

    def _on_canvas_geometry_change(self) -> None:
        new_polys = self._canvas.get_polylines_state()
        # Keep the canvas entity ids for newly drawn outlines.  The canvas
        # creates those ids at draw time; generating independent Pattern ids
        # here makes the shape visible but impossible to assign to a zone.
        new_outline_ids = self._sync_outline_ids(
            new_polys,
            entity_ids=self._canvas.get_entity_ids(),
        )
        # The canvas is the interactive source of truth. If an external load
        # or operation replaced its runtime ids, reconcile the canvas back to
        # the durable Pattern ids (and preserve selection by position) before
        # any zone/reference filtering runs.
        canvas_ids = self._canvas.get_entity_ids()
        if canvas_ids != new_outline_ids:
            selected_indices = {
                index
                for index, entity_id in enumerate(canvas_ids)
                if entity_id in set(self._canvas.get_selected_ids())
            }
            self._load_outline_canvas(
                fit=False,
                polys=new_polys,
                entity_ids=new_outline_ids,
            )
            self._canvas.set_selection(
                [
                    new_outline_ids[index]
                    for index in selected_indices
                    if index < len(new_outline_ids)
                ]
            )
        self._edit_polys = new_polys
        self._outline_ids = new_outline_ids
        self._outline_layers = {
            entity_id: entity.layer or "Outline"
            for entity_id, entity in self._canvas._entities_by_id.items()
            if entity_id in set(new_outline_ids)
        }
        # Regions are derived from geometry, so drawing a shape creates one.
        # This ran only when a treatment already existed, which left the
        # Regions list stuck on "No closed regions yet" for a fresh document —
        # and it ran before the new geometry was stored, so it pruned against
        # a stale outline list.
        self._invalidate_zones_for_geometry_change(set(new_outline_ids))
        self._refresh_canvas_panels()
        self._schedule_preview()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        # Select shapes on canvas without toggling preview mode — the user
        # should be able to highlight shapes in the layer tree while reviewing
        # the generated pattern.
        # Preview tree layers are virtual categories. Editable outlines keep
        # their real source-layer model so layer activation/reordering and
        # moves continue to work after selecting from the tree.
        selected_ids = flatten_shape_keys(indices)
        valid_ids = [eid for eid in selected_ids if eid in self._canvas._entities_by_id]
        self._canvas.set_selection(valid_ids)
        # Update toolbar and status strip without rebuilding the tree —
        # rebuilding would immediately clear the visual selection just made.
        self._canvas_runtime.on_selection_change(len(valid_ids))
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
        """The generator the document-level path should run.

        "Image" is a treatment choice, not a generator: the region's outline
        is emitted and the raster is exported alongside it. Since the pattern
        combo became the region editor, selecting an Engrave region leaves
        "Image" showing here — reporting it as a generator made the solver
        fail with "Pattern 'Image' is no longer available".
        """
        key = self._pattern_key(self._pattern_combo.currentText())
        return NULL_PATTERN if key == IMAGE_PATTERN else key

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

    def _sync_outline_ids(
        self,
        new_polys: list[list[tuple[float, float]]],
        *,
        entity_ids: list[str] | None = None,
    ) -> list[str]:
        return self._pattern_service.sync_outline_ids(
            new_polys,
            list(self._edit_polys),
            list(self._outline_ids),
            new_entity_ids=entity_ids,
        )

    def _region_tree(self) -> dict:
        return region_tree(self)

    @property
    def _zones(self) -> list[dict]:
        """Region treatments in the zone shape ``engine/patterns`` consumes.

        Read-only on purpose: treatments are the single source of truth, so
        there is nowhere for a zone list to drift out of sync with geometry.
        """
        return project_treatment_zones(self)

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

    # ── Output ────────────────────────────────────────────────────────────────
    #
    # One panel listing what the document produces, in the order the machine
    # runs it. There is no export "kind" to choose: the operations are derived
    # from the treatments, and one Export writes every enabled one.

    def _document_operations(self) -> list:
        from simple_stipple.features.pattern.export import document_operations

        operations = document_operations(self)
        order = {key: index for index, key in enumerate(self._output_order)}
        operations.sort(key=lambda op: order.get(op.key, len(order)))
        self._output_order = [op.key for op in operations]
        return operations

    def _enabled_operations(self) -> list:
        return [op for op in self._document_operations() if op.key not in self._output_disabled]

    def _refresh_output_panel(self) -> None:
        if not hasattr(self, "_output_list"):
            return
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QListWidgetItem

        operations = self._document_operations()
        self._output_list.blockSignals(True)
        self._output_list.clear()
        for index, operation in enumerate(operations, start=1):
            item = QListWidgetItem(f"{index}  {operation.label}")
            item.setFlags(item.flags() | _Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                _Qt.CheckState.Unchecked
                if operation.key in self._output_disabled
                else _Qt.CheckState.Checked
            )
            item.setData(_Qt.ItemDataRole.UserRole, operation.key)
            self._output_list.addItem(item)
        self._output_list.blockSignals(False)
        # Reordering needs a row to move; on an empty list an active button
        # reads as broken rather than as "nothing to reorder yet".
        self._output_up_btn.setEnabled(bool(operations))
        self._output_down_btn.setEnabled(bool(operations))
        self._refresh_preflight_markers()

    def _on_output_row_toggled(self, item) -> None:
        from PySide6.QtCore import Qt as _Qt

        key = str(item.data(_Qt.ItemDataRole.UserRole) or "")
        if item.checkState() == _Qt.CheckState.Checked:
            self._output_disabled.discard(key)
        else:
            self._output_disabled.add(key)
        self._emit_state_changed()

    def _move_output_row(self, delta: int) -> None:
        row = self._output_list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(self._output_order):
            return
        order = list(self._output_order)
        order[row], order[target] = order[target], order[row]
        self._output_order = order
        self._refresh_output_panel()
        self._output_list.setCurrentRow(target)
        self._emit_state_changed()

    # ── Continuous validation ─────────────────────────────────────────────────

    def _refresh_preflight_markers(self, *_args) -> None:
        """Put preflight findings on the part while it is being designed.

        Preflight already produced records carrying a point and a severity; it
        just ran at export, where a fix is expensive. It now runs on the same
        debounce as the solver and draws on the canvas.
        """
        if not hasattr(self, "_output_preflight"):
            return
        from simple_stipple.core.cad.preflight import analyze_geometry
        from simple_stipple.features.pattern.export import density_issues

        report = analyze_geometry([list(poly) for poly in self._edit_polys])
        # An open endpoint is only informational when open paths are a valid
        # output. When they are not, it is the finding the user most needs.
        allow_open = self._export_open_paths_cb.isChecked()
        issues = [
            issue
            for issue in report.issues
            if issue.severity in {"warning", "error"}
            or (not allow_open and issue.kind in {"open_start", "open_end"})
        ]
        try:
            minimum = float(self._min_density_edit.text() or 0.0)
        except (TypeError, ValueError):
            minimum = 0.0
        if minimum > 0:
            try:
                issues.extend(density_issues(self._snapshot_zone_jobs(), minimum))
            except ValueError:
                pass  # nothing solvable yet; geometry findings still stand
        self._canvas.set_issue_markers(issues)
        if not self._edit_polys:
            text = "Preflight · Load an outline to begin"
        elif issues:
            errors = sum(1 for issue in issues if issue.severity == "error")
            text = (
                f"Preflight · {len(issues)} finding{'s' if len(issues) != 1 else ''}"
                f"{f' ({errors} blocking)' if errors else ''} — marked on the canvas"
            )
        else:
            text = f"Preflight · {report.paths} paths, no findings"
        self._output_preflight.setText(text)

    def _on_issue_marker_clicked(self, marker) -> bool:
        """Select the path a finding belongs to, so the fix is one click away."""
        index = int(getattr(marker, "path_index", -1))
        if not 0 <= index < len(self._outline_ids):
            return False
        self._canvas.set_selection([self._outline_ids[index]])
        self._set_status(marker.message, STATUS_WARN)
        return True

    # ── Document pattern grid ─────────────────────────────────────────────────

    def _on_document_lattice_changed(self, *_args) -> None:
        """Push the document grid to the engine and re-solve every region.

        This is document scope on purpose: it must never be routed through
        ``_on_inspector_edit``, which would write the grid into whichever
        region happened to be selected.
        """
        self._push_document_lattice()
        self._schedule_preview()
        self._emit_state_changed()

    def _push_document_lattice(self) -> None:
        def number(field, fallback: float) -> float:
            try:
                return float(field.text())
            except (TypeError, ValueError):
                return fallback

        self._pattern_service.lattice_origin = (
            number(self._lattice_origin_x, 0.0),
            number(self._lattice_origin_y, 0.0),
        )
        seed_text = self._lattice_seed.text().strip()
        self._pattern_service.lattice_seed = int(seed_text) if seed_text.isdigit() else None

    def _snap_lattice_to_selection(self) -> None:
        """Anchor the document grid on the selection's lower-left corner."""
        points = [point for poly in self._canvas.get_selected() for point in poly]
        if not points:
            self._set_status("Select geometry to snap the pattern grid to it.", STATUS_WARN)
            return
        self._lattice_origin_x.setText(f"{min(x for x, _y in points):g}")
        self._lattice_origin_y.setText(f"{min(y for _x, y in points):g}")
        self._set_status("Pattern grid snapped to the selection.", STATUS_OK)

    # ── Preview / reset ───────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._clear_preview_state()
        self._schedule_preview()
        self._emit_state_changed()

    def _clear_preview_state(self) -> None:
        """Drop the solved overlay and caches without scheduling a re-solve."""
        self._export_is_current = False
        self._preview_is_stale = False
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        self._preview_zone_owners = []
        self._canvas.set_result_polylines([])
        self._set_preview_status("Choose a treatment to solve a pattern")
        self._update_preview_controls()

    # ── Build (UI construction) ───────────────────────────────────────────────

    def _update_engraving_section_summaries(self, *_args) -> None:
        self._refresh_engraving_ui()
        self._engraving_process_section.set_subtitle(
            f"{self._engrave_material.currentText()} · "
            f"{self._engrave_max_power.value():.0f}% · {self._engrave_speed.value():.0f} mm/s"
        )
        valid = self._engrave_min_power.value() <= self._engrave_max_power.value()
        self._engraving_process_error.setText(
            "Minimum power cannot exceed maximum power. Lower Min power or raise Max power."
            if not valid
            else ""
        )
        self._engraving_process_error.setVisible(not valid)

    def _refresh_engraving_ui(self) -> None:
        """Keep the image workspace actions clear and truthful at all times."""
        has_image = bool(self._engraving_image_path)
        if has_image:
            name = Path(self._engraving_image_path).name
            self._engraving_image_label.setText(name)
            self._engraving_section.set_subtitle(
                f"{name} · {self._engrave_w.value():.1f} × {self._engrave_h.value():.1f} mm"
            )
            self._engrave_choose_btn.setText("Replace image…")
        else:
            self._engraving_image_label.setText(
                "No image selected — add one to place and engrave it."
            )
            self._engraving_section.set_subtitle("Add an image to begin")
            self._engrave_choose_btn.setText("Add image…")
        self._engrave_remove_btn.setEnabled(has_image)
        self._engrave_edit_btn.setEnabled(has_image)
        self._engrave_fit_btn.setEnabled(has_image)
        self._engrave_center_btn.setEnabled(has_image)
        # Keep the CTA actionable. It explains the prerequisite if no source
        # exists instead of becoming a dead, undiscoverable control.
        self._engrave_export_btn.setEnabled(True)

    def _engraving_bounds(self) -> tuple[float, float, float, float] | None:
        """Return the active outline bounds, if the workspace has an outline."""
        points = [point for poly in self._generation_polys() for point in poly]
        if not points:
            return None
        xs, ys = zip(*points, strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    def _remove_engraving_image(self) -> None:
        """Detach the image from the workspace without touching the source file."""
        if not self._engraving_image_path:
            return
        self._engraving_image_path = ""
        self._canvas.clear_background_image()
        self._refresh_engraving_ui()
        self._set_status("Engraving image removed from this workspace.", STATUS_OK)
        self._emit_state_changed()

    def _edit_engraving_on_canvas(self) -> None:
        if not self._engraving_image_path:
            self._set_status("Add an engraving image before editing it.", STATUS_WARN)
            return
        self._engraving_section.set_expanded(True)
        if not self._engrave_canvas_edit.isChecked():
            self._engrave_canvas_edit.setChecked(True)
        self._update_engraving_overlay()
        self._canvas.select_background_image(True)
        self._set_status(
            "Image selected — drag it, use handles, or press Tab for placement fields.", STATUS_OK
        )

    def _center_engraving_image(self) -> None:
        if not self._engraving_image_path:
            return
        bounds = self._engraving_bounds()
        if bounds is not None:
            left, top, right, bottom = bounds
            center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
            context = "the outline"
        else:
            center_x, center_y = self._canvas._c2w(
                self._canvas.width() / 2.0, self._canvas.height() / 2.0
            )
            context = "the current canvas view"
        self._engrave_x.setValue(center_x - self._engrave_w.value() / 2.0)
        self._engrave_y.setValue(center_y - self._engrave_h.value() / 2.0)
        self._set_status(f"Engraving image centered in {context}.", STATUS_OK)

    def _fit_engraving_to_outline(self) -> None:
        if not self._engraving_image_path:
            return
        bounds = self._engraving_bounds()
        if bounds is None:
            self._set_status("Add an outline before fitting the engraving image.", STATUS_WARN)
            return
        left, top, right, bottom = bounds
        self._engrave_x.setValue(left)
        self._engrave_y.setValue(top)
        self._engrave_w.setValue(max(right - left, 0.01))
        self._engrave_h.setValue(max(bottom - top, 0.01))
        self._set_status("Engraving image fitted to the outline bounds.", STATUS_OK)

    def _on_engraving_canvas_key(self, action: str, reverse: bool = False) -> None:
        """Hand a canvas-selected image over to the inspector, or remove it.

        Tab moves focus into the placement fields once; from there Qt's own
        focus chain walks them, which is what the hand-rolled index cycle was
        badly reimplementing.
        """
        if action == "remove":
            self._remove_engraving_image()
            return
        self._engraving_section.set_expanded(True)
        target = self._engrave_rotation if reverse else self._engrave_x
        target.setFocus()
        target.selectAll()

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
        self._attach_image_to_selected_region(path)
        # Natural size, not auto-fitted: the image arrives at the size it
        # actually is and the user places it. Auto-scaling it to the outline
        # silently changed the artwork's dimensions before it was ever seen.
        width_mm, height_mm = self._natural_image_size_mm(path)
        if width_mm > 0 and height_mm > 0:
            self._engrave_w.setValue(width_mm)
            self._engrave_h.setValue(height_mm)
        self._center_engraving_image()
        self._push_engraving_placement_to_region()
        self._update_engraving_overlay()
        self._canvas.select_background_image(True)
        self._engraving_section.set_expanded(True)
        self._refresh_engraving_ui()
        self._set_status(
            "Engraving image placed in the outline — drag it or use the corner handles.",
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
        # Dragging the image on canvas is a placement edit like any other, so
        # it lands on the region and rides the same undo stack.
        self._push_engraving_placement_to_region()
        self._emit_state_changed()

    def _on_engraving_selection_changed(self, selected: bool) -> None:
        if selected:
            self._engraving_section.set_expanded(True)
            self._set_status(
                "Active target: engraving image — drag it, use handles, or press Tab to edit placement.",
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
                # Shown whole on canvas, deliberately: the image is something
                # you position and resize, so cropping the preview to its
                # region hid the very edges you drag. Export still clips it.
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
                self._canvas.set_background_image_key_callback(self._on_engraving_canvas_key)
        except OSError:
            self._canvas.clear_background_image()

    def _natural_image_size_mm(self, path: str) -> tuple[float, float]:
        """Physical size of the image, from its DPI when it declares one."""
        try:
            with Image.open(path) as source:
                dpi = source.info.get("dpi")
                if isinstance(dpi, tuple) and len(dpi) >= 2 and dpi[0] and dpi[1]:
                    return (
                        source.width / float(dpi[0]) * 25.4,
                        source.height / float(dpi[1]) * 25.4,
                    )
                if source.width > 0 and source.height > 0:
                    # No DPI: fall back to the current width, keeping aspect.
                    width_mm = max(self._engrave_w.value(), 1.0)
                    return width_mm, width_mm * source.height / source.width
        except OSError:
            pass
        return 0.0, 0.0

    def _drop_image_into_region(self, path: str, event) -> bool:
        """Engrave a dropped image into the region under the cursor."""
        from simple_stipple.features.pattern.regions.zones import row_for_region_id

        canvas_pos = self._canvas.mapFrom(self, event.position().toPoint())
        region_id = self._canvas._find_region_at(canvas_pos.x(), canvas_pos.y())
        if region_id is None or region_id not in self._outline_ids:
            self._set_status(
                "Drop the image inside a closed region to engrave it there.", STATUS_WARN
            )
            return False
        # Land it at its natural size, centred on the drop point — not scaled
        # to fill the region, which would resize the artwork behind the user's
        # back. The region still masks it; only the sizing is theirs.
        self._engraving_image_path = path
        width_mm, height_mm = self._natural_image_size_mm(path)
        if width_mm > 0 and height_mm > 0:
            self._engrave_w.setValue(width_mm)
            self._engrave_h.setValue(height_mm)
        drop_x, drop_y = self._canvas._c2w(canvas_pos.x(), canvas_pos.y())
        self._engrave_x.setValue(drop_x - self._engrave_w.value() / 2.0)
        self._engrave_y.setValue(drop_y - self._engrave_h.value() / 2.0)
        row = row_for_region_id(self, region_id)
        if row >= 0:
            self._zone_list.setCurrentRow(row)
        self._attach_image_to_selected_region(path)
        self._update_engraving_overlay()
        self._set_status(f"Engraving {Path(path).name} into this region.", STATUS_OK)
        return True

    def _selected_region_id(self) -> str | None:
        from simple_stipple.features.pattern.regions.zones import selected_region_id

        return selected_region_id(self)

    def _attach_image_to_selected_region(self, path: str) -> None:
        """Give the selected region this image, as one undo step.

        The image belongs to the region that masks it — that is what removes
        the need for a separate "which target?" choice, and it is why choosing
        an image also makes the region an Engrave region.
        """
        region_id = self._selected_region_id()
        if region_id is None:
            # No region selected is not a dead end: the image is on the part,
            # so it engraves over the whole outline and shows up in Output as
            # its own operation. Silently doing nothing here is what let an
            # image sit on the canvas and vanish from the export.
            self._set_status(
                "Image added over the whole outline. Select a region and re-add it "
                "to clip it to that region instead.",
                STATUS_OK,
            )
            self._refresh_output_panel()
            return
        set_region_engraving(
            self,
            region_id,
            {
                "path": path,
                "x": self._engrave_x.value(),
                "y": self._engrave_y.value(),
                "width": self._engrave_w.value(),
                "height": self._engrave_h.value(),
                "rotation": self._engrave_rotation.value(),
            },
        )
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()

    def _sync_engraving_widgets_from_region(self, region_id: str | None) -> None:
        """Point the placement controls at whichever region is selected."""
        engraving = region_engraving(self, region_id) if region_id else None
        if engraving is None:
            return
        self._suspend_state = True
        try:
            self._engraving_image_path = str(engraving.get("path", ""))
            self._engrave_x.setValue(float(engraving.get("x", 0.0)))
            self._engrave_y.setValue(float(engraving.get("y", 0.0)))
            if float(engraving.get("width", 0.0)) > 0:
                self._engrave_w.setValue(float(engraving["width"]))
            if float(engraving.get("height", 0.0)) > 0:
                self._engrave_h.setValue(float(engraving["height"]))
            self._engrave_rotation.setValue(float(engraving.get("rotation", 0.0)))
        finally:
            self._suspend_state = False
        self._refresh_engraving_ui()

    def _push_engraving_placement_to_region(self) -> None:
        """Mirror a placement edit back onto the region that owns the image."""
        region_id = self._selected_region_id()
        if region_id is None or region_engraving(self, region_id) is None:
            return
        update_region_engraving(
            self,
            region_id,
            x=self._engrave_x.value(),
            y=self._engrave_y.value(),
            width=self._engrave_w.value(),
            height=self._engrave_h.value(),
            rotation=self._engrave_rotation.value(),
        )

    def _engraving_mask_polys(self) -> list[list[tuple[float, float]]]:
        return engraving_mask_polys(self)

    def _use_engraving_export(self) -> None:
        """Point at the one Export button; the image is already an operation."""
        self._output_section.set_expanded(True)
        self._set_status(
            "This image is an Engrave operation in Output — Export writes it with everything else.",
            STATUS_OK,
        )

    def _active_engraving(self) -> tuple[str, dict] | None:
        """The image to export: the selected region's, else the first one."""
        from simple_stipple.features.pattern.regions.treatments import engraving_regions

        found = engraving_regions(self)
        if not found:
            return None
        selected = self._selected_region_id()
        for region_id, engraving in found:
            if region_id == selected:
                return region_id, engraving
        return found[0]

    def _with_solved_pattern(self, continuation) -> None:
        """Run an export once the geometry it writes has finished solving."""
        if self._preview_polys_cache and not self._preview_is_stale:
            continuation()
            return
        if not self._zones and not self._edit_polys:
            self._set_status("Load an outline before exporting.", STATUS_WARN)
            return
        self._pending_export_after_preview = continuation
        self._set_status("Solving the pattern before export…", STATUS_WARN)
        self._schedule_preview()

    # ── Format ────────────────────────────────────────────────────────────
    #
    # The format picker sits beside Export and changes what the file *is*.
    # It never changes which operations get written — the Output panel owns
    # that, which is what separates this from the old three-kind fork where
    # picking "engraving" silently dropped your vectors.

    def _select_export_format(self, export_format: str) -> None:
        if export_format not in EXPORT_FORMAT_KEYS:
            return
        self._export_format = export_format
        self._settings["pattern_export_format"] = export_format
        self._refresh_export_format_label()
        self._emit_state_changed()
        self._set_status(f"Export format set to {export_format.upper()}", STATUS_OK)

    def _refresh_export_format_label(self) -> None:
        if not hasattr(self, "_gen_btn"):
            return
        self._gen_btn.setText(EXPORT_BUTTON_LABEL[self._export_format])
        for key, action in getattr(self, "_export_actions", {}).items():
            action.setChecked(key == self._export_format)

    def _export_document_job(self) -> None:
        """One Export: every enabled operation, in the chosen format."""
        operations = self._enabled_operations()
        if not operations:
            self._set_status("Nothing to export — load or draw an outline first.", STATUS_WARN)
            return
        engraving = any(op.kind == "engrave" for op in operations)
        if engraving and self._engrave_min_power.value() > self._engrave_max_power.value():
            self._engraving_section.set_expanded(True)
            self._engraving_process_section.set_expanded(True)
            self._set_status(
                "Export blocked: minimum engraving power exceeds maximum power.", STATUS_ERR
            )
            return
        proceed, _report = export_preflight(
            self,
            [list(poly) for poly in self._edit_polys],
            action="Export",
            allow_open_paths=self._export_open_paths_cb.isChecked(),
        )
        if not proceed:
            self._canvas.set_geometry_health_visible(True, announce=True)
            self._set_status("Export paused — review highlighted geometry.", STATUS_WARN)
            return
        # Export solves at full quality regardless of the preview setting: the
        # thing being written is the part, not a picture of it.
        self._force_export_quality = True
        self._preview_is_stale = True
        self._with_solved_pattern(self._perform_document_export)

    def _collect_engraving_job(self) -> tuple[str | None, Any, list | None]:
        """Source, settings, and clip mask for the enabled Engrave operation."""
        active = self._active_engraving()
        if active is not None:
            self._sync_engraving_widgets_from_region(active[0])
        if not self._engraving_image_path:
            return None, None, None
        job = build_engraving_job(
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
        return self._engraving_image_path, job, self._engraving_mask_polys()

    def _perform_document_export(self) -> None:
        self._force_export_quality = False
        operations = self._enabled_operations()
        wants_engraving = any(op.kind == "engrave" for op in operations)
        wants_vectors = any(op.kind in {"mark", "cut"} for op in operations)
        try:
            raster_source, engraving_job, raster_mask = (
                self._collect_engraving_job() if wants_engraving else (None, None, None)
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        vectors = list(self._preview_polys_cache) if wants_vectors else []
        if self._export_format == "laserstar":
            self._write_laserstar_package(
                operations, vectors, raster_source, engraving_job, raster_mask
            )
            return
        self._write_single_file(operations, vectors, raster_source, engraving_job, raster_mask)

    def _write_single_file(
        self, operations, vectors, raster_source, engraving_job, raster_mask
    ) -> None:
        suffix = export_format_suffix(self._export_format)
        source_name = (
            Path(self._dxf_edit.text()).stem if self._dxf_edit.text().strip() else "pattern"
        )
        out_path = pick_save_file(
            self,
            self._settings,
            "pattern_output",
            f"Export {self._export_format.upper()}",
            f"{source_name}{suffix}",
            f"{self._export_format.upper()} files (*{suffix});;All files (*)",
            fallback_dir=self._settings.get("pattern_output_dir", ""),
        )
        if not out_path:
            return
        try:
            written = export_document_file(
                out_path,
                self._export_format,
                vectors,
                engraving_source=raster_source,
                engraving_job=engraving_job,
                engraving_mask=raster_mask,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        self._last_out_path = str(written[0])
        self._export_is_current = True
        self._reveal_btn.setVisible(True)
        self._operator_notes_btn.setVisible(False)
        extra = (
            f" (+{len(written) - 1} engraving file{'s' if len(written) > 2 else ''})"
            if len(written) > 1
            else ""
        )
        self._set_status(
            f"{len(operations)} operation{'s' if len(operations) != 1 else ''} exported → "
            f"{Path(written[0]).name}{extra}",
            STATUS_OK,
        )
        self._update_preview_controls()

    def _write_laserstar_package(
        self, operations, vectors, raster_source, engraving_job, raster_mask
    ) -> None:
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
            has_engraving=bool(raster_source),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._settings["laserstar_job_name"] = values["job_name"]
        self._settings["laserstar_output_dir"] = values["destination"]
        try:
            folder = export_laserstar_job(
                values["destination"],
                values["job_name"],
                vectors,
                engraving_source=raster_source,
                engraving_job=engraving_job,
                engraving_mask=raster_mask,
                vector_format=values["format"],
            )
            self._last_out_path = str(folder / "LaserStar-Setup.txt")
            self._export_is_current = True
            self._reveal_btn.setVisible(True)
            self._operator_notes_btn.setVisible(True)
            self._set_status(
                f"{len(operations)} operation{'s' if len(operations) != 1 else ''} "
                f"exported as one package → {folder.name}",
                STATUS_OK,
            )
        except (FileExistsError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
        self._update_preview_controls()

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
        # “None” is a valid treatment: it produces fill-only or outline-only
        # output. Refresh it exactly as we do every other pattern choice.
        self._on_inspector_edit()
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
                self._zones_section.set_subtitle(
                    "Optional · different pattern for a selection", dim=True
                )
            else:
                self._zones_section.set_subtitle(f"{n} zone{'s' if n != 1 else ''} assigned")

    def _install_pattern_shortcuts(self) -> None:
        modifier = "Meta" if platform.system() == "Darwin" else "Ctrl"
        QShortcut(QKeySequence(f"{modifier}+Z"), self, self._undo_pattern)
        QShortcut(QKeySequence(f"{modifier}+Shift+Z"), self, self._redo_pattern)
        QShortcut(QKeySequence(f"{modifier}+E"), self, self._export_document_job)
        QShortcut(QKeySequence(f"{modifier}+R"), self, self._reload_dxf)
        QShortcut(QKeySequence(f"{modifier}+P"), self, self._apply_selected_preset)

    def _undo_treatment_hook(self) -> bool:
        """Canvas undo hook — revert a treatment change if it is the latest."""
        if not undo_treatments(self):
            return False
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()
        self._set_status("Undo — region treatment restored", STATUS_OK)
        return True

    def _redo_treatment_hook(self) -> bool:
        if not redo_treatments(self):
            return False
        self._refresh_zone_list()
        self._schedule_preview()
        self._emit_state_changed()
        self._set_status("Redo — region treatment reapplied", STATUS_OK)
        return True

    def _undo_pattern(self) -> None:
        """Undo the latest Pattern change — treatment or geometry."""
        if self._canvas.undo():
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def _redo_pattern(self) -> None:
        """Redo the latest Pattern change — treatment or geometry."""
        if self._canvas.redo():
            self._refresh_canvas_panels()
            self._schedule_preview()
            self._emit_state_changed()

    def command_palette_commands(self) -> list[dict]:
        """Commands contributed to the application's single global palette."""
        modifier = "Meta" if platform.system() == "Darwin" else "Ctrl"

        def shortcut(key: str) -> str:
            return QKeySequence(f"{modifier}+{key}").toString(
                QKeySequence.SequenceFormat.NativeText
            )

        commands: list[dict] = [
            {
                "title": "Export job",
                "shortcut": shortcut("E"),
                "subtitle": "Write every enabled operation as one job",
                "run": self._export_document_job,
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
                "title": "Manage presets…",
                "subtitle": "Rename, duplicate, import, export",
                "run": self._open_preset_manager,
            },
            {
                "title": "Apply treatment to selection",
                "subtitle": "Give the selected regions these pattern settings",
                "run": self._assign_zone,
            },
            {"title": "Clear all region treatments", "run": self._clear_zones},
            {
                "title": "Toggle border on separate layer",
                "run": lambda: self._include_border_cb.setChecked(
                    not self._include_border_cb.isChecked()
                ),
            },
        ]
        return commands

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

    def _show_outline_path(self, path: str) -> None:
        """Show an outline path from its beginning, with its full value discoverable.

        ``QLineEdit.setText`` places its cursor at the end on some Qt styles,
        which horizontally scrolls a long path until the filename's context is
        cut off. Keeping the cursor at the beginning makes the field stable;
        the full, untruncated path remains available in the tooltip and is
        still the editable value used by reload.
        """
        self._dxf_edit.setText(path)
        self._dxf_edit.setCursorPosition(0)
        self._dxf_edit.setToolTip(path or "Path to the current outline file")

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
            self._show_outline_path(path)
            self._load_outline_file(path)

    def _load_outline_file(self, path: str) -> None:
        suffix = Path(path).suffix.lower()
        if suffix == ".dxf":
            self._load_dxf(path)
            return
        try:
            imported = read_outline_vector(
                path,
                # Keep the established page-module dependency bindings
                # injectable for downstream integrations and tests.
                read_fvi_file=read_fvi,
                convert_svg=svg_to_dxf,
                read_dxf=load_dxf_polylines_with_report,
                read_svg_artwork=read_svg_images,
            )
            if not imported.polylines and not imported.images:
                raise ValueError(f"No supported outline geometry was found in {Path(path).name}.")
            if imported.polylines:
                self.load_outline_polys(imported.polylines, source_label=Path(path).name)
            self._show_outline_path(path)
            if imported.images:
                self._restore_imported_image(imported.images[0], Path(path).stem)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))

    def _restore_imported_image(self, placement, stem: str) -> None:
        """Put an image carried by an imported SVG back on the part.

        Reopening our own export used to silently lose the artwork: the
        importer only ever recovered linework. The raster is unpacked to the
        app's data directory so the placement keeps a real file to point at
        after the source SVG moves or the temp directory goes away.
        """
        folder = user_data_dir() / "imported-images"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stem or 'imported'}.png"
        try:
            target.write_bytes(placement.png_bytes)
        except OSError as exc:
            self._set_status(f"Image in this SVG could not be unpacked: {exc}", STATUS_WARN)
            return
        self._engraving_image_path = str(target)
        self._engrave_x.setValue(placement.x_mm)
        self._engrave_y.setValue(placement.y_mm)
        self._engrave_w.setValue(max(placement.width_mm, 0.01))
        self._engrave_h.setValue(max(placement.height_mm, 0.01))
        self._engrave_rotation.setValue(placement.rotation_deg)
        # Land it in the region it sits inside, so it keeps its clip mask.
        region_id = self._region_under_placement(placement)
        if region_id is not None:
            from simple_stipple.features.pattern.regions.zones import row_for_region_id

            self._zone_list.setCurrentRow(row_for_region_id(self, region_id))
        self._attach_image_to_selected_region(str(target))
        self._update_engraving_overlay()
        self._refresh_engraving_ui()
        self._refresh_output_panel()
        self._set_status(f"Imported the outline and its engraving image from {stem}.", STATUS_OK)

    def _region_under_placement(self, placement) -> str | None:
        """The smallest region containing the image's centre, if any."""
        return smallest_containing_outline(
            self._outline_ids,
            self._edit_polys,
            (
                placement.x_mm + placement.width_mm / 2.0,
                placement.y_mm + placement.height_mm / 2.0,
            ),
        )

    def load_outline_polys(
        self,
        polys: list,
        *,
        source_label: str = "Draft selection",
        offer_undo: bool = False,
    ) -> None:
        if not polys:
            return
        self._pre_transfer_state = self.get_workspace_state() if offer_undo else None
        normalized = normalize_outline_items(polys)
        incoming, layers = normalized.polylines, normalized.layers
        if not incoming:
            return
        self._suspend_state = True
        self._canvas.set_result_polylines([])
        self._orig_polys = [list(poly) for poly in incoming]
        self._edit_polys = [list(poly) for poly in incoming]
        self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
        self._outline_layers = {
            entity_id: layer or "Outline"
            for entity_id, layer in zip(self._outline_ids, layers, strict=True)
        }
        self._treatments = {}
        self._export_is_current = False
        self._preview_is_stale = False
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        self._refresh_zone_list()
        self._load_outline_canvas(fit=True)
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

    def _load_outline_canvas(
        self,
        *,
        fit: bool,
        polys: list[list[tuple[float, float]]] | None = None,
        entity_ids: list[str] | None = None,
    ) -> None:
        """Load editable outlines without discarding their source layers."""
        paths = self._edit_polys if polys is None else polys
        ids = self._outline_ids if entity_ids is None else entity_ids
        if len(ids) != len(paths):
            # A desync must never take the app down. Rebuild the identities
            # that are missing and carry on with the geometry we have.
            LOGGER.warning(
                "Outline ids (%d) and polys (%d) disagree; reconciling",
                len(ids),
                len(paths),
            )
            ids = reconcile_outline_ids(ids, paths, self._fresh_outline_ids)
            if entity_ids is None:
                self._outline_ids = ids
        records, layer_order = canvas_records(paths, ids, self._outline_layers)
        self._canvas.set_layer_model(layer_order, layer_order[0] if layer_order else None)
        self._canvas.set_entity_records(records, fit=fit)

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
        bounds = outline_bounds(polys)
        if bounds is not None:
            self._orig_w, self._orig_h = bounds
            self._orig_dims_label.setText(f"{self._orig_w:.2f} × {self._orig_h:.2f} mm")
            self._scale_w.blockSignals(True)
            self._scale_h.blockSignals(True)
            self._scale_w.setText(f"{self._orig_w:.2f}")
            self._scale_h.setText(f"{self._orig_h:.2f}")
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
        try:
            polys, report = load_dxf_polylines_with_report(path)
            self._show_outline_path(path)
            self._orig_polys = polys
            self._edit_polys = list(polys)
            self._outline_ids = self._fresh_outline_ids(len(self._edit_polys))
            self._treatments = {}
            self._refresh_zone_list()
            self._canvas.load(polys, entity_ids=list(self._outline_ids))
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

    def _quick_load(self, path: str) -> None:
        self._show_outline_path(path)
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

    def _populate_pattern_combo(self, combo: QComboBox, current: str | None = None) -> None:
        current = combo.currentText() if current is None else current
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._base_patterns)
        # An image is one of the things a region can be, so it belongs in the
        # same list as the generated patterns rather than in its own section.
        combo.insertItem(1, IMAGE_PATTERN)
        if self._tile_motifs:
            combo.insertSeparator(combo.count())
            for name in sorted(self._tile_motifs, key=str.casefold):
                combo.addItem(f"Custom · {name}")
        target = current if combo.findText(current) >= 0 else "— None —"
        combo.setCurrentText(target)
        combo.blockSignals(False)
        if combo is getattr(self, "_pattern_combo", None):
            self._update_custom_pattern_actions(target)

    def _live_update_selected_zone(self, *_args) -> bool:
        return live_update_selected_zone(self, *_args)

    def _on_inspector_edit(self, *_args) -> None:
        """One editor, two possible targets.

        With a region selected the edit lands on that region's treatment;
        with nothing selected the same widgets are the document defaults.
        Either way the pattern re-solves — there is no scope to remember.
        """
        if not self._live_update_selected_zone():
            self._schedule_preview()

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
        # Preflight clears its own markers on empty geometry, so it runs
        # before the empty-workspace guard to wipe stale findings too.
        self._refresh_preflight_markers()
        if not self._zones and not self._edit_polys:
            # Deleting the last outline must drop the last-good solve too —
            # keeping it on screen is only right while a re-solve is coming.
            # _schedule_preview also fires during __init__, before the canvas
            # exists; there is nothing to clear yet in that case.
            if hasattr(self, "_canvas"):
                self._clear_preview_state()
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
            params["quality"] = (
                "high"
                if self._force_export_quality
                else (self._preview_quality_combo.currentData() or DEFAULT_PREVIEW_QUALITY)
            )
            if not self._zones:
                self._validate_outline_inputs(self._edit_polys)
        except ValueError as exc:
            self._preview_task.finish_run()
            self._set_preview_status(str(exc), "error")
            self._update_preview_controls()
            return
        try:
            border_fade = max(0.0, float(self._border_fade.text() or DEFAULT_BORDER_FADE))
        except ValueError:
            border_fade = 0.0
        fill_options = self._collect_fill_options()
        self._set_preview_status("Solving…")
        border_polys = None
        if self._zones:
            try:
                zones_snap = self._snapshot_zone_jobs()
            except ValueError as exc:
                self._preview_task.finish_run()
                self._set_preview_status(str(exc), "error")
                self._update_preview_controls()
                return
            all_polys_snap = self._generation_polys()
        else:
            polys_snap = self._generation_polys()
            border_polys = self._apply_scale(polys_snap, *scale) if include_border else None
            zones_snap = []
            all_polys_snap = polys_snap
        worker_call = build_preview_worker_call(
            zones=zones_snap,
            all_polys=all_polys_snap,
            pattern=pattern,
            params=params,
            scale=scale,
            border_polys=border_polys,
            border_fade=border_fade,
            preview_token=preview_token,
            cancel_event=cancel_event,
            pattern_service=self._pattern_service,
            orig_w=self._orig_w,
            orig_h=self._orig_h,
            on_done=self._preview_done.emit,
            on_error=self._preview_error.emit,
            fill_options=fill_options,
            compute_preview=compute_preview,
            compute_preview_zones=compute_preview_zones,
        )
        self._preview_thread = threading.Thread(
            target=worker_call.target,
            args=worker_call.args,
            kwargs=worker_call.kwargs,
            daemon=True,
        )
        self._preview_thread.start()

    def _handle_preview_done(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        # Reloading the canvas and rebuilding the layer tree can pull keyboard
        # focus out of whatever the user is typing in. A preview is background
        # work; it must never take the caret away mid-edit.
        focused = QApplication.focusWidget()
        try:
            self._handle_preview_done_inner(payload)
        finally:
            if focused is not None and focused.isVisible():
                focused.setFocus(Qt.FocusReason.OtherFocusReason)

    def _handle_preview_done_inner(self, payload: tuple) -> None:
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
        from simple_stipple.core.patterns.output import diagnose_output

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
        # The solved pattern is an overlay, never an entity swap: the canvas
        # keeps holding the real outlines, so editing during a preview is
        # editing the document.
        outline_count = len(self._preview_categories.get("outline", []))
        pattern_count = len(self._preview_categories.get("pattern", []))
        self._canvas.set_result_polylines(
            display_polys,
            pattern_span=(outline_count, outline_count + pattern_count),
        )
        selected_row = self._zone_list.currentRow()
        if selected_row >= 0:
            self._highlight_zone_on_canvas(selected_row)
        if self._canvas.result_visible():
            self._set_preview_status(f"{status_text} — pattern shown", "success")
        else:
            self._set_preview_status(
                f"{status_text} ready — hidden by the Pattern result layer", "success"
            )
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
        self._set_preview_status(f"Solve failed: {msg}", "error")
        if self._pending_export_after_preview is not None:
            self._pending_export_after_preview = None
            self._set_status(f"Export blocked — the pattern failed to solve: {msg}", STATUS_ERR)
        self._canvas.setToolTip("Solve failed; showing the last completed result.")
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
        self._preview_is_stale = had_cache
        self._preview_polys_cache = []
        # Keep the last-good result on screen while the next one solves, and
        # keep its metadata so a cell can still be picked during the rebuild.
        # Both are replaced atomically when the worker completes.
        if had_cache:
            self._canvas.setToolTip("Re-solving — the geometry shown is the last completed result.")
            self._set_preview_status("Refreshing pattern…")
        self._update_preview_controls()

    def _invalidate_zones_for_geometry_change(self, valid_outline_ids: set[str]) -> None:
        return invalidate_zones_for_geometry_change(self, valid_outline_ids)

    def _update_preview_controls(self) -> None:
        is_computing = self._preview_task.running
        self._cancel_preview_btn.setVisible(is_computing)
        if hasattr(self, "_gen_btn"):
            can_export = bool(self._edit_polys or self._zones)
            # Do not leave the user with a disabled primary action. Clicking
            # without an outline gives the existing clear recovery message;
            # “None” exports fill-only or outline-only geometry.
            self._gen_btn.setEnabled(not self._generate_task.running)
            if self._generate_task.running:
                export_tip = "Stop the current pattern export"
            elif can_export:
                export_tip = "Export the current outline, pattern, and/or fill as a DXF  (⌘E)"
            else:
                export_tip = "Load an outline to export it, a fill, or a pattern  (⌘E)"
            self._gen_btn.setToolTip(export_tip)
        self._refresh_output_panel()
        # Keep the core Pattern controls discoverable in the empty state.
        # They serve as editable defaults before an outline or zone exists.
        # Zones are a core workflow step, not an expert-only panel. Keep the
        # section mounted so users always have a visible recovery path even if
        # Advanced controls are disabled; only secondary fabrication controls
        # belong behind the advanced toggle.
        if hasattr(self, "_zone_scroll"):
            self._zone_scroll.setVisible(True)
        if hasattr(self, "_pattern_properties_scroll"):
            self._pattern_properties_scroll.setVisible(True)

    def _update_zone_actions(self) -> None:
        return update_zone_actions(self)

    def _set_advanced_mode(self, enabled: bool) -> None:
        """Keep laser calibration optional without hiding the image itself.

        This used to hide the whole engraving section, which since the
        inspector rework is the *only* place an image can be removed or
        placed — turning Advanced off left an image on the canvas with no
        way to delete or edit it. Image controls follow the selection; only
        the power/speed/passes detail is advanced.
        """
        enabled = bool(enabled)
        self._settings["pattern_advanced_mode"] = enabled
        for name in ("_engraving_process_section",):
            section = getattr(self, name, None)
            if section is not None:
                section.setVisible(enabled)
        sync_engraving_visibility(self)
        if hasattr(self, "_zone_scroll"):
            self._zone_scroll.setVisible(True)
        self._set_status(
            "Advanced pattern controls shown"
            if enabled
            else "Basic mode · outline, pattern, fill, preview, and export",
        )

    # ── Generation ────────────────────────────────────────────────────────────

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
        self._canvas.set_result_polylines(polys)
        self._set_preview_status(f"{count} shapes exported", "success")
        self._update_preview_controls()
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
