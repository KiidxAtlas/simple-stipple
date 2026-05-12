"""Pattern Generator page."""

# isort: skip_file
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false, reportUndefinedVariable=false

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, PATTERNS
from src.ui.core.base_page import BasePage
from src.ui.pages.pattern.session import (
    apply_pattern_workspace_state,
    clear_pattern_workspace_state,
    get_pattern_workspace_state,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.canvas.modules import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
)
from src.ui.canvas.page_runtimes import PatternCanvasPageRuntime
from src.ui.widgets.status_strip import CanvasStatusStrip
from src.ui.core.factories import (
    content_splitter,
    sidebar_panel,
    surface_frame,
    clear_line_edit_error,
    parse_float_field_with_feedback,
    set_line_edit_error,
)
from src.ui.core.focus_policy import EscapeBlurFilter
from src.ui.pages.pattern.params import (
    collect_pattern_params,
)
from src.ui.pages.pattern.presets import (
    SETTINGS_KEY as PRESET_SETTINGS_KEY,
    ensure_builtins_seeded,
)
from src.ui.pages.pattern.services import PatternProcessingService
from src.ui.pages.pattern.task_state import CancellableTaskState

from src.ui.pages.pattern._build_mixin import _BuildMixin
from src.ui.pages.pattern._subtitles_mixin import _SubtitlesMixin
from src.ui.pages.pattern._preview_mixin import _PreviewMixin
from src.ui.pages.pattern._generate_mixin import _GenerateMixin
from src.ui.pages.pattern._zone_mixin import _ZoneMixin
from src.ui.pages.pattern._exclusion_mixin import _ExclusionMixin
from src.ui.pages.pattern._preset_mixin import _PresetMixin
from src.ui.pages.pattern._dxf_mixin import _DxfMixin

LOGGER = logging.getLogger(__name__)


