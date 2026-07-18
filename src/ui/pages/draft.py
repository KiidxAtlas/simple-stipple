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
from typing import Any, cast

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.cad.recognition import convert_to_parametric, recognized_entities
from src.backend.dxf.fvi import read_fvi, summarize_fvi_import, write_fvi
from src.backend.dxf.io import (
    load_dxf_polylines_by_layer_with_report,
    write_polylines_dxf,
)
from src.backend.dxf.svg_dxf import svg_to_dxf, write_polylines_svg
from src.backend.model.document import DraftTabState, EntityRecord
from src.ui.canvas.canvas_runtime import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasRuntime,
    CanvasToolbarModule,
)
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components import RecentFilesButton, content_splitter, surface_frame
from src.ui.pages.base import BasePage
from src.ui.util import KIND_VECTOR, pick_open_file, pick_save_file, record_recent
from src.ui.widgets.canvas.properties_panel import CanvasPropertiesPanel
from src.ui.widgets.canvas.status_strip import CanvasStatusStrip
from src.ui.widgets.dialogs.fvi_dialog import FviExportDialog
from src.ui.widgets.dialogs.import_dialog import DxfImportPreviewDialog
from src.ui.widgets.shape_recognition_dialog import ShapeRecognitionDialog

LOGGER = logging.getLogger(__name__)

# ── Page default settings ────────────────────────────────────────────────
DEFAULT_QUICK_SHAPE_MODE = "rectangle"
VECTOR_IMPORT_EXTENSIONS = (".dxf", ".fvi", ".svg")


def _toolbar_sep() -> QLabel:
    sep = QLabel("│")
    sep.setProperty("role", "toolbar-sep")
    return sep


