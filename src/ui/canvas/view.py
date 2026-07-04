"""PolylineView — interactive pan/zoom canvas widget with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PIL import Image as PILImage
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMenu,
    QWidget,
)
from shapely.errors import GEOSException
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import linemerge, unary_union
from shapely.ops import split as shapely_split

from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.constants import DRAG_THRESH
from src.ui.canvas._constants import EDGE_HIT as _EDGE_HIT
from src.ui.canvas._constants import MIN_SCALE as _MIN_SCALE

_MAX_SCALE = 20000.0  # px per mm — deep zoom for tiny features
from src.backend.behaviors.snapping import polygon_centroid as _polygon_centroid
from src.backend.behaviors.snapping import (
    snap_to_polyline as _snap_to_polyline_candidates,
)
from src.backend.shapes.factory import ShapeFactory, transform_legacy_meta
from src.ui.canvas import commands as canvas_commands
from src.ui.canvas import tools as canvas_tools
from src.ui.canvas._constants import SNAP_DIST as _SNAP_DIST
from src.ui.canvas._constants import VERT_HIT as _VERT_HIT
from src.ui.canvas.entities import EntityRecord
from src.ui.canvas.render import CanvasRenderer
from src.ui.canvas.snap import SnapEngine
from src.ui.canvas.undo import UndoStore
from src.ui.core.focus_policy import blur_focused_line_edit
from src.ui.sidebars.canvas_sidebar import DrawSidebar
from src.ui.widgets.tool_picker_dialog import ToolPickerDialog


def _rehydrate_meta(meta: dict) -> dict:
    """Convert JSON-round-tripped meta lists back to point tuples."""
    out = dict(meta)
    for key in ("start", "end", "center"):
        v = out.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            out[key] = (float(v[0]), float(v[1]))
    cps = out.get("control_points")
    if isinstance(cps, list):
        out["control_points"] = [
            (float(p[0]), float(p[1]))
            for p in cps
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
    return out


class PolylineView(
    QWidget,
    CanvasRenderer,
):
    """
    Displays polyline lists with Select / Draw / Edit modes.

    Modes:
    - ``select`` — click polylines to select/deselect, Shift+drag rubber-band
    - ``draw``   — click to place vertices, finish with dbl-click/Enter/right-click
    - ``edit``   — drag vertices, double-click edge to insert, right-click vertex to delete

    Set ``selectable=False`` for a display-only preview (no mode switching).
    """

    selectionChanged = Signal(int)  # type: ignore[assignment]
    modeChanged = Signal(str)

    def _flagged(self, attr: str) -> set[int]:
        """Indices of entities whose boolean ``attr`` is set."""
        return {i for i, e in enumerate(self._entities) if getattr(e, attr)}

    def _set_flagged(self, attr: str, indices) -> None:
        """Set boolean ``attr`` to exactly ``indices`` (wholesale assignment)."""
        wanted = {i for i in indices if isinstance(i, int)}
        for i, e in enumerate(self._entities):
            setattr(e, attr, i in wanted)

    def _is_locked(self, idx: int) -> bool:
        return 0 <= idx < len(self._entities) and self._entities[idx].locked

    def _group_of(self, idx: int) -> int | None:
        ents = self._entities
        return ents[idx].group if 0 <= idx < len(ents) else None

    def _group_map(self) -> dict[int, int]:
        """{entity index: group id} for grouped entities."""
        return {i: e.group for i, e in enumerate(self._entities) if e.group is not None}

    # ── Layer model ───────────────────────────────────────────────────────────

    @property
    def active_layer(self) -> str | None:
        return self._active_layer

    def layer_names(self) -> list[str]:
        names = list(self._layer_order)
        for e in self._entities:
            if e.layer is not None and e.layer not in names:
                names.append(e.layer)
        return names

    def set_layer_model(self, order: list[str], active: str | None) -> None:
        """Install the layer list + active layer (used on load/restore)."""
        self._layer_order = [str(n) for n in order if str(n)]
        if active is not None and str(active) not in self._layer_order:
            self._layer_order.append(str(active))
        self._active_layer = str(active) if active is not None else None
        if self._active_layer is not None:
            for e in self._entities:
                if e.layer is None:
                    e.layer = self._active_layer
        self._drop_inactive_selection()
        self._redraw()

    def set_active_layer(self, name: str) -> None:
        name = str(name)
        if name not in self._layer_order:
            self._layer_order.append(name)
        if self._active_layer == name:
            return
        self._active_layer = name
        self._drop_inactive_selection()
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()

    def add_layer(self, name: str, *, activate: bool = False) -> None:
        name = str(name)
        if name not in self._layer_order:
            self._push_undo()
            self._layer_order.append(name)
        if activate:
            self.set_active_layer(name)
        else:
            self._redraw()

    def rename_layer(self, old: str, new: str) -> None:
        old, new = str(old), str(new).strip()
        if not new or old == new or new in self._layer_order:
            return
        self._push_undo()
        self._layer_order = [new if n == old else n for n in self._layer_order]
        for e in self._entities:
            if e.layer == old:
                e.layer = new
        if self._active_layer == old:
            self._active_layer = new
        old_color = self._layer_colors.pop(old, None)
        if old_color is not None:
            self._layer_colors[new] = old_color
        self._redraw()

    def delete_layer(self, name: str) -> None:
        """Delete a layer and every entity on it (undoable)."""
        name = str(name)
        self._push_undo()
        drop = {i for i, e in enumerate(self._entities) if e.layer == name}
        if drop:
            self._compact_entities(drop)
            self._sel = set()
        self._layer_order = [n for n in self._layer_order if n != name]
        if not self._layer_order:
            self._layer_order = [
                self._active_layer if self._active_layer != name else "Layer 1"
            ]
        if self._active_layer == name:
            self._active_layer = self._layer_order[0]
        self._layer_colors.pop(name, None)
        self._redraw()
        self._notify()
        if drop:
            self._fire_poly_change()

    def layer_color(self, name: str) -> str | None:
        return self._layer_colors.get(str(name))

    def consolidate_layers(self, source_layers: list[str], target_layer: str) -> int:
        """Move every shape on ``source_layers`` onto ``target_layer``, then
        remove those (now-empty) source layers. Single undo step. Returns
        the number of entities moved."""
        target_layer = str(target_layer)
        sources = [str(s) for s in source_layers if str(s) and str(s) != target_layer]
        if not sources:
            return 0
        src_set = set(sources)
        self._push_undo()
        moved = 0
        if target_layer not in self._layer_order:
            self._layer_order.append(target_layer)
        for e in self._entities:
            if e.layer in src_set:
                e.layer = target_layer
                moved += 1
        self._layer_order = [n for n in self._layer_order if n not in src_set]
        if not self._layer_order:
            self._layer_order = [target_layer]
        if self._active_layer in src_set:
            self._active_layer = target_layer
        for name in sources:
            self._layer_colors.pop(name, None)
        self._drop_inactive_selection()
        self._redraw()
        self._notify()
        if moved:
            self._fire_poly_change()
        return moved

    def set_layer_color(self, name: str, color: str | None) -> None:
        """Assign (or clear, with ``color=None``) a layer's display color."""
        name = str(name)
        if color is None:
            self._layer_colors.pop(name, None)
        else:
            self._layer_colors[name] = str(color)
        self._redraw()

    def move_layer(self, name: str, new_index: int) -> None:
        name = str(name)
        names = self.layer_names()
        if name not in names:
            return
        self._push_undo()
        names.remove(name)
        names.insert(max(0, min(int(new_index), len(names))), name)
        self._layer_order = names
        self._redraw()

    def move_indices_to_layer(self, indices: list[int], layer: str) -> int:
        """Reassign entities to ``layer``; returns how many moved."""
        layer = str(layer)
        if layer not in self._layer_order:
            self._layer_order.append(layer)
        moved = 0
        pushed = False
        for idx in indices:
            if not (0 <= idx < len(self._entities)):
                continue
            e = self._entities[idx]
            if e.layer == layer:
                continue
            if not pushed:
                self._push_undo()
                pushed = True
            e.layer = layer
            moved += 1
        if moved:
            self._drop_inactive_selection()
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return moved

    def _on_active_layer(self, e: EntityRecord) -> bool:
        return (
            self._active_layer is None
            or e.layer is None
            or e.layer == self._active_layer
        )

    def _entity_selectable(self, idx: int) -> bool:
        if not (0 <= idx < len(self._entities)):
            return False
        e = self._entities[idx]
        return not e.hidden and self._on_active_layer(e)

    def _noninteractive_indices(self) -> set[int]:
        """Hidden entities plus entities on non-active layers."""
        return {
            i
            for i, e in enumerate(self._entities)
            if e.hidden or not self._on_active_layer(e)
        }

    def _drop_inactive_selection(self) -> None:
        keep = {i for i in self._sel if self._entity_selectable(i)}
        if keep != self._sel:
            self._sel = keep
            self._notify()

    @staticmethod
    def _clone_polys(
        polys: list[list[tuple[float, float]]],
    ) -> list[list[tuple[float, float]]]:
        return [list(poly) for poly in polys]

    def __init__(
        self,
        parent: QWidget | None = None,
        selectable: bool = True,
        on_change=None,
        on_mode_change=None,
        on_poly_change=None,
    ):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._selectable = selectable
        self._empty_message = "No polylines loaded"
        self._show_selection_bbox: bool = False
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        # Single source of truth for drawable entities.
        self._entities: list[EntityRecord] = []
        self._sel: set[int] = set()

        # Layer model. ``_active_layer is None`` = single-layer mode: every
        # entity is interactive and ``EntityRecord.layer`` is ignored.
        # Multi-layer pages (Draft) install an ordered layer list + active
        # layer; entities on non-active layers render dimmed and are not
        # selectable/editable until their layer is activated.
        self._layer_order: list[str] = []
        self._active_layer: str | None = None
        # Optional per-layer color (hex string), shown in the layer tree
        # swatch and tinting that layer's canvas outlines.
        self._layer_colors: dict[str, str] = {}

        # Lazily-built Shape objects for the snap engine (invalidated on
        # any structural/geometry change).
        self._snap_shapes_cache: list | None = None

        # construction/hidden/locked/group flags live on EntityRecord.
        self._accent_polys: dict[int, str] = {}  # index → color hex for role overlays
        self._next_group_id: int = 0
        self._group_labels: dict[int, str] = {}  # gid → custom name
        self._draw_construction_mode: bool = False
        self._draw_split_enabled: bool = True

        # Ghost polylines: a non-interactive secondary set rendered beneath the
        # main polys (faded, dashed). Used for showing context layers — e.g.
        # the source outline beneath a generated pattern preview — without
        # putting them into the editable poly list.
        self._ghost_polys: list[list[tuple[float, float]]] = []
        self._ghost_visible: bool = True

        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0

        # LMB interaction state
        self._lmb_press: QPointF | None = None
        self._lmb_prev: QPointF | None = None
        self._lmb_target: int | None = None

        # MMB pan state
        self._mmb_prev: QPointF | None = None
        self._space_pan_active: bool = False
        self._space_pan_dragging: bool = False

        # Cursor world position
        self._cursor_wx: float | None = None
        self._cursor_wy: float | None = None

        # Rubber-band select
        self._shift_drag: bool = False
        self._band_start: QPointF | None = None
        self._band_additive: bool = False

        # Undo / redo history (delta-based; see src/ui/canvas/undo.py)
        self._undo_store = UndoStore()

        # Unified snap engine (src/ui/canvas/snap.py) and guide lines
        # (("h", y_world) or ("v", x_world)); guides participate in snapping.
        self._snap_engine = SnapEngine(self)
        self._guides: list[tuple[str, float]] = []
        self._guide_drag: int | None = None
        self._guide_drag_moved: bool = False
        self._selected_guide: int | None = None
        # mm rulers along the top/left edges; drag out of a ruler to create
        # a guide, drop a guide back onto a ruler to delete it.
        self._rulers_visible: bool = False

        # Fit scale for zoom-% display
        self._fit_scale: float = 1.0

        # Measure tool
        self._measure_mode: bool = False
        self._measure_anchor: tuple[float, float] | None = None
        self._measure_hover: tuple[float, float] | None = None
        self._measure_locked: bool = False
        self._measure_end: tuple[float, float] | None = None
        self._measure_snapped_a: bool = False
        self._measure_snapped_b: bool = False
        self._measure_edit: QLineEdit | None = None

        # Mode: "select" | "draw" | "edit"
        self._mode: str = "select"

        # Interaction tools (src/ui/canvas/tools.py): per-mode strategy
        # objects dispatched by the mouse event handlers. All interaction
        # state stays on the view; tools are stateless.
        trim_tool = canvas_tools.TrimExtendTool(self)
        self._tools: dict[str, canvas_tools.CanvasTool] = {
            "select": canvas_tools.SelectTool(self),
            "draw": canvas_tools.DrawTool(self),
            "edit": canvas_tools.EditTool(self),
            "trim": trim_tool,
            "extend": trim_tool,
        }
        self._measure_tool = canvas_tools.MeasureTool(self)

        # Draw mode state
        self._draw_pts: list[tuple[float, float]] = []
        self._draw_point_snap_types: list[str | None] = []
        self._draw_primitive: str = (
            "polyline"  # polyline|line|arc|rectangle|circle|ellipse|polygon
        )
        self._draw_shape_preview_active: bool = False
        self._draw_shape_anchor_w: tuple[float, float] | None = None
        self._draw_shape_cursor_w: tuple[float, float] | None = None
        self._draw_arc_pts: list[tuple[float, float]] = []
        self._draw_arc_mode: str = "3point"
        self._draw_constraint_lock: str | None = None

        # Edit mode state
        self._edit_poly: int | None = None
        self._edit_vert: int | None = None
        self._edit_dragging: bool = False
        self._edit_linked_verts: set[tuple[int, int]] = set()
        self._edit_selected_verts: set[tuple[int, int]] = set()
        self._edit_drag_targets: set[tuple[int, int]] = set()
        self._edit_drag_anchor: tuple[float, float] | None = None
        self._edit_drag_moved: bool = False
        self._edit_undo_pushed: bool = False
        self._hover_vert: tuple[int, int] | None = None
        # Select-mode hover pre-highlight: which polyline a click would pick
        self._hover_poly: int | None = None

        # Move state (select mode drag-to-move). Object snapping works on
        # absolute deltas from the drag anchor: the selection's own vertices
        # (sampled at drag start) snap against static vertices/edges/grid/
        # guides regardless of where the user grabbed the shape.
        self._move_dragging: bool = False
        self._move_origin: tuple[float, float] | None = None
        self._move_undo_pushed: bool = False
        self._move_anchor_w: tuple[float, float] | None = None
        self._move_applied_w: tuple[float, float] = (0.0, 0.0)
        self._move_start_pts: list[tuple[float, float]] = []
        self._move_snap_exclude_vertices: set[tuple[int, int]] = set()
        self._move_snap_exclude_segments: set[tuple[int, int]] = set()

        # Clipboard
        self._clipboard: list[dict[str, Any]] = []

        # Image bounds reference rectangle
        self._img_bounds: tuple[float, float] | None = None

        # Background image overlay
        self._bg_pil: PILImage.Image | None = None
        self._bg_w_mm: float = 0.0
        self._bg_h_mm: float = 0.0
        self._bg_pixmap: QPixmap | None = None
        self._bg_cached_scale: float = 0.0

        # Measure button rect
        self._mbtn_rect: tuple[float, float, float, float] = (0, 0, 0, 0)

        # Draw mode snap (world-space snap point under cursor)
        self._draw_snap: tuple[float, float] | None = None
        self._draw_snap_type: str | None = None
        # Cross-mode hover snap indicator (select/edit/move/measure)
        self._hover_snap: tuple[float, float] | None = None
        self._hover_snap_type: str | None = None
        # Independent X/Y axis snap indicators for whole-shape drag (up to
        # two entries — lets one axis align to a different feature than the
        # other, e.g. left edge to shape A while top edge aligns to shape B).
        # Each entry is (target_point, kind, dragged_point) so the renderer
        # can draw a dashed guide line connecting the two — without it, a
        # match that's only aligned on one axis can appear at a point that's
        # visually far from the shape, looking like a snapping glitch.
        self._hover_snap_multi: list[
            tuple[tuple[float, float], str, tuple[float, float]]
        ] = []
        # Measure pre-anchor hover snap point
        self._measure_hover_pre: tuple[float, float] | None = None

        # Precision aids
        self._grid_visible: bool = False
        self._grid_snap: bool = False
        self._grid_spacing: float = 5.0

        # Construction / reference lines: list of ("h", y_world) or ("v", x_world)

        # Auto-dimension HUD inputs (Fusion 360 style)
        self._dim_distance_edit: QLineEdit | None = None
        self._dim_angle_edit: QLineEdit | None = None
        self._dim_distance_dirty: bool = False
        self._dim_angle_dirty: bool = False

        # Selection dimension badge hit rects (for inline editing)
        self._sel_badge_w_rect: QRectF | None = None
        self._sel_badge_h_rect: QRectF | None = None
        # Single-line selection: length / angle badge hit rects
        self._sel_badge_l_rect: QRectF | None = None
        self._sel_badge_a_rect: QRectF | None = None
        # Inline single-dimension editor
        self._sel_dim_edit: QLineEdit | None = None
        self._sel_dim_axis: str | None = None  # "w" or "h"

        # Transform gizmo (select mode)
        self._gizmo_scale_rect: QRectF | None = None
        self._gizmo_rotate_rect: QRectF | None = None
        self._gizmo_move_rect: QRectF | None = None
        # 8-handle selection frame: [(handle name, hit rect), ...] where the
        # name is a compass direction ("nw", "n", …) in world orientation.
        self._gizmo_handle_rects: list[tuple[str, QRectF]] = []
        self._gizmo_anchor_w: tuple[float, float] | None = None
        self._gizmo_handle_w: tuple[float, float] | None = None
        self._gizmo_drag_mode: str | None = None  # "scale" | "rotate"
        self._gizmo_center_w: tuple[float, float] | None = None
        self._gizmo_start_vec: tuple[float, float] | None = None
        self._gizmo_snapshot: dict[int, list[tuple[float, float]]] = {}
        # Parallel snapshot of each entity's meta ("center" etc.) at drag
        # start — needed so scale/rotate can recompute e.g. a circle's true
        # center from its ORIGINAL (pre-drag) value every mouse-move event,
        # instead of compounding the transform onto an already-updated value.
        self._gizmo_meta_snapshot: dict[int, dict[str, Any] | None] = {}
        self._gizmo_drag_moved: bool = False
        self._gizmo_undo_pushed: bool = False

        # Auto-constraint detection (H/V)
        self._draw_constraint: str | None = None

        # Flash indicator for transient messages
        self._flash_text: str | None = None
        self._flash_timer: QTimer | None = None

        # Angle snap active flag (for ortho display)
        self._angle_snap_active: bool = False

        # Draw-mode slide-in sidebar
        self._draw_sidebar: DrawSidebar | None = None
        self._draw_sidebar_anim: QPropertyAnimation | None = None
        self._draw_sidebar_visible: bool = False
        self._draw_shape_w_edit: QLineEdit | None = None
        self._draw_shape_h_edit: QLineEdit | None = None

        self._needs_fit = True
        self.setMouseTracking(True)
        self._build_draw_sidebar()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, polys: list[list[tuple[float, float]]]) -> None:
        self._entities = [
            EntityRecord(points=list(p), layer=self._active_layer) for p in polys
        ]
        self._sel.clear()
        self._group_labels.clear()
        self._undo_store.clear()

        self._sync_shape_storage_from_entities()

        self._needs_fit = True
        self._fit()
        self._notify()

    def set_accent_polys(self, accent: dict[int, str]) -> None:
        """Override render color for specific poly indices (e.g. cutout shapes).

        Pass an empty dict to clear all accents.
        """
        self._accent_polys = dict(accent)
        self._redraw()

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [list(e.points) for e in self._entities]

    def set_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        self._entities = [
            EntityRecord(points=list(p), layer=self._active_layer) for p in polys
        ]
        self._sel.clear()
        self._group_labels.clear()
        self._undo_store.clear()

        self._sync_shape_storage_from_entities()

        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()

    def get_view_state(
        self,
    ) -> dict[str, float | str | bool | list[int] | dict[str, int]]:
        return {
            "scale": self._scale,
            "ox": self._ox,
            "oy": self._oy,
            "fit_scale": self._fit_scale,
            "mode": self._mode,
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
            "guides": [[o, c] for o, c in self._guides],
            "rulers_visible": self._rulers_visible,
            "layer_colors": dict(self._layer_colors),
            "hidden_indices": sorted(self._flagged("hidden")),
            "locked_indices": sorted(self._flagged("locked")),
            "groups": {str(i): g for i, g in self._group_map().items()},
            "group_labels": {str(k): v for k, v in self._group_labels.items()},
        }

    def set_view_state(self, state: dict[str, float | str | bool | list[int]]) -> None:
        scale_state = state.get("scale", self._scale)
        ox_state = state.get("ox", self._ox)
        oy_state = state.get("oy", self._oy)
        fit_scale_state = state.get("fit_scale", self._fit_scale)
        grid_spacing_state = state.get("grid_spacing", self._grid_spacing)

        def _to_float(value, default: float) -> float:
            if isinstance(value, (int, float, str, bool)):
                try:
                    return float(value)
                except ValueError:
                    return default
            return default

        self._scale = max(_MIN_SCALE, _to_float(scale_state, self._scale))
        self._ox = _to_float(ox_state, self._ox)
        self._oy = _to_float(oy_state, self._oy)
        self._fit_scale = max(_MIN_SCALE, _to_float(fit_scale_state, self._fit_scale))
        mode = str(state.get("mode", self._mode))
        if mode in ("select", "draw", "edit"):
            self.set_mode(mode)
        self._grid_visible = bool(state.get("grid_visible", self._grid_visible))
        self._grid_snap = bool(state.get("grid_snap", self._grid_snap))
        self._grid_spacing = max(
            0.001, _to_float(grid_spacing_state, self._grid_spacing)
        )
        hidden_state = state.get("hidden_indices", [])
        if not isinstance(hidden_state, list):
            hidden_state = []
        locked_state = state.get("locked_indices", [])
        if not isinstance(locked_state, list):
            locked_state = []
        raw_guides = state.get("guides", [])
        if isinstance(raw_guides, list):
            self._guides = [
                (str(o), float(c))
                for o, c in raw_guides
                if str(o) in ("h", "v")
            ]
        if "rulers_visible" in state:
            self._rulers_visible = bool(state.get("rulers_visible"))
        raw_colors = state.get("layer_colors", {})
        if isinstance(raw_colors, dict):
            self._layer_colors = {
                str(k): str(v) for k, v in raw_colors.items() if v
            }
        self._set_flagged("hidden", hidden_state)
        self._set_flagged("locked", locked_state)
        raw_groups = state.get("groups", {})
        if isinstance(raw_groups, dict):
            parsed = {
                int(k): int(v)
                for k, v in raw_groups.items()
                if str(k).lstrip("-").isdigit()
                and str(v).lstrip("-").isdigit()
                and 0 <= int(k) < len(self._entities)
            }
            for i, e in enumerate(self._entities):
                e.group = parsed.get(i)
            self._next_group_id = max(parsed.values(), default=0) + 1
        raw_labels = state.get("group_labels", {})
        if isinstance(raw_labels, dict):
            self._group_labels = {
                int(k): str(v)
                for k, v in raw_labels.items()
                if str(k).lstrip("-").isdigit() and str(v).strip()
            }
        self._sel -= self._flagged("hidden")
        self._redraw()

    def get_selected(self) -> list[list[tuple[float, float]]]:
        return [p for i, p in enumerate(e.points for e in self._entities) if i in self._sel]

    def _append_entity(
        self,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> int:
        self._entities.append(
            EntityRecord(
                points=list(poly),
                kind=kind,
                meta=deepcopy(meta) if meta is not None else None,
                layer=self._active_layer,
            )
        )
        self._sync_shape_storage_from_entities()
        return len(self._entities) - 1

    def _restore_history_state(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        layers: tuple[tuple[str, ...], str | None] | None = None,
    ) -> None:
        self._entities = entities
        if layers is not None:
            order, active = layers
            self._layer_order = list(order)
            self._active_layer = active
        self._sel = {
            i for i in sel if i < len(self._entities) and self._entity_selectable(i)
        }
        self._sync_shape_storage_from_entities()

    def _reset_edit_interaction_state(self) -> None:
        self._hover_poly = None
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None

    def _sync_shape_storage_from_entities(self) -> None:
        """Invalidate the lazily-built snap-shape cache."""
        self._snap_shapes_cache = None

    def _snap_shapes(self) -> list:
        """Shape objects for the snap engine, rebuilt from entities on demand."""
        if self._snap_shapes_cache is None:
            ShapeFactory.reset_id_counter(0)
            self._snap_shapes_cache = [
                ShapeFactory.from_legacy(kind=e.kind, points=e.points, metadata=e.meta)
                for e in self._entities
            ]
        return self._snap_shapes_cache

    def get_selection_indices(self) -> list[int]:
        return self._selected_indices()

    def set_selection(self, indices: list[int]) -> None:
        new_sel = {idx for idx in indices if self._entity_selectable(idx)}
        if new_sel == self._sel:
            return
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_hidden_indices(self, indices: list[int]) -> None:
        new_hidden = {idx for idx in indices if 0 <= idx < len(self._entities)}
        new_sel = self._sel - new_hidden
        if new_hidden == self._flagged("hidden") and new_sel == self._sel:
            return
        self._set_flagged("hidden", new_hidden)
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_locked_indices(self, indices: list[int]) -> None:
        new_locked = {idx for idx in indices if 0 <= idx < len(self._entities)}
        if new_locked == self._flagged("locked"):
            return
        self._set_flagged("locked", new_locked)
        self._redraw()

    def set_ghost_polylines(
        self,
        polys: list[list[tuple[float, float]]] | None,
        *,
        visible: bool | None = None,
    ) -> None:
        """Install (or clear) the ghost-overlay polylines.

        Pass ``None`` or an empty list to clear the overlay. ``visible`` may be
        used to control overlay visibility independently of the data; if
        omitted the current visibility flag is preserved.
        """
        new_ghosts: list[list[tuple[float, float]]]
        if not polys:
            new_ghosts = []
        else:
            new_ghosts = [list(p) for p in polys]
        changed = new_ghosts != self._ghost_polys
        self._ghost_polys = new_ghosts
        if visible is not None and bool(visible) != self._ghost_visible:
            self._ghost_visible = bool(visible)
            changed = True
        if changed:
            self._redraw()

    def get_status_summary(self) -> dict[str, object]:
        precision = []
        if self._grid_visible:
            precision.append(f"Grid {self._grid_spacing:g}mm")
        if self._grid_snap:
            precision.append("Snap")
        if self._measure_mode:
            precision.append("Measure")
        if self._draw_construction_mode:
            precision.append("Construction")
        topo = self.get_topology_summary()
        return {
            "mode": self._mode,
            "selected_count": len(self._sel),
            "object_count": len(self._entities),
            "precision": " · ".join(precision) if precision else "Free move",
            "topology": (
                f"{topo['closed']} closed · {topo['open']} open · {topo['points']} pts"
            ),
        }

    def get_topology_summary(self) -> dict[str, int]:
        closed = 0
        logical_points = 0
        for poly in (e.points for e in self._entities):
            if self._is_poly_closed(poly):
                closed += 1
                logical_points += max(0, len(poly) - 1)
            else:
                logical_points += len(poly)
        total = len(self._entities)
        return {
            "total": total,
            "closed": closed,
            "open": max(0, total - closed),
            "points": logical_points,
        }

    def _compact_entities(self, drop: set[int]) -> None:
        """Remove entities at ``drop`` indices.

        Kind/meta/flags live on the EntityRecord, so they travel with the
        surviving entities — no index remapping needed (previously ~45 lines
        of error-prone bookkeeping).
        """
        self._entities = [
            e for i, e in enumerate(self._entities) if i not in drop
        ]
        # A group with fewer than two surviving members is meaningless —
        # dissolve it so no phantom "Group · 1 shapes" rows linger.
        counts: dict[int, int] = {}
        for e in self._entities:
            if e.group is not None:
                counts[e.group] = counts.get(e.group, 0) + 1
        for e in self._entities:
            if e.group is not None and counts[e.group] < 2:
                e.group = None
    def delete_indices(self, indices: list[int]) -> int:
        """Delete specific entities regardless of the active layer (used by
        the layer tree); locked entities survive."""
        drop = {
            i
            for i in indices
            if 0 <= i < len(self._entities) and not self._entities[i].locked
        }
        if not drop:
            return 0
        self._push_undo()
        self._compact_entities(drop)
        self._sel -= drop
        self._sel = {i - sum(1 for d in drop if d < i) for i in self._sel}
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(drop)

    def delete_selected(self) -> int:
        delete_set = {idx for idx in self._sel if not self._is_locked(idx)}
        n = len(delete_set)
        if n:
            self._push_undo()
        self._compact_entities(delete_set)
        self._sel.clear()
        self._redraw()
        self._notify()
        if n:
            self._fire_poly_change()
        return n

    def undo(self) -> bool:
        result = self._undo_store.undo(
            self._entities, self._sel, self._layer_state()
        )
        if result is None:
            return False
        self._restore_history_state(*result)
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def redo(self) -> bool:
        result = self._undo_store.redo(
            self._entities, self._sel, self._layer_state()
        )
        if result is None:
            return False
        self._restore_history_state(*result)
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def select_all(self) -> None:
        self._sel = set(range(len(self._entities))) - self._noninteractive_indices()
        self._redraw()
        self._notify()

    def deselect_all(self) -> None:
        self._sel.clear()
        self._redraw()
        self._notify()

    def _invert_selection(self) -> None:
        """Invert selection: select all unselected, deselect all selected."""
        all_indices = set(range(len(self._entities))) - self._noninteractive_indices()
        self._sel = all_indices - self._sel
        self._redraw()
        self._notify()

    def toggle_measure(self) -> None:
        self._measure_mode = not self._measure_mode
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_locked = False
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._dismiss_measure_edit()
        self._update_cursor()
        self._redraw()

    def set_image_bounds(self, w_mm: float, h_mm: float) -> None:
        self._img_bounds = (w_mm, h_mm)
        self._redraw()

    def set_background_image(
        self, pil_img: PILImage.Image, w_mm: float, h_mm: float
    ) -> None:
        self._bg_pil = pil_img
        self._bg_w_mm = w_mm
        self._bg_h_mm = h_mm
        self._bg_pixmap = None
        self._bg_cached_scale = 0.0
        self._redraw()

    def clear_background_image(self) -> None:
        self._bg_pil = None
        self._bg_pixmap = None
        self._redraw()

    def set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        if self._mode == "draw":
            self._draw_pts.clear()
            self._draw_point_snap_types.clear()
            self._draw_snap = None
            self._draw_snap_type = None
            self._hover_snap = None
            self._hover_snap_type = None
            self._angle_snap_active = False
            self._draw_constraint = None
            self._draw_shape_preview_active = False
            self._draw_shape_anchor_w = None
            self._draw_shape_cursor_w = None
            self._draw_arc_pts.clear()
            self._dismiss_dim_inputs()
        elif self._mode == "edit":
            self._edit_poly = None
            self._edit_vert = None
            self._edit_dragging = False
            self._edit_linked_verts = set()
            self._edit_selected_verts = set()
            self._edit_drag_targets = set()
            self._hover_vert = None
        self._mode = mode
        if mode in ("draw", "edit"):
            self._measure_mode = False
        self._set_draw_sidebar_visible(mode == "draw")
        self._refresh_draw_sidebar_state()
        self._update_cursor()
        self._redraw()
        self.modeChanged.emit(mode)

    def get_mode(self) -> str:
        return self._mode

    def fit(self) -> None:
        self._fit()

    def fit_selection(self) -> bool:
        bounds = self._selection_bounds()
        if bounds is None:
            return False
        self._fit_to_bounds(bounds)
        return True

    def set_empty_message(self, message: str) -> None:
        """Set the hint shown on an empty canvas ("Title\\nhint line")."""
        self._empty_message = message

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._redraw()

    def set_grid_snap(self, enabled: bool) -> None:
        self._grid_snap = bool(enabled)
        self._redraw()

    def set_grid_spacing(self, spacing: float) -> None:
        self._grid_spacing = max(0.001, float(spacing))
        self._redraw()

    # ── Inlined from removed mixins (methods actually called from view.py) ──

    def _transform_entity_meta(
        self,
        idx: int,
        *,
        center: tuple[float, float],
        kind: str,
        meta: dict[str, Any] | None,
        transform: str,
        factor: float | None = None,
        angle_deg: float = 0.0,
        axis: str | None = None,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> None:
        """Transform an entity's parametric metadata via its Shape class.

        All per-kind transform math lives on the Shape subclasses in
        src/backend/shapes/shape.py — this is a thin delegation shim kept for
        the legacy kind+meta storage until the canvas migrates to shapes.
        """
        updated = transform_legacy_meta(
            kind,
            meta,
            transform=transform,
            center=center,
            factor=factor,
            angle_deg=angle_deg,
            axis=axis,
            dx=dx,
            dy=dy,
        )
        if updated is not None and idx < len(self._entities):
            self._entities[idx].meta = updated

    @staticmethod
    def _translated_entity_meta(
        kind: str,
        meta: dict[str, Any] | None,
        dx: float,
        dy: float,
    ) -> dict[str, Any] | None:
        return transform_legacy_meta(kind, meta, transform="translate", dx=dx, dy=dy)

    @staticmethod
    def _rotate_point(
        point: tuple[float, float],
        center: tuple[float, float],
        angle_deg: float,
    ) -> tuple[float, float]:
        if abs(angle_deg) < 1e-9:
            return point
        ang = math.radians(angle_deg)
        ca = math.cos(ang)
        sa = math.sin(ang)
        px, py = point
        cx, cy = center
        dx = px - cx
        dy = py - cy
        return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

    @staticmethod
    def _scale_point(
        point: tuple[float, float],
        center: tuple[float, float],
        factor: float,
    ) -> tuple[float, float]:
        if abs(factor - 1.0) < 1e-9:
            return point
        px, py = point
        cx, cy = center
        return (cx + (px - cx) * factor, cy + (py - cy) * factor)

    @staticmethod
    def _mirror_point(
        point: tuple[float, float],
        center: tuple[float, float],
        axis: str,
    ) -> tuple[float, float]:
        px, py = point
        cx, cy = center
        if axis == "horizontal":
            return (2 * cx - px, py)
        if axis == "vertical":
            return (px, 2 * cy - py)
        return point

    def _key_delete(self) -> None:
        if self._selected_guide is not None:
            self._delete_selected_guide()
            return
        if self._mode == "edit":
            if getattr(self, "_edit_selected_verts", None):
                self._delete_edit_vertices(set(getattr(self, "_edit_selected_verts", set())))
                return
            if getattr(self, "_hover_vert", None) is not None:
                self._delete_edit_vertices({self._hover_vert})
                return
        if self._mode == "select":
            self.delete_selected()

    def _delete_selected_guide(self) -> None:
        """Remove the currently selected ruler guide (Delete/Backspace)."""
        gi = self._selected_guide
        if gi is None or not (0 <= gi < len(self._guides)):
            self._selected_guide = None
            return
        del self._guides[gi]
        self._selected_guide = None
        self._guide_drag = None
        self._redraw()
        self._notify()

    def _key_backspace(self) -> None:
        if self._selected_guide is not None:
            self._delete_selected_guide()
            return
        if getattr(self, "_mode", None) == "draw" and getattr(self, "_draw_pts", []):
            self._draw_pts.pop()
            if getattr(self, "_draw_point_snap_types", []):
                self._draw_point_snap_types.pop()
            if not getattr(self, "_draw_pts", []):
                self._dismiss_dim_inputs()
                self._draw_constraint = None
            self._refresh_draw_sidebar_state()
            self._redraw()
        elif getattr(self, "_mode", None) == "edit":
            self._key_delete()
        elif getattr(self, "_mode", None) == "select":
            self.delete_selected()

    def _linked_vertices(self, poly_idx: int, vert_idx: int) -> set[tuple[int, int]]:
        if poly_idx >= len(self._entities) or vert_idx >= len(self._entities[poly_idx].points):
            return set()
        target_pt = self._entities[poly_idx].points[vert_idx]
        linked = {(poly_idx, vert_idx)}
        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6
        for i, poly in enumerate(e.points for e in self._entities):
            if i == poly_idx:
                is_closed = len(poly) >= 4 and _eq(poly[0], poly[-1])
                if is_closed and (vert_idx == 0 or vert_idx == len(poly) - 1):
                    linked.add((i, 0))
                    linked.add((i, len(poly) - 1))
            else:
                for j, pt in enumerate(poly):
                    if _eq(target_pt, pt):
                        linked.add((i, j))
        return linked

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        if getattr(self, "_edit_poly", None) is None or getattr(self, "_edit_vert", None) is None:
            return
        kind = self._entities[self._edit_poly].kind
        meta = self._entities[self._edit_poly].meta
        if kind == "arc" and isinstance(meta, dict):
            center = meta.get("center")
            if isinstance(center, tuple):
                cx = float(center[0])
                cy = float(center[1])
                radius = max(1e-3, math.hypot(wx - cx, wy - cy))
                meta["radius"] = radius
                poly_len = len(self._entities[self._edit_poly].points)
                if self._edit_vert <= 1:
                    meta["start_angle"] = (math.degrees(math.atan2(wy - cy, wx - cx)) % 360.0)
                elif self._edit_vert >= max(0, poly_len - 2):
                    meta["end_angle"] = (math.degrees(math.atan2(wy - cy, wx - cx)) % 360.0)
                return
        if kind == "rectangle" and isinstance(meta, dict):
            center = meta.get("center")
            if isinstance(center, tuple):
                cx = float(center[0])
                cy = float(center[1])
                meta["width"] = max(1e-3, 2.0 * abs(wx - cx))
                meta["height"] = max(1e-3, 2.0 * abs(wy - cy))
                return

        targets = (
            getattr(self, "_edit_drag_targets", None)
            or getattr(self, "_edit_linked_verts", None)
            or {(self._edit_poly, self._edit_vert)}
        )
        for pi, vi in targets:
            if self._is_locked(pi):
                continue
            if 0 <= pi < len(self._entities) and 0 <= vi < len(self._entities[pi].points):
                self._entities[pi].points[vi] = (wx, wy)

        if self._edit_poly is not None and 0 <= self._edit_poly < len(self._entities):
            if kind == "line" and isinstance(meta, dict) and len(self._entities[self._edit_poly].points) >= 2:
                meta["start"] = tuple(self._entities[self._edit_poly].points[0])
                meta["end"] = tuple(self._entities[self._edit_poly].points[-1])
            elif kind == "spline" and isinstance(meta, dict):
                meta["control_points"] = [tuple(pt) for pt in self._entities[self._edit_poly].points]

    def _select_edit_vertices_in_rect(
        self, x1c: float, y1c: float, x2c: float, y2c: float, *, additive: bool = True
    ) -> int:
        if not additive:
            self._edit_selected_verts.clear()
        added = 0
        for pi, poly in enumerate(e.points for e in self._entities):
            for vi, (vx, vy) in enumerate(poly):
                cx, cy = self._w2c(vx, vy)
                if x1c <= cx <= x2c and y1c <= cy <= y2c:
                    self._edit_selected_verts.add((pi, vi))
                    added += 1
        return added

    def _copy_selected(self) -> None:
        if not self._sel:
            return
        self._clipboard = []
        for i in sorted(self._sel):
            if i >= len(self._entities):
                continue
            self._clipboard.append({
                "polyline": list(self._entities[i].points),
                "kind": self._entities[i].kind,
                "meta": deepcopy(self._entities[i].meta) if self._entities[i].meta is not None else None,
                "construction": self._entities[i].construction,
                "group": self._entities[i].group,
            })

    def _paste_records(self, offset: float) -> list[int]:
        """Append clipboard records at ``offset``; grouped sources stay
        grouped in the copy (each source group maps to a fresh group id)."""
        new_indices: list[int] = []
        gid_map: dict[int, int] = {}
        for record in getattr(self, "_clipboard", []):
            poly = list(record.get("polyline", []))
            new_poly = [(x + offset, y + offset) for x, y in poly]
            kind = str(record.get("kind", "polyline"))
            meta = self._translated_entity_meta(kind, record.get("meta"), offset, offset)
            new_idx = self._append_entity(new_poly, kind=kind, meta=meta)
            if record.get("construction"):
                self._entities[new_idx].construction = True
            src_gid = record.get("group")
            if src_gid is not None:
                if src_gid not in gid_map:
                    gid_map[src_gid] = self._next_group_id
                    self._next_group_id += 1
                self._entities[new_idx].group = gid_map[src_gid]
            new_indices.append(new_idx)
        return new_indices

    def _paste_clipboard(self) -> None:
        if not getattr(self, "_clipboard", []):
            return
        self._push_undo()
        new_indices = self._paste_records(1.0)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _duplicate_selected(self) -> None:
        if not self._sel:
            return
        self._copy_selected()
        self._paste_clipboard()

    def _duplicate_selected_with_offset(self) -> None:
        if not self._sel or not self._entities:
            return
        min_x, max_x, min_y, max_y = (float("inf"), float("-inf"), float("inf"), float("-inf"))
        for idx in self._sel:
            if idx < len(self._entities):
                for x, y in self._entities[idx].points:
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
        width = max_x - min_x if max_x > min_x else 10.0
        height = max_y - min_y if max_y > min_y else 10.0
        offset = max(2.0, min(width, height) * 0.1)
        self._copy_selected()
        self._paste_clipboard_with_offset(offset)

    def _paste_clipboard_with_offset(self, offset: float) -> None:
        if not getattr(self, "_clipboard", []):
            return
        self._push_undo()
        new_indices = self._paste_records(offset)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _cut_selected(self) -> None:
        if not self._sel:
            return
        cut_set = {idx for idx in self._sel if not self._is_locked(idx)}
        if not cut_set:
            return
        self._copy_selected()
        self._push_undo()
        self._compact_entities(cut_set)
        self._sel.clear()
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._sel:
            return
        mutable = [idx for idx in self._sel if not self._is_locked(idx)]
        if not mutable:
            return
        self._push_undo(coalesce="nudge")
        QTimer.singleShot(500, self._undo_store.break_coalescing)
        for idx in mutable:
            if idx < len(self._entities):
                self._entities[idx].points = [(x + dx, y + dy) for x, y in self._entities[idx].points]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entities[idx].kind,
                    meta=self._entities[idx].meta,
                    transform="translate", dx=dx, dy=dy,
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _shape_primitive_active(self) -> bool:
        return getattr(self, "_draw_primitive", "polyline") in {"rectangle", "circle", "ellipse", "polygon"}

    def _is_near_start(self) -> bool:
        if getattr(self, "_mode", None) != "draw":
            return False
        draw_pts = getattr(self, "_draw_pts", [])
        if len(draw_pts) < 3:
            return False
        cursor_wx = getattr(self, "_cursor_wx", None)
        cursor_wy = getattr(self, "_cursor_wy", None)
        if cursor_wx is None or cursor_wy is None:
            return False
        start_cx, start_cy = self._w2c(*draw_pts[0])
        cur_cx, cur_cy = self._w2c(cursor_wx, cursor_wy)
        return math.hypot(cur_cx - start_cx, cur_cy - start_cy) < 10.0

    def _finish_draw(self, *, close: bool = False) -> None:
        if getattr(self, "_mode", None) != "draw" or len(getattr(self, "_draw_pts", [])) < 2:
            return
        draw_pts = self._draw_pts
        primitive = getattr(self, "_draw_primitive", "polyline")
        if primitive == "spline" and len(draw_pts) < 3:
            self._show_flash("Spline needs at least 3 points", 900)
            return
        if close and draw_pts[0] != draw_pts[-1]:
            draw_pts.append(draw_pts[0])
        drawn = list(draw_pts)
        self._commit_drawn_polyline(drawn, primitive=primitive, close=close, created_flash="Polyline created")

    def _commit_drawn_polyline(
        self, poly: list[tuple[float, float]], *, primitive: str, close: bool = False, created_flash: str = "Polyline created"
    ) -> bool:
        if len(poly) < 2:
            return False
        self._push_undo()
        split_happened = False
        split_closed = 0
        split_open = 0
        can_cut_split = getattr(self, "_draw_split_enabled", True) and primitive in {"line", "polyline", "arc", "spline"}
        if can_cut_split and not close and len(poly) >= 2:
            split_happened, split_closed, split_open = self._split_geometry_with_line(poly)

        kind = "polyline"
        meta: dict[str, Any] | None = None
        if primitive == "line" and len(poly) >= 2:
            kind = "line"
            meta = {"start": tuple(poly[0]), "end": tuple(poly[-1])}
        elif primitive == "arc" and len(poly) >= 3:
            from src.backend.geometry.arc import (
                arc_spec_from_center_start_end,
                arc_spec_from_three_points,
            )
            if getattr(self, "_draw_arc_mode", "center-start-end") == "center-start-end":
                spec = arc_spec_from_center_start_end(poly[0], poly[1], poly[2])
            else:
                spec = arc_spec_from_three_points(poly[0], poly[1], poly[2])
            if spec is not None:
                meta = {"center": spec.center, "radius": spec.radius, "start_angle": spec.start_angle, "end_angle": spec.end_angle}
        elif primitive == "spline" and len(poly) >= 2:
            kind = "spline"
            meta = {"segments": 24, "closed": close, "control_points": [tuple(pt) for pt in poly], "degree": 3}

        rec = EntityRecord(
            points=list(poly), kind=kind, meta=meta, layer=self._active_layer
        )
        self._entities.append(rec)
        new_idx = len(self._entities) - 1
        if getattr(self, "_draw_construction_mode", False):
            self._entities[new_idx].construction = True

        merged_idx: int | None = None
        if (primitive in {"line", "polyline"} and not getattr(self, "_draw_construction_mode", False) and not split_happened and any(snap_type == "vertex" for snap_type in getattr(self, "_draw_point_snap_types", []))):
            merged_idx = self._try_merge_endpoints()
            if merged_idx is not None:
                new_idx = merged_idx

        self._sel.clear()
        self._sel.add(new_idx)
        self._notify()
        self._fire_poly_change()
        object.__setattr__(self, "_draw_pts", [])
        object.__setattr__(self, "_draw_point_snap_types", [])
        self._draw_constraint = None
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        if split_happened:
            if split_closed and split_open:
                self._show_flash("Regions cut + segments split", 900)
            elif split_closed:
                self._show_flash("Regions cut", 900)
            else:
                self._show_flash("Segments split", 900)
        elif merged_idx is not None and self._is_poly_closed(self._entities[new_idx].points):
            self._show_flash("Polyline closed", 800)
        elif merged_idx is not None:
            self._show_flash("Segments merged", 800)
        else:
            self._show_flash(created_flash, 800)
        self._redraw()
        return True

    def _close_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._entities[idx].points
            if len(poly) < 3 or self._is_poly_closed(poly):
                continue
            self._entities[idx].points = [*poly, poly[0]]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def add_text_at(
        self,
        wx: float,
        wy: float,
        *,
        text: str,
        family: str,
        height_mm: float,
        bold: bool = False,
        italic: bool = False,
    ) -> int:
        """Place ``text`` as grouped polyline outlines with its bottom-left
        at world (wx, wy). Returns the number of contours created."""
        from src.ui.canvas.text_shapes import text_to_polylines

        polys = text_to_polylines(
            text, family=family, height_mm=height_mm, bold=bold, italic=italic
        )
        if not polys:
            return 0
        self._push_undo()
        new_indices = self._place_text_contours(
            polys,
            wx,
            wy,
            {
                "text": text,
                "family": family,
                "height_mm": float(height_mm),
                "bold": bool(bold),
                "italic": bool(italic),
            },
        )
        self._sel = set(new_indices)
        self._show_flash(f"Text placed ({len(new_indices)} contours)", 900)
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_indices)

    def _place_text_contours(
        self,
        polys: list[list[tuple[float, float]]],
        wx: float,
        wy: float,
        params: dict[str, Any],
    ) -> list[int]:
        """Append text glyph contours at (wx, wy), grouped, each carrying
        the text parameters in meta so the text stays editable."""
        new_indices: list[int] = []
        for poly in polys:
            idx = self._append_entity([(x + wx, y + wy) for x, y in poly])
            self._entities[idx].meta = {"text_params": dict(params)}
            new_indices.append(idx)
        # Group the glyph contours so the text behaves as one object in the
        # canvas and shows as a single row in the layer tree.
        if len(new_indices) > 1:
            gid = self._next_group_id
            self._next_group_id += 1
            for idx in new_indices:
                self._entities[idx].group = gid
        return new_indices

    def text_params_at(self, idx: int) -> dict[str, Any] | None:
        if not (0 <= idx < len(self._entities)):
            return None
        params = (self._entities[idx].meta or {}).get("text_params")
        return dict(params) if isinstance(params, dict) else None

    def _text_member_indices(self, idx: int) -> list[int]:
        gid = self._entities[idx].group
        if gid is None:
            return [idx]
        return [i for i, e in enumerate(self._entities) if e.group == gid]

    def rebuild_text(self, idx: int, values: dict[str, Any]) -> bool:
        """Replace a text entity's contours with newly rendered ones (same
        bottom-left anchor)."""
        from src.ui.canvas.text_shapes import text_to_polylines

        members = self._text_member_indices(idx)
        pts = [pt for i in members for pt in self._entities[i].points]
        if not pts:
            return False
        anchor_x = min(x for x, _ in pts)
        anchor_y = min(y for _, y in pts)
        polys = text_to_polylines(
            values["text"],
            family=values["family"],
            height_mm=float(values["height_mm"]),
            bold=bool(values.get("bold", False)),
            italic=bool(values.get("italic", False)),
        )
        if not polys:
            self._show_flash("Text rendered no contours", 1000)
            return False
        self._push_undo()
        self._compact_entities(set(members))
        new_indices = self._place_text_contours(polys, anchor_x, anchor_y, values)
        self._sel = set(new_indices)
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash("Text updated", 800)
        return True

    def prompt_edit_text(self, idx: int) -> None:
        """Reopen the text dialog prefilled with an entity's parameters."""
        params = self.text_params_at(idx)
        if params is None:
            return
        from src.ui.widgets.text_dialog import AddTextDialog

        dlg = AddTextDialog(self)
        dlg.set_values(params)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            return
        self.rebuild_text(idx, vals)

    def prompt_add_text(self, wx: float, wy: float) -> None:
        """Open the Add Text dialog and place the result at world (wx, wy)."""
        from src.ui.widgets.text_dialog import AddTextDialog

        dlg = AddTextDialog(self)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            self._show_flash("No text entered", 900)
            return
        self.add_text_at(wx, wy, **vals)

    def close_selection_as_path(self) -> None:
        """Join the selected segments into one path (when several are
        selected) and close it — the context-menu "Close path" action."""
        if not self._sel:
            return
        if len(self._sel) > 1:
            self.merge_selected_segments_to_objects()
        closed = self._close_selected_polylines()
        if closed:
            self._show_flash("Path closed", 900)
        else:
            self._show_flash("Already closed", 900)

    def _open_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._entities[idx].points
            if not self._is_poly_closed(poly) or len(poly) < 2:
                continue
            self._entities[idx].points = poly[:-1]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _toggle_selected_construction(self) -> None:
        if not self._sel:
            return
        self._push_undo()
        for idx in list(self._sel):
            if 0 <= idx < len(self._entities):
                e = self._entities[idx]
                e.construction = not e.construction
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _try_merge_endpoints(self) -> int | None:
        if len(self._entities) < 2:
            return None
        survivor_idx = len(self._entities) - 1
        if len(self._entities[survivor_idx].points) < 2:
            return None

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        merged_any = False
        changed = True
        while changed:
            changed = False
            survivor = self._entities[survivor_idx].points
            if len(survivor) < 2:
                break
            survivor_start, survivor_end = survivor[0], survivor[-1]
            for i, poly in enumerate(e.points for e in self._entities):
                if i == survivor_idx or len(poly) < 2:
                    continue
                p_start, p_end = poly[0], poly[-1]
                if _eq(p_start, p_end):
                    continue
                merged: list[tuple[float, float]] | None = None
                if _eq(survivor_end, p_start):
                    merged = survivor[:-1] + poly
                elif _eq(survivor_end, p_end):
                    merged = survivor[:-1] + list(reversed(poly))
                elif _eq(survivor_start, p_end):
                    merged = poly[:-1] + survivor
                elif _eq(survivor_start, p_start):
                    merged = list(reversed(poly))[:-1] + survivor
                if merged is None:
                    continue
                popped_was_construction = self._entities[i].construction
                survivor_was_construction = self._entities[survivor_idx].construction
                self._entities[survivor_idx].points = merged
                self._entities[survivor_idx].kind = "polyline"
                self._entities[survivor_idx].meta = None
                del self._entities[i]
                if i < survivor_idx:
                    survivor_idx -= 1
                if popped_was_construction or survivor_was_construction:
                    self._entities[survivor_idx].construction = True
                merged_any = True
                changed = True
                break
        return survivor_idx if merged_any else None

    def _delete_edit_vertices(self, verts: set[tuple[int, int]]) -> int:
        if not verts:
            return 0
        grouped: dict[int, set[int]] = {}
        for pi, vi in verts:
            if self._is_locked(pi):
                continue
            poly = self._entities[pi].points
            if not (0 <= vi < len(poly)):
                continue
            if self._is_poly_closed(poly):
                if len(poly) - 1 > 3:
                    grouped.setdefault(pi, set()).add(vi)
            elif len(poly) > 3:
                grouped.setdefault(pi, set()).add(vi)
        if not grouped:
            return 0
        self._push_undo()
        deleted = 0
        for pi in sorted(grouped.keys(), reverse=True):
            if not (0 <= pi < len(self._entities)):
                continue
            poly = self._entities[pi].points
            if self._is_poly_closed(poly):
                for vi in sorted(grouped[pi], reverse=True):
                    if 0 <= vi < len(poly):
                        poly.pop(vi)
                        deleted += 1
                if len(poly) >= 4:
                    poly[-1] = poly[0]
            else:
                for vi in sorted(grouped[pi], reverse=True):
                    if 0 <= vi < len(poly):
                        poly.pop(vi)
                        deleted += 1
                poly[-1] = poly[0]
        self._edit_selected_verts.clear()
        object.__setattr__(self, "_edit_drag_targets", set())
        object.__setattr__(self, "_edit_linked_verts", set())
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return deleted

    def _prompt_offset_selected(self) -> None:
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return
        self._show_hud_prompt(
            "Offset distance (mm)", 1.0, self.offset_selected
        )

    def set_construction_mode(self, enabled: bool) -> None:
        self._draw_construction_mode = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def get_zoom_percent(self) -> int:
        if self._fit_scale < _MIN_SCALE:
            return 100
        return round(self._scale / self._fit_scale * 100)

    def get_cursor_world_pos(self) -> tuple[float, float] | None:
        if self._cursor_wx is not None and self._cursor_wy is not None:
            return (self._cursor_wx, self._cursor_wy)
        return None

    def duplicate_selected(self) -> None:
        self._duplicate_selected()

    def close_selected_polylines(self) -> int:
        return self._close_selected_polylines()

    def open_selected_polylines(self) -> int:
        return self._open_selected_polylines()

    @property
    def poly_count(self) -> int:
        return len(self._entities)

    @property
    def sel_count(self) -> int:
        return len(self._sel)

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _w2c(self, x: float, y: float) -> tuple[float, float]:
        return x * self._scale + self._ox, -y * self._scale + self._oy

    def _c2w(self, cx: float, cy: float) -> tuple[float, float]:
        scale = self._scale if abs(self._scale) >= _MIN_SCALE else _MIN_SCALE
        return (cx - self._ox) / scale, -(cy - self._oy) / scale

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        self.selectionChanged.emit(len(self._sel))

    def _update_cursor(self) -> None:
        if self._space_pan_active:
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self._space_pan_dragging
                else Qt.CursorShape.OpenHandCursor
            )
            return
        if self._measure_mode or self._mode in ("draw", "edit", "trim", "extend"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._mode == "select" and self._hover_vert is not None and self._sel:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _layer_state(self) -> tuple[tuple[str, ...], str | None]:
        return (tuple(self._layer_order), self._active_layer)

    def _push_undo(self, coalesce: str | None = None) -> None:
        """Record the pre-state of an operation (call before mutating)."""
        self._undo_store.mark(
            self._entities,
            self._sel,
            coalesce=coalesce,
            layers=self._layer_state(),
        )

    def get_entity_records(self) -> list[dict[str, Any]]:
        """Serialize entities (geometry + kind/meta/flags/group) for layer
        storage and sessions. JSON-safe: points become [x, y] lists."""
        out: list[dict[str, Any]] = []
        for e in self._entities:
            out.append(
                {
                    "points": [[float(x), float(y)] for x, y in e.points],
                    "kind": e.kind,
                    "meta": deepcopy(e.meta) if e.meta is not None else None,
                    "construction": e.construction,
                    "hidden": e.hidden,
                    "locked": e.locked,
                    "group": e.group,
                    "layer": e.layer,
                    "group_label": (
                        self._group_labels.get(e.group)
                        if e.group is not None
                        else None
                    ),
                }
            )
        return out

    def set_entity_records(
        self, records: list[dict[str, Any]], *, fit: bool = False
    ) -> None:
        """Restore entities from :meth:`get_entity_records` output."""
        ents: list[EntityRecord] = []
        max_gid = -1
        for r in records or []:
            pts = [(float(p[0]), float(p[1])) for p in r.get("points", [])]
            meta = r.get("meta")
            if isinstance(meta, dict):
                meta = _rehydrate_meta(meta)
            gid = r.get("group")
            gid = int(gid) if isinstance(gid, (int, float)) and gid is not None else None
            if gid is not None:
                max_gid = max(max_gid, gid)
            ents.append(
                EntityRecord(
                    points=pts,
                    kind=str(r.get("kind", "polyline")),
                    meta=meta,
                    construction=bool(r.get("construction", False)),
                    hidden=bool(r.get("hidden", False)),
                    locked=bool(r.get("locked", False)),
                    group=gid,
                    layer=(
                        str(r["layer"]) if r.get("layer") is not None else None
                    ),
                )
            )
        self._entities = ents
        self._group_labels = {}
        for r in records or []:
            g = r.get("group")
            lbl = r.get("group_label")
            if g is not None and lbl:
                self._group_labels[int(g)] = str(lbl)
        self._next_group_id = max(self._next_group_id, max_gid + 1)
        self._sel.clear()
        self._undo_store.clear()
        self._sync_shape_storage_from_entities()
        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()

    def get_export_dxf_state(self) -> list[dict[str, Any]]:
        self._sync_shape_storage_from_entities()
        result: list[dict[str, Any]] = []
        for idx, poly in enumerate(e.points for e in self._entities):
            if self._entities[idx].construction:
                continue
            kind = (
                self._entities[idx].kind
            )
            meta = self._entities[idx].meta
            # Only shapes the user actually named (custom label, or a named
            # group) get their own DXF layer on export — auto-generating a
            # "shape_N"/"group_N" name for every ordinary shape used to force
            # each one onto its own layer, silently fragmenting a document
            # the user had deliberately organized onto a single app layer.
            # Un-named shapes simply follow their real `layer` assignment,
            # which _export() already groups correctly.
            gid = self._entities[idx].group
            if gid is not None:
                default_name = self._group_labels.get(gid)
            else:
                default_name = (meta or {}).get("label")
            if meta is None:
                export_meta: dict[str, Any] = {}
            else:
                export_meta = deepcopy(meta)
            if default_name:
                export_meta.setdefault("name", default_name)
            if kind == "line" and len(poly) >= 2:
                export_meta["start"] = tuple(poly[0])
                export_meta["end"] = tuple(poly[-1])
            elif kind == "spline":
                export_meta["control_points"] = [tuple(pt) for pt in poly]
                export_meta.setdefault("degree", 3)
                export_meta.setdefault("closed", self._is_poly_closed(poly))
            result.append({
                "index": idx,
                "polyline": list(poly),
                "kind": kind,
                "meta": export_meta,
                "layer": self._entities[idx].layer,
            })
        return result

    # Default work-area shown before anything is drawn — without this, the
    # canvas keeps its raw __init__ scale (1 px/mm) until the first fit,
    # so an empty document's rulers show a meaningless 0-800mm span instead
    # of a plausible small work area.
    _EMPTY_BBOX = (0.0, 0.0, 100.0, 100.0)

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [pt for p in (e.points for e in self._entities) for pt in p]
        if self._img_bounds:

            bw, bh = self._img_bounds
            pts.extend([(0.0, 0.0), (bw, bh)])
        if not pts:
            return self._EMPTY_BBOX
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _poly_bounds(
        poly: list[tuple[float, float]],
    ) -> tuple[float, float, float, float]:
        if not poly:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = zip(*poly)
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _poly_rect_for_culling(
        poly: list[tuple[float, float]],
        *,
        epsilon: float = 1e-6,
    ) -> QRectF:
        """Return a non-degenerate world rect for robust viewport culling."""
        if not poly:
            return QRectF(QPointF(0.0, 0.0), QPointF(epsilon, epsilon))
        x0, y0, x1, y1 = PolylineView._poly_bounds(poly)
        if abs(x1 - x0) < epsilon:
            x0 -= epsilon
            x1 += epsilon
        if abs(y1 - y0) < epsilon:
            y0 -= epsilon
            y1 += epsilon
        return QRectF(QPointF(x0, y0), QPointF(x1, y1))

    def _selected_indices(self) -> list[int]:
        return [
            idx
            for idx in sorted(self._sel)
            if idx < len(self._entities) and not self._entities[idx].hidden
        ]

    def _mutable_selected_indices(self) -> list[int]:
        return [
            idx for idx in self._selected_indices() if not self._entities[idx].locked
        ]

    def _selection_bounds(
        self, indices: list[int] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = indices if indices is not None else self._selected_indices()
        pts = [pt for idx in items for pt in self._entities[idx].points]
        if not pts:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    def _connected_poly_indices(self, start_idx: int) -> set[int]:
        """Return polylines connected to start_idx via shared vertices."""
        if not (0 <= start_idx < len(self._entities)):
            return set()

        def _key(pt: tuple[float, float]) -> tuple[int, int]:
            return (round(pt[0] * 1_000_000), round(pt[1] * 1_000_000))

        graph: dict[int, set[int]] = {i: set() for i in range(len(self._entities))}
        point_to_polys: dict[tuple[int, int], set[int]] = {}
        for pi, poly in enumerate(e.points for e in self._entities):
            seen: set[tuple[int, int]] = set()
            for pt in poly:
                k = _key(pt)
                if k in seen:
                    continue
                seen.add(k)
                point_to_polys.setdefault(k, set()).add(pi)

        for linked in point_to_polys.values():
            if len(linked) < 2:
                continue
            idxs = list(linked)
            for i in range(len(idxs)):
                a = idxs[i]
                for j in range(i + 1, len(idxs)):
                    b = idxs[j]
                    graph[a].add(b)
                    graph[b].add(a)

        visited = {start_idx}
        stack = [start_idx]
        while stack:
            cur = stack.pop()
            for nxt in graph.get(cur, set()):
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        return visited

    def _fit_to_bounds(self, bounds: tuple[float, float, float, float]) -> None:
        w = max(self.width(), 100)
        h = max(self.height(), 100)
        x0, y0, x1, y1 = bounds
        dw = max(x1 - x0, 1e-6)
        dh = max(y1 - y0, 1e-6)
        self._scale = max(_MIN_SCALE, min(w / dw, h / dh) * 0.8)
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self._ox = w / 2 - cx * self._scale
        self._oy = h / 2 + cy * self._scale
        self._redraw()

    def _fit(self) -> None:
        self._fit_to_bounds(self._bbox())
        self._fit_scale = self._scale

    def _escape_cb(self) -> None:
        # Hard reset interaction states.
        self._dismiss_hud_prompt()
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._dismiss_shape_dim_inputs()
        self._draw_constraint = None
        self._draw_snap = None
        self._draw_snap_type = None
        self._hover_snap = None
        self._hover_snap_type = None
        self._hover_snap_multi = []
        self._angle_snap_active = False
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        self._dismiss_dim_inputs()

        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._edit_drag_moved = False
        self._edit_undo_pushed = False
        self._hover_vert = None

        self._measure_mode = False
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_locked = False
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._measure_hover_pre = None
        self._dismiss_measure_edit()

        self._end_gizmo_drag()
        self._gizmo_scale_rect = None
        self._gizmo_rotate_rect = None

        self._mode = "select"
        self._sel.clear()
        self._set_draw_sidebar_visible(False)
        self._update_cursor()
        self.modeChanged.emit("select")
        self._notify()
        self._redraw()

    def _zoom_by(self, factor: float) -> None:
        w, h = max(self.width(), 100), max(self.height(), 100)
        self._zoom_at(w / 2, h / 2, factor)

    # ── Hit testing ───────────────────────────────────────────────────────────

    @staticmethod
    def _segment_intersection_point(
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> tuple[float, float] | None:
        """Return proper segment intersection point, excluding near-endpoint overlap noise."""
        x1, y1 = a1
        x2, y2 = a2
        x3, y3 = b1
        x4, y4 = b2
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-9:
            return None

        det_a = x1 * y2 - y1 * x2
        det_b = x3 * y4 - y3 * x4
        px = (det_a * (x3 - x4) - (x1 - x2) * det_b) / denom
        py = (det_a * (y3 - y4) - (y1 - y2) * det_b) / denom

        def _within(p: float, a: float, b: float) -> bool:
            return min(a, b) - 1e-6 <= p <= max(a, b) + 1e-6

        if not (
            _within(px, x1, x2)
            and _within(py, y1, y2)
            and _within(px, x3, x4)
            and _within(py, y3, y4)
        ):
            return None

        # Ignore intersections that are effectively just a shared endpoint; those
        # are covered by endpoint snap already and otherwise make labels noisy.
        for ex, ey in (a1, a2, b1, b2):
            if math.hypot(px - ex, py - ey) < 1e-6:
                return None

        return (px, py)

    def _find_nearest_endpoint(
        self, cx: float, cy: float
    ) -> tuple[float, float] | None:
        """Find the nearest start/end point of existing polylines within snap distance.

        Used to connect new drawings to existing polyline endpoints (Fusion 360 behavior).
        """
        best_dist = _SNAP_DIST
        best_pt: tuple[float, float] | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            if len(poly) < 2:
                continue
            for pt in (poly[0], poly[-1]):
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
        return best_pt

    def _find_nearest_vertex(self, cx: float, cy: float) -> tuple[int, int] | None:
        best_dist = _VERT_HIT
        best = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            for vi, pt in enumerate(poly):
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best = (pi, vi)
        return best

    @staticmethod
    def _poly_closed_n(poly: list[tuple[float, float]]) -> int:
        """Return segment count: n if closed, n-1 if open.  0 for tiny polys."""
        n = len(poly)
        if n < 2:
            return 0
        if n >= 3 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01:
            return n
        return n - 1

    def _closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: bool = False,
    ):
        """Compute closest point on every segment of *poly* to world point (wx,wy).

        Returns the screen-distance and, depending on *return_segment*, either
        just the poly index (``int | None``) or a tuple ``(pi, seg_idx,
        closest_world_pt) | None``.  This is the single source of truth for
        all segment-distance hit-testing.
        """
        best_dist = float("inf")
        best: Any = None
        n = len(poly)
        if n < 2:
            return (None, (None, None, None)) if return_segment else None
        seg_count = self._poly_closed_n(poly)
        for vi in range(seg_count):
            ax, ay = poly[vi]
            bx, by = poly[(vi + 1) % n]
            dx, dy = bx - ax, by - ay
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-12:
                scx, scy = self._w2c(ax, ay)
                d = math.hypot(cx - scx, cy - scy)
            else:
                t = max(0.0, min(1.0, ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq))
                px, py_ = ax + t * dx, ay + t * dy
                scx, scy = self._w2c(px, py_)
                d = math.hypot(cx - scx, cy - scy)
            if d < best_dist:
                best_dist = d
                px_py = (ax + t * dx, ay + t * dy) if seg_len_sq >= 1e-12 else (ax, ay)
                if return_segment:
                    best = (vi, px_py)
                else:
                    best = vi
        if return_segment:
            return best_dist, best
        return best_dist if best is not None else None

    def _find_nearest_edge(
        self, cx: float, cy: float
    ) -> tuple[int, int, tuple[float, float]] | None:
        best_dist = _EDGE_HIT
        wx, wy = self._c2w(cx, cy)
        best: tuple[int, int, tuple[float, float]] | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            dist, result = self._closest_point_on_poly(
                poly, wx, wy, cx, cy, return_segment=True
            )
            if dist is not None and dist < best_dist and result is not None:
                best_dist = dist
                seg_idx, closest_pt = result
                best = (pi, seg_idx, closest_pt)
        return best

    def _find_poly_at(self, cx: float, cy: float) -> int | None:
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_selectable(pi):
                continue
            dist = self._closest_point_on_poly(poly, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    RULER_PX = 22

    def set_rulers_visible(self, visible: bool) -> None:
        self._rulers_visible = bool(visible)
        self._layout_draw_sidebar()
        self._redraw()

    _MOVE_SNAP_SAMPLE = 64  # max moving vertices considered per drag event

    def _entity_center(self, idx: int) -> tuple[float, float] | None:
        """An entity's true center point, if it has one: the exact
        meta-defined center for parametric shapes (circle/arc/ellipse —
        precise even for coarse tessellation or open arcs), else the
        area-weighted centroid for any other closed polygon. Returns None
        for open polylines/lines, which have no meaningful "center".
        """
        if not (0 <= idx < len(self._entities)):
            return None
        e = self._entities[idx]
        meta = e.meta
        if isinstance(meta, dict):
            center = meta.get("center")
            if isinstance(center, (tuple, list)) and len(center) == 2:
                return (float(center[0]), float(center[1]))
        poly = e.points
        if len(poly) >= 3 and self._is_poly_closed(poly):
            return _polygon_centroid(poly)
        return None

    def _moving_sample_points(self) -> list[tuple[float, float]]:
        """Sampled vertices of the selection at drag start (for object snap).

        Includes each selected shape's own CENTER (circle/arc/ellipse exact
        center, or polygon centroid) as an extra sample point — otherwise
        only the shape's rim/vertex points are tested against other shapes'
        snap targets, so dragging one circle's center onto another circle's
        center (object-centroid snapping) would never actually trigger.

        Centers are collected SEPARATELY from rim points and appended AFTER
        the rim-point subsampling below, rather than being appended to the
        same list before subsampling — a rim tessellated with >=64 points
        (e.g. the default 64-segment circle) plus one appended center
        already exceeds `_MOVE_SNAP_SAMPLE`, and the naive stride subsample
        `pts[int(i * step)]` for i in range(64) never actually lands on the
        last (center) index, silently dropping it every time. That's why
        circle-to-circle center snapping could appear to simply not work.
        """
        rim_pts: list[tuple[float, float]] = []
        center_pts: list[tuple[float, float]] = []
        for idx in self._sel:
            if 0 <= idx < len(self._entities) and not self._is_locked(idx):
                rim_pts.extend(self._entities[idx].points)
                center = self._entity_center(idx)
                if center is not None:
                    center_pts.append(center)
        if len(rim_pts) > self._MOVE_SNAP_SAMPLE:
            step = len(rim_pts) / self._MOVE_SNAP_SAMPLE
            rim_pts = [rim_pts[int(i * step)] for i in range(self._MOVE_SNAP_SAMPLE)]
        return rim_pts + center_pts

    def _static_snap_geometry(
        self, *, exclude: set[int] | None = None
    ) -> tuple[
        list[tuple[float, float]],
        list[tuple[tuple[float, float], tuple[float, float]]],
        list[tuple[float, float]],
    ]:
        """Vertices, edge segments, and shape centers of every entity NOT
        excluded — the universal snap-target set for drag/resize. Centers
        use the exact meta-defined center for circle/arc/ellipse shapes
        (so an open arc's center is still a valid target, not just closed
        polygons) or the centroid for other closed polygons. Shapes on
        non-active layers are included; only the excluded (usually the
        selection being manipulated) and hidden entities are skipped.
        """
        excluded = exclude or set()
        pts: list[tuple[float, float]] = []
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        centers: list[tuple[float, float]] = []
        for i, e in enumerate(self._entities):
            if i in excluded or e.hidden:
                continue
            poly = e.points
            pts.extend(poly)
            n = len(poly)
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            for k in range(seg_count):
                segs.append((poly[k], poly[(k + 1) % n]))
            center = self._entity_center(i)
            if center is not None:
                centers.append(center)
        if len(segs) > 4000:
            segs = []  # keep drags/resizes responsive on huge documents
        return pts, segs, centers

    _EDGE_AXIS_EPS = 1e-6

    @classmethod
    def _edge_axis_lock(cls, ax: float, ay: float, bx: float, by: float) -> str | None:
        """Which axis of a segment's "closest point" is stable/independent
        of the other axis — i.e. safe to combine with an unrelated match's
        other-axis correction during multi-touch snapping.

        A horizontal segment's Y is constant along its whole length, so
        Y is meaningful on its own (returns "y"); a vertical segment's X is
        likewise stable (returns "x"). A DIAGONAL segment's closest point
        has both X and Y depend on the projection of the incoming (mx, my),
        so neither coordinate means anything if paired with a different Y/X
        from another match — returns None ("coupled": only usable as a full
        2D touch from this same segment, never split across two matches).
        """
        if abs(by - ay) <= cls._EDGE_AXIS_EPS:
            return "y"
        if abs(bx - ax) <= cls._EDGE_AXIS_EPS:
            return "x"
        return None

    @staticmethod
    def _nearest_snap_candidate(
        mx: float,
        my: float,
        pts: list[tuple[float, float]],
        segs: list[tuple[tuple[float, float], tuple[float, float]]],
        centers: list[tuple[float, float]],
        *,
        world_r: float,
        scale: float,
        thresh: float,
    ) -> tuple[float, tuple[float, float], str, str | None] | None:
        """Best (dist_px, pos, kind, axis_lock) among vertex/midpoint/edge/
        center candidates near (mx, my). Priority: vertex > midpoint > edge >
        center.

        ``axis_lock`` is only meaningful for kind == "edge": "x"/"y" means
        only that coordinate of the returned point is stable independent of
        the other axis (vertical/horizontal segment); None means the point
        is only valid as a full 2D touch (diagonal segment) and must not be
        split across two independently-chosen matches — see
        ``_edge_axis_lock``. All other kinds are literal fixed points, so
        their axis_lock is always None but they ARE freely decomposable
        (unlike a diagonal edge's None, which means the opposite).

        Midpoint gets a small preference window (like draw-mode snapping):
        the generic "closest point on edge" is by definition always at least
        as close as the exact midpoint, so without a bias midpoint would
        only ever win at the single infinitesimal point where they tie —
        effectively unreachable with a mouse.
        """
        best_vertex: tuple[float, tuple[float, float]] | None = None
        best_midpoint: tuple[float, tuple[float, float]] | None = None
        best_edge: tuple[float, tuple[float, float], str | None] | None = None
        best_center: tuple[float, tuple[float, float]] | None = None

        for qx, qy in pts:
            if abs(qx - mx) > world_r or abs(qy - my) > world_r:
                continue
            d = math.hypot(qx - mx, qy - my) * scale
            if d <= thresh and (best_vertex is None or d < best_vertex[0]):
                best_vertex = (d, (qx, qy))

        for (ax, ay), (bx, by) in segs:
            mxm, mym = (ax + bx) / 2.0, (ay + by) / 2.0
            if abs(mxm - mx) <= world_r and abs(mym - my) <= world_r:
                d = math.hypot(mxm - mx, mym - my) * scale
                if d <= thresh and (best_midpoint is None or d < best_midpoint[0]):
                    best_midpoint = (d, (mxm, mym))
            sdx, sdy = bx - ax, by - ay
            seg_len_sq = sdx * sdx + sdy * sdy
            if seg_len_sq < 1e-12:
                continue
            t = max(0.0, min(1.0, ((mx - ax) * sdx + (my - ay) * sdy) / seg_len_sq))
            cxp, cyp = ax + t * sdx, ay + t * sdy
            if abs(cxp - mx) > world_r or abs(cyp - my) > world_r:
                continue
            d = math.hypot(cxp - mx, cyp - my) * scale
            if d <= thresh and (best_edge is None or d < best_edge[0]):
                lock = PolylineView._edge_axis_lock(ax, ay, bx, by)
                best_edge = (d, (cxp, cyp), lock)

        for cx_, cy_ in centers:
            if abs(cx_ - mx) > world_r or abs(cy_ - my) > world_r:
                continue
            d = math.hypot(cx_ - mx, cy_ - my) * scale
            if d <= thresh and (best_center is None or d < best_center[0]):
                best_center = (d, (cx_, cy_))

        if best_vertex is not None:
            return (best_vertex[0], best_vertex[1], "vertex", None)

        others: list[tuple[float, tuple[float, float], str, str | None]] = []
        if best_edge is not None:
            others.append((best_edge[0], best_edge[1], "edge", best_edge[2]))
        if best_center is not None:
            others.append((best_center[0], best_center[1], "center", None))

        MIDPOINT_BIAS = 4.0
        if best_midpoint is not None:
            other_best = min((d for d, _, _, _ in others), default=None)
            if other_best is None or best_midpoint[0] <= other_best + MIDPOINT_BIAS:
                return (best_midpoint[0], best_midpoint[1], "midpoint", None)
            others.append((best_midpoint[0], best_midpoint[1], "midpoint", None))

        if not others:
            return None
        return min(others, key=lambda c: c[0])

    def _object_snap_adjust(
        self, dx: float, dy: float
    ) -> (
        tuple[float, float, list[tuple[tuple[float, float], str, tuple[float, float]]]]
        | None
    ):
        """Snap for a whole-selection drag, allowing MULTIPLE simultaneous
        touches — e.g. the shape's bottom can be touching one thing while
        its right side independently touches something else, and both
        should be visible, not just whichever is closest overall.

        Every candidate considered here is a genuine 2D-proximity match
        (full distance to a real vertex/midpoint/edge/center/grid-point/
        guide is within the snap threshold) — unlike the old "smart guide"
        approach, a feature can never qualify just because it happens to
        share an X or Y coordinate from far away. Among all the qualifying
        touches (one candidate per moved point), the closest X-correcting
        one and the closest Y-correcting one are applied independently —
        which is what lets two different real touches (e.g. bottom edge to
        one shape, right edge to another) both take effect at once.

        Returns (adj_dx, adj_dy, indicators) — indicators has zero, one, or
        two (target_point, kind, dragged_point) entries (deduplicated when
        the same match supplies both axes) for the caller to render.
        """
        pts = self._move_start_pts
        if not pts:
            return None
        scale = max(self._scale, _MIN_SCALE)
        thresh = _SNAP_DIST
        world_r = thresh / scale

        static_pts, static_segs, static_centers = self._static_snap_geometry(
            exclude=self._sel
        )

        # Every entry is a genuinely-nearby (real 2D distance <= thresh)
        # touch: (d_screen, adj_dx, adj_dy, target_point, origin_point, kind).
        # adj_dx/adj_dy is None when that match doesn't actually constrain
        # that axis (a horizontal guide only constrains Y, for instance) —
        # using 0.0 as a placeholder there would make guides look like the
        # "best" (smallest) possible X-correction and wrongly win every time.
        #
        # A diagonal edge's "closest point" is only valid as a FULL 2D touch
        # (both dx and dy from the same match) — its X and Y both depend on
        # the projection of the incoming point, so pairing just one of its
        # coordinates with a different match's other axis lands nowhere near
        # the edge. Such matches go into `coupled_matches` instead of
        # `matches`, and are only used (as a whole) when nothing axis-safe
        # (vertex/midpoint/center/grid/guide/horizontal-or-vertical edge) was
        # found for EITHER axis.
        matches: list[
            tuple[
                float,
                float | None,
                float | None,
                tuple[float, float],
                tuple[float, float],
                str,
            ]
        ] = []
        coupled_matches: list[
            tuple[
                float,
                float,
                float,
                tuple[float, float],
                tuple[float, float],
                str,
            ]
        ] = []

        for px, py in pts:
            mx, my = px + dx, py + dy
            # NOTE: `origin` here is the point's CURRENT (raw-dragged, i.e.
            # (px, py) + dx/dy) position, NOT its drag-start position — the
            # final dragged_pt computed below is `origin + adj_dx/adj_dy`,
            # so using drag-start (px, py) instead would silently drop the
            # raw drag delta and show the indicator ring in the wrong place
            # (this was a real bug: fixed by using (mx, my) here).
            origin = (mx, my)

            candidate = self._nearest_snap_candidate(
                mx,
                my,
                static_pts,
                static_segs,
                static_centers,
                world_r=world_r,
                scale=scale,
                thresh=thresh,
            )
            if candidate is not None:
                d_screen, (qx, qy), kind, axis_lock = candidate
                if kind == "edge" and axis_lock is None:
                    coupled_matches.append((
                        d_screen,
                        qx - mx,
                        qy - my,
                        (qx, qy),
                        origin,
                        kind,
                    ))
                elif kind == "edge" and axis_lock == "x":
                    matches.append((d_screen, qx - mx, None, (qx, qy), origin, kind))
                elif kind == "edge" and axis_lock == "y":
                    matches.append((d_screen, None, qy - my, (qx, qy), origin, kind))
                else:
                    matches.append((d_screen, qx - mx, qy - my, (qx, qy), origin, kind))

            if self._grid_snap:
                gx = round(mx / self._grid_spacing) * self._grid_spacing
                gy = round(my / self._grid_spacing) * self._grid_spacing
                d = math.hypot(gx - mx, gy - my) * scale
                if d <= thresh:
                    matches.append((d, gx - mx, gy - my, (gx, gy), origin, "grid"))

            for orient, coord in self._guides:
                if orient == "v":
                    d = abs(coord - mx) * scale
                    if d <= thresh:
                        matches.append((
                            d,
                            coord - mx,
                            None,
                            (coord, my),
                            origin,
                            "guide",
                        ))
                else:
                    d = abs(coord - my) * scale
                    if d <= thresh:
                        matches.append((
                            d,
                            None,
                            coord - my,
                            (mx, coord),
                            origin,
                            "guide",
                        ))

        if not matches and not coupled_matches:
            return None

        x_candidates = [m for m in matches if m[1] is not None]
        y_candidates = [m for m in matches if m[2] is not None]

        if not x_candidates and not y_candidates:
            if not coupled_matches:
                return None
            # Nothing axis-safe nearby at all — the only thing to snap to is
            # a diagonal edge, so use it as a single full 2D touch (both axes
            # from the same match), same as the classic single-nearest-point
            # snap. Do NOT mix it in below: it must never supply just one of
            # its two coordinates alongside an unrelated match's other axis.
            d_screen, cdx, cdy, target, origin, kind = min(
                coupled_matches, key=lambda m: m[0]
            )
            dragged_pt = (origin[0] + cdx, origin[1] + cdy)
            return cdx, cdy, [(target, kind, dragged_pt)]

        best_x = (
            min(x_candidates, key=lambda m: abs(m[1] or 0.0)) if x_candidates else None
        )
        best_y = (
            min(y_candidates, key=lambda m: abs(m[2] or 0.0)) if y_candidates else None
        )
        adj_dx: float = (
            best_x[1] if best_x is not None and best_x[1] is not None else 0.0
        )
        adj_dy: float = (
            best_y[2] if best_y is not None and best_y[2] is not None else 0.0
        )

        indicators: list[tuple[tuple[float, float], str, tuple[float, float]]] = []
        seen_targets: set[tuple[float, float]] = set()
        for m in (best_x, best_y):
            if m is None:
                continue
            _, _, _, target, origin, kind = m
            if target in seen_targets:
                continue
            seen_targets.add(target)
            dragged_pt = (origin[0] + adj_dx, origin[1] + adj_dy)
            indicators.append((target, kind, dragged_pt))
        return adj_dx, adj_dy, indicators

    def _resize_handle_snap_adjust(
        self, wx: float, wy: float
    ) -> tuple[float, float, str] | None:
        """Snap a dragged resize-handle position to nearby vertex/midpoint/
        edge/center of other shapes (any layer), plus grid/guides — mirrors
        the move-drag snap behavior so resizing feels consistent."""
        scale = max(self._scale, _MIN_SCALE)
        thresh = _SNAP_DIST
        world_r = thresh / scale
        static_pts, static_segs, static_centers = self._static_snap_geometry(
            exclude=self._sel
        )
        candidate = self._nearest_snap_candidate(
            wx,
            wy,
            static_pts,
            static_segs,
            static_centers,
            world_r=world_r,
            scale=scale,
            thresh=thresh,
        )
        # A resize handle only ever moves as a single full 2D point, so the
        # edge axis-lock distinction (only relevant for splitting a match
        # across two independently-chosen touches) doesn't apply here.
        best: tuple[float, tuple[float, float], str] | None = (
            (candidate[0], candidate[1], candidate[2])
            if candidate is not None
            else None
        )
        if self._grid_snap:
            gx = round(wx / self._grid_spacing) * self._grid_spacing
            gy = round(wy / self._grid_spacing) * self._grid_spacing
            d = math.hypot(gx - wx, gy - wy) * scale
            if d <= thresh and (best is None or d < best[0]):
                best = (d, (gx, gy), "grid")
        for orient, coord in self._guides:
            if orient == "v":
                d = abs(coord - wx) * scale
                if d <= thresh and (best is None or d < best[0]):
                    best = (d, (coord, wy), "guide")
            else:
                d = abs(coord - wy) * scale
                if d <= thresh and (best is None or d < best[0]):
                    best = (d, (wx, coord), "guide")
        if best is None:
            return None
        _, (sx, sy), kind = best
        return sx, sy, kind

    def _find_guide_at(self, cx: float, cy: float) -> int | None:
        """Guide index within grab distance of the cursor (screen px)."""
        best: int | None = None
        best_d = 6.0
        for i, (orient, coord) in enumerate(self._guides):
            if orient == "v":
                gx, _ = self._w2c(coord, 0.0)
                d = abs(cx - gx)
            else:
                _, gy = self._w2c(0.0, coord)
                d = abs(cy - gy)
            if d < best_d:
                best_d = d
                best = i
        return best

    def _find_inactive_poly_at(self, cx: float, cy: float) -> int | None:
        """Hit-test entities on non-active layers; returns entity index."""
        if self._active_layer is None:
            return None
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, e in enumerate(self._entities):
            if e.hidden or self._on_active_layer(e):
                continue
            dist = self._closest_point_on_poly(e.points, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    def _find_ghost_poly_at(self, cx: float, cy: float) -> int | None:
        """Hit-test the ghost overlay polys; returns ghost-list index or None."""
        if not self._ghost_polys or not self._ghost_visible:
            return None
        best_dist = 8.0
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        for pi, poly in enumerate(self._ghost_polys):
            dist = self._closest_point_on_poly(poly, wx, wy, cx, cy)
            if dist is not None and dist < best_dist:
                best_dist = dist
                best = pi
        return best

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        """Bridge Qt paint dispatch to CanvasRenderer mixin implementation."""
        CanvasRenderer.paintEvent(self, event)
        tool = (
            self._measure_tool if self._measure_mode else self._tools.get(self._mode)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if tool is not None:
            tool.paint_overlay(painter)
        self._paint_chrome_rulers(painter)
        painter.end()

    _HANDLE_ANCHORS = {
        # handle → (anchor position, handle position) as bbox fractions
        "nw": ((1.0, 0.0), (0.0, 1.0)),
        "n": ((0.5, 0.0), (0.5, 1.0)),
        "ne": ((0.0, 0.0), (1.0, 1.0)),
        "e": ((0.0, 0.5), (1.0, 0.5)),
        "se": ((0.0, 1.0), (1.0, 0.0)),
        "s": ((0.5, 1.0), (0.5, 0.0)),
        "sw": ((1.0, 1.0), (0.0, 0.0)),
        "w": ((1.0, 0.5), (0.0, 0.5)),
    }

    def _start_gizmo_drag(
        self, mode: str, wx: float, wy: float, *, from_center: bool = False
    ) -> bool:
        bounds = self._selection_bounds()
        if bounds is None or not self._sel:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        if mode.startswith("scale-"):
            frac_a, frac_h = self._HANDLE_ANCHORS[mode[6:]]
            if from_center:
                frac_a = (0.5, 0.5)
            x0, y0, x1, y1 = bounds
            self._gizmo_anchor_w = (
                x0 + (x1 - x0) * frac_a[0],
                y0 + (y1 - y0) * frac_a[1],
            )
            self._gizmo_handle_w = (
                x0 + (x1 - x0) * frac_h[0],
                y0 + (y1 - y0) * frac_h[1],
            )
        else:
            vec = (wx - cx, wy - cy)
            if math.hypot(vec[0], vec[1]) < 1e-9:
                return False
            self._gizmo_start_vec = vec
        self._gizmo_drag_mode = mode
        self._gizmo_center_w = (cx, cy)
        self._gizmo_snapshot = {
            idx: list(self._entities[idx].points) for idx in self._mutable_selected_indices()
        }

        def _meta_copy(idx: int) -> dict[str, Any] | None:
            meta = self._entities[idx].meta
            return dict(meta) if isinstance(meta, dict) else None

        self._gizmo_meta_snapshot = {
            idx: _meta_copy(idx) for idx in self._mutable_selected_indices()
        }
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        return bool(self._gizmo_snapshot)

    def _apply_handle_scale(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        """Resize the selection by dragging a frame handle. Corners resize
        X and Y independently (Shift = keep aspect), edges scale one axis;
        holding Alt at press scales from the center."""
        if self._gizmo_anchor_w is None or self._gizmo_handle_w is None:
            return
        handle = (self._gizmo_drag_mode or "")[6:]
        ax, ay = self._gizmo_anchor_w
        hx, hy = self._gizmo_handle_w

        if mods is None:
            mods = QApplication.keyboardModifiers()

        # Snap the dragged handle itself to nearby vertex/midpoint/edge/
        # center of other shapes (any layer) plus grid/guides — mirrors
        # move-drag snapping so resize feels consistent. Alt disables it.
        allow_snap = not bool(mods & Qt.KeyboardModifier.AltModifier)
        snap_result = self._resize_handle_snap_adjust(wx, wy) if allow_snap else None
        if snap_result is not None:
            wx, wy, snap_type = snap_result
            self._hover_snap = (wx, wy)
            self._hover_snap_type = snap_type
        else:
            self._hover_snap = None
            self._hover_snap_type = None

        def _factor(cur: float, a: float, h: float) -> float:
            span = h - a
            if abs(span) < 1e-9:
                return 1.0
            f = (cur - a) / span
            # Clamp magnitude only — preserve sign so dragging a handle past
            # the opposite edge flips the shape (mirrors it) instead of
            # getting stuck at a minimum positive scale.
            if abs(f) < 0.05:
                f = 0.05 if f >= 0.0 else -0.05
            return max(-20.0, min(20.0, f))

        sx = _factor(wx, ax, hx)
        sy = _factor(wy, ay, hy)
        if handle in ("n", "s"):
            sx = 1.0
        elif handle in ("e", "w"):
            sy = 1.0
        else:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                # Shift = keep aspect (uniform, dominant axis wins)
                s = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
                sx = sy = s
        if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
            self._gizmo_drag_moved = True
        if not self._gizmo_undo_pushed:
            self._push_undo()
            self._gizmo_undo_pushed = True
        for idx, src_poly in self._gizmo_snapshot.items():
            self._entities[idx].points = [
                (ax + (x - ax) * sx, ay + (y - ay) * sy) for x, y in src_poly
            ]
            # Keep parametric meta (circle/ellipse/rectangle "center") in
            # sync with the resized points — otherwise centroid-based snap
            # targets stay stale at the shape's PRE-resize position, since
            # `_entity_center()` reads meta["center"] directly rather than
            # recomputing it from `.points`. Always derive from the drag-
            # start snapshot (never the live/already-updated meta) so
            # repeated mouse-move events don't compound the transform.
            snap_meta = self._gizmo_meta_snapshot.get(idx)
            if isinstance(snap_meta, dict) and isinstance(
                snap_meta.get("center"), (tuple, list)
            ):
                cx0, cy0 = snap_meta["center"][0], snap_meta["center"][1]
                new_meta = dict(snap_meta)
                new_meta["center"] = (
                    ax + (float(cx0) - ax) * sx,
                    ay + (float(cy0) - ay) * sy,
                )
                self._entities[idx].meta = new_meta

    def _apply_gizmo_drag(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        if self._gizmo_drag_mode is None or not self._gizmo_snapshot:
            return
        if self._gizmo_drag_mode.startswith("scale-"):
            self._apply_handle_scale(wx, wy, mods)
            return
        if self._gizmo_center_w is None or self._gizmo_start_vec is None:
            return

        if not self._gizmo_undo_pushed:
            self._push_undo()
            self._gizmo_undo_pushed = True

        cx, cy = self._gizmo_center_w
        start_vx, start_vy = self._gizmo_start_vec
        cur_vx, cur_vy = wx - cx, wy - cy

        scale = 1.0
        angle = 0.0
        if self._gizmo_drag_mode == "scale":
            start_d = math.hypot(start_vx, start_vy)
            cur_d = math.hypot(cur_vx, cur_vy)
            if start_d > 1e-9:
                scale = max(0.05, min(20.0, cur_d / start_d))
            if abs(scale - 1.0) > 1e-4:
                self._gizmo_drag_moved = True
        elif self._gizmo_drag_mode == "rotate":
            start_a = math.atan2(start_vy, start_vx)
            cur_a = math.atan2(cur_vy, cur_vx)
            angle = cur_a - start_a
            if abs(angle) > math.radians(0.2):
                self._gizmo_drag_moved = True

        ca, sa = math.cos(angle), math.sin(angle)
        for idx, src_poly in self._gizmo_snapshot.items():
            out_poly: list[tuple[float, float]] = []
            for x, y in src_poly:
                sx = cx + (x - cx) * scale
                sy = cy + (y - cy) * scale
                rx = cx + (sx - cx) * ca - (sy - cy) * sa
                ry = cy + (sx - cx) * sa + (sy - cy) * ca
                out_poly.append((rx, ry))
            self._entities[idx].points = out_poly
            # Same staleness fix as _apply_handle_scale: recompute meta["center"]
            # under the identical scale+rotate transform, from the drag-start
            # snapshot, so circle/ellipse centroid snapping stays accurate
            # after a uniform corner-scale or rotate gizmo drag too.
            snap_meta = self._gizmo_meta_snapshot.get(idx)
            if isinstance(snap_meta, dict) and isinstance(
                snap_meta.get("center"), (tuple, list)
            ):
                ecx0, ecy0 = (
                    float(snap_meta["center"][0]),
                    float(snap_meta["center"][1]),
                )
                scx = cx + (ecx0 - cx) * scale
                scy = cy + (ecy0 - cy) * scale
                rcx = cx + (scx - cx) * ca - (scy - cy) * sa
                rcy = cy + (scx - cx) * sa + (scy - cy) * ca
                new_meta = dict(snap_meta)
                new_meta["center"] = (rcx, rcy)
                self._entities[idx].meta = new_meta

    def _end_gizmo_drag(self) -> bool:
        moved = self._gizmo_drag_moved
        self._gizmo_drag_mode = None
        self._gizmo_center_w = None
        self._gizmo_start_vec = None
        self._gizmo_anchor_w = None
        self._gizmo_handle_w = None
        self._gizmo_snapshot = {}
        self._gizmo_meta_snapshot = {}
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        self._hover_snap = None
        self._hover_snap_type = None
        return moved

    def eventFilter(self, obj, event) -> bool:
        """Intercept Tab/Backtab on the draw-mode dim-input QLineEdits."""
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                reverse = key == Qt.Key.Key_Backtab
                if (
                    self._draw_shape_w_edit is not None
                    and self._draw_shape_h_edit is not None
                    and (
                        obj is self._draw_shape_w_edit or obj is self._draw_shape_h_edit
                    )
                ):
                    if (obj is self._draw_shape_w_edit and not reverse) or (
                        obj is self._draw_shape_h_edit and reverse
                    ):
                        self._draw_shape_h_edit.setFocus()
                        self._draw_shape_h_edit.selectAll()
                    else:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                    return True
                if (
                    self._dim_distance_edit is not None
                    and self._dim_angle_edit is not None
                    and (obj is self._dim_distance_edit or obj is self._dim_angle_edit)
                ):
                    # Focus + select only: dirty is driven by textEdited, so the
                    # live value keeps tracking the cursor until the user types.
                    if (obj is self._dim_distance_edit and not reverse) or (
                        obj is self._dim_angle_edit and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                    elif (obj is self._dim_angle_edit and not reverse) or (
                        obj is self._dim_distance_edit and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                    return True  # Consume the Tab — prevent Qt focus chain
        return super().eventFilter(obj, event)

    def focusNextPrevChild(self, next: bool) -> bool:
        """Keep Tab/Shift+Tab inside canvas interaction workflows."""
        if self._selectable and self._mode in {"select", "draw", "edit"}:
            return False
        return super().focusNextPrevChild(next)

    def _show_shape_dim_inputs(self) -> None:
        if (
            not self._shape_primitive_active()
            or self._draw_shape_anchor_w is None
            or self._draw_shape_cursor_w is None
            or self._draw_shape_w_edit is not None
            or self._draw_shape_h_edit is not None
        ):
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        w = abs(ex - sx)
        h = abs(ey - sy)
        cx, cy = self._w2c((sx + ex) / 2.0, (sy + ey) / 2.0)

        w_edit = self._make_hud_edit(
            width=86, height=24, align=Qt.AlignmentFlag.AlignCenter
        )
        w_edit.setText(f"{w:.2f}")
        w_edit.setProperty("shape_hud_temp", True)
        w_edit.move(int(cx + 16), int(cy + 12))
        w_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        h_edit = self._make_hud_edit(
            width=86, height=24, align=Qt.AlignmentFlag.AlignCenter
        )
        h_edit.setText(f"{h:.2f}")
        h_edit.setProperty("shape_hud_temp", True)
        h_edit.move(int(cx + 16), int(cy + 40))
        h_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        self._draw_shape_w_edit = w_edit
        self._draw_shape_h_edit = h_edit
        self._draw_shape_w_edit.setFocus()
        self._draw_shape_w_edit.selectAll()

    def _dismiss_shape_dim_inputs(self) -> None:
        for edit in (self._draw_shape_w_edit, self._draw_shape_h_edit):
            if edit is None:
                continue
            if bool(edit.property("shape_hud_temp")):
                edit.hide()
                edit.deleteLater()
        if self._draw_shape_w_edit is not None and bool(
            self._draw_shape_w_edit.property("shape_hud_temp")
        ):
            self._draw_shape_w_edit = None
        if self._draw_shape_h_edit is not None and bool(
            self._draw_shape_h_edit.property("shape_hud_temp")
        ):
            self._draw_shape_h_edit = None

    def _apply_and_commit_shape_preview(self) -> None:
        if not (self._mode == "draw" and self._draw_shape_preview_active):
            return
        self._apply_shape_size_inputs()
        self._dismiss_shape_dim_inputs()
        self._commit_shape_preview()

    def _hit_measure_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._mbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    # ── Events ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_draw_sidebar()
        if self._needs_fit:
            if self._entities:
                self._needs_fit = False
            self._fit()
        else:
            self._redraw()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        shift_mod = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = True
            self._space_pan_dragging = False
            self._update_cursor()
            event.accept()
            return

        # Tool-specific keys (e.g. quick-shape letters) beat the registry.
        _tool = self._tools.get(self._mode)
        if _tool is not None and _tool.key(event):
            event.accept()
            return

        # Arrow key nudge
        if (
            self._selectable
            and self._sel
            and key
            in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            amount = 1.0 if shift_mod else 0.1
            dx, dy = 0.0, 0.0
            if key == Qt.Key.Key_Left:
                dx = -amount
            elif key == Qt.Key.Key_Right:
                dx = amount
            elif key == Qt.Key.Key_Up:
                dy = amount
            elif key == Qt.Key.Key_Down:
                dy = -amount
            self._nudge_selected(dx, dy)
            return

        if key == Qt.Key.Key_Escape:
            self._dismiss_hud_prompt()
            fw = QApplication.focusWidget()
            if isinstance(fw, QLineEdit) and bool(fw.property("shape_hud_temp")):
                self._dismiss_shape_dim_inputs()
            if blur_focused_line_edit(self, within=self):
                return
            # If a dim field has focus or is dirty, blur and reset it first
            has_dim_focus = (
                self._dim_distance_edit is not None
                and self._dim_distance_edit.hasFocus()
            ) or (self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus())
            if has_dim_focus or self._dim_distance_dirty or self._dim_angle_dirty:
                self._dim_distance_dirty = False
                self._dim_angle_dirty = False
                self.setFocus()  # return focus to canvas
                return
            # In select mode, Escape clears selection
            if self._mode == "select" and self._sel:
                self.deselect_all()
                return
            self._escape_cb()
            return

        if self._selectable:
            # B. Dimension HUD key interception — digits/period/minus go to distance field
            if self._dim_distance_edit is not None and key in (
                Qt.Key.Key_0,
                Qt.Key.Key_1,
                Qt.Key.Key_2,
                Qt.Key.Key_3,
                Qt.Key.Key_4,
                Qt.Key.Key_5,
                Qt.Key.Key_6,
                Qt.Key.Key_7,
                Qt.Key.Key_8,
                Qt.Key.Key_9,
                Qt.Key.Key_Period,
                Qt.Key.Key_Minus,
            ):
                # Determine which field to target
                target = self._dim_distance_edit
                if self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus():
                    target = self._dim_angle_edit
                    if not self._dim_angle_dirty:
                        target.clear()
                        self._dim_angle_dirty = True
                else:
                    if not self._dim_distance_dirty:
                        target.clear()
                        self._dim_distance_dirty = True
                    target.setFocus()
                # Insert the character
                target.insert(event.text())
                event.accept()
                return
            if key == Qt.Key.Key_Backspace:
                # If a dim field is focused and dirty, let backspace work on the field
                if (
                    self._dim_distance_edit is not None
                    and self._dim_distance_dirty
                    and self._dim_distance_edit.hasFocus()
                ):
                    self._dim_distance_edit.backspace()
                    if not self._dim_distance_edit.text():
                        self._dim_distance_dirty = False
                    event.accept()
                    return
                if (
                    self._dim_angle_edit is not None
                    and self._dim_angle_dirty
                    and self._dim_angle_edit.hasFocus()
                ):
                    self._dim_angle_edit.backspace()
                    if not self._dim_angle_edit.text():
                        self._dim_angle_dirty = False
                        event.accept()
                        return
                self._key_backspace()
                return
            if (
                key == Qt.Key.Key_A
                and self._mode == "draw"
                and self._draw_pts
                and not self._shape_primitive_active()
            ):
                # Quick-focus the angle field so the next segment can be typed.
                if self._dim_angle_edit is None:
                    self._show_dim_inputs()
                if self._dim_angle_edit is not None:
                    self._dim_angle_edit.setFocus()
                    self._dim_angle_edit.selectAll()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if (
                    self._mode == "draw"
                    and self._draw_shape_preview_active
                    and self._shape_primitive_active()
                ):
                    if (
                        self._draw_shape_w_edit is not None
                        and self._draw_shape_w_edit.hasFocus()
                    ) or (
                        self._draw_shape_h_edit is not None
                        and self._draw_shape_h_edit.hasFocus()
                    ):
                        self._apply_and_commit_shape_preview()
                    else:
                        self._dismiss_shape_dim_inputs()
                        self._commit_shape_preview()
                    return
                # If dim inputs are dirty, apply them; otherwise finish draw
                if self._dim_distance_dirty or self._dim_angle_dirty:
                    self._apply_dim_input()
                else:
                    self._finish_draw()
                return
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                reverse = key == Qt.Key.Key_Backtab
                if self._mode == "select" and self._sel:
                    # Tab cycles through the available selection badges
                    # (W, H, and for a single line also L and ∠).
                    axes = self._sel_badge_axes()
                    if axes:
                        if self._sel_dim_edit is None:
                            axis, rect = axes[-1] if reverse else axes[0]
                            self._show_sel_dim_editor(axis, rect)
                        else:
                            cur = self._sel_dim_axis
                            self._apply_sel_dim_editor()
                            axes = self._sel_badge_axes()
                            if axes:
                                names = [a for a, _ in axes]
                                pos_i = names.index(cur) if cur in names else -1
                                step = -1 if reverse else 1
                                axis, rect = axes[(pos_i + step) % len(axes)]
                                self._show_sel_dim_editor(axis, rect)
                    event.accept()
                    return
                if (
                    self._mode == "draw"
                    and not self._shape_primitive_active()
                    and self._draw_pts
                    and (
                        self._dim_distance_edit is None or self._dim_angle_edit is None
                    )
                ):
                    self._show_dim_inputs()
                if (
                    self._mode == "draw"
                    and self._shape_primitive_active()
                    and self._draw_shape_preview_active
                ):
                    if (
                        self._draw_shape_w_edit is None
                        or self._draw_shape_h_edit is None
                    ):
                        self._show_shape_dim_inputs()
                    if (
                        self._draw_shape_w_edit is None
                        or self._draw_shape_h_edit is None
                    ):
                        event.accept()
                        return
                    if (self._draw_shape_w_edit.hasFocus() and not reverse) or (
                        self._draw_shape_h_edit.hasFocus() and reverse
                    ):
                        self._draw_shape_h_edit.setFocus()
                        self._draw_shape_h_edit.selectAll()
                    else:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                    event.accept()
                    return
                # Tab cycles focus between distance and angle fields
                if (
                    self._dim_distance_edit is not None
                    and self._dim_angle_edit is not None
                ):
                    # Focus + select only — dirty is set by textEdited when the
                    # user actually types, so the value keeps live-updating and
                    # the first keystroke replaces it.
                    if (self._dim_distance_edit.hasFocus() and not reverse) or (
                        self._dim_angle_edit.hasFocus() and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                    elif (self._dim_angle_edit.hasFocus() and not reverse) or (
                        self._dim_distance_edit.hasFocus() and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                    else:
                        # Neither field has focus — give focus to distance
                        # (Shift+Tab goes straight to angle)
                        if reverse:
                            self._dim_angle_edit.setFocus()
                            self._dim_angle_edit.selectAll()
                        else:
                            self._dim_distance_edit.setFocus()
                            self._dim_distance_edit.selectAll()
                event.accept()
                return
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.accept()
            return

        # Declarative command shortcuts — see src/ui/canvas/commands.py.
        cmd = canvas_commands.match_key(key, mods)
        if cmd is not None and canvas_commands.can_run(self, cmd):
            cmd.run(self)
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = False
            self._space_pan_dragging = False
            self._lmb_prev = None
            self._update_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return
        # Ctrl+wheel = fine zoom for precision work.
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        span = 0.02 if fine else 0.1
        factor = max(1.0 - span, min(1.0 + span, 1.0 + delta * 0.0007))
        pos = event.position()
        self._zoom_at(pos.x(), pos.y(), factor)
        event.accept()

    def _zoom_at(self, cx: float, cy: float, factor: float) -> None:
        """Zoom about a canvas point, clamped to sane bounds."""
        wx, wy = self._c2w(cx, cy)
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        self._ox = cx - wx * self._scale
        self._oy = cy + wy * self._scale
        self._redraw()

    def event(self, ev) -> bool:
        # macOS trackpad pinch zoom.
        if ev.type() == QEvent.Type.NativeGesture:
            if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                pos = ev.position()
                self._zoom_at(pos.x(), pos.y(), 1.0 + float(ev.value()))
                return True
        return super().event(ev)

    def set_zoom_percent(self, percent: float) -> None:
        """Set zoom relative to the fit scale, anchored at the view center."""
        if self._fit_scale < _MIN_SCALE or percent <= 0:
            return
        target = self._fit_scale * percent / 100.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        self._animate_view_to(target, cx, cy)

    def _animate_view_to(self, target_scale: float, cx: float, cy: float) -> None:
        """Animate to a new scale anchored at a canvas point (instant when
        the widget is not visible, e.g. headless tests)."""
        target_scale = max(_MIN_SCALE, min(_MAX_SCALE, target_scale))
        wx, wy = self._c2w(cx, cy)
        end_ox = cx - wx * target_scale
        end_oy = cy + wy * target_scale
        if not self.isVisible():
            self._scale, self._ox, self._oy = target_scale, end_ox, end_oy
            self._redraw()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(140)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        s0, ox0, oy0 = self._scale, self._ox, self._oy

        def _step(t: float) -> None:
            self._scale = s0 + (target_scale - s0) * t
            self._ox = ox0 + (end_ox - ox0) * t
            self._oy = oy0 + (end_oy - oy0) * t
            self._redraw()

        anim.valueChanged.connect(_step)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        btn = event.button()

        if btn == Qt.MouseButton.MiddleButton:
            self._mmb_prev = pos
            return

        if btn == Qt.MouseButton.LeftButton and self._space_pan_active:
            self._space_pan_dragging = True
            self._lmb_prev = pos
            self._update_cursor()
            return

        if btn == Qt.MouseButton.RightButton:
            if self._selectable:
                self._rightclick_cb(pos.x(), pos.y())
            return

        if btn != Qt.MouseButton.LeftButton:
            return

        if self._hit_measure_button(pos.x(), pos.y()):
            self.toggle_measure()
            return

        # Rulers: press inside a ruler strip drags out a new guide.
        if self._rulers_visible and self._selectable:
            r = self.RULER_PX
            wx0, wy0 = self._c2w(pos.x(), pos.y())
            if pos.x() <= r and pos.y() <= r:
                return  # corner box
            if pos.y() <= r:
                self._guides.append(("h", wy0))
                self._guide_drag = len(self._guides) - 1
                self._selected_guide = self._guide_drag
                self._guide_drag_moved = False
                self._redraw()
                return
            if pos.x() <= r:
                self._guides.append(("v", wx0))
                self._guide_drag = len(self._guides) - 1
                self._selected_guide = self._guide_drag
                self._guide_drag_moved = False
                self._redraw()
                return
        # Grab an existing guide (only when not over a shape) — click selects
        # it (Delete/Backspace removes it); dragging moves it.
        if (
            self._selectable
            and self._mode == "select"
            and self._guides
            and self._find_poly_at(pos.x(), pos.y()) is None
        ):
            gi = self._find_guide_at(pos.x(), pos.y())
            if gi is not None:
                self._guide_drag = gi
                self._selected_guide = gi
                self._guide_drag_moved = False
                self._redraw()
                return
        # Clicking elsewhere clears any selected guide.
        if self._selected_guide is not None:
            self._selected_guide = None
            self._redraw()

        # Selection badges / transform gizmo take priority over tools.
        if (
            self._mode == "select"
            and self._sel
            and self._tools["select"].press_overlays(event)
        ):
            return

        if self._measure_mode:
            self._measure_tool.press(event)
            return

        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.press(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        wx, wy = self._c2w(pos.x(), pos.y())
        self._cursor_wx = wx
        self._cursor_wy = wy
        self._hover_snap = None
        self._hover_snap_type = None
        self._hover_snap_multi = []

        if self._mmb_prev is not None and event.buttons() & Qt.MouseButton.MiddleButton:
            self._ox += pos.x() - self._mmb_prev.x()
            self._oy += pos.y() - self._mmb_prev.y()
            self._mmb_prev = pos
            self._redraw()
            return

        if (
            self._space_pan_active
            and self._lmb_prev is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._ox += pos.x() - self._lmb_prev.x()
            self._oy += pos.y() - self._lmb_prev.y()
            self._lmb_prev = pos
            self._redraw()
            return

        if (
            self._gizmo_drag_mode is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._apply_gizmo_drag(wx, wy, event.modifiers())
            self._redraw()
            return

        if (
            self._guide_drag is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            orient, _ = self._guides[self._guide_drag]
            self._guides[self._guide_drag] = (
                orient,
                wy if orient == "h" else wx,
            )
            self._guide_drag_moved = True
            self._redraw()
            return

        if self._measure_mode:
            self._measure_tool.move(event)
            return

        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.move(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        pos = event.position()

        if event.button() == Qt.MouseButton.MiddleButton:
            self._mmb_prev = None
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._space_pan_active:
            self._space_pan_dragging = False
            self._lmb_prev = None
            self._update_cursor()
            return

        if self._gizmo_drag_mode is not None:
            moved = self._end_gizmo_drag()
            self._redraw()
            self._notify()
            if moved:
                self._fire_poly_change()
            return

        if self._guide_drag is not None:
            if self._guide_drag_moved and (
                pos.x() <= self.RULER_PX or pos.y() <= self.RULER_PX
            ):
                del self._guides[self._guide_drag]
                self._selected_guide = None
            self._guide_drag = None
            self._guide_drag_moved = False
            self._redraw()
            return

        if self._measure_mode:
            return

        if self._mode in ("edit", "select") and self._edit_dragging:
            canvas_tools.release_edit_drag(self)
            return

        tool = self._tools.get(self._mode)
        if tool is not None and tool.release(event):
            return

        # Click select / deselect fall-through (no tool consumed the release).
        if (
            self._selectable
            and self._mode != "select"
            and self._lmb_press is not None
            and self._lmb_target is not None
        ):
            dx = pos.x() - self._lmb_press.x()
            dy = pos.y() - self._lmb_press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH:
                idx = self._lmb_target
                mods = event.modifiers()
                if mods & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.MetaModifier
                ):
                    if idx in self._sel:
                        self._sel.discard(idx)
                    else:
                        self._sel.add(idx)
                else:
                    self._sel = {idx}
                self._redraw()
                self._notify()
        elif (
            self._mode == "select"
            and self._selectable
            and self._lmb_press is not None
            and self._lmb_target is None
        ):
            dx = pos.x() - self._lmb_press.x()
            dy = pos.y() - self._lmb_press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH and self._sel:
                self.deselect_all()
        self._lmb_press = None
        self._lmb_prev = None
        self._lmb_target = None
        self._shift_drag = False
        self._band_start = None
        self._band_additive = False
        self._move_origin = None
        self._move_undo_pushed = False
        self._move_anchor_w = None
        self._move_applied_w = (0.0, 0.0)
        self._move_start_pts = []
        self._move_snap_exclude_vertices = set()
        self._move_snap_exclude_segments = set()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.double_click(event)

    @staticmethod
    def _is_poly_closed(poly: list[tuple[float, float]]) -> bool:
        """Check if a polyline is geometrically closed (first ≈ last)."""
        if len(poly) < 3:
            return False
        return math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01

    def _split_geometry_with_line(
        self,
        new_poly: list[tuple[float, float]],
    ) -> tuple[bool, int, int]:
        """Split existing polylines using a drawn cutting line (Shapely-based).

        Handles both closed polygons (split into sub-polygons) and open
        polylines (split at intersection points).  The cutting line itself
        is consumed (not kept) when at least one closed split succeeds.

        Returns (any_split, closed_region_splits, open_segment_splits).
        """
        if len(new_poly) < 2:
            return False, 0, 0
        try:
            cutter = LineString(new_poly)
            if cutter.is_empty or cutter.length < 1e-9:
                return False, 0, 0
        except (TypeError, ValueError, GEOSException):
            return False, 0, 0

        any_split = False
        closed_splits = 0
        open_splits = 0
        result_polys: list[list[tuple[float, float]]] = []
        result_kinds: list[str] = []
        result_meta: list[dict[str, Any] | None] = []
        new_construction: set[int] = set()
        new_hidden: set[int] = set()
        new_locked: set[int] = set()
        new_groups: dict[int, int] = {}
        new_layers: dict[int, str | None] = {}

        def _carry_flags(src_idx: int, ni: int) -> None:
            src = self._entities[src_idx]
            if src.construction:
                new_construction.add(ni)
            if src.hidden:
                new_hidden.add(ni)
            if src.locked:
                new_locked.add(ni)
            # Preserve the source entity's layer — unrelated entities that
            # merely pass through a split (or split pieces of a shape on a
            # non-active layer) must NOT be reassigned to the active layer.
            new_layers[ni] = src.layer

        def _keep(src_idx: int, p: list[tuple[float, float]]) -> None:
            """Carry an unchanged poly through with its kind/meta/flags intact."""
            ni = len(result_polys)
            result_polys.append(p)
            result_kinds.append(
                self._entities[src_idx].kind
            )
            m = self._entities[src_idx].meta
            result_meta.append(deepcopy(m) if m is not None else None)
            _carry_flags(src_idx, ni)
            src_gid = self._entities[src_idx].group
            if src_gid is not None:
                new_groups[ni] = src_gid

        def _emit(src_idx: int, p: list[tuple[float, float]]) -> None:
            """Emit a split piece: geometry changed, so it demotes to a plain
            polyline (stale circle/arc meta must not survive the cut)."""
            ni = len(result_polys)
            result_polys.append(p)
            result_kinds.append("polyline")
            result_meta.append(None)
            _carry_flags(src_idx, ni)

        for src_idx, poly in enumerate(e.points for e in self._entities):
            if len(poly) < 2:
                _keep(src_idx, poly)
                continue

            is_closed = self._is_poly_closed(poly)

            if is_closed:
                # ── Split closed polygon ──────────────────────────────────
                try:
                    coords = list(poly)
                    # Ensure ring is properly closed for Shapely
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    shapely_poly = Polygon(coords)
                    if not shapely_poly.is_valid:
                        shapely_poly = shapely_poly.buffer(0)
                    if shapely_poly.is_empty:
                        _keep(src_idx, poly)
                        continue

                    # Check if cutting line actually intersects
                    if not cutter.intersects(shapely_poly):
                        _keep(src_idx, poly)
                        continue

                    if self._would_split_closed_polygon(shapely_poly, cutter):
                        bounds = shapely_poly.bounds  # (minx, miny, maxx, maxy)
                        diag = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])
                        ext_amount = max(diag * 2.0, 1.0)
                        ext_cutter = self._extend_line(cutter, ext_amount)
                        split_candidates: list[tuple[int, list]] = []
                        for order, candidate in enumerate((cutter, ext_cutter)):
                            pieces = shapely_split(shapely_poly, candidate)
                            trial = (
                                list(pieces.geoms) if hasattr(pieces, "geoms") else []
                            )
                            if len(trial) >= 2:
                                split_candidates.append((order, trial))

                        geoms: list = []
                        if split_candidates:
                            # Prefer fewer resulting pieces (avoids over-splitting),
                            # and prefer original cutter over extended when tied.
                            _, geoms = min(
                                split_candidates,
                                key=lambda item: (len(item[1]), item[0]),
                            )

                        for g in geoms:
                            if isinstance(g, Polygon) and not g.is_empty:
                                coords_out = list(g.exterior.coords)
                                if len(coords_out) >= 3:
                                    _emit(src_idx, [(x, y) for x, y in coords_out])
                        any_split = True
                        closed_splits += 1
                    else:
                        # Boundary-only cut: split the impacted edge(s) but keep one closed shape.
                        pts = list(
                            poly[:-1] if self._points_equal(poly[0], poly[-1]) else poly
                        )
                        if len(pts) < 3:
                            _keep(src_idx, poly)
                            continue
                        rebuilt: list[tuple[float, float]] = [pts[0]]
                        boundary_changed = False
                        for j in range(len(pts)):
                            a = pts[j]
                            b = pts[(j + 1) % len(pts)]
                            pieces = self._split_segment_by_cutter_points(a, b, cutter)
                            if len(pieces) >= 2:
                                boundary_changed = True
                            for piece in pieces:
                                p1 = piece[1]
                                if not self._points_equal(rebuilt[-1], p1):
                                    rebuilt.append(p1)
                        if len(rebuilt) >= 3:
                            if not self._points_equal(rebuilt[0], rebuilt[-1]):
                                rebuilt.append(rebuilt[0])
                            if boundary_changed:
                                _emit(src_idx, rebuilt)
                                any_split = True
                                closed_splits += 1
                            else:
                                _keep(src_idx, poly)
                        else:
                            _keep(src_idx, poly)
                except (TypeError, ValueError, GEOSException):
                    # Any Shapely error — keep original geometry untouched
                    _keep(src_idx, poly)
            else:
                # ── Split open geometry segment-by-segment ────────────────
                try:
                    pts = list(poly)
                    if len(pts) < 2:
                        _keep(src_idx, poly)
                        continue

                    # Each "chain" grows into one output polyline.
                    # We break into a new chain at each cut point.
                    chains: list[list[tuple[float, float]]] = [[pts[0]]]
                    segment_changed = False
                    for j in range(len(pts) - 1):
                        a = pts[j]
                        b = pts[j + 1]
                        if math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-12:
                            continue
                        pieces = self._split_segment_by_cutter_points(a, b, cutter)
                        if len(pieces) >= 2:
                            for k, piece in enumerate(pieces):
                                if k == 0:
                                    chains[-1].append(piece[1])
                                else:
                                    chains.append(list(piece))
                            segment_changed = True
                        else:
                            chains[-1].append(b)

                    if segment_changed:
                        for c in chains:
                            if len(c) >= 2:
                                _emit(src_idx, c)
                        any_split = True
                        open_splits += 1
                    else:
                        _keep(src_idx, poly)
                except (TypeError, ValueError, GEOSException):
                    _keep(src_idx, poly)

        self._entities = [
            EntityRecord(
                points=p,
                kind=k,
                meta=m,
                construction=i in new_construction,
                hidden=i in new_hidden,
                locked=i in new_locked,
                group=new_groups.get(i),
                layer=new_layers.get(i, self._active_layer),
            )
            for i, (p, k, m) in enumerate(zip(result_polys, result_kinds, result_meta))
        ]
        return any_split, closed_splits, open_splits

    @staticmethod
    def _extend_line(line: LineString, amount: float) -> LineString:
        """Extend a LineString at both ends by *amount* along its direction."""
        coords = list(line.coords)
        if len(coords) < 2:
            return line
        # Extend start
        ax, ay = coords[0]
        bx, by = coords[1]
        dx, dy = ax - bx, ay - by
        d = math.hypot(dx, dy)
        if d > 1e-12:
            coords[0] = (ax + dx / d * amount, ay + dy / d * amount)
        # Extend end
        ax, ay = coords[-1]
        bx, by = coords[-2]
        dx, dy = ax - bx, ay - by
        d = math.hypot(dx, dy)
        if d > 1e-12:
            coords[-1] = (ax + dx / d * amount, ay + dy / d * amount)
        return LineString(coords)

    # ---- Snap helpers (inlined from _SnapMixin) ----

    def _snap_to_polyline(
        self,
        cx: float,
        cy: float,
        *,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return _snap_to_polyline_candidates(
            cx, cy, [e.points for e in self._entities], self._noninteractive_indices(), self._scale,
            self._w2c, self._c2w, self._poly_bounds,
            self._is_poly_closed, self._segment_intersection_point,
            reference_point=reference_point, draw_points=self._draw_pts, mode=self._mode,
        )

    def _resolve_snap(
        self,
        cx: float, cy: float, wx: float, wy: float,
        *,
        allow_polyline: bool = True, allow_grid: bool = True,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return self._snap_engine.query(
            cx, cy, wx, wy,
            allow_polyline=allow_polyline, allow_grid=allow_grid,
            reference_point=reference_point,
        )

    def _resolve_drag_snap(
        self,
        cx: float, cy: float, wx: float, wy: float,
        *,
        allow_polyline: bool = True, allow_grid: bool = True, allow_vertex: bool = True,
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return self._snap_engine.query(
            cx, cy, wx, wy, drag=True,
            allow_polyline=allow_polyline, allow_grid=allow_grid,
            allow_vertex=allow_vertex,
            exclude_vertices=exclude_vertices, exclude_segments=exclude_segments,
            reference_point=reference_point,
        )

    def _angle_snap(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        return self._snap_engine.angle(ax, ay, wx, wy)

    # ---- Shape preview helpers (inlined from _DrawModeMixin) ----




    def _offset_selected(self, distance: float) -> int:
        indices = self._mutable_selected_indices()
        if not indices or abs(distance) <= 1e-9:
            return 0

        created: list[tuple[list[tuple[float, float]], bool]] = []
        for idx in indices:
            poly = self._entities[idx].points
            offset_poly = self._offset_polyline(poly, distance)
            if offset_poly is None or len(offset_poly) < 2:
                continue
            created.append((offset_poly, self._entities[idx].construction))
        if not created:
            return 0

        self._push_undo()
        new_sel: set[int] = set()
        for poly, is_construction in created:
            # _append_entity keeps _entity_kinds/_entity_meta in sync — a bare
            # _polys.append desyncs them and corrupts later DXF export.
            new_idx = self._append_entity(poly)
            if is_construction:
                self._entities[new_idx].construction = True
            new_sel.add(new_idx)
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(created)

    def _fire_poly_change(self) -> None:
        """Notify the on_poly_change callback when polylines are structurally modified."""
        self._sync_shape_storage_from_entities()
        if callable(self._on_poly_change):
            self._on_poly_change()



    # ── Methods restored from pre-refactor mixins (were dropped in the
    #    mixin-inlining refactor; callers in dxf_canvas.py/render.py remained). ──

    def _append_draw_polyline(
        self,
        poly: list[tuple[float, float]],
        *,
        enter_edit: bool = False,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> None:
        if len(poly) < 2:
            return
        self._push_undo()
        new_idx = self._append_entity(list(poly), kind=kind, meta=meta)
        if self._draw_construction_mode:
            self._entities[new_idx].construction = True
        self._sel = {new_idx}
        self._notify()
        self._fire_poly_change()
        self._refresh_draw_sidebar_state()
        self._redraw()
        if enter_edit:
            self.set_mode("edit")

    def _ctx_delete_poly(self, idx: int) -> None:
        self._push_undo()
        # _compact_entities removes the entity and remaps all flag state.
        self._compact_entities({idx})
        self._sel = {i if i < idx else i - 1 for i in self._sel if i != idx}
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _ctx_deselect(self, idx: int) -> None:
        self._sel.discard(idx)
        self._redraw()
        self._notify()

    def _ctx_select(self, idx: int) -> None:
        self._sel.add(idx)
        self._redraw()
        self._notify()

    def _distribute_selected(
        self, axis: str, spacing: float, *, mode: str = "gap"
    ) -> bool:
        """Distribute selected shapes along ``axis`` at fixed ``spacing``.

        ``mode="gap"`` spaces adjacent bounding-box edges (edge-to-edge);
        ``mode="center"`` spaces bounding-box centers (center-to-center).
        The shape lowest along the axis stays anchored.
        """
        indices = self._mutable_selected_indices()
        if len(indices) < 2 or spacing < 0 or mode not in ("gap", "center"):
            return False
        if axis == "horizontal":
            lo, hi = 0, 2
        elif axis == "vertical":
            lo, hi = 1, 3
        else:
            return False

        keyed = [(idx, self._poly_bounds(self._entities[idx].points)) for idx in indices]
        keyed.sort(key=lambda x: x[1][lo])

        self._push_undo()
        first_b = keyed[0][1]
        cur_edge = first_b[hi]
        cur_center = (first_b[lo] + first_b[hi]) / 2.0
        for idx, b in keyed[1:]:
            if mode == "center":
                delta = (cur_center + spacing) - (b[lo] + b[hi]) / 2.0
            else:
                delta = (cur_edge + spacing) - b[lo]
            dx, dy = (delta, 0.0) if axis == "horizontal" else (0.0, delta)
            self._entities[idx].points = [(x + dx, y + dy) for x, y in self._entities[idx].points]
            self._transform_entity_meta(
                idx,
                center=(0.0, 0.0),
                kind=self._entities[idx].kind,
                meta=self._entities[idx].meta,
                transform="translate",
                dx=dx,
                dy=dy,
            )
            nb = self._poly_bounds(self._entities[idx].points)
            cur_edge = nb[hi]
            cur_center = (nb[lo] + nb[hi]) / 2.0

        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _scale_single_line_extent(self, idx: int, axis: str, target: float) -> bool:
        """Uniformly scale a 2-point line about its start point so its extent
        along ``axis`` ("w"/"h") equals ``target``, preserving its angle.

        Axis-only scaling would shear the segment and change its angle, so a
        lone line gets proportional scaling instead.
        """
        (ax, ay), (bx, by) = self._entities[idx].points
        extent = abs(bx - ax) if axis == "w" else abs(by - ay)
        if extent <= 1e-9:
            self._show_flash(
                "Line has no {} — change its angle first".format(
                    "width" if axis == "w" else "height"
                ),
                1100,
            )
            return False
        f = target / extent
        self._push_undo()
        self._entities[idx].points[1] = (ax + (bx - ax) * f, ay + (by - ay) * f)
        self._sync_line_meta_from_poly(idx)
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_height(self, height: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or height <= 0:
            return False
        if len(indices) == 1 and len(self._entities[indices[0]].points) == 2:
            return self._scale_single_line_extent(indices[0], "h", height)
        cur_h = bounds[3] - bounds[1]
        if cur_h <= 1e-9:
            return False
        fy = height / cur_h
        cy = (bounds[1] + bounds[3]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [(x, cy + (y - cy) * fy) for x, y in self._entities[idx].points]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_width(self, width: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or width <= 0:
            return False
        if len(indices) == 1 and len(self._entities[indices[0]].points) == 2:
            return self._scale_single_line_extent(indices[0], "w", width)
        cur_w = bounds[2] - bounds[0]
        if cur_w <= 1e-9:
            return False
        fx = width / cur_w
        cx = (bounds[0] + bounds[2]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [(cx + (x - cx) * fx, y) for x, y in self._entities[idx].points]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _other_linework(self, exclude_idx: int):
        """Union of every other visible entity's linework (for trim/extend)."""
        lines = []
        for i, e in enumerate(self._entities):
            if i == exclude_idx or not self._entity_selectable(i):
                continue
            if len(e.points) >= 2:
                lines.append(LineString(e.points))
        if not lines:
            return None
        return unary_union(lines)

    def trim_at(self, cx: float, cy: float) -> bool:
        """Remove the clicked portion of a polyline up to its nearest
        intersections with other shapes."""
        idx = self._find_poly_at(cx, cy)
        if idx is None:
            self._show_flash("Click a segment to trim", 1000)
            return False
        if self._is_locked(idx):
            self._show_flash("Shape is locked", 1000)
            return False
        wx, wy = self._c2w(cx, cy)
        pts = self._entities[idx].points
        if len(pts) < 2:
            return False
        cutters = self._other_linework(idx)
        if cutters is None or cutters.is_empty:
            self._show_flash("Nothing to trim against", 1100)
            return False
        target = LineString(pts)
        try:
            pieces = [
                g
                for g in shapely_split(target, cutters).geoms
                if isinstance(g, LineString) and len(g.coords) >= 2
            ]
        except GEOSException:
            self._show_flash("Trim failed", 1100)
            return False
        if len(pieces) < 2:
            self._show_flash("No intersection to trim to", 1100)
            return False
        click = Point(wx, wy)
        drop = min(pieces, key=lambda g: g.distance(click))
        kept = [g for g in pieces if g is not drop]
        merged = linemerge(kept) if len(kept) > 1 else kept[0]
        out = (
            list(merged.geoms)
            if isinstance(merged, MultiLineString)
            else [merged]
        )
        self._push_undo()
        first, *rest = out
        e = self._entities[idx]
        e.points = [(float(x), float(y)) for x, y in first.coords]
        e.kind = "polyline"
        e.meta = None
        new_sel = {idx}
        for piece in rest:
            new_sel.add(
                self._append_entity(
                    [(float(x), float(y)) for x, y in piece.coords]
                )
            )
        self._sel = new_sel
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash("Trimmed", 800)
        return True

    def extend_at(self, cx: float, cy: float) -> bool:
        """Lengthen the nearest open polyline end to its first intersection
        with another shape."""
        best: tuple[int, int] | None = None  # (entity idx, 0=start / -1=end)
        best_d = 12.0
        for i, e in enumerate(self._entities):
            if not self._entity_selectable(i) or self._is_locked(i):
                continue
            pts = e.points
            if len(pts) < 2 or self._is_poly_closed(pts):
                continue
            for endsel in (0, -1):
                ex, ey = self._w2c(*pts[endsel])
                d = math.hypot(cx - ex, cy - ey)
                if d < best_d:
                    best_d = d
                    best = (i, endsel)
        if best is None:
            # Fall back to the polyline under the cursor: extend whichever
            # open end is closer to the click.
            poly_hit = self._find_poly_at(cx, cy)
            if (
                poly_hit is not None
                and not self._is_locked(poly_hit)
                and len(self._entities[poly_hit].points) >= 2
                and not self._is_poly_closed(self._entities[poly_hit].points)
            ):
                wx, wy = self._c2w(cx, cy)
                pts_hit = self._entities[poly_hit].points
                d_start = math.hypot(pts_hit[0][0] - wx, pts_hit[0][1] - wy)
                d_end = math.hypot(pts_hit[-1][0] - wx, pts_hit[-1][1] - wy)
                best = (poly_hit, 0 if d_start <= d_end else -1)
        if best is None:
            self._show_flash("Click an open polyline to extend", 1100)
            return False
        idx, endsel = best
        pts = self._entities[idx].points
        tip = pts[endsel]
        neighbor = pts[1] if endsel == 0 else pts[-2]
        dx, dy = tip[0] - neighbor[0], tip[1] - neighbor[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return False
        dx, dy = dx / length, dy / length
        bx0, by0, bx1, by1 = self._bbox()
        reach = max(bx1 - bx0, by1 - by0, 1.0) * 3.0
        ray = LineString(
            [tip, (tip[0] + dx * reach, tip[1] + dy * reach)]
        )
        others = self._other_linework(idx)
        if others is None or others.is_empty:
            self._show_flash("Nothing to extend to", 1100)
            return False
        try:
            inter = ray.intersection(others)
        except GEOSException:
            return False
        candidates: list[tuple[float, tuple[float, float]]] = []
        for g in getattr(inter, "geoms", [inter]):
            if isinstance(g, Point):
                t = math.hypot(g.x - tip[0], g.y - tip[1])
                if t > 1e-6:
                    candidates.append((t, (float(g.x), float(g.y))))
            elif isinstance(g, LineString):
                for x, y in g.coords:
                    t = math.hypot(x - tip[0], y - tip[1])
                    if t > 1e-6:
                        candidates.append((t, (float(x), float(y))))
        if not candidates:
            self._show_flash("No shape in that direction", 1100)
            return False
        _, hit = min(candidates, key=lambda item: item[0])
        self._push_undo()
        e = self._entities[idx]
        if endsel == 0:
            e.points = [hit] + list(pts)
        else:
            e.points = list(pts) + [hit]
        e.kind = "polyline"
        e.meta = None
        self._sel = {idx}
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash("Extended", 800)
        return True

    def boolean_selected(self, op: str) -> int:
        """Boolean operation on the closed shapes in the selection.

        ``union`` welds overlapping shapes, ``subtract`` cuts the later
        shapes out of the first (lowest-index) shape, ``intersect`` keeps
        the common area, ``divide`` splits the union into its faces.
        Results are plain closed polylines (holes become separate loops so
        laser paths stay cuttable). Returns the number of result shapes.
        """
        indices = [
            i
            for i in self._mutable_selected_indices()
            if len(self._entities[i].points) >= 4
            and self._is_poly_closed(self._entities[i].points)
        ]
        if len(indices) < 2:
            self._show_flash("Select 2+ closed shapes", 1100)
            return 0
        try:
            shapes = []
            for i in indices:
                pg = Polygon(self._entities[i].points[:-1]).buffer(0)
                if not pg.is_empty:
                    shapes.append(pg)
            if len(shapes) < 2:
                self._show_flash("Shapes are degenerate", 1100)
                return 0
            if op == "union":
                result = unary_union(shapes)
            elif op == "subtract":
                result = shapes[0]
                for other in shapes[1:]:
                    result = result.difference(other)
            elif op == "intersect":
                result = shapes[0]
                for other in shapes[1:]:
                    result = result.intersection(other)
            elif op == "divide":
                cutters = unary_union([pg.boundary for pg in shapes])
                from shapely.ops import polygonize

                result = MultiPolygon(list(polygonize(cutters)))
            else:
                return 0
        except GEOSException:
            self._show_flash("Boolean operation failed", 1200)
            return 0

        rings: list[list[tuple[float, float]]] = []

        def _collect(geom) -> None:
            if geom.is_empty:
                return
            if isinstance(geom, Polygon):
                ext = [(float(x), float(y)) for x, y in geom.exterior.coords]
                if len(ext) >= 4:
                    rings.append(ext)
                for hole in geom.interiors:
                    ring = [(float(x), float(y)) for x, y in hole.coords]
                    if len(ring) >= 4:
                        rings.append(ring)
            elif isinstance(geom, (MultiPolygon, GeometryCollection)):
                for g in geom.geoms:
                    _collect(g)

        _collect(result)
        if not rings:
            self._show_flash("No area left after operation", 1200)
            return 0

        self._push_undo()
        self._compact_entities(set(indices))
        new_sel: set[int] = set()
        for ring in rings:
            new_sel.add(self._append_entity(ring))
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"{op.capitalize()}: {len(rings)} shape(s)", 1000)
        return len(rings)

    def selection_geometry(self) -> dict[str, Any] | None:
        """Bbox + single-entity parameters for the properties panel."""
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return None
        info: dict[str, Any] = {
            "x": bounds[0],
            "y": bounds[1],
            "w": bounds[2] - bounds[0],
            "h": bounds[3] - bounds[1],
            "count": len(indices),
        }
        if len(indices) == 1:
            e = self._entities[indices[0]]
            info["kind"] = e.kind
            info["meta"] = deepcopy(e.meta) if e.meta else {}
            info["index"] = indices[0]
        return info

    def move_selection_to(self, x: float | None, y: float | None) -> bool:
        """Place the selection bbox's bottom-left corner at (x, y)."""
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        dx = (x - bounds[0]) if x is not None else 0.0
        dy = (y - bounds[1]) if y is not None else 0.0
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return False
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [
                (px + dx, py + dy) for px, py in self._entities[idx].points
            ]
            self._transform_entity_meta(
                idx,
                center=(0.0, 0.0),
                kind=self._entities[idx].kind,
                meta=self._entities[idx].meta,
                transform="translate",
                dx=dx,
                dy=dy,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def set_shape_param(self, idx: int, key: str, value: float) -> bool:
        """Edit a parametric entity's defining parameter and rebuild its
        geometry (circle radius, polygon radius/sides, ellipse rx/ry,
        arc radius). Returns False for non-parametric entities."""
        if not (0 <= idx < len(self._entities)):
            return False
        e = self._entities[idx]
        meta = dict(e.meta or {})
        center = meta.get("center")
        if center is None or len(center) < 2:
            return False
        cx, cy = float(center[0]), float(center[1])
        kind = e.kind
        new_points: list[tuple[float, float]] | None = None
        if kind == "circle" and key == "radius" and value > 0:
            meta["radius"] = float(value)
            new_points = build_circle_poly(cx, cy, float(value))
        elif kind == "polygon" and key == "radius" and value > 0:
            meta["radius"] = float(value)
            new_points = build_polygon_poly(
                cx, cy, float(value), int(meta.get("sides", 6))
            )
        elif kind == "polygon" and key == "sides" and 3 <= int(value) <= 64:
            meta["sides"] = int(value)
            new_points = build_polygon_poly(
                cx, cy, float(meta.get("radius", 1.0)), int(value)
            )
        elif kind == "ellipse" and key in ("rx", "ry") and value > 0:
            meta[key] = float(value)
            new_points = build_ellipse_poly(
                cx,
                cy,
                float(meta.get("rx", 1.0)),
                float(meta.get("ry", 1.0)),
            )
            rot = math.radians(float(meta.get("rotation", 0.0) or 0.0))
            if abs(rot) > 1e-9:
                ca, sa = math.cos(rot), math.sin(rot)
                new_points = [
                    (
                        cx + (x - cx) * ca - (y - cy) * sa,
                        cy + (x - cx) * sa + (y - cy) * ca,
                    )
                    for x, y in new_points
                ]
        elif kind == "arc" and key == "radius" and value > 0:
            meta["radius"] = float(value)
            a0 = math.radians(float(meta.get("start_angle", 0.0)))
            a1 = math.radians(float(meta.get("end_angle", 360.0)))
            if a1 <= a0:
                a1 += 2 * math.pi
            r = float(value)
            new_points = [
                (
                    cx + r * math.cos(a0 + (a1 - a0) * i / 24),
                    cy + r * math.sin(a0 + (a1 - a0) * i / 24),
                )
                for i in range(25)
            ]
        if new_points is None:
            return False
        self._push_undo()
        e.points = new_points
        e.meta = meta
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def align_selected(self, mode: str) -> bool:
        """Align each selected alignment "unit" to the selection's overall
        bounds. Grouped shapes are treated as a single rigid unit (aligned
        together by their combined bbox) so aligning a single selected group
        is a no-op and aligning a group alongside other shapes keeps the
        group's internal layout intact."""
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if len(indices) < 2 or bounds is None:
            return False
        bx0, by0, bx1, by1 = bounds
        center_x = (bx0 + bx1) / 2.0
        center_y = (by0 + by1) / 2.0

        units: dict[object, list[int]] = {}
        for idx in indices:
            gid = self._entities[idx].group
            key: object = ("group", gid) if gid is not None else ("shape", idx)
            units.setdefault(key, []).append(idx)
        if len(units) < 2:
            return False  # a single shape (or single group) has nothing to align to
        if mode not in ("left", "center-x", "right", "top", "center-y", "bottom"):
            return False

        self._push_undo()
        for member_indices in units.values():
            unit_bounds = self._selection_bounds(member_indices)
            if unit_bounds is None:
                continue
            px0, py0, px1, py1 = unit_bounds
            dx = dy = 0.0
            if mode == "left":
                dx = bx0 - px0
            elif mode == "center-x":
                dx = center_x - (px0 + px1) / 2.0
            elif mode == "right":
                dx = bx1 - px1
            elif mode == "top":
                dy = by1 - py1
            elif mode == "center-y":
                dy = center_y - (py0 + py1) / 2.0
            elif mode == "bottom":
                dy = by0 - py0
            if dx == 0.0 and dy == 0.0:
                continue
            for idx in member_indices:
                self._entities[idx].points = [
                    (x + dx, y + dy) for x, y in self._entities[idx].points
                ]
                self._transform_entity_meta(
                    idx,
                    center=(center_x, center_y),
                    kind=self._entities[idx].kind,
                    meta=self._entities[idx].meta,
                    transform="translate",
                    dx=dx,
                    dy=dy,
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def mirror_selected(self, axis: str) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._push_undo()
        for idx in indices:
            if axis == "horizontal":
                self._entities[idx].points = [(2 * cx - x, y) for x, y in self._entities[idx].points]
            elif axis == "vertical":
                self._entities[idx].points = [(x, 2 * cy - y) for x, y in self._entities[idx].points]
            else:
                return False
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entities[idx].kind,
                meta=self._entities[idx].meta,
                transform="mirror",
                axis=axis,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def rotate_selected(self, angle_deg: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        angle = math.radians(angle_deg)
        ca, sa = math.cos(angle), math.sin(angle)
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [
                (
                    cx + (x - cx) * ca - (y - cy) * sa,
                    cy + (x - cx) * sa + (y - cy) * ca,
                )
                for x, y in self._entities[idx].points
            ]
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entities[idx].kind,
                meta=self._entities[idx].meta,
                transform="rotate",
                angle_deg=angle_deg,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _scale_all(self, factor: float) -> None:
        """Scale all polylines uniformly around their bounding box center."""
        if not self._entities:
            return
        self._push_undo()
        all_pts = [pt for p in (e.points for e in self._entities) for pt in p]
        xs, ys = zip(*all_pts)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        for ent in self._entities:
            ent.points = [
                (cx + (x - cx) * factor, cy + (y - cy) * factor)
                for x, y in ent.points
            ]
        for idx in range(len(self._entities)):
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entities[idx].kind,
                meta=self._entities[idx].meta,
                transform="scale",
                factor=factor,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _apply_shape_size_inputs(self) -> None:
        if (
            self._draw_shape_w_edit is None
            or self._draw_shape_h_edit is None
            or self._draw_shape_anchor_w is None
            or not self._shape_primitive_active()
        ):
            return
        try:
            w = max(0.001, float(self._draw_shape_w_edit.text().strip()))
            h = max(0.001, float(self._draw_shape_h_edit.text().strip()))
        except ValueError:
            return

        sx, sy = self._draw_shape_anchor_w
        if self._draw_shape_cursor_w is None:
            self._draw_shape_cursor_w = (sx + w, sy + h)
        ex0, ey0 = self._draw_shape_cursor_w
        sign_x = 1.0 if ex0 >= sx else -1.0
        sign_y = 1.0 if ey0 >= sy else -1.0
        self._draw_shape_cursor_w = (sx + sign_x * w, sy + sign_y * h)
        self._redraw()

    def _immediate_segments_for_vertices(
        self,
        vertices: set[tuple[int, int]],
    ) -> set[tuple[int, int]]:
        """Return segment keys ``(poly_idx, seg_idx)`` touching the given vertices."""
        excluded: set[tuple[int, int]] = set()
        for pi, vi in vertices:
            if not (0 <= pi < len(self._entities)):
                continue
            poly = self._entities[pi].points
            n = len(poly)
            if n < 2:
                continue
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            if seg_count <= 0:
                continue
            if closed:
                excluded.add((pi, vi % seg_count))
                excluded.add((pi, (vi - 1) % seg_count))
            else:
                if 0 <= vi < seg_count:
                    excluded.add((pi, vi))
                if 0 <= (vi - 1) < seg_count:
                    excluded.add((pi, vi - 1))
        return excluded

    def _offset_polyline(
        self,
        poly: list[tuple[float, float]],
        distance: float,
    ) -> list[tuple[float, float]] | None:
        if len(poly) < 2:
            return None
        try:
            if self._is_poly_closed(poly):
                pts = list(poly)
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                geom = Polygon(pts)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty:
                    return None
                # Round joins prevent spikes at sharp corners on closed shapes.
                buffered = geom.buffer(distance, join_style="round")
                if buffered.is_empty:
                    return None
                if isinstance(buffered, MultiPolygon):
                    buffered = max(buffered.geoms, key=lambda g: g.area)
                if not isinstance(buffered, Polygon):
                    return None
                coords = list(buffered.exterior.coords)
                return [(float(x), float(y)) for x, y in coords]

            line = LineString(poly)
            if line.is_empty:
                return None
            side = "left" if distance >= 0 else "right"
            offset_geom = line.parallel_offset(
                abs(distance),
                side,
                join_style="mitre",
                mitre_limit=2.0,  # cap spike length at 2× offset distance
            )
            if offset_geom.is_empty:
                return None
            if isinstance(offset_geom, MultiLineString):
                offset_geom = max(offset_geom.geoms, key=lambda g: g.length)
            if not isinstance(offset_geom, LineString):
                return None
            coords = list(offset_geom.coords)
            return [(float(x), float(y)) for x, y in coords]
        except (TypeError, ValueError, GEOSException):
            return None

    @staticmethod
    def _points_equal(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6

    def _segments_for_polylines(self, poly_indices: set[int]) -> set[tuple[int, int]]:
        segments: set[tuple[int, int]] = set()
        for pi in poly_indices:
            if not (0 <= pi < len(self._entities)):
                continue
            poly = self._entities[pi].points
            n = len(poly)
            if n < 2:
                continue
            closed = self._is_poly_closed(poly)
            seg_count = n if closed else n - 1
            segments.update((pi, si) for si in range(max(0, seg_count)))
        return segments

    def _split_segment_by_cutter_points(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        cutter: LineString,
    ) -> list[list[tuple[float, float]]]:
        if self._points_equal(a, b):
            return []
        seg_line = LineString([a, b])
        if seg_line.is_empty or not cutter.intersects(seg_line):
            return [[a, b]]

        try:
            inter = seg_line.intersection(cutter)
        except (TypeError, ValueError, GEOSException):
            return [[a, b]]

        dx = b[0] - a[0]
        dy = b[1] - a[1]
        denom = dx * dx + dy * dy
        if denom < 1e-12:
            return [[a, b]]

        points: list[tuple[float, float]] = [a, b]

        def _add_point(pt: tuple[float, float]) -> None:
            if any(self._points_equal(pt, existing) for existing in points):
                return
            points.append(pt)

        if isinstance(inter, Point):
            _add_point((float(inter.x), float(inter.y)))
        elif isinstance(inter, MultiPoint):
            for g in inter.geoms:
                _add_point((float(g.x), float(g.y)))
        elif isinstance(inter, LineString):
            coords = list(inter.coords)
            if len(coords) >= 2:
                _add_point((float(coords[0][0]), float(coords[0][1])))
                _add_point((float(coords[-1][0]), float(coords[-1][1])))
        elif isinstance(inter, MultiLineString):
            for g in inter.geoms:
                coords = list(g.coords)
                if len(coords) >= 2:
                    _add_point((float(coords[0][0]), float(coords[0][1])))
                    _add_point((float(coords[-1][0]), float(coords[-1][1])))
        elif isinstance(inter, GeometryCollection):
            for pt in self._iter_intersection_points(inter):
                _add_point((float(pt[0]), float(pt[1])))

        def _param(pt: tuple[float, float]) -> float:
            return ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / denom

        ordered = sorted(points, key=_param)
        parts: list[list[tuple[float, float]]] = []
        for i in range(len(ordered) - 1):
            p0 = ordered[i]
            p1 = ordered[i + 1]
            if self._points_equal(p0, p1):
                continue
            parts.append([p0, p1])
        return parts or [[a, b]]

    def _update_shape_size_fields_from_preview(self) -> None:
        if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
            return
        enabled = (
            self._shape_primitive_active() and self._draw_shape_anchor_w is not None
        )
        self._draw_shape_w_edit.setEnabled(enabled)
        self._draw_shape_h_edit.setEnabled(enabled)
        if not enabled:
            return
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        self._draw_shape_w_edit.setText(f"{abs(ex - sx):.2f}")
        self._draw_shape_h_edit.setText(f"{abs(ey - sy):.2f}")

    def _vertices_for_polylines(self, poly_indices: set[int]) -> set[tuple[int, int]]:
        vertices: set[tuple[int, int]] = set()
        for pi in poly_indices:
            if 0 <= pi < len(self._entities):
                vertices.update((pi, vi) for vi in range(len(self._entities[pi].points)))
        return vertices

    def _would_split_closed_polygon(self, polygon: Polygon, cutter: LineString) -> bool:
        if polygon.is_empty or cutter.is_empty or not cutter.intersects(polygon):
            return False
        boundary = polygon.boundary
        try:
            overlap = cutter.intersection(boundary)
        except (TypeError, ValueError, GEOSException):
            return False
        if isinstance(overlap, (LineString, MultiLineString)) and overlap.length > 1e-6:
            return False

        inner = polygon.buffer(-1e-6)
        if inner.is_empty:
            inner = polygon
        try:
            inside = cutter.intersection(inner)
        except (TypeError, ValueError, GEOSException):
            return False
        if getattr(inside, "is_empty", True):
            return False

        bounds = polygon.bounds
        diag = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])
        ext_cutter = self._extend_line(cutter, max(diag * 2.0, 1.0))
        split_candidates: list[tuple[int, list]] = []
        for order, candidate in enumerate((cutter, ext_cutter)):
            pieces = shapely_split(polygon, candidate)
            trial = list(pieces.geoms) if hasattr(pieces, "geoms") else []
            if len(trial) >= 2:
                split_candidates.append((order, trial))
        return bool(split_candidates)

    def offset_selected(self, distance: float) -> int:
        indices = self._mutable_selected_indices()
        if not indices or abs(distance) <= 1e-9:
            return 0

        created: list[tuple[list[tuple[float, float]], bool]] = []
        for idx in indices:
            poly = self._entities[idx].points
            offset_poly = self._offset_polyline(poly, distance)
            if offset_poly is None or len(offset_poly) < 2:
                continue
            created.append((offset_poly, self._entities[idx].construction))
        if not created:
            return 0

        self._push_undo()
        new_sel: set[int] = set()
        for poly, is_construction in created:
            # _append_entity keeps _entity_kinds/_entity_meta in sync — a bare
            # _polys.append desyncs them and corrupts later DXF export.
            new_idx = self._append_entity(poly)
            if is_construction:
                self._entities[new_idx].construction = True
            new_sel.add(new_idx)
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(created)

    def _selected_single_line(self) -> int | None:
        """Index of the sole selected 2-point line, or ``None``."""
        if len(self._sel) != 1:
            return None
        idx = next(iter(self._sel))
        if not (0 <= idx < len(self._entities)) or len(self._entities[idx].points) != 2:
            return None
        return idx

    def _sel_badge_axes(self) -> list[tuple[str, QRectF]]:
        """Available selection badges as ordered (axis, hit-rect) pairs."""
        pairs = [
            ("w", self._sel_badge_w_rect),
            ("h", self._sel_badge_h_rect),
            ("l", self._sel_badge_l_rect),
            ("a", self._sel_badge_a_rect),
        ]
        return [(a, r) for a, r in pairs if r is not None]

    def _sync_line_meta_from_poly(self, idx: int) -> None:
        """Refresh a line entity's start/end meta from its polyline points."""
        kind = self._entities[idx].kind
        meta = self._entities[idx].meta
        if kind == "line" and isinstance(meta, dict):
            meta["start"] = tuple(self._entities[idx].points[0])
            meta["end"] = tuple(self._entities[idx].points[-1])

    def _set_selected_line_length(self, length: float) -> bool:
        indices = self._mutable_selected_indices()
        if len(indices) != 1 or length <= 0:
            return False
        poly = self._entities[indices[0]].points
        if len(poly) != 2:
            return False
        ax, ay = poly[0]
        bx, by = poly[1]
        dx, dy = bx - ax, by - ay
        cur_len = math.hypot(dx, dy)
        if cur_len <= 1e-9:
            return False
        ux, uy = dx / cur_len, dy / cur_len
        self._push_undo()
        self._entities[indices[0]].points[1] = (ax + ux * length, ay + uy * length)
        # Only the free endpoint moves — sync meta from points rather than
        # translating it (which would also shift the anchored start point).
        self._sync_line_meta_from_poly(indices[0])
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_line_angle(self, angle_deg: float) -> bool:
        """Rotate the selected 2-point line about its start point to an
        absolute angle in degrees (CCW from +X), preserving its length."""
        indices = self._mutable_selected_indices()
        if len(indices) != 1:
            return False
        poly = self._entities[indices[0]].points
        if len(poly) != 2:
            return False
        ax, ay = poly[0]
        bx, by = poly[1]
        cur_len = math.hypot(bx - ax, by - ay)
        if cur_len <= 1e-9:
            return False
        ar = math.radians(angle_deg)
        self._push_undo()
        self._entities[indices[0]].points[1] = (
            ax + cur_len * math.cos(ar),
            ay + cur_len * math.sin(ar),
        )
        self._sync_line_meta_from_poly(indices[0])
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _iter_intersection_points(self, geom) -> list[tuple[float, float]]:
        if geom is None or getattr(geom, "is_empty", True):
            return []
        if isinstance(geom, Point):
            return [(float(geom.x), float(geom.y))]
        if isinstance(geom, MultiPoint):
            return [(float(g.x), float(g.y)) for g in geom.geoms]
        if isinstance(geom, LineString):
            coords = list(geom.coords)
            if len(coords) >= 2:
                return [
                    (float(coords[0][0]), float(coords[0][1])),
                    (float(coords[-1][0]), float(coords[-1][1])),
                ]
            return []
        if isinstance(geom, MultiLineString):
            pts: list[tuple[float, float]] = []
            for g in geom.geoms:
                pts.extend(self._iter_intersection_points(g))
            return pts
        if isinstance(geom, GeometryCollection):
            pts: list[tuple[float, float]] = []
            for g in geom.geoms:
                pts.extend(self._iter_intersection_points(g))
            return pts
        return []

    def _demote_selected_entities_to_polylines(
        self, indices: list[int] | None = None
    ) -> None:
        if indices is None:
            indices = self._selected_indices()
        for idx in indices:
            if 0 <= idx < len(self._entities):
                self._entities[idx].kind = "polyline"
                self._entities[idx].meta = None

    # ── Draw sidebar + shape preview (restored from pre-refactor _draw_mixin) ──

    def _build_draw_sidebar(self) -> None:
        panel = DrawSidebar(
            parent=self,
            on_draw_clicked=self._on_draw_button_clicked,
            on_finish_open=lambda: self._finish_draw(close=False),
            on_close_edit=lambda: self._finish_draw(close=True),
            on_undo_point=self._key_backspace,
            on_toggle_snap=self._toggle_sidebar_snap,
            on_toggle_split=self._toggle_sidebar_split,
            on_cycle_arc_mode=self._cycle_arc_mode,
            on_cycle_constraint_mode=self._cycle_constraint_mode,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self.set_mode("select"),
        )
        panel.hide()

        # Create tool picker dialog
        self._tool_picker_dialog = ToolPickerDialog(parent=self)

        anim = QPropertyAnimation(panel, b"pos", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._draw_sidebar = panel
        self._draw_sidebar_anim = anim
        self._refresh_draw_sidebar_state()

    def _layout_draw_sidebar(self) -> None:
        if self._draw_sidebar is None:
            return
        left = self._chrome_left()
        top = self._chrome_top()
        y = top + 8
        target_h = max(260, self.height() - y - 8)
        self._draw_sidebar.setFixedHeight(min(430, target_h))
        x = (
            left + 8
            if self._draw_sidebar_visible
            else left - self._draw_sidebar.width() + 20
        )
        self._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._draw_sidebar is None or self._draw_sidebar_anim is None:
            return
        if self._draw_sidebar_visible == visible and self._draw_sidebar.isVisible():
            self._refresh_draw_sidebar_state()
            return

        self._draw_sidebar_visible = visible
        self._refresh_draw_sidebar_state()
        left = self._chrome_left()
        y = self._chrome_top() + 8
        hidden_x = left - self._draw_sidebar.width() + 20
        shown_x = left + 8
        self._draw_sidebar.setFixedHeight(
            min(430, max(260, self.height() - y - 8))
        )

        if not animate:
            if visible:
                self._draw_sidebar.show()
                self._draw_sidebar.move(shown_x, y)
            else:
                self._draw_sidebar.move(hidden_x, y)
                self._draw_sidebar.hide()
            return

        if visible:
            self._draw_sidebar.show()
            self._draw_sidebar.move(hidden_x, y)
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.setEndValue(QPoint(shown_x, y))
            self._draw_sidebar_anim.start()
        else:
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(self._draw_sidebar.pos())
            self._draw_sidebar_anim.setEndValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.start()

    def _refresh_draw_sidebar_state(self) -> None:
        if not isinstance(self._draw_sidebar, DrawSidebar):
            return
        has_pts = len(self._draw_pts)
        self._draw_sidebar.set_polyline_actions_enabled(
            can_finish=has_pts >= 2,
            can_close=has_pts >= 3,
            can_undo=has_pts >= 1,
        )
        self._draw_sidebar.set_snap_label(self._grid_snap)
        self._draw_sidebar.set_split_label(self._draw_split_enabled)
        self._draw_sidebar.set_active_tool(self._draw_primitive)
        self._draw_sidebar.set_arc_mode(self._draw_arc_mode)
        self._draw_sidebar.set_arc_mode_enabled(self._draw_primitive == "arc")
        self._draw_sidebar.set_constraint_mode(self._draw_constraint_lock)
        self._draw_sidebar.set_constraint_mode_enabled(
            self._draw_primitive in {"line", "polyline"}
        )
        self._update_shape_size_fields_from_preview()

    def _commit_shape_preview(self) -> bool:
        if not self._draw_shape_preview_active:
            return False
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return False
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)

        poly: list[tuple[float, float]] = []
        kind = "polyline"
        meta: dict[str, Any] | None = None
        if self._draw_primitive == "rectangle":
            poly = build_rect_poly(cx, cy, w, h)
            kind = "rectangle"
            meta = {
                "center": (cx, cy),
                "width": w,
                "height": h,
                "rotation": 0.0,
            }
        elif self._draw_primitive == "circle":
            # Match preview behavior: first click is center, drag to radius.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
            kind = "circle"
            meta = {"center": (sx, sy), "radius": radius}
        elif self._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
            kind = "ellipse"
            meta = {"center": (cx, cy), "rx": w / 2.0, "ry": h / 2.0, "rotation": 0.0}
        elif self._draw_primitive == "polygon":
            poly = build_polygon_poly(cx, cy, min(w, h) / 2.0, 6)

        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None

        if len(poly) >= 2:
            self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._show_flash(f"{self._draw_primitive.title()} created", 800)
            self._refresh_draw_sidebar_state()
            self._redraw()
            return True

        self._refresh_draw_sidebar_state()
        self._redraw()
        return False

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._draw_sidebar is None:
            return
        if not self._draw_sidebar_visible:
            self._draw_sidebar.hide()

    def _on_draw_button_clicked(self) -> None:
        """Show the tool picker modal and handle tool selection."""
        if (
            hasattr(self, "_tool_picker_dialog")
            and self._tool_picker_dialog is not None
        ) and self._tool_picker_dialog.exec() == 1:  # QDialog.Accepted
            tool = self._tool_picker_dialog.get_selected_tool()
            if tool is not None:
                self._set_draw_primitive(tool)

    def _toggle_sidebar_snap(self) -> None:
        self._grid_snap = not self._grid_snap
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _toggle_sidebar_split(self) -> None:
        self._draw_split_enabled = not self._draw_split_enabled
        self._refresh_draw_sidebar_state()
        self._show_flash("Split: on" if self._draw_split_enabled else "Split: off", 800)

    def _cycle_arc_mode(self) -> None:
        if self._draw_primitive != "arc":
            self._set_draw_primitive("arc")
            return
        self._draw_arc_mode = (
            "center-start-end" if self._draw_arc_mode == "3point" else "3point"
        )
        self._draw_arc_pts.clear()
        self._refresh_draw_sidebar_state()
        self._show_flash(
            "Arc: center-start-end"
            if self._draw_arc_mode == "center-start-end"
            else "Arc: three-point",
            900,
        )
        self._redraw()

    def _cycle_constraint_mode(self) -> None:
        modes = [None, "H", "V", "45"]
        try:
            idx = modes.index(self._draw_constraint_lock)
        except ValueError:
            idx = 0
        self._draw_constraint_lock = modes[(idx + 1) % len(modes)]
        self._refresh_draw_sidebar_state()
        self._show_flash(
            f"Constraint: {self._draw_constraint_lock}"
            if self._draw_constraint_lock
            else "Constraint: Free",
            900,
        )
        self._redraw()

    def _cancel_draw_points(self) -> None:
        if self._mode != "draw":
            return
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._draw_snap = None
        self._draw_snap_type = None
        self._draw_constraint = None
        self._angle_snap_active = False
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        if hasattr(self, "_dismiss_shape_dim_inputs"):
            self._dismiss_shape_dim_inputs()
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _set_draw_primitive(self, tool: str) -> None:
        valid = {
            "polyline",
            "line",
            "arc",
            "spline",
            "rectangle",
            "circle",
            "ellipse",
            "polygon",
            "text",
        }
        if tool not in valid:
            return
        self._draw_primitive = tool
        self._draw_pts.clear()
        self._draw_arc_pts.clear()
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._dismiss_dim_inputs()
        self._update_shape_size_fields_from_preview()
        self._refresh_draw_sidebar_state()
        self._show_flash(f"Tool: {tool}", 650)
        self._redraw()

    # ── Second restoration pass: methods referenced as callbacks
    #    (menu actions) that the call-only audit missed. ──

    def _group_selected(self) -> None:
        if len(self._sel) < 2:
            self._show_flash("Select 2+ shapes to group", 1000)
            return
        gid = self._next_group_id
        self._next_group_id += 1
        for idx in self._sel:
            self._entities[idx].group = gid
        self._show_flash(f"Grouped {len(self._sel)} shapes", 900)
        self._notify()
        self._fire_poly_change()

    def set_group_label(self, gid: int, label: str) -> None:
        label = str(label).strip()
        if label:
            self._group_labels[int(gid)] = label
        else:
            self._group_labels.pop(int(gid), None)
        self._notify()
        self._fire_poly_change()

    def _ungroup_selected(self) -> None:
        ungrouped: set[int] = set()
        for idx in self._sel:
            gid = self._group_of(idx)
            if gid is not None:
                ungrouped.add(gid)
                self._entities[idx].group = None
        if not ungrouped:
            return
        # Also remove other group members if their whole group is being dissolved
        for e in self._entities:
            if e.group in ungrouped:
                e.group = None
        for gid in ungrouped:
            self._group_labels.pop(gid, None)
        self._show_flash("Ungrouped", 700)
        self._notify()
        self._fire_poly_change()

    # ── Layer-tree-driven group/ungroup (accept index lists) ─────────────

    def group_indices(self, indices: list[int]) -> int:
        """Group the entities at ``indices`` (from layer tree). Returns count."""
        valid = [i for i in indices if 0 <= i < len(self._entities)]
        if len(valid) < 2:
            self._show_flash("Select 2+ shapes to group", 1000)
            return 0
        self._push_undo()
        gid = self._next_group_id
        self._next_group_id += 1
        for idx in valid:
            self._entities[idx].group = gid
        self._show_flash(f"Grouped {len(valid)} shapes", 900)
        self._sel = set(valid)
        self._notify()
        self._fire_poly_change()
        return len(valid)

    def ungroup_indices(self, indices: list[int]) -> int:
        """Ungroup the entities at ``indices`` (from layer tree). Returns count."""
        self._push_undo()
        ungrouped_gids: set[int] = set()
        valid = [i for i in indices if 0 <= i < len(self._entities)]
        for idx in valid:
            gid = self._group_of(idx)
            if gid is not None:
                ungrouped_gids.add(gid)
                self._entities[idx].group = None
        if not ungrouped_gids:
            self._show_flash("Shapes are not grouped", 700)
            return 0
        # Dissolve all members of affected groups.
        for e in self._entities:
            if e.group in ungrouped_gids:
                e.group = None
        for gid in ungrouped_gids:
            self._group_labels.pop(gid, None)
        self._show_flash("Ungrouped", 700)
        self._notify()
        self._fire_poly_change()
        return len(valid)

    def _send_selected_to_draft(self) -> None:
        cb = getattr(self, "_send_selected_to_draft_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent to Draft", 900)

    def _send_selected_to_pattern(self) -> None:
        cb = getattr(self, "_send_selected_to_pattern_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent to Pattern Fill", 900)

    def _use_selected_as_fill_pattern(self) -> None:
        cb = getattr(self, "_use_selected_as_fill_pattern_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        payload = [[(x, y) for x, y in poly] for poly in selected]
        cb(payload)
        self._show_flash("Sent as fill pattern", 900)

    def explode_selected_to_segments(self) -> int:
        """Split each selected multi-vertex polyline into individual 2-pt line segments.

        Returns the number of new segments created.
        """
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        # Only explode polylines with more than 2 points (1 segment is already atomic).
        to_explode = [i for i in indices if len(self._entities[i].points) > 2]
        if not to_explode:
            return 0
        self._push_undo()
        new_polys: list[list[tuple[float, float]]] = []
        new_construction: set[int] = set()
        new_kinds: list[str] = []
        new_meta: list[dict[str, Any] | None] = []
        new_sel: set[int] = set()
        for i, poly in enumerate(e.points for e in self._entities):
            is_construction = self._entities[i].construction
            kind = self._entities[i].kind
            meta = self._entities[i].meta
            if i in to_explode:
                pts = list(poly)
                is_closed = False
                # If closed (first == last) drop the duplicate closing point first
                if (
                    len(pts) >= 3
                    and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                    < 1e-6
                ):
                    pts = pts[:-1]
                    is_closed = True
                seg_count = len(pts) if is_closed else max(0, len(pts) - 1)
                for j in range(seg_count):
                    seg = [pts[j], pts[(j + 1) % len(pts)]]
                    si = len(new_polys)
                    new_polys.append(seg)
                    new_kinds.append("line")
                    new_meta.append({"start": seg[0], "end": seg[1]})
                    if is_construction:
                        new_construction.add(si)
                    new_sel.add(si)
            else:
                ni = len(new_polys)
                new_polys.append(poly)
                new_kinds.append(kind)
                new_meta.append(deepcopy(meta) if meta is not None else None)
                if is_construction:
                    new_construction.add(ni)
        self._entities = [
            EntityRecord(
                points=p,
                kind=k,
                meta=m,
                construction=i in new_construction,
                layer=self._active_layer,
            )
            for i, (p, k, m) in enumerate(zip(new_polys, new_kinds, new_meta))
        ]
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_sel)

    def merge_selected_segments_to_objects(self) -> int:
        """Merge selected geometry by segment connectivity.

        Accepts a mix of atomic 2-point segments and multi-vertex polylines.
        Selected polylines are decomposed into their constituent segments first,
        then rebuilt into one or more connected objects.
        """
        indices = self._mutable_selected_indices()
        if len(indices) < 2:
            return 0

        # Endpoint weld tolerance. 1e-6 mm was so tight that float error from
        # prior transforms (rotate/scale/move) made visually-coincident
        # endpoints fail to join; 0.01 mm is far below drawing scale but
        # absorbs accumulated round-off.
        _MERGE_TOL = 0.01

        def _eq(a: tuple[float, float], b: tuple[float, float]) -> bool:
            return abs(a[0] - b[0]) < _MERGE_TOL and abs(a[1] - b[1]) < _MERGE_TOL

        segs: list[tuple[tuple[float, float], tuple[float, float], bool]] = []
        for i in indices:
            poly = self._entities[i].points
            if len(poly) < 2:
                continue
            is_construction = self._entities[i].construction
            pts = list(poly)
            is_closed = False
            if (
                len(pts) >= 3
                and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6
            ):
                pts = pts[:-1]
                is_closed = True
            if len(pts) < 2:
                continue
            seg_count = len(pts) if is_closed else max(0, len(pts) - 1)
            for j in range(seg_count):
                a = pts[j]
                b = pts[(j + 1) % len(pts)]
                if _eq(a, b):
                    continue
                segs.append((a, b, is_construction))

        if len(segs) < 2:
            return 0

        sel_set = set(indices)

        self._push_undo()

        merged_polys: list[tuple[list[tuple[float, float]], bool]] = []
        used = [False] * len(segs)

        for si, seg in enumerate(segs):
            if used[si]:
                continue
            used[si] = True
            chain = [seg[0], seg[1]]
            chain_construction = seg[2]

            changed = True
            while changed:
                changed = False
                for j, s in enumerate(segs):
                    if used[j]:
                        continue
                    a, b = s[0], s[1]
                    head, tail = chain[0], chain[-1]
                    if _eq(tail, a):
                        chain.append(b)
                    elif _eq(tail, b):
                        chain.append(a)
                    elif _eq(head, b):
                        chain.insert(0, a)
                    elif _eq(head, a):
                        chain.insert(0, b)
                    else:
                        continue
                    used[j] = True
                    chain_construction = chain_construction or s[2]
                    changed = True
                    break

            if len(chain) >= 3 and _eq(chain[0], chain[-1]):
                # normalize explicit closure point
                chain[-1] = chain[0]
            merged_polys.append((
                self._normalize_merged_chain(chain),
                chain_construction,
            ))

        # Remove the merged sources via _compact_entities so entity kinds,
        # meta, hidden/locked sets, and groups are all remapped consistently —
        # rebuilding _polys alone left those arrays describing the wrong
        # shapes (stale meta then exported incorrect entities to DXF).
        self._compact_entities(sel_set)
        new_sel: set[int] = set()
        for poly, is_construction in merged_polys:
            ni = self._append_entity(poly)
            new_sel.add(ni)
            if is_construction:
                self._entities[ni].construction = True
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_sel)

    @staticmethod
    def _normalize_merged_chain(
        chain: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not chain:
            return []
        normalized: list[tuple[float, float]] = [chain[0]]
        for pt in chain[1:]:
            if math.hypot(normalized[-1][0] - pt[0], normalized[-1][1] - pt[1]) >= 1e-6:
                normalized.append(pt)
        if (
            len(normalized) >= 3
            and math.hypot(
                normalized[0][0] - normalized[-1][0],
                normalized[0][1] - normalized[-1][1],
            )
            < 1e-6
        ):
            normalized[-1] = normalized[0]
        return normalized

    def smooth_selected(self, iterations: int = 2) -> int:
        """Smooth jagged selected polylines using Chaikin's corner-cutting
        algorithm — repeatedly cuts corners, rounding sharp vertices into a
        smooth curve while preserving the overall shape. Closed shapes stay
        closed; open polylines keep their endpoints fixed. Returns the
        number of shapes smoothed.
        """
        indices = self._mutable_selected_indices()
        to_smooth = [i for i in indices if len(self._entities[i].points) >= 3]
        if not to_smooth:
            return 0
        self._push_undo()
        count = 0
        for idx in to_smooth:
            poly = self._entities[idx].points
            closed = self._is_poly_closed(poly)
            pts = poly[:-1] if closed else list(poly)
            if len(pts) < 3:
                continue
            for _ in range(max(1, iterations)):
                pts = self._chaikin_pass(pts, closed=closed)
            if closed:
                pts.append(pts[0])
            self._entities[idx].points = pts
            # Smoothing invalidates any parametric meta (circle/arc/rect/…);
            # the result is a plain freeform polyline.
            self._entities[idx].kind = "polyline"
            self._entities[idx].meta = None
            count += 1
        if count == 0:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return count

    @staticmethod
    def _chaikin_pass(
        pts: list[tuple[float, float]], *, closed: bool
    ) -> list[tuple[float, float]]:
        """One Chaikin corner-cutting pass: replaces each corner with two
        points 1/4 and 3/4 along its adjoining segments."""
        n = len(pts)
        out: list[tuple[float, float]] = []
        if closed:
            for i in range(n):
                p0 = pts[i]
                p1 = pts[(i + 1) % n]
                out.append((p0[0] * 0.75 + p1[0] * 0.25, p0[1] * 0.75 + p1[1] * 0.25))
                out.append((p0[0] * 0.25 + p1[0] * 0.75, p0[1] * 0.25 + p1[1] * 0.75))
        else:
            out.append(pts[0])
            for i in range(n - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                out.append((p0[0] * 0.75 + p1[0] * 0.25, p0[1] * 0.75 + p1[1] * 0.25))
                out.append((p0[0] * 0.25 + p1[0] * 0.75, p0[1] * 0.25 + p1[1] * 0.75))
            out.append(pts[-1])
        return out

    def simplify_selected(self, tolerance: float = 0.2) -> int:
        """Reduce vertex count on selected polylines via Douglas-Peucker
        simplification (Shapely), preserving overall shape within
        ``tolerance`` mm. Closed shapes stay closed. Returns the number of
        shapes actually simplified (unchanged/degenerate results are
        skipped).
        """
        indices = self._mutable_selected_indices()
        to_simplify = [i for i in indices if len(self._entities[i].points) >= 3]
        if not to_simplify:
            return 0
        self._push_undo()
        count = 0
        for idx in to_simplify:
            poly = self._entities[idx].points
            closed = self._is_poly_closed(poly)
            try:
                simplified = LineString(poly).simplify(
                    tolerance, preserve_topology=False
                )
            except (GEOSException, ValueError):
                continue
            coords = [(float(x), float(y)) for x, y in simplified.coords]
            if len(coords) < 2:
                continue
            if closed and coords[0] != coords[-1]:
                coords.append(coords[0])
            if len(coords) >= len(poly):
                continue  # no reduction achieved — leave the shape as-is
            self._entities[idx].points = coords
            # Simplification invalidates any parametric meta (circle/arc/
            # rect/…); the result is a plain freeform polyline.
            self._entities[idx].kind = "polyline"
            self._entities[idx].meta = None
            count += 1
        if count == 0:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return count

    # ── Base right-click handling + vertex ops (restored from _select/_edit mixins) ──

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if self._mode == "draw":
            if self._draw_shape_preview_active and self._shape_primitive_active():
                self._cancel_draw_points()
                self._show_flash("Shape preview canceled", 700)
                return
            # Right-click = finish open polyline (no close), stay in draw mode
            self._finish_draw(close=False)
            return

        if self._mode == "edit":
            hit = self._find_nearest_vertex(cx, cy)
            if hit is not None:
                pi, vi = hit
                menu = QMenu()

                def _prompt_round_corner() -> None:
                    self._show_hud_prompt(
                        "Round radius (mm)",
                        1.0,
                        lambda r: self._round_vertex(pi, vi, r),
                        minimum=0.01,
                    )

                def _prompt_chamfer_corner() -> None:
                    self._show_hud_prompt(
                        "Chamfer distance (mm)",
                        1.0,
                        lambda d: self._chamfer_vertex(pi, vi, d),
                        minimum=0.01,
                    )

                poly = self._entities[pi].points
                is_closed = (
                    len(poly) >= 4
                    and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                    < 0.01
                )
                unique_count = len(poly) - 1 if is_closed else len(poly)
                if unique_count > 3:
                    menu.addAction("Delete vertex", lambda: self._delete_vertex(pi, vi))
                if (is_closed and unique_count >= 3) or (
                    not is_closed and 0 < vi < len(poly) - 1
                ):
                    menu.addAction("Round corner…", _prompt_round_corner)
                    menu.addAction("Chamfer corner…", _prompt_chamfer_corner)
                menu.addAction("Delete polyline", lambda: self._delete_poly(pi))
                menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))
            return

    def _delete_poly(self, pi: int) -> None:
        self._push_undo()
        # Previously popped only _polys, silently desyncing kinds/meta —
        # _compact_entities removes the entity and remaps all flag state.
        self._compact_entities({pi})
        self._sel = {i if i < pi else i - 1 for i in self._sel if i != pi}
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _delete_vertex(self, pi: int, vi: int) -> None:
        # Check if shape is currently closed BEFORE deletion
        poly = self._entities[pi].points
        is_closed = (
            len(poly) >= 4
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        )

        self._push_undo()
        self._entities[pi].points.pop(vi)
        self._redraw()

        # Re-close shape if it was closed before deletion
        if is_closed and len(self._entities[pi].points) >= 4:
            self._entities[pi].points[-1] = self._entities[pi].points[0]
        self._notify()
        self._fire_poly_change()

    def _chamfer_vertex(self, pi: int, vi: int, dist: float) -> bool:
        if not (0 <= pi < len(self._entities)) or dist <= 0:
            return False
        poly = self._entities[pi].points
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        v1 = (ax - bx, ay - by)
        v2 = (cx - bx, cy - by)
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        d = min(dist, l1 * 0.45, l2 * 0.45)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        p1 = (bx + u1[0] * d, by + u1[1] * d)
        p2 = (bx + u2[0] * d, by + u2[1] * d)

        new_pts = pts[:vi] + [p1, p2] + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        self._push_undo()
        self._entities[pi].points = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _round_vertex(self, pi: int, vi: int, radius: float) -> bool:
        if not (0 <= pi < len(self._entities)) or radius <= 0:
            return False
        poly = self._entities[pi].points
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        u1 = (ax - bx, ay - by)
        u2 = (cx - bx, cy - by)
        l1 = math.hypot(*u1)
        l2 = math.hypot(*u2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        u1 = (u1[0] / l1, u1[1] / l1)
        u2 = (u2[0] / l2, u2[1] / l2)
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        phi = math.acos(dot)
        if phi < 1e-3 or abs(math.pi - phi) < 1e-3:
            return False

        offset = radius / math.tan(phi / 2.0)
        offset = min(offset, l1 * 0.45, l2 * 0.45)
        if offset <= 1e-6:
            return False
        r = offset * math.tan(phi / 2.0)

        t1 = (bx + u1[0] * offset, by + u1[1] * offset)
        t2 = (bx + u2[0] * offset, by + u2[1] * offset)

        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        if bl < 1e-9:
            return False
        bis = (bis[0] / bl, bis[1] / bl)
        center_dist = r / math.sin(phi / 2.0)
        center = (bx + bis[0] * center_dist, by + bis[1] * center_dist)

        a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
        a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
        # Use the minor arc between tangent points; choosing the major arc
        # produces loop-like rounding artifacts.
        span = a2 - a1
        while span <= -math.pi:
            span += 2 * math.pi
        while span > math.pi:
            span -= 2 * math.pi
        steps = max(4, min(24, int(abs(span) / (math.pi / 18.0))))
        arc_pts = [
            (
                center[0] + r * math.cos(a1 + span * (i / steps)),
                center[1] + r * math.sin(a1 + span * (i / steps)),
            )
            for i in range(steps + 1)
        ]

        new_pts = pts[:vi] + arc_pts + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        self._push_undo()
        self._entities[pi].points = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