class PatternPage(
    _BuildMixin,
    _SubtitlesMixin,
    _PreviewMixin,
    _GenerateMixin,
    _ZoneMixin,
    _ExclusionMixin,
    _PresetMixin,
    _DxfMixin,
    BasePage,
):
    _gen_done = Signal(object)  # (count, name, polys)
    _gen_error = Signal(object)
    _preview_done = Signal(object)  # (display_polys, count)
    _preview_error = Signal(object)
    sendSelectedToDraftRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)  # BasePage sets _settings and _suspend_state

        # Runtime state
        self._orig_polys: list[list[tuple[float, float]]] = []
        self._edit_polys: list[list[tuple[float, float]]] = []
        self._orig_w: float = 0.0
        self._orig_h: float = 0.0
        self._ar_locked: bool = True
        self._updating_dims: bool = False
        self._preview_task = CancellableTaskState()
        self._generate_task = CancellableTaskState()
        self._last_out_path: str | None = None
        self._presets: dict[str, dict] = dict(self._settings.get("pattern_presets", {}))
        # Seed factory starter presets once on first run; respects deletions.
        seeded = ensure_builtins_seeded(self._settings, self._presets)
        if (
            seeded is not self._presets
            or self._settings.get("pattern_presets") != seeded
        ):
            self._presets = seeded
            self._settings[PRESET_SETTINGS_KEY] = dict(self._presets)
            try:
                from src.settings import save_settings
                save_settings(self._settings)
            except OSError:
                LOGGER.exception("Failed to persist seeded pattern presets")
        self._base_patterns: list[str] = list(PATTERNS)
        self._library_patterns: dict[str, str] = {}
        self._imported_dxf_layers: list[tuple[str, int, bool, bool]] = []
        self._tile_interlock_cb = None

        self._showing_preview: bool = False
        self._preview_polys_cache: list[list[tuple[float, float]]] = []
        self._preview_categories: dict[str, list[list[tuple[float, float]]]] = {
            "outline": [],
            "pattern": [],
            "fill": [],
        }
        self._outline_ids: list[str] = []
        self._preview_revision: int = 0
        self._generation_revision: int = 0
        self._pattern_service = PatternProcessingService()
        # Per-zone pattern assignments: each zone is a snapshot of
        # {"outline_ids": [...], "pattern": str, "params": dict,
        #  "interlace": bool, "scale": (w, h), "label": str}
        self._zones: list[dict] = []
        # Outline IDs marked as exclusion cutouts (pattern fills around them)
        self._exclusion_ids: list[str] = []

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
        root.addWidget(self._splitter)

        self._build_left(left)
        self._build_right(right)
        self._left_esc_filter = EscapeBlurFilter(self._canvas, within=left_w)
        for edit in left_w.findChildren(QLineEdit):
            edit.installEventFilter(self._left_esc_filter)
        self._update_preview_controls()

        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".dxf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".dxf"):
                self._dxf_edit.setText(path)
                self._load_dxf(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self, layout: QVBoxLayout) -> None:
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setMinimumHeight(28)
        self._preview_btn.setToolTip(
            "Toggle between outline editing and pattern preview"
        )
        self._preview_btn.clicked.connect(self._on_preview_toggled)

        self._reset_preview_btn = QPushButton("Reset")
        self._reset_preview_btn.setToolTip("Clear the preview cache and rebuild")
        self._reset_preview_btn.clicked.connect(self._reset_preview)

        self._preview_status = QLabel("No preview available")
        self._set_preview_status("No preview available")
        self._preview_status.setWordWrap(True)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_geometry_change,
            on_send_selected_to_draft=self._on_send_selected_to_draft_from_canvas,
            on_cutout_toggle=self._on_canvas_cutout_toggle,
            draft_profile=True,
        )
        self._canvas.set_grid_visible(True)
        self._canvas.set_grid_snap(False)
        self._canvas.set_grid_spacing(1.0)

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
            extra_widgets=[self._preview_btn, self._reset_preview_btn],
        )
        layout.addWidget(self._toolbar_module)

        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_canvas_panels,
        )
        layout.addWidget(self._grid_module)
        self._precision_bar = self._grid_module

        self._canvas_status = CanvasStatusStrip()
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_shell_layout = QVBoxLayout(canvas_shell)
        canvas_shell_layout.setContentsMargins(0, 0, 0, 0)
        canvas_shell_layout.setSpacing(8)
        canvas_shell_layout.addWidget(self._preview_status)
        canvas_shell_layout.addWidget(self._canvas, stretch=1)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._layer_module = CanvasLayerTreeModule(
            canvas=self._canvas,
            title="Layers",
            editable=True,
            get_active_layer_name=lambda: (
                "pattern_preview" if self._showing_preview else "pattern_active"
            ),
            build_layer_rows=self._build_layer_tree_rows,
            on_selection_requested=self._on_browser_selection_requested,
            on_fit_requested=self._fit_selection,
            on_visibility_changed=self._refresh_canvas_panels,
        )
        self._layers_tree = self._layer_module.tree
        self._layer_sidebar = self._layer_module.controller
        # Wire outline-mode shape rename to the runtime's label store.
        self._layers_tree.shapeRenamed.connect(self._on_shape_renamed)
        side_layout.addWidget(self._layer_module, stretch=1)

        self._canvas_runtime = PatternCanvasPageRuntime(
            canvas=self._canvas,
            toolbar_module=self._toolbar_module,
            layer_sidebar=self._layer_sidebar,
            canvas_status=self._canvas_status,
            precision_bar=self._precision_bar,
            get_orig_polys=lambda: self._edit_polys,
            get_showing_preview=lambda: self._showing_preview,
            is_preview_running=lambda: self._preview_task.running,
            has_preview_cache=lambda: bool(self._preview_polys_cache),
            has_zones=lambda: bool(self._zones),
            get_preview_categories=lambda: self._preview_categories,
        )

        splitter = content_splitter(canvas_shell, side_panel, sizes=(860, 260))
        layout.addWidget(splitter, stretch=1)
        self._refresh_canvas_panels()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_preview_toggled(self, checked: bool) -> None:
        """Toggle between outline editing and pattern preview display."""
        if checked and self._preview_polys_cache:
            # Switch to preview view
            self._showing_preview = True
            self._canvas.load(self._preview_polys_cache)
            # Show the source outline as a faded ghost overlay so the user can
            # see both the outline and the generated pattern at the same time.
            if self._edit_polys:
                self._canvas.set_ghost_polylines(self._edit_polys)
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
            self._canvas.set_ghost_polylines(None)
            if self._edit_polys:
                self._canvas.load(self._edit_polys)
            if self._preview_polys_cache:
                self._set_preview_status("Editing outline — preview cached")
            else:
                self._set_preview_status("Adjust settings to build a preview")
        self._preview_btn.setProperty("active", self._showing_preview)
        self._preview_btn.style().unpolish(self._preview_btn)
        self._preview_btn.style().polish(self._preview_btn)
        self._update_preview_controls()
        self._update_zone_actions()
        self._refresh_canvas_panels()

    def _on_sel_change(self, count: int) -> None:
        if self._showing_preview:
            return
        self._canvas_runtime.on_selection_change(count)  # updates toolbar
        # `_edit_polys` always mirrors the FULL canvas state — never just the
        # selection subset. Otherwise toggling preview off would only restore
        # the previously-selected shapes (and silently drop all the others).
        # If users want to pattern only specific outlines, they should create
        # zones; selection is purely for selection, not for scoping the fill.
        self._edit_polys = self._canvas.get_polylines_state()
        self._update_zone_actions()
        # Update status strip selection count without rebuilding the tree.
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
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
            return
        if self._zones:
            self._invalidate_zones_for_geometry_change()
        self._edit_polys = self._canvas.get_polylines_state()
        self._outline_ids = self._sync_outline_ids(self._edit_polys)
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

    def _on_shape_renamed(
        self, layer_name: str, shape_key: object, new_label: str
    ) -> None:
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

    def get_workspace_state(self) -> dict:
        return get_pattern_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_pattern_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_pattern_workspace_state(self)

    def _set_status(self, text: str, color: str = DIM) -> None:
        self._status.setText(text)
        if not text:
            role = ""
        elif color == "#3fb950":
            role = "status-ok"
        elif color == "#f85149":
            role = "status-err"
        else:
            role = "status-chip"
        self._status.setProperty("role", role)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

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
            self._set_status(message, "#f85149")
            raise ValueError(message)
        clear_line_edit_error(entry)
        return value

    def _collect_scale(self) -> tuple[float, float]:
        sw = self._parse_float_field(
            self._scale_w,
            "Scale width",
            minimum=0.001,
            allow_empty=True,
        )
        sh = self._parse_float_field(
            self._scale_h,
            "Scale height",
            minimum=0.001,
            allow_empty=True,
        )
        sw = self._orig_w if sw is None else sw
        sh = self._orig_h if sh is None else sh
        return sw, sh

    def _collect_pattern_params(self, pattern: str) -> dict:
        return collect_pattern_params(self, pattern)

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
        self, new_polys: list[list[tuple[float, float]]]
    ) -> list[str]:
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

    def _validate_outline_inputs(self, polys: list[list[tuple[float, float]]]) -> None:
        warning = self._pattern_service.validate_outline_inputs(polys)
        if warning:
            self._set_status(
                warning,
                "#e3b341",
            )

    def _snapshot_zone_jobs(self) -> list[dict]:
        jobs, warnings = self._pattern_service.snapshot_zone_jobs(
            self._zones,
            self._outline_ids,
            self._edit_polys,
        )
        if warnings:
            self._set_status(warnings[-1], "#e3b341")
        return jobs

    # ── Preview / reset ───────────────────────────────────────────────────────

    def _reset_preview(self) -> None:
        self._preview_polys_cache = []
        self._preview_categories = {"outline": [], "pattern": [], "fill": []}
        if self._showing_preview:
            self._preview_btn.setChecked(False)
            self._on_preview_toggled(False)
        self._set_preview_status("Adjust settings to build a preview")
        self._update_preview_controls()
        self._schedule_preview()
        self._emit_state_changed()