class DraftPage(BasePage):
    """Canvas-first drafting page optimized for interaction speed."""

    DEFAULT_LAYER = "Layer 1"

    sendSelectedToPatternRequested = Signal(object)
    customTileRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._last_out_path: str | None = None
        self._last_in_path: str | None = None
        self._runtime: CanvasRuntime | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        canvas_host = self._build_canvas()

        _toolbar_panel = surface_frame("panel")
        _tp_layout = QVBoxLayout(_toolbar_panel)
        _tp_layout.setContentsMargins(8, 4, 8, 4)
        _tp_layout.setSpacing(2)
        _tp_layout.addWidget(self._build_toolbar())
        _tp_layout.addWidget(self._build_grid())
        root.addWidget(_toolbar_panel)
        root.addWidget(canvas_host, stretch=1)

        self._canvas_status = CanvasStatusStrip()
        self._canvas_status.set_zoom_callback(self._on_zoom_preset)
        root.addWidget(self._canvas_status)
        self.setAcceptDrops(True)

        self._refresh_status()

    def _rt(self) -> CanvasRuntime:
        assert self._runtime is not None
        return self._runtime

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        open_btn = QToolButton()
        open_btn.setText("Import Vector")
        open_btn.setMinimumHeight(30)
        open_btn.setToolTip(
            "Import a DXF, FVI, or SVG file and replace the drawing.\n"
            "Use the arrow to add it to the current drawing instead."
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
        self._recent_btn.setMinimumHeight(30)
        self._recent_btn.setToolTip("Pick from recently imported DXF, FVI, or SVG files")
        self._recent_btn.fileSelected.connect(self._load_vector)

        explode_btn = QPushButton("Explode")
        explode_btn.setMinimumHeight(30)
        explode_btn.setToolTip("Explode selected shapes into segments")
        explode_btn.clicked.connect(self._explode_selected)

        merge_btn = QPushButton("Merge")
        merge_btn.setMinimumHeight(30)
        merge_btn.setToolTip("Merge selected segments into connected objects")
        merge_btn.clicked.connect(self._merge_selected)

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
            show_fit=False,
            extra_widgets=[
                _toolbar_sep(),
                explode_btn,
                merge_btn,
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
        )
        self._precision_bar = self._grid_module
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
        self._canvas.set_selection_follows_geometry(True)
        self._canvas.set_empty_message(
            "Start a drawing\nUse Import Vector above, drop a file here, or choose Draw"
        )
        self._canvas.quickShapeChanged.connect(self._on_quick_shape_changed)
        self._canvas.quickShapeEnabledChanged.connect(self._on_quick_shape_enabled_changed)
        self._runtime = CanvasRuntime(
            canvas=self._canvas,
            default_layer=self.DEFAULT_LAYER,
        )

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._props_panel = CanvasPropertiesPanel(self._canvas)
        side_layout.addWidget(self._props_panel)

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
        side_layout.addWidget(self._layer_module, stretch=1)
        side_layout.addWidget(self._build_export_controls())

        splitter = content_splitter(self._canvas, side_panel, sizes=(860, 280))
        layout.addWidget(splitter, stretch=1)
        return w

    def _build_export_controls(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._export_btn = QPushButton("Export Drawing DXF")
        self._export_btn.setMinimumHeight(38)
        self._export_btn.setProperty("role", "primary")
        self._export_btn.setToolTip(
            "Export as DXF; grouped shapes share a layer so a laser runs each group as one job"
        )
        self._export_btn.clicked.connect(self._export)
        row.addWidget(self._export_btn, stretch=1)
        overflow = QToolButton()
        overflow.setText("⋯")
        overflow.setProperty("role", "overflow")
        overflow.setFixedSize(32, 38)
        overflow.setToolTip("More export formats")
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
            self._toolbar_module.set_active_mode(mode)
        self._refresh_status()

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
        if hasattr(self, "_canvas_status"):
            self._canvas_status.set_selection_count(count)

    def _current_layer_name(self) -> str:
        return self._rt().current_layer_name()

    def _on_tree_selection_requested(self, indices: list[int]) -> None:
        self._rt().on_tree_selection_requested(indices)
        # Do NOT call _refresh_status() — that rebuilds the tree and clears
        # the selection highlight the user just created.

    def _reload_active_layer(self, *, fit: bool = False) -> None:
        self._rt().reload_active_layer(fit=fit)

    def _switch_active_layer(self, layer: str, *, fit: bool = False) -> None:
        if self._rt().switch_active_layer(layer, fit=fit):
            self._refresh_status()
            self._emit_state_changed()

    def _add_layer_and_activate(self, layer: str) -> None:
        self._rt().add_layer_and_activate(layer)
        self._refresh_status()
        self._emit_state_changed()

    def _offer_shape_recognition(self) -> None:
        candidates = recognized_entities(self._canvas._entities)
        if not candidates:
            return
        dialog = ShapeRecognitionDialog([shape for _, shape in candidates], self)
        recognized_by_id = {
            self._canvas._entities[index].id: shape for index, shape in candidates
        }

        def convert() -> None:
            def mutate(document) -> None:
                document.entities = [
                    convert_to_parametric(entity, recognized_by_id[entity.id])
                    if entity.id in recognized_by_id
                    else entity
                    for entity in document.entities
                ]

            self._canvas._canvas_service.update_document(mutate)
            self._canvas._sync_shape_storage_from_entities()
            self._canvas._redraw()
            self._canvas._notify()
            self._canvas._show_flash(f"Converted {len(candidates)} parametric shapes", 1400)

        dialog.accepted.connect(convert)
        dialog.finished.connect(lambda _result: setattr(self, "_shape_recognition_dialog", None))
        self._shape_recognition_dialog = dialog
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
            count = len([k for k in shape_keys if isinstance(k, int)])
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
        from src.ui.widgets.layer_tree.logic import flatten_shape_keys

        indices = flatten_shape_keys(keys)
        if not indices:
            return
        # Keys are entity indices, so rows from any layer can be deleted.
        if self._canvas.delete_indices(indices):
            self._refresh_status()
            self._emit_state_changed()

    def _on_canvas_edit(self) -> None:
        self._rt().on_canvas_edit()
        if hasattr(self, "_props_panel"):
            self._props_panel.refresh()
        self._refresh_status()
        self._emit_state_changed()

    def _on_ghost_poly_click(self, entity_idx: int) -> None:
        """Clicking a shape from another layer activates that layer and selects the shape."""
        canvas = self._canvas
        if not (0 <= entity_idx < len(canvas._entities)):
            return
        layer_name = canvas._entities[entity_idx].layer
        if layer_name:
            self._switch_active_layer(layer_name, fit=False)
        canvas.set_selection([entity_idx])
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
        records = self._canvas.get_export_dxf_state()
        active_name = self._rt().current_layer_name()
        for dimension in self._canvas._dimensions:
            p1 = tuple(dimension["p1"])
            p2 = tuple(dimension["p2"])
            records.append(
                {
                    "polyline": [p1, p2],
                    "kind": "dimension",
                    "meta": dict(dimension),
                    "layer": active_name,
                }
            )
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
            "Export DXF",
            "draft.dxf",
            "DXF files (*.dxf);;All files (*)",
            fallback_dir=self._settings.get("draft_output_dir", ""),
        )
        if not out_path:
            return

        try:
            # Group export records by document layer, preserving layer order.
            # Entities carry their layer, so no graph capture is needed.
            by_layer: dict[str, list[dict[str, Any]]] = {}
            for r in records:
                by_layer.setdefault(str(r.get("layer") or active_name), []).append(r)
            order = [n for n in self._canvas.layer_names() if n in by_layer]
            if not order:
                order = list(by_layer)

            # The first layer becomes the main entity stream so it can carry
            # kind/meta info (lines, circles, ellipses, arcs); remaining
            # layers are emitted as additional DXF layers. Emit onto a named
            # layer (instead of the AutoCAD default "0") so downstream CAM
            # tools assign it a real color and can fill it.
            first_name = order[0] if order else (active_name or "Layer")
            first = by_layer.get(first_name, [])
            extra_records = {name: by_layer[name] for name in order[1:]}
            write_polylines_dxf(
                [list(r["polyline"]) for r in first],
                out_path,
                close=True,
                pattern_layer=first_name,
                entity_kinds=[str(r.get("kind", "polyline")) for r in first],
                entity_meta=[r.get("meta") for r in first],
                extra_layer_records=extra_records or None,
            )
            self._last_out_path = out_path
            self._canvas._show_flash(f"Exported: {Path(out_path).name}", 1200)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

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

        if hasattr(self, "_precision_bar"):
            self._precision_bar.refresh()

        if hasattr(self, "_layers_tree"):
            self._layer_sidebar.refresh_tree()

    def _refresh_command_guidance(self) -> None:
        if hasattr(self, "_canvas_status") and hasattr(self._canvas, "get_command_guidance"):
            text, tone = self._canvas.get_command_guidance()
            self._canvas_status.set_readiness(text, tone)

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]],
    ) -> list[dict[str, Any]]:
        return self._rt().build_layer_tree_rows(layer_view_state)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(VECTOR_IMPORT_EXTENSIONS):
                    event.acceptProposedAction()
                    return
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
        suffix = Path(path).suffix.lower()
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

    def _import_dxf_add(self, path: str) -> None:
        """Add a DXF's shapes to the existing drawing (instead of replacing)."""
        try:
            by_layer, report = load_dxf_polylines_by_layer_with_report(path)
            if not by_layer:
                QMessageBox.information(self, "Import DXF", "No shapes found in that DXF.")
                return
            decision = self._review_dxf_import(path, by_layer, report, default_append=True)
            if decision is not None:
                selected, append = decision
                self._apply_dxf_import(path, selected, append=append)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Import DXF Failed", str(exc))

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
            stats = write_polylines_svg([list(r["polyline"]) for r in records], out_path)
            self._last_out_path = out_path
            self._canvas._show_flash(
                f"Exported SVG: {Path(out_path).name} ({stats['polylines']} paths)",
                1200,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

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
            report = write_fvi(records, out_path, dialog.options())
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
            QMessageBox.critical(self, "FVI Export Failed", str(exc))

    def _load_dxf(self, path: str) -> None:
        try:
            by_layer, report = load_dxf_polylines_by_layer_with_report(path)
            if not by_layer:
                QMessageBox.information(self, "Open DXF", "No usable shapes found in that DXF.")
                return
            decision = self._review_dxf_import(path, by_layer, report, default_append=False)
            if decision is not None:
                selected, append = decision
                self._apply_dxf_import(path, selected, append=append)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Open DXF Failed", str(exc))

    def _load_fvi(self, path: str, *, append: bool = False) -> None:
        try:
            document = read_fvi(path)
            polys = [list(poly) for poly in document.paths]
            if not polys:
                details = summarize_fvi_import(document.report)
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
            details = summarize_fvi_import(document.report)
            if details:
                self._canvas.setToolTip(f"FVI import notes:\n{details}")
                self._canvas._show_flash("Imported with notes — hover the canvas for details", 4000)
            self._refresh_status()
            self._emit_state_changed()
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Import FVI Failed", str(exc))

    def _load_svg(self, path: str, *, append: bool = False) -> None:
        """Import supported SVG primitives through the audited DXF geometry boundary."""
        try:
            with tempfile.TemporaryDirectory(prefix="simple-stipple-svg-") as directory:
                converted = Path(directory) / "import.dxf"
                stats = svg_to_dxf(path, converted)
                by_layer, _report = load_dxf_polylines_by_layer_with_report(str(converted))
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
            if unsupported or unsupported_features:
                notes: list[str] = []
                if unsupported:
                    notes.append(
                        f"Skipped {unsupported} path(s) containing Bézier, arc, or other "
                        "unsupported path commands."
                    )
                if unsupported_features:
                    notes.append("Unsupported SVG features: " + ", ".join(unsupported_features))
                details = "\n".join(notes)
                self._canvas.setToolTip(f"SVG import notes:\n{details}")
                self._canvas._show_flash("Imported with notes — hover the canvas for details", 4000)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Import SVG Failed", str(exc))

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
            self._offer_shape_recognition()

    def load_outline_polys(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Pattern selection",
    ) -> None:
        """Add polylines sent from another tab into Draft, alongside
        whatever's already there — this is a "send selection here" action,
        not a fresh load, so it must not discard the existing draft."""
        if not polys:
            return
        incoming = [[(x, y) for x, y in poly] for poly in polys]
        self._rt().add_polys(incoming, fit=True)
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


# ══════════════════════════════════════════════════════════════════════════
# Workspace state serialisation/restoration (merged from former session.py)
# ══════════════════════════════════════════════════════════════════════════
#
# Current format: a flat entity-record list (each record carries its layer,
# flags, and group) plus the ordered layer list and active layer.
#
# State management uses Pydantic models (``DraftTabState``) for schema
# validation at the load/save boundary. The ``get_*`` / ``apply_*``
# functions work with raw dicts (for compatibility with existing UI code)
# but validate and coerce those dicts through ``DraftTabState`` internally.


def _coerce_to_draft_state(state: dict | None) -> DraftTabState:
    """Coerce a raw dict (possibly from an old workspace file) into a
    ``DraftTabState``. Returns a minimal valid instance if the data is
    completely malformed."""
    if not isinstance(state, dict):
        return DraftTabState()
    try:
        return cast(DraftTabState, DraftTabState.from_dict(state))
    except (ValidationError, TypeError, ValueError) as exc:
        LOGGER.warning("Discarding invalid Draft workspace state: %s", exc)
        return DraftTabState()


def get_draft_workspace_state(page: Any) -> dict:
    canvas = page._canvas
    state_dict = {
        "entities": canvas.get_entity_records(),
        "layer_order": canvas.layer_names(),
        "active_layer": canvas.active_layer,
        "canvas_view": canvas.get_view_state(),
        "quick_shape_mode": canvas.quick_shape_mode,
        "quick_shape_enabled": canvas.quick_shape_enabled,
        "last_input_dxf": str(page._last_in_path or ""),
    }
    return state_dict


def apply_draft_workspace_state(page: Any, state: dict | None) -> None:
    page._suspend_state = True
    draft_state = _coerce_to_draft_state(state)

    rt = page._rt()
    canvas = page._canvas

    entities = draft_state.entities
    if isinstance(entities, list) and entities:
        canvas.set_entity_records(entities)
        order = [str(n) for n in draft_state.layer_order if str(n)]
        active = draft_state.active_layer
        if not order:
            order = [rt.default_layer]
        canvas.set_layer_model(order, str(active) if active else order[0])
        if draft_state.canvas_view:
            canvas.set_view_state(draft_state.canvas_view)
    else:
        rt.reset_empty()

    if canvas.poly_count == 0:
        canvas.fit()

    quick_shape_enabled = bool(draft_state.quick_shape_enabled)
    canvas.set_quick_shape_enabled(quick_shape_enabled)
    if quick_shape_enabled and draft_state.quick_shape_mode:
        canvas.set_quick_shape_mode(str(draft_state.quick_shape_mode), flash=False)
    page._last_in_path = str(draft_state.last_input_dxf or "") or None

    page._suspend_state = False
    page._refresh_status()


def clear_draft_workspace_state(page: Any) -> None:
    page._suspend_state = True
    page._rt().reset_empty()
    page._canvas.set_mode("select")
    page._canvas.set_quick_shape_mode(DEFAULT_QUICK_SHAPE_MODE, flash=False)
    page._canvas.set_quick_shape_enabled(False)
    page._last_in_path = None
    page._suspend_state = False
    page._refresh_status()
