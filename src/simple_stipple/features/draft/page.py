# pyright: reportAttributeAccessIssue=false
"""Draft page — interaction-first 2D drafting.

Design goals:
- Maximize canvas space; minimize persistent chrome
- Primary creation path is direct drag on canvas (no dialog/dropdown)
- Context menu and hotkeys provide secondary fast paths
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.canvas.runtime import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasRuntime,
    CanvasToolbarModule,
)
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.canvas.widgets.properties_panel import CanvasPropertiesPanel
from simple_stipple.canvas.widgets.toolbar import CanvasStatusStrip
from simple_stipple.core.cad.detection import (
    convert_to_parametric,
    detected_entities,
)
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.core.formats.service import DxfService, summarize_dxf_import_report
from simple_stipple.features.base import BasePage
from simple_stipple.features.draft.detection_dialog import ShapeDetectionDialog
from simple_stipple.features.draft.model import DraftModel
from simple_stipple.features.draft.session import (
    apply_draft_workspace_state,
    build_dxf_export_plan,
    clear_draft_workspace_state,
    get_draft_workspace_state,
)
from simple_stipple.features.draft.session import (
    on_backdrop_key as _on_backdrop_key,
)
from simple_stipple.features.draft.session import (
    on_backdrop_transform as _on_backdrop_transform,
)
from simple_stipple.features.draft.session import (
    show_imported_svg_image as _show_imported_svg_image,
)
from simple_stipple.ui.components.feedback import show_error
from simple_stipple.ui.components.layout import (
    content_splitter,
    surface_frame,
)
from simple_stipple.ui.components.recent import KIND_VECTOR, RecentFilesButton, record_recent
from simple_stipple.ui.dialogs.export_preflight import export_preflight
from simple_stipple.ui.dialogs.files import (
    DxfImportPreviewDialog,
    VectorImportModeDialog,
    pick_open_file,
    pick_save_file,
)
from simple_stipple.ui.dialogs.fvi_dialog import FviExportDialog

LOGGER = logging.getLogger(__name__)

# ── Page default settings ────────────────────────────────────────────────
VECTOR_IMPORT_EXTENSIONS = (".dxf", ".fvi", ".svg")


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setProperty("role", "toolbar-sep")
    return sep


class DraftPage(BasePage):
    """Canvas-first drafting page optimized for interaction speed."""

    DEFAULT_LAYER = "Layer 1"

    sendSelectedToPatternRequested = Signal(object)
    openPageRequested = Signal(str)
    customTileRequested = Signal(object)

    _MODEL_STATE_FIELDS = {
        "_last_out_path": "last_output_path",
        "_last_in_path": "last_input_path",
        "_import_note": "import_note",
    }

    def __getattr__(self, name: str) -> Any:
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            return getattr(model, field)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            setattr(model, field, value)
            return
        super().__setattr__(name, value)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._model = DraftModel()
        self._last_out_path: str | None = None
        self._last_in_path: str | None = None
        self._import_note: str = ""
        self._runtime: CanvasRuntime | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        canvas_host = self._build_canvas()

        _toolbar_panel = surface_frame("panel")
        _tp_layout = QVBoxLayout(_toolbar_panel)
        _tp_layout.setContentsMargins(8, 4, 8, 4)
        _tp_layout.setSpacing(4)
        _tp_layout.addWidget(self._build_toolbar())
        self._build_grid()
        root.addWidget(_toolbar_panel)
        root.addWidget(canvas_host, stretch=1)

        self._canvas_status = CanvasStatusStrip()
        self._canvas_status.set_zoom_callback(self._on_zoom_preset)
        self._canvas_status.bind_canvas(self._canvas)
        self._canvas_status.contextActionRequested.connect(self._on_context_action)
        root.addWidget(self._canvas_status)
        self.setAcceptDrops(True)

        self._refresh_status()

    def _rt(self) -> CanvasRuntime:
        assert self._runtime is not None
        return self._runtime

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        open_btn = QToolButton()
        open_btn.setText("Import…")
        open_btn.setToolTip(
            "Choose a DXF, FVI, or SVG file. If the drawing has content, "
            "choose whether to replace it or add the imported geometry."
        )
        open_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        open_btn.clicked.connect(self._browse_vector)
        open_menu = QMenu(open_btn)
        open_menu.addAction("Import vector into drawing (add)…", self._browse_vector_add)
        open_btn.setMenu(open_menu)

        self._recent_btn = RecentFilesButton(
            self._settings,
            KIND_VECTOR,
            empty_message="No recent vector files.",
        )
        self._recent_btn.setToolTip("Pick from recently imported DXF, FVI, or SVG files")
        self._recent_btn.fileSelected.connect(self._load_vector)

        self._explode_btn = QPushButton("Explode")
        self._explode_btn.setToolTip("Explode selected shapes into segments")
        self._explode_btn.setEnabled(False)
        self._explode_btn.clicked.connect(self._explode_selected)

        self._merge_btn = QPushButton("Merge")
        self._merge_btn.setToolTip("Merge selected segments into connected objects (select 2+)")
        self._merge_btn.setEnabled(False)
        self._merge_btn.clicked.connect(self._merge_selected)

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
            show_fit=False,
            extra_widgets=[
                _toolbar_sep(),
                self._explode_btn,
                self._merge_btn,
                _toolbar_sep(),
                open_btn,
                self._recent_btn,
            ],
        )
        return self._toolbar_module

    def _explode_selected(self) -> None:
        count = self._canvas.explode_selected_to_segments()
        if count:
            self._refresh_status()

    def _merge_selected(self) -> None:
        count = self._canvas.merge_selected_segments_to_objects()
        if count:
            self._refresh_status()

    def _build_grid(self) -> QWidget:
        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_status,
            compact=True,
        )
        self._precision_bar = self._grid_module
        self._toolbar_module.add_context_widget(self._grid_module)
        return self._grid_module

    # ── Canvas ────────────────────────────────────────────────────────────

    def _build_canvas(self) -> QWidget:
        w = surface_frame("canvas")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_edit,
            on_send_selected_to_pattern=self._on_send_selected_to_pattern,
            on_use_selected_as_custom_tile=self.customTileRequested.emit,
            on_ghost_click=self._on_ghost_poly_click,
            draft_profile=True,
        )
        self._canvas.set_context_menu_profile("draft")
        self._canvas.set_context_menu_profiles(self._settings.get("context_menu_profiles", {}))
        self._canvas.set_selection_follows_geometry(True)
        self._canvas.set_empty_message("Start a drawing\nImport a vector, draw, or trace an image")
        self._canvas.set_empty_actions(
            [
                ("Import vector…", self._browse_vector),
                ("Draw one", lambda: self._canvas.set_mode("draw")),
                ("Trace an image", lambda: self.openPageRequested.emit("trace")),
            ]
        )
        self._canvas.quickShapeChanged.connect(self._on_quick_shape_changed)
        self._canvas.quickShapeEnabledChanged.connect(self._on_quick_shape_enabled_changed)
        self._canvas.viewChanged.connect(self._on_canvas_view_changed)
        self._runtime = CanvasRuntime(
            canvas=self._canvas,
            default_layer=self.DEFAULT_LAYER,
        )

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._props_panel = CanvasPropertiesPanel(self._canvas)
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        props_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        props_scroll.setWidget(self._props_panel)

        self._layer_module = CanvasLayerTreeModule(
            canvas=self._canvas,
            title="Layers",
            editable=True,
            get_active_layer_name=self._current_layer_name,
            build_layer_rows=self._build_layer_tree_rows,
            on_selection_requested=self._on_tree_selection_requested,
            on_fit_requested=self._fit_selection,
            on_visibility_changed=self._refresh_status,
            visibility_adapter=self._runtime,
        )
        self._layers_tree = self._layer_module.tree
        self._layer_sidebar = self._layer_module.controller

        self._layers_tree.layerActivated.connect(self._on_layer_activated)
        self._layers_tree.layerAdded.connect(self._on_layer_added)
        self._layers_tree.layerRenamed.connect(self._on_layer_renamed)
        self._layers_tree.layerDeleted.connect(self._on_layer_deleted)
        self._layers_tree.layersDeleteRequested.connect(self._on_layers_deleted)
        self._layers_tree.layersConsolidateRequested.connect(self._on_layers_consolidate_requested)
        self._layers_tree.layerMoved.connect(self._on_layer_moved)
        self._layers_tree.shapeMoveRequested.connect(self._on_shape_move_requested)
        self._layers_tree.shapesMoveRequested.connect(self._on_shapes_move_requested)
        self._layers_tree.moveSelectedRequested.connect(self._on_move_selected_to_layer)
        self._layers_tree.shapeRenamed.connect(self._on_shape_renamed)
        self._layers_tree.shapesDeleteRequested.connect(self._on_shapes_delete_requested)
        self._layers_tree.layerColorChangeRequested.connect(self._on_layer_color_change_requested)
        inspector_splitter = QSplitter(Qt.Orientation.Vertical)
        inspector_splitter.setChildrenCollapsible(False)
        inspector_splitter.addWidget(props_scroll)
        inspector_splitter.addWidget(self._layer_module)
        # Layers are the durable document navigator; start with enough room
        # to manage them instead of letting a mostly-empty Properties panel
        # monopolize the inspector.
        inspector_splitter.setStretchFactor(0, 1)
        inspector_splitter.setStretchFactor(1, 2)
        inspector_splitter.setSizes([220, 440])
        side_layout.addWidget(inspector_splitter, stretch=1)
        self._inspector_splitter = inspector_splitter
        side_layout.addWidget(self._build_export_controls())

        splitter = content_splitter(self._canvas, side_panel, sizes=(860, 280))
        splitter.set_responsive_secondary(1, "Inspector")
        self._content_splitter = splitter
        layout.addWidget(splitter, stretch=1)
        return w

    def _build_export_controls(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._export_btn = QPushButton("Export Drawing DXF…")
        self._export_btn.setProperty("role", "primary")
        self._export_btn.setToolTip(
            "Export as DXF; grouped shapes share a layer so a laser runs each group as one job"
        )
        self._export_btn.clicked.connect(self._export)
        row.addWidget(self._export_btn, stretch=1)
        overflow = QToolButton()
        overflow.setText("Format")
        overflow.setProperty("role", "overflow")
        overflow.setMinimumWidth(72)
        overflow.setToolTip("Choose an export format")
        overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(overflow)
        menu.addAction("Export StarFX FVI…", self._export_fvi)
        menu.addAction("Export SVG (single layer)…", self._export_svg)
        overflow.setMenu(menu)
        row.addWidget(overflow)
        self._export_overflow_btn = overflow
        return container

    # ── Mode / callbacks ──────────────────────────────────────────────────

    def _on_toolbar_mode(self, mode: str) -> None:
        self._toolbar_module.set_active_mode(mode)
        self._canvas.set_mode(mode.lower())
        self._refresh_status()

    def _on_canvas_mode_change(self, mode: str) -> None:
        if hasattr(self, "_toolbar_module"):
            self._toolbar_module.sync_from_canvas()
        self._refresh_status()

    def _on_canvas_view_changed(self) -> None:
        # Zoom/pan fire rapidly; update just the zoom readout rather than
        # rebuilding the whole status snapshot (which also rebuilds the tree).
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_zoom(
                self._canvas.get_zoom_percent(),
                self._canvas.get_cursor_world_pos(),
                unit=str(getattr(self._canvas, "_unit_system", "mm")),
            )

    def _on_quick_shape_changed(self, mode: str) -> None:
        self._refresh_status()

    def _on_quick_shape_enabled_changed(self, enabled: bool) -> None:
        self._refresh_status()

    def _on_sel_change(self, count: int) -> None:
        if hasattr(self, "_toolbar_module"):
            self._toolbar_module.set_selection_count(count)
        # Update only the selection count in the status strip — do NOT call
        # _refresh_status() here because that rebuilds the layer tree and
        # immediately erases the visual selection the user just made.
        # But DO update button states so Explode/Merge/Export reflect the
        # current selection without triggering a tree rebuild.
        self._update_action_buttons()
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _current_layer_name(self) -> str:
        return self._rt().current_layer_name()

    def _on_tree_selection_requested(self, indices: list[int]) -> None:
        self._rt().on_tree_selection_requested(indices)
        # Do NOT call _refresh_status() — that rebuilds the tree and clears
        # the selection highlight the user just created.

    def _switch_active_layer(self, layer: str, *, fit: bool = False) -> None:
        if self._rt().switch_active_layer(layer, fit=fit):
            self._refresh_status()
            self._emit_state_changed()

    def _add_layer_and_activate(self, layer: str) -> None:
        self._rt().add_layer_and_activate(layer)
        self._refresh_status()
        self._emit_state_changed()

    def _offer_shape_detection(self) -> None:
        candidates = detected_entities(self._canvas._entities)
        if not candidates:
            return
        dialog = ShapeDetectionDialog([shape for _, shape in candidates], self)

        def convert() -> None:
            chosen = set(dialog.selected_indices())
            selected_by_id = {
                self._canvas._entities[candidates[candidate_index][0]].id: shape
                for candidate_index, (_, shape) in enumerate(candidates)
                if candidate_index in chosen
            }
            if not selected_by_id:
                return

            def mutate(document) -> None:
                document.entities = [
                    convert_to_parametric(entity, selected_by_id[entity.id])
                    if entity.id in selected_by_id
                    else entity
                    for entity in document.entities
                ]

            self._canvas._canvas_service.update_document(mutate)
            self._canvas._sync_shape_storage_from_entities()
            self._canvas._redraw()
            self._canvas._notify()
            self._canvas._show_flash(f"Converted {len(selected_by_id)} parametric shape(s)", 1400)

        dialog.accepted.connect(convert)
        dialog.finished.connect(lambda _result: setattr(self, "_shape_detection_dialog", None))
        self._shape_detection_dialog = dialog
        dialog.open()

    def _on_layer_activated(self, layer: str) -> None:
        self._switch_active_layer(layer, fit=False)

    def _on_shape_move_requested(
        self, source_layer: str, shape_key: object, target_layer: str
    ) -> None:
        if self._rt().shape_move_requested(source_layer, shape_key, target_layer):
            self._refresh_status()
            self._emit_state_changed()

    def _on_shape_renamed(self, layer_name: str, shape_key: object, new_label: str) -> None:
        self._rt().rename_shape(layer_name, shape_key, new_label)
        self._refresh_status()

    def _on_shapes_move_requested(
        self, source_layer: str, shape_keys: list, target_layer: str
    ) -> None:
        if self._rt().shapes_move_requested(source_layer, shape_keys, target_layer):
            from simple_stipple.canvas.layers.logic import flatten_shape_keys

            entity_ids = flatten_shape_keys(shape_keys)
            count = len(entity_ids)
            self._canvas._show_flash(
                f"Moved {count} shape{'s' if count != 1 else ''} to {target_layer}",
                1200,
            )
            self._refresh_status()
            self._emit_state_changed()

    def _on_layer_added(self, layer: str) -> None:
        self._add_layer_and_activate(layer)

    def _on_layer_renamed(self, old_name: str, new_name: str) -> None:
        self._rt().layer_renamed(old_name, new_name)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_color_change_requested(self, layer: str, color: str | None) -> None:
        self._canvas.set_layer_color(layer, color)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_deleted(self, layer: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Layer",
            f"Delete layer '{layer}' and all of its contents?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._rt().layer_deleted(layer)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layers_deleted(self, layers: list[str]) -> None:
        """Batch layer delete (multi-selected layer rows) — one confirmation
        for the whole set instead of one dialog per layer."""
        names = [str(n) for n in layers if n and n != "geometry"]
        if not names:
            return
        if len(names) == 1:
            self._on_layer_deleted(names[0])
            return
        reply = QMessageBox.question(
            self,
            "Delete Layers",
            "Delete {} layers and all of their contents?\n\n{}".format(
                len(names), ", ".join(names)
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        rt = self._rt()
        for name in names:
            rt.layer_deleted(name)
        self._refresh_status()
        self._emit_state_changed()

    def _on_layers_consolidate_requested(self, source_layers: list[str], target_layer: str) -> None:
        """Move every shape from the given source layers onto target_layer
        and remove the (now-empty) source layers, in one undo step."""
        sources = [str(n) for n in source_layers if n and n != target_layer]
        if not sources:
            return
        reply = QMessageBox.question(
            self,
            "Consolidate Layers",
            "Move all shapes from {} layer(s) into '{}' and remove the empty layers?\n\n{}".format(
                len(sources), target_layer, ", ".join(sources)
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        moved = self._canvas.consolidate_layers(sources, target_layer)
        self._canvas._show_flash(
            f"Consolidated {len(sources)} layer(s) into '{target_layer}' "
            f"({moved} shape{'s' if moved != 1 else ''})",
            1400,
        )
        self._refresh_status()
        self._emit_state_changed()

    def _on_layer_moved(self, layer: str, new_index: int) -> None:
        self._rt().layer_moved(layer, new_index)
        self._refresh_status()
        self._emit_state_changed()

    def _on_move_selected_to_layer(self, target_layer: str) -> None:
        if not self._rt().move_selected_to_layer(target_layer):
            self._canvas._show_flash("Select shape(s) first", 1000)
            return
        self._canvas._show_flash(f"Moved selection to {target_layer}", 1200)
        self._refresh_status()
        self._emit_state_changed()

    def _on_zoom_preset(self, value) -> None:
        if value == "fit":
            self._canvas.fit()
        else:
            self._canvas.set_zoom_percent(float(value))
        self._refresh_status()

    def _fit_selection(self) -> None:
        if self._canvas.fit_selection():
            self._refresh_status()

    def _on_shapes_delete_requested(self, layer: str, keys: list) -> None:
        from simple_stipple.canvas.layers.logic import flatten_shape_keys

        entity_ids = flatten_shape_keys(keys)
        if not entity_ids:
            return
        if self._canvas.delete_entities(entity_ids):
            self._refresh_status()
            self._emit_state_changed()

    def _on_canvas_edit(self) -> None:
        self._rt().on_canvas_edit()
        if hasattr(self, "_props_panel"):
            self._props_panel.refresh()
        self._refresh_status()
        self._emit_state_changed()

    def _on_ghost_poly_click(self, entity_id: str) -> None:
        """Clicking a shape from another layer activates that layer and selects the shape."""
        canvas = self._canvas
        layer_name = canvas._entities_by_id[entity_id].layer
        if layer_name:
            self._switch_active_layer(layer_name, fit=False)
        canvas.set_selection([entity_id])
        self._refresh_status()

    def _on_send_selected_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self.sendSelectedToPatternRequested.emit(polys)

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self) -> None:
        active_name = self._rt().current_layer_name()
        export_plan = build_dxf_export_plan(
            self._canvas.get_export_dxf_state(),
            self._canvas._dimensions,
            active_layer_name=active_name,
            layer_names=self._canvas.layer_names(),
        )
        if not export_plan.records:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "The canvas is empty — draw or drag-create shapes first.",
            )
            return
        proceed, _report = export_preflight(
            self,
            [list(record["polyline"]) for record in export_plan.records],
            action="Export",
            allow_open_paths=True,
        )
        if not proceed:
            self._canvas.set_geometry_health_visible(True, announce=True)
            return

        out_path = pick_save_file(
            self,
            self._settings,
            "draft_output",
            "Export DXF",
            "draft.dxf",
            "DXF files (*.dxf);;All files (*)",
            fallback_dir=self._settings.get("draft_output_dir", ""),
        )
        if not out_path:
            return

        try:
            # The first layer becomes the main entity stream so it can carry
            # kind/meta info (lines, circles, ellipses, arcs); remaining
            # layers are emitted as additional DXF layers. Emit onto a named
            # layer (instead of the AutoCAD default "0") so downstream CAM
            # tools assign it a real color and can fill it.
            DxfService.write_polylines_dxf(
                [list(record["polyline"]) for record in export_plan.first_layer_records],
                out_path,
                close=False,
                pattern_layer=export_plan.first_layer_name,
                entity_kinds=[
                    str(record.get("kind", "polyline"))
                    for record in export_plan.first_layer_records
                ],
                entity_meta=[record.get("meta") for record in export_plan.first_layer_records],
                extra_layer_records=export_plan.extra_layer_records,
            )
            self._last_out_path = out_path
            self._canvas._show_flash(f"Exported: {Path(out_path).name}", 1200)
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "Export Failed", exc)

    # ── Status ────────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return

        summary = self._canvas.get_status_summary()
        topo = self._canvas.get_topology_summary()
        n = self._canvas.poly_count
        mode = str(summary["mode"])

        if hasattr(self._canvas, "get_command_guidance"):
            readiness, tone = self._canvas.get_command_guidance()
        elif n:
            quick_mode = (
                f"Quick shape: {self._canvas.quick_shape_mode.title()}"
                if self._canvas.quick_shape_enabled
                else "Quick shape: Off"
            )
            readiness = f"{quick_mode} · {topo['closed']} closed/{topo['open']} open"
            tone = "accent"
        else:
            readiness = (
                "Drag on canvas to create shape"
                if self._canvas.quick_shape_enabled
                else "Quick shape disabled"
            )
            tone = "warn"

        zoom = self._canvas.get_zoom_percent()
        cursor = self._canvas.get_cursor_world_pos()

        self._canvas_status.set_snapshot(
            mode=mode,
            selected_count=self._canvas.sel_count,
            object_count=n,
            precision_text=str(summary["precision"]),
            topology_text=str(summary.get("topology", "")),
            readiness_text=readiness,
            readiness_tone=tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        if self._import_note:
            self._canvas_status.set_readiness(
                "Import notes",
                "warn",
                self._import_note,
            )
        self._canvas_status.set_context_actions(self._canvas.get_context_actions())

        if hasattr(self, "_precision_bar"):
            self._precision_bar.refresh()

        # The mode toolbar (Select / Edit / Draw) and precision controls stay
        # visible at all times so the editing surface never appears or vanishes
        # under the user.

        # Keep selection-dependent actions disabled rather than letting a
        # click on an unmet precondition (nothing selected, one shape when
        # two are needed) silently do nothing — the previous no-op gave no
        # feedback at all that anything was wrong.
        self._update_action_buttons()

        if hasattr(self, "_layers_tree"):
            self._layer_sidebar.refresh_tree()

    def _update_action_buttons(self) -> None:
        """Update Explode, Merge, and Export button enabled states.

        Separated from ``_refresh_status`` so selection changes can update
        button states without rebuilding the layer tree (which would erase
        the visual selection highlight the user just made).
        """
        selected = self._canvas.sel_count
        n = self._canvas.poly_count
        if hasattr(self, "_explode_btn"):
            self._explode_btn.setEnabled(selected > 0)
        if hasattr(self, "_merge_btn"):
            self._merge_btn.setEnabled(selected > 1)
        if hasattr(self, "_export_btn"):
            self._export_btn.setEnabled(n > 0 or bool(self._canvas._dimensions))

    def _on_context_action(self, action: str) -> None:
        """Execute a canvas-owned action then refresh only the affected UI."""
        if self._canvas.trigger_context_action(action):
            self._refresh_status()

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[str]]],
    ) -> list[dict[str, Any]]:
        return self._rt().build_layer_tree_rows(layer_view_state)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(VECTOR_IMPORT_EXTENSIONS):
                    event.acceptProposedAction()
                    return
        # Qt withholds dropEvent entirely once dragEnterEvent rejects, so
        # this is the only chance to say why — otherwise the OS "no drop"
        # cursor is the only feedback the user gets.
        if event.mimeData().hasUrls():
            self._canvas._show_flash("Draft accepts DXF, FVI, or SVG files", 1400)
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(VECTOR_IMPORT_EXTENSIONS):
                self._load_vector(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _browse_vector(self) -> None:
        self._pick_vector(append=False)

    def _browse_vector_add(self) -> None:
        self._pick_vector(append=True)

    def _pick_vector(self, *, append: bool) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "draft_input_vector",
            "Import Vector",
            "Vector files (*.dxf *.DXF *.fvi *.FVI *.svg *.SVG);;"
            "DXF files (*.dxf *.DXF);;StarFX FVI programs (*.fvi *.FVI);;"
            "SVG files (*.svg *.SVG);;All files (*)",
            fallback_dir=self._settings.get("draft_input_dxf_dir", ""),
        )
        if path:
            self._load_vector(path, append=append)

    def _load_vector(self, path: str, *, append: bool = False) -> None:
        self._import_note = ""
        suffix = Path(path).suffix.lower()
        # Every format must make the same destructive-state decision. DXF
        # includes it in its layer review; FVI/SVG use the compact equivalent.
        if suffix in (".fvi", ".svg") and not append and self._canvas._entities:
            choice = self._review_vector_import(path, suffix)
            if choice is None:
                return
            append = choice
        if suffix == ".dxf":
            if append:
                self._import_dxf_add(path)
            else:
                self._load_dxf(path)
        elif suffix == ".fvi":
            self._load_fvi(path, append=append)
        elif suffix == ".svg":
            self._load_svg(path, append=append)
        else:
            QMessageBox.warning(
                self,
                "Unsupported Vector File",
                "Choose a DXF, FVI, or SVG vector file.",
            )

    def _review_vector_import(self, path: str, suffix: str) -> bool | None:
        """Return the selected Add/Replace mode, or ``None`` when cancelled."""
        format_name = {".fvi": "StarFX FVI", ".svg": "SVG"}.get(suffix, "vector")
        dialog = VectorImportModeDialog(
            path,
            format_name=format_name,
            has_existing_geometry=bool(self._canvas._entities),
            parent=self,
        )
        if not dialog.exec():
            return None
        return dialog.append_mode()

    def _import_dxf_add(self, path: str) -> None:
        """Add a DXF's shapes to the existing drawing (instead of replacing)."""
        try:
            by_layer, report = DxfService.load_dxf_polylines_by_layer_with_report(path)
            if not by_layer:
                QMessageBox.information(self, "Import DXF", "No shapes found in that DXF.")
                return
            decision = self._review_dxf_import(path, by_layer, report, default_append=True)
            if decision is not None:
                selected, append = decision
                self._apply_dxf_import(path, selected, append=append)
                self._set_import_note(summarize_dxf_import_report(report) or "")
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "Import DXF Failed", exc)

    def _export_svg(self) -> None:
        records = self._canvas.get_export_dxf_state()
        if not records:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "The canvas is empty — draw or drag-create shapes first.",
            )
            return
        out_path = pick_save_file(
            self,
            self._settings,
            "draft_output",
            "Export SVG",
            "draft.svg",
            "SVG files (*.svg);;All files (*)",
            fallback_dir=self._settings.get("draft_output_dir", ""),
        )
        if not out_path:
            return
        try:
            stats = DxfService.write_polylines_svg([list(r["polyline"]) for r in records], out_path)
            self._last_out_path = out_path
            self._canvas._show_flash(
                f"Exported SVG: {Path(out_path).name} ({stats['polylines']} paths)",
                1200,
            )
        except (OSError, ValueError) as exc:
            show_error(self, "Export Failed", exc)

    def _export_fvi(self) -> None:
        records = self._canvas.get_export_dxf_state()
        if not records:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "The canvas is empty — draw or import geometry first.",
            )
            return
        dialog = FviExportDialog(records, self)
        if not dialog.exec():
            return
        out_path = pick_save_file(
            self,
            self._settings,
            "draft_output_fvi",
            "Export StarFX FVI",
            "draft.fvi",
            "StarFX FVI programs (*.fvi *.FVI);;All files (*)",
            fallback_dir=self._settings.get("draft_output_dir", ""),
        )
        if not out_path:
            return
        try:
            report = DxfService.write_fvi(records, out_path, dialog.options())
            self._last_out_path = out_path
            message = (
                f"Exported FVI: {Path(out_path).name} "
                f"({report.path_count} paths, {report.draw_arc_count} native arcs)"
            )
            self._canvas._show_flash(message, 1600)
            if report.warnings:
                QMessageBox.information(
                    self,
                    "FVI Export Notes",
                    "\n".join(report.warnings)
                    + "\n\nUse StarFX's red trace/profile preview before enabling the laser.",
                )
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "FVI Export Failed", exc)

    def _load_dxf(self, path: str) -> None:
        try:
            by_layer, report = DxfService.load_dxf_polylines_by_layer_with_report(path)
            if not by_layer:
                QMessageBox.information(self, "Open DXF", "No usable shapes found in that DXF.")
                return
            decision = self._review_dxf_import(path, by_layer, report, default_append=False)
            if decision is not None:
                selected, append = decision
                self._apply_dxf_import(path, selected, append=append)
                self._set_import_note(summarize_dxf_import_report(report) or "")
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "Open DXF Failed", exc)

    def _load_fvi(self, path: str, *, append: bool = False) -> None:
        try:
            document = DxfService.read_fvi(path)
            polys = [list(poly) for poly in document.paths]
            if not polys:
                details = DxfService.summarize_fvi_import(document.report)
                message = "No supported drawable geometry was found in that FVI program."
                if details:
                    message += f"\n\n{details}"
                QMessageBox.information(self, "Import FVI", message)
                return
            if append and self._canvas._entities:
                self._rt().add_polys(polys, fit=True)
                verb = "Added"
            else:
                self._rt().load_polys(polys, fit=True)
                verb = "Loaded"
            self._last_in_path = path
            record_recent(self._settings, KIND_VECTOR, path)
            self._canvas._show_flash(f"{verb} FVI: {Path(path).name} ({len(polys)} paths)", 1400)
            details = DxfService.summarize_fvi_import(document.report)
            if details:
                self._set_import_note(details)
            self._refresh_status()
            self._emit_state_changed()
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "Import FVI Failed", exc)

    def _load_svg(self, path: str, *, append: bool = False) -> None:
        """Import supported SVG primitives through the audited DXF geometry boundary."""
        try:
            with tempfile.TemporaryDirectory(prefix="simple-stipple-svg-") as directory:
                converted = Path(directory) / "import.dxf"
                stats = DxfService.svg_to_dxf(path, converted)
                by_layer, _report = DxfService.load_dxf_polylines_by_layer_with_report(
                    str(converted)
                )
            if not by_layer:
                QMessageBox.information(
                    self,
                    "Import SVG",
                    "No supported vector geometry was found in that SVG.",
                )
                return
            self._apply_dxf_import(path, by_layer, append=append, source_kind="SVG")
            unsupported = int(stats.get("unsupported_paths", 0))
            unsupported_features = tuple(stats.get("unsupported_features", ()))
            # Draft is linework only. An SVG carrying an engraving image is
            # still a valid import here, but saying nothing would look like
            # the image had been lost.
            embedded_images = self._show_imported_svg_image(path)
            if embedded_images or unsupported or unsupported_features:
                notes: list[str] = []
                if embedded_images:
                    notes.append(
                        f"Showing {embedded_images} engraving image"
                        f"{'s' if embedded_images != 1 else ''} from this SVG. Click it "
                        "for handles, or press Delete to remove it. Open the file on the "
                        "Pattern page to change its engraving settings or export it."
                    )
                if unsupported:
                    notes.append(
                        f"Skipped {unsupported} path(s) containing Bézier, arc, or other "
                        "unsupported path commands."
                    )
                if unsupported_features:
                    notes.append("Unsupported SVG features: " + ", ".join(unsupported_features))
                details = "\n".join(notes)
                self._set_import_note(details)
        except (OSError, ValueError, RuntimeError) as exc:
            show_error(self, "Import SVG Failed", exc)

    def _review_dxf_import(self, path, by_layer, report, *, default_append: bool):
        dialog = DxfImportPreviewDialog(
            path,
            by_layer,
            report,
            has_existing_geometry=bool(self._canvas._entities),
            default_append=default_append,
            parent=self,
        )
        if not dialog.exec():
            return None
        selected_names = dialog.selected_layers()
        selected = {name: by_layer[name] for name in selected_names if name in by_layer}
        if not selected:
            QMessageBox.information(self, "Import DXF", "Select at least one layer to import.")
            return None
        return selected, dialog.append_mode()

    def _set_import_note(self, detail: str) -> None:
        """Persist conversion-loss information in the visible status surface."""
        self._import_note = detail.strip()
        if self._import_note:
            self._canvas._show_flash("Imported with notes — see status details", 2400)
        self._refresh_status()

    def _apply_dxf_import(
        self,
        path: str,
        by_layer,
        *,
        append: bool,
        source_kind: str = "DXF",
    ) -> None:
        flat = [poly for polys in by_layer.values() for poly in polys]
        if append and self._canvas._entities:
            canvas = self._canvas
            created_ids: list[str] = []

            def mutate(document) -> None:
                for layer, polys in by_layer.items():
                    if layer not in document.layer_order:
                        document.layer_order.append(layer)
                    for poly in polys:
                        entity = EntityRecord(points=list(poly), layer=layer)
                        document.append(entity)
                        created_ids.append(entity.id)
                document.select_ids(created_ids)

            canvas._canvas_service.update_document(mutate)
            canvas._sync_shape_storage_from_entities()
            canvas._redraw()
            canvas._notify()
            canvas._fire_poly_change()
            canvas._show_flash(f"Added {len(created_ids)} shapes from {Path(path).name}", 1200)
        else:
            self._rt().load_polys_by_layer(by_layer, fit=bool(flat))
            self._canvas._show_flash(f"Loaded {source_kind}: {Path(path).name}", 1200)
        self._last_in_path = path
        record_recent(self._settings, KIND_VECTOR, path)
        self._refresh_status()
        self._emit_state_changed()
        if source_kind == "DXF":
            self._offer_shape_detection()

    def load_outline_polys(
        self,
        polys: list,
        *,
        source_label: str = "Pattern selection",
    ) -> None:
        """Add polylines sent from another tab into Draft, alongside
        whatever's already there — this is a "send selection here" action,
        not a fresh load, so it must not discard the existing draft."""
        if not polys:
            return
        incoming: list[list[tuple[float, float]]] = []
        layers: list[str | None] = []
        for item in polys:
            if isinstance(item, dict):
                points = item.get("points", [])
                layer = item.get("layer")
            else:
                points, layer = item, None
            try:
                poly = [(float(x), float(y)) for x, y in points]
            except (TypeError, ValueError):
                continue
            if len(poly) >= 2:
                incoming.append(poly)
                layers.append(str(layer) if layer else None)
        if not incoming:
            return
        before = set(self._canvas.get_entity_ids())
        self._rt().add_polys(incoming, fit=True)
        added = [eid for eid in self._canvas.get_entity_ids() if eid not in before]
        for entity_id, layer in zip(added, layers, strict=True):
            if layer:
                self._canvas.move_indices_to_layer([entity_id], layer)
        self._canvas._show_flash(f"Added {len(incoming)} from {source_label}", 1200)
        self._refresh_status()
        self._emit_state_changed()

    # ── Workspace persistence ─────────────────────────────────────────────

    def get_workspace_state(self) -> dict:
        return get_draft_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_draft_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_draft_workspace_state(self)

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, state: dict | None) -> None:
        pass


# Preserve DraftPage's existing canvas callback and import patch surfaces;
# implementation is owned by the SVG imported-artwork workflow module.
DraftPage._show_imported_svg_image = _show_imported_svg_image
DraftPage._on_backdrop_transform = _on_backdrop_transform
DraftPage._on_backdrop_key = _on_backdrop_key
