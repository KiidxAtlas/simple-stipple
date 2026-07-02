"""PolylineView — interactive pan/zoom QGraphicsView with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, TypeAlias

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
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
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
from shapely.ops import split as shapely_split

from src.backend.geometry.arc import (
    arc_from_center_start_end,
    arc_from_three_points,
)
from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.constants import DRAG_THRESH, Q_BG
from src.ui.canvas._constants import EDGE_HIT as _EDGE_HIT
from src.ui.canvas._constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas._constants import SNAP_DIST as _SNAP_DIST
from src.ui.canvas._constants import VERT_HIT as _VERT_HIT

from src.backend.shapes.factory import ShapeFactory, transform_legacy_meta
from src.ui.canvas.entities import (
    EntityRecord,
    FlagSetView,
    GroupsView,
)
from src.ui.canvas.render import CanvasRenderer
from src.ui.canvas.shape_snapping import ShapeSnapEngine
from src.ui.core.focus_policy import blur_focused_line_edit
from src.backend.behaviors.snapping import (
    resolve_snap as _legacy_resolve_snap,
    resolve_drag_snap as _legacy_resolve_drag_snap,
    snap_to_polyline as _legacy_snap_to_polyline,
    angle_snap as _legacy_angle_snap,
)
from src.ui.sidebars.canvas_sidebar import DrawSidebar
from src.ui.widgets.tool_picker_dialog import ToolPickerDialog

# Undo snapshot: the full entity list (geometry, kind, meta, and all flags —
# the old 5-tuple silently dropped hidden/locked/group state) + selection.
CanvasState: TypeAlias = tuple[list[EntityRecord], set[int]]



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
    QGraphicsView,
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

    @property
    def _construction_polys(self) -> FlagSetView:
        view = self.__dict__.get("_construction_view")
        if view is None:
            view = self.__dict__["_construction_view"] = FlagSetView(self, "construction")
        return view

    @_construction_polys.setter
    def _construction_polys(self, indices) -> None:
        self._construction_polys.replace(indices)

    @property
    def _hidden_polys(self) -> FlagSetView:
        view = self.__dict__.get("_hidden_view")
        if view is None:
            view = self.__dict__["_hidden_view"] = FlagSetView(self, "hidden")
        return view

    @_hidden_polys.setter
    def _hidden_polys(self, indices) -> None:
        self._hidden_polys.replace(indices)

    @property
    def _locked_polys(self) -> FlagSetView:
        view = self.__dict__.get("_locked_view")
        if view is None:
            view = self.__dict__["_locked_view"] = FlagSetView(self, "locked")
        return view

    @_locked_polys.setter
    def _locked_polys(self, indices) -> None:
        self._locked_polys.replace(indices)

    @property
    def _groups(self) -> GroupsView:
        view = self.__dict__.get("_groups_view")
        if view is None:
            view = self.__dict__["_groups_view"] = GroupsView(self)
        return view

    @_groups.setter
    def _groups(self, mapping) -> None:
        self._groups.replace(mapping)

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
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(Q_BG))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._selectable = selectable
        self._empty_message = "No polylines loaded"
        self._show_selection_bbox: bool = False
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        # Single source of truth for drawable entities. The legacy names
        # (_polys / _entity_kinds / _entity_meta) are live views over this
        # list — see src/ui/canvas/entities.py.
        self._entities: list[EntityRecord] = []
        self._sel: set[int] = set()

        # Lazily-built Shape objects for the snap engine (invalidated on
        # any structural/geometry change).
        self._snap_shapes_cache: list | None = None

        # construction/hidden/locked/group flags live on EntityRecord;
        # _construction_polys/_hidden_polys/_locked_polys/_groups are live
        # views (see the properties above).
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

        # Undo / redo stacks
        self._undo_stack: list[CanvasState] = []
        self._redo_stack: list[CanvasState] = []

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

        # Move state (select mode drag-to-move)
        self._move_dragging: bool = False
        self._move_origin: tuple[float, float] | None = None
        self._move_undo_pushed: bool = False
        self._move_snap_exclude_vertices: set[tuple[int, int]] = set()
        self._move_snap_exclude_segments: set[tuple[int, int]] = set()

        # Clipboard
        self._clipboard: list[dict[str, Any]] = []

        # Nudge undo debounce
        self._nudge_undo_pushed: bool = False

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
        self._gizmo_drag_mode: str | None = None  # "scale" | "rotate"
        self._gizmo_center_w: tuple[float, float] | None = None
        self._gizmo_start_vec: tuple[float, float] | None = None
        self._gizmo_snapshot: dict[int, list[tuple[float, float]]] = {}
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
        self._entities = [EntityRecord(points=list(p)) for p in polys]
        self._sel.clear()
        self._hidden_polys.clear()
        self._locked_polys.clear()
        self._groups.clear()
        self._group_labels.clear()
        self._construction_polys.clear()

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
        self._entities = [EntityRecord(points=list(p)) for p in polys]
        self._sel.clear()
        self._construction_polys.clear()
        self._hidden_polys.clear()
        self._locked_polys.clear()
        self._groups.clear()
        self._group_labels.clear()

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
            "hidden_indices": sorted(self._hidden_polys),
            "locked_indices": sorted(self._locked_polys),
            "groups": {str(k): v for k, v in self._groups.items()},
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
        self._hidden_polys = {
            i for i in hidden_state if isinstance(i, int) and 0 <= i < len(self._entities)
        }
        self._locked_polys = {
            i for i in locked_state if isinstance(i, int) and 0 <= i < len(self._entities)
        }
        raw_groups = state.get("groups", {})
        if isinstance(raw_groups, dict):
            self._groups = {
                int(k): int(v)
                for k, v in raw_groups.items()
                if str(k).lstrip("-").isdigit()
                and str(v).lstrip("-").isdigit()
                and 0 <= int(k) < len(self._entities)
            }
            self._next_group_id = max(self._groups.values(), default=0) + 1
        raw_labels = state.get("group_labels", {})
        if isinstance(raw_labels, dict):
            self._group_labels = {
                int(k): str(v)
                for k, v in raw_labels.items()
                if str(k).lstrip("-").isdigit() and str(v).strip()
            }
        self._sel -= self._hidden_polys
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
            )
        )
        self._sync_shape_storage_from_entities()
        return len(self._entities) - 1

    def _snapshot_state(self) -> CanvasState:
        return (deepcopy(self._entities), set(self._sel))

    def _restore_state_snapshot(self, snapshot: CanvasState) -> None:
        entities, sel = snapshot
        # The snapshot was popped off its stack, so we can install the
        # records directly without another copy.
        self._entities = list(entities)
        self._sel = {i for i in sel if i < len(self._entities)}
        self._sync_shape_storage_from_entities()

    def _reset_edit_interaction_state(self) -> None:
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None

    @staticmethod
    def _push_stack_capped(stack: list[CanvasState], snapshot: CanvasState) -> None:
        stack.append(snapshot)
        if len(stack) > 30:
            stack.pop(0)

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
        new_sel = {
            idx
            for idx in indices
            if 0 <= idx < len(self._entities) and idx not in self._hidden_polys
        }
        if new_sel == self._sel:
            return
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_hidden_indices(self, indices: list[int]) -> None:
        new_hidden = {idx for idx in indices if 0 <= idx < len(self._entities)}
        new_sel = self._sel - new_hidden
        if new_hidden == self._hidden_polys and new_sel == self._sel:
            return
        self._hidden_polys = new_hidden
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_locked_indices(self, indices: list[int]) -> None:
        new_locked = {idx for idx in indices if 0 <= idx < len(self._entities)}
        if new_locked == self._locked_polys:
            return
        self._locked_polys = new_locked
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
    def delete_selected(self) -> int:
        delete_set = {idx for idx in self._sel if idx not in self._locked_polys}
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
        if not self._undo_stack:
            return False
        self._push_stack_capped(self._redo_stack, self._snapshot_state())
        self._restore_state_snapshot(self._undo_stack.pop())
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._push_stack_capped(self._undo_stack, self._snapshot_state())
        self._restore_state_snapshot(self._redo_stack.pop())
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def select_all(self) -> None:
        self._sel = set(range(len(self._entities))) - self._hidden_polys
        self._redraw()
        self._notify()

    def deselect_all(self) -> None:
        self._sel.clear()
        self._redraw()
        self._notify()

    def _invert_selection(self) -> None:
        """Invert selection: select all unselected, deselect all selected."""
        all_indices = set(range(len(self._entities))) - self._hidden_polys
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
        if self._mode == "edit":
            if getattr(self, "_edit_selected_verts", None):
                self._delete_edit_vertices(set(getattr(self, "_edit_selected_verts", set())))
                return
            if getattr(self, "_hover_vert", None) is not None:
                self._delete_edit_vertices({self._hover_vert})
                return
        if self._mode == "select":
            self.delete_selected()

    def _key_backspace(self) -> None:
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
            if pi in self._locked_polys:
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
                "construction": i in getattr(self, "_construction_polys", set()),
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
                self._construction_polys.add(new_idx)
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
        cut_set = {idx for idx in self._sel if idx not in getattr(self, "_locked_polys", set())}
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
        mutable = [idx for idx in self._sel if idx not in getattr(self, "_locked_polys", set())]
        if not mutable:
            return
        if not getattr(self, "_nudge_undo_pushed", False):
            self._push_undo()
            object.__setattr__(self, "_nudge_undo_pushed", True)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self._reset_nudge_undo)
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

    def _reset_nudge_undo(self) -> None:
        object.__setattr__(self, "_nudge_undo_pushed", False)

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
            from src.backend.geometry.arc import arc_spec_from_center_start_end, arc_spec_from_three_points
            if getattr(self, "_draw_arc_mode", "center-start-end") == "center-start-end":
                spec = arc_spec_from_center_start_end(poly[0], poly[1], poly[2])
            else:
                spec = arc_spec_from_three_points(poly[0], poly[1], poly[2])
            if spec is not None:
                meta = {"center": spec.center, "radius": spec.radius, "start_angle": spec.start_angle, "end_angle": spec.end_angle}
        elif primitive == "spline" and len(poly) >= 2:
            kind = "spline"
            meta = {"segments": 24, "closed": close, "control_points": [tuple(pt) for pt in poly], "degree": 3}

        self._entities.append(EntityRecord(points=list(poly), kind=kind, meta=meta))
        new_idx = len(self._entities) - 1
        if getattr(self, "_draw_construction_mode", False):
            self._construction_polys.add(new_idx)

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
        new_indices: list[int] = []
        for poly in polys:
            idx = self._append_entity([(x + wx, y + wy) for x, y in poly])
            new_indices.append(idx)
        # Group the glyph contours so the text behaves as one object in the
        # canvas and shows as a single row in the layer tree.
        if len(new_indices) > 1:
            gid = self._next_group_id
            self._next_group_id += 1
            for idx in new_indices:
                self._groups[idx] = gid
        self._sel = set(new_indices)
        self._show_flash(f"Text placed ({len(new_indices)} contours)", 900)
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_indices)

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
            if idx in getattr(self, "_construction_polys", set()):
                self._construction_polys.discard(idx)
            else:
                self._construction_polys.add(idx)
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
                popped_was_construction = i in getattr(self, "_construction_polys", set())
                survivor_was_construction = survivor_idx in getattr(self, "_construction_polys", set())
                self._entities[survivor_idx].points = merged
                self._entities[survivor_idx].kind = "polyline"
                self._entities[survivor_idx].meta = None
                del self._entities[i]
                remapped: set[int] = set()
                for ci in getattr(self, "_construction_polys", set()):
                    if ci == i:
                        continue
                    remapped.add(ci - 1 if ci > i else ci)
                self._construction_polys = remapped
                if i < survivor_idx:
                    survivor_idx -= 1
                if popped_was_construction or survivor_was_construction:
                    self._construction_polys.add(survivor_idx)
                merged_any = True
                changed = True
                break
        return survivor_idx if merged_any else None

    def _delete_edit_vertices(self, verts: set[tuple[int, int]]) -> int:
        if not verts:
            return 0
        grouped: dict[int, set[int]] = {}
        for pi, vi in verts:
            if pi in getattr(self, "_locked_polys", set()):
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
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Offset Geometry", "Offset distance (mm):", 1.0, -1_000_000.0, 1_000_000.0, 3
        )
        if ok:
            self.offset_selected(value)

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
        if self._measure_mode or self._mode in ("draw", "edit"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._mode == "select" and self._hover_vert is not None and self._sel:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _push_undo(self) -> None:
        self._push_stack_capped(self._undo_stack, self._snapshot_state())
        # Soft cap on total vertices retained across the stack so a few
        # huge snapshots do not balloon process memory. A scene with
        # ~200k vertices roughly equals ~3 MB of float pairs; we keep
        # the budget generous but bounded.
        _UNDO_VERTEX_BUDGET = 200_000
        total = sum(
            sum(len(e.points) for e in entry[0]) for entry in self._undo_stack
        )
        while total > _UNDO_VERTEX_BUDGET and len(self._undo_stack) > 1:
            dropped = self._undo_stack.pop(0)
            total -= sum(len(e.points) for e in dropped[0])
        self._redo_stack.clear()

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
            if idx in self._construction_polys:
                continue
            kind = (
                self._entities[idx].kind
            )
            meta = self._entities[idx].meta
            # Grouped shapes share one layer name so downstream laser
            # software runs the whole group as a single job; ungrouped
            # shapes keep their own per-shape layer.
            gid = self._groups.get(idx)
            if gid is not None:
                default_name = self._group_labels.get(gid) or f"group_{gid + 1}"
            else:
                default_name = f"shape_{idx + 1}"
            if meta is None:
                export_meta: dict[str, Any] = {"name": default_name}
            else:
                export_meta = deepcopy(meta)
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
            })
        return result

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [pt for p in (e.points for e in self._entities) for pt in p]
        if self._img_bounds:

            bw, bh = self._img_bounds
            pts.extend([(0.0, 0.0), (bw, bh)])
        if not pts:
            return 0.0, 0.0, 1.0, 1.0
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
            if idx < len(self._entities) and idx not in self._hidden_polys
        ]

    def _mutable_selected_indices(self) -> list[int]:
        return [
            idx for idx in self._selected_indices() if idx not in self._locked_polys
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
        vp = self.viewport()
        w = max(vp.width(), 100)
        h = max(vp.height(), 100)
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
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._dismiss_shape_dim_inputs()
        self._draw_constraint = None
        self._draw_snap = None
        self._draw_snap_type = None
        self._hover_snap = None
        self._hover_snap_type = None
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
        vp = self.viewport()
        w, h = max(vp.width(), 100), max(vp.height(), 100)
        cx, cy = w / 2, h / 2
        wx, wy = self._c2w(cx, cy)
        self._scale = max(_MIN_SCALE, self._scale * factor)
        self._ox = cx - wx * self._scale
        self._oy = cy + wy * self._scale
        self._redraw()

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
            if pi in self._hidden_polys:
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
            if pi in self._hidden_polys:
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
            if pi in self._hidden_polys:
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
            if pi in self._hidden_polys:
                continue
            dist = self._closest_point_on_poly(poly, wx, wy, cx, cy)
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
        self.viewport().update()

    def paintEvent(self, event) -> None:
        """Bridge Qt paint dispatch to CanvasRenderer mixin implementation."""
        super().paintEvent(event)
        CanvasRenderer.paintEvent(self, event)

    def _start_gizmo_drag(self, mode: str, wx: float, wy: float) -> bool:
        bounds = self._selection_bounds()
        if bounds is None or not self._sel:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        vec = (wx - cx, wy - cy)
        if math.hypot(vec[0], vec[1]) < 1e-9:
            return False
        self._gizmo_drag_mode = mode
        self._gizmo_center_w = (cx, cy)
        self._gizmo_start_vec = vec
        self._gizmo_snapshot = {
            idx: list(self._entities[idx].points) for idx in self._mutable_selected_indices()
        }
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        return bool(self._gizmo_snapshot)

    def _apply_gizmo_drag(self, wx: float, wy: float) -> None:
        if (
            self._gizmo_drag_mode is None
            or self._gizmo_center_w is None
            or self._gizmo_start_vec is None
            or not self._gizmo_snapshot
        ):
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

    def _end_gizmo_drag(self) -> bool:
        moved = self._gizmo_drag_moved
        self._gizmo_drag_mode = None
        self._gizmo_center_w = None
        self._gizmo_start_vec = None
        self._gizmo_snapshot = {}
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
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
        if self._needs_fit and self._entities:
            self._needs_fit = False
            self._fit()
        else:
            self._redraw()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift_mod = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = True
            self._space_pan_dragging = False
            self._update_cursor()
            event.accept()
            return

        # Standard keyboard shortcuts
        if ctrl and self._selectable:
            if key == Qt.Key.Key_Z:
                if shift_mod:
                    self.redo()
                else:
                    self.undo()
                return
            elif key == Qt.Key.Key_Y:
                self.redo()
                return
            elif key == Qt.Key.Key_A:
                if shift_mod:
                    self.deselect_all()
                else:
                    self.select_all()
                return
            elif key == Qt.Key.Key_G:
                if shift_mod:
                    self._ungroup_selected()
                else:
                    self._group_selected()
                return
            elif key == Qt.Key.Key_I:
                # Ctrl+I: Invert selection
                self._invert_selection()
                return
            elif key == Qt.Key.Key_C:
                self._copy_selected()
                return
            elif key == Qt.Key.Key_V:
                self._paste_clipboard()
                return
            elif key == Qt.Key.Key_D and shift_mod:
                # Ctrl+Shift+D: Duplicate with offset (smarter placement)
                self._duplicate_selected_with_offset()
                return
            elif key == Qt.Key.Key_D:
                self._duplicate_selected()
                return
            elif key == Qt.Key.Key_X:
                self._cut_selected()
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

        if key == Qt.Key.Key_F:
            self.fit()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(1.15)
        elif key == Qt.Key.Key_Minus:
            self._zoom_by(1 / 1.15)
        elif key == Qt.Key.Key_G:
            self._grid_visible = not self._grid_visible
            self._redraw()
        elif key == Qt.Key.Key_M:
            self.toggle_measure()
        elif key == Qt.Key.Key_C and shift_mod and not ctrl:
            if self._mode in ("select", "edit"):
                n_closed = self._close_selected_polylines()
                if n_closed:
                    self._show_flash(f"Closed {n_closed} polyline(s)", 900)
                else:
                    self._show_flash("No open polyline selected", 900)
                return
        elif key == Qt.Key.Key_O and not ctrl and not shift_mod:
            if self._mode in ("select", "edit"):
                self._prompt_offset_selected()
                return
        elif key == Qt.Key.Key_O and shift_mod and not ctrl:
            if self._mode in ("select", "edit"):
                n_opened = self._open_selected_polylines()
                if n_opened:
                    self._show_flash(f"Opened {n_opened} polyline(s)", 900)
                else:
                    self._show_flash("No closed polyline selected", 900)
                return
        elif key == Qt.Key.Key_X and not ctrl:
            if self._sel and self._mode in ("select", "edit"):
                self._toggle_selected_construction()
                self._show_flash("Toggled construction for selection", 900)
                return
            self._draw_construction_mode = not self._draw_construction_mode
            if self._mode != "draw":
                self.set_mode("draw")
            else:
                self._redraw()
            self._show_flash(
                "Construction draw: ON"
                if self._draw_construction_mode
                else "Construction draw: OFF",
                900,
            )
            self._refresh_draw_sidebar_state()
            return
        elif key == Qt.Key.Key_Escape:
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
        elif key == Qt.Key.Key_BracketRight and not ctrl:
            # ] = increase grid spacing (Feature 13)
            new_spacing = min(100.0, self._grid_spacing * 2.0)
            self._grid_spacing = new_spacing
            self._show_flash(f"Grid: {self._grid_spacing:g} mm")
            return
        elif key == Qt.Key.Key_BracketLeft and not ctrl:
            # [ = decrease grid spacing (Feature 13)
            new_spacing = max(0.1, self._grid_spacing / 2.0)
            self._grid_spacing = new_spacing
            self._show_flash(f"Grid: {self._grid_spacing:g} mm")
            return
        elif key == Qt.Key.Key_S and not ctrl:
            # S = toggle grid snap (Feature 14)
            self._grid_snap = not self._grid_snap
            self._show_flash("Snap: ON" if self._grid_snap else "Snap: OFF")
            return
        elif self._selectable:
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
            if key == Qt.Key.Key_Delete:
                self._key_delete()
            elif key == Qt.Key.Key_Backspace:
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
            elif key == Qt.Key.Key_D:
                self.set_mode("draw" if self._mode != "draw" else "select")
            elif key == Qt.Key.Key_E:
                self.set_mode("edit" if self._mode != "edit" else "select")
            elif key == Qt.Key.Key_T and self._mode == "select":
                wx = self._cursor_wx
                wy = self._cursor_wy
                if wx is None or wy is None:
                    vp = self.viewport()
                    wx, wy = self._c2w(vp.width() / 2.0, vp.height() / 2.0)
                self.prompt_add_text(wx, wy)
            elif (
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
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
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
            elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
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
            else:
                super().keyPressEvent(event)
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.accept()
            return
        else:
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
        factor = max(0.9, min(1.1, 1.0 + delta * 0.0007))
        pos = event.position()
        wx, wy = self._c2w(pos.x(), pos.y())
        self._scale = max(_MIN_SCALE, self._scale * factor)
        self._ox = pos.x() - wx * self._scale
        self._oy = pos.y() + wy * self._scale
        self._redraw()
        event.accept()

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

        # Clicking directly on a selection badge (W/H, or L/∠ for a single
        # line) opens the inline dimension editor
        if self._mode == "select" and self._sel:
            pt = QPointF(pos.x(), pos.y())
            for axis, rect in self._sel_badge_axes():
                if rect.contains(pt):
                    self._show_sel_dim_editor(axis, rect)
                    return
            wx0, wy0 = self._c2w(pos.x(), pos.y())
            if (
                self._gizmo_rotate_rect is not None
                and self._gizmo_rotate_rect.contains(pt)
                and self._start_gizmo_drag("rotate", wx0, wy0)
            ):
                self._redraw()
                return
            if (
                self._gizmo_scale_rect is not None
                and self._gizmo_scale_rect.contains(pt)
                and self._start_gizmo_drag("scale", wx0, wy0)
            ):
                self._redraw()
                return

        if self._measure_mode:
            if self._measure_locked:
                # Click again to reset measurement
                self._measure_locked = False
                self._measure_anchor = None
                self._measure_hover = None
                self._measure_end = None
                self._measure_snapped_a = False
                self._measure_snapped_b = False
                self._dismiss_measure_edit()
                self._redraw()
                return
            wx, wy = self._c2w(pos.x(), pos.y())
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap_result = self._resolve_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
                reference_point=self._measure_anchor,
            )
            snapped = snap_result is not None
            if snapped:
                wx, wy = snap_result[0], snap_result[1]
            # Angle snap with Shift
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift and self._measure_anchor is not None:
                wx, wy = self._angle_snap(*self._measure_anchor, wx, wy)
            if self._measure_anchor is None:
                self._measure_anchor = (wx, wy)
                self._measure_hover = (wx, wy)
                self._measure_snapped_a = snapped
            else:
                self._measure_end = (wx, wy)
                self._measure_hover = (wx, wy)
                self._measure_snapped_b = snapped
                self._measure_locked = True
                self._show_measure_edit()
            self._redraw()
            return

        if self._mode == "edit":
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            hit = self._find_nearest_vertex(pos.x(), pos.y())

            if shift:
                if hit is not None:
                    if hit in self._edit_selected_verts:
                        self._edit_selected_verts.discard(hit)
                    else:
                        self._edit_selected_verts.add(hit)
                    self._redraw()
                    return
                self._shift_drag = True
                self._band_start = pos
                self._lmb_prev = pos
                self._lmb_press = None
                return

            if hit is not None:
                pi, vi = hit
                if pi in self._locked_polys:
                    return
                if pi < 0 or pi >= len(self._entities):
                    return
                if vi < 0 or vi >= len(self._entities[pi].points):
                    return
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._edit_drag_moved = False
                self._edit_undo_pushed = False
                self._edit_drag_anchor = self._entities[pi].points[vi]
                if (
                    hit in self._edit_selected_verts
                    and len(self._edit_selected_verts) > 1
                ):
                    self._edit_drag_targets = set(self._edit_selected_verts)
                else:
                    self._edit_selected_verts = {hit}
                    self._edit_drag_targets = self._linked_vertices(pi, vi)
                self._edit_linked_verts = set(self._edit_drag_targets)
                self._redraw()
                return

            if self._edit_selected_verts:
                self._edit_selected_verts.clear()
                self._redraw()
            self._lmb_press = pos
            self._lmb_prev = pos
            return

        if self._mode == "draw":
            wx, wy = self._c2w(pos.x(), pos.y())

            if self._draw_snap is not None:
                wx, wy = self._draw_snap

            if self._draw_primitive == "text":
                # Click chooses the anchor; the dialog does the rest.
                self.prompt_add_text(wx, wy)
                self.set_mode("select")
                return

            if self._draw_primitive in {"rectangle", "circle", "ellipse", "polygon"}:
                if not self._draw_shape_preview_active:
                    self._draw_shape_preview_active = True
                    self._draw_shape_anchor_w = (wx, wy)
                    self._draw_shape_cursor_w = (wx, wy)
                    if self._draw_shape_w_edit is not None:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                else:
                    self._draw_shape_cursor_w = (wx, wy)
                    self._commit_shape_preview()
                self._refresh_draw_sidebar_state()
                self._redraw()
                return

            if self._draw_primitive == "line":
                if not self._draw_pts:
                    self._draw_pts = [(wx, wy)]
                    self._draw_point_snap_types = [self._draw_snap_type or None]
                    self._refresh_draw_sidebar_state()
                    self._redraw()
                    return
                p0 = self._draw_pts[0]
                self._draw_pts = [p0, (wx, wy)]
                first_snap = (
                    self._draw_point_snap_types[0]
                    if self._draw_point_snap_types
                    else None
                )
                self._draw_point_snap_types = [first_snap, self._draw_snap_type or None]
                self._finish_draw(close=False)
                self._draw_pts.clear()
                self._draw_point_snap_types.clear()
                self._refresh_draw_sidebar_state()
                return

            if self._draw_primitive == "arc":
                self._draw_arc_pts.append((wx, wy))
                if len(self._draw_arc_pts) >= 3:
                    p0, p1, p2 = self._draw_arc_pts[:3]
                    if self._draw_arc_mode == "center-start-end":
                        arc_poly = arc_from_center_start_end(p0, p1, p2, 24)
                    else:
                        arc_poly = arc_from_three_points(p0, p1, p2, 24)
                    self._commit_drawn_polyline(
                        arc_poly,
                        primitive="arc",
                        created_flash="Arc created",
                    )
                    self._draw_arc_pts.clear()
                self._refresh_draw_sidebar_state()
                self._redraw()
                return

            if self._draw_primitive == "spline":
                self._draw_pts.append((wx, wy))
                self._draw_point_snap_types.append(self._draw_snap_type or None)
                if len(self._draw_pts) == 1:
                    self._show_dim_inputs()
                self._dim_distance_dirty = False
                self._dim_angle_dirty = False
                self._refresh_draw_sidebar_state()
                self._redraw()
                return

            # B. If dim inputs have user-typed values, compute point from those
            if self._draw_pts and (self._dim_distance_dirty or self._dim_angle_dirty):
                self._apply_dim_input()
                # Show dim inputs again for the next segment
                self._show_dim_inputs()
                return
            # Apply H/V constraint to the placed point
            if self._draw_constraint == "H" and self._draw_pts:
                wy = self._draw_pts[-1][1]
            elif self._draw_constraint == "V" and self._draw_pts:
                wx = self._draw_pts[-1][0]
            # Close polygon when clicking near start point
            if self._is_near_start():
                self._finish_draw(close=True)
                return
            # Connect to existing polyline endpoint when starting a new draw
            if not self._draw_pts:
                endpoint_snap = self._find_nearest_endpoint(pos.x(), pos.y())
                if endpoint_snap is not None:
                    wx, wy = endpoint_snap
                    self._draw_snap_type = "vertex"
            self._draw_pts.append((wx, wy))
            self._draw_point_snap_types.append(self._draw_snap_type or None)
            # B. Show dim inputs after first point is placed
            if len(self._draw_pts) == 1:
                self._show_dim_inputs()
            # Reset dirty flags for the new segment
            self._dim_distance_dirty = False
            self._dim_angle_dirty = False
            self._refresh_draw_sidebar_state()
            self._redraw()
            return

        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._shift_drag = False
        self._band_start = None
        self._band_additive = False
        self._lmb_press = pos
        self._lmb_prev = pos
        target = self._find_poly_at(pos.x(), pos.y())
        was_selected_before = target in self._sel if target is not None else False
        self._lmb_target = target

        if self._mode == "select" and self._selectable and target is None:
            # Default drag behavior in select mode is box selection.
            self._shift_drag = True
            self._band_start = pos
            self._band_additive = shift
            self._lmb_press = None
            self._lmb_prev = pos
            self._lmb_target = None
            return

        # Select-mode direct vertex editing: single-click selects the segment,
        # shows its points, and allows immediate vertex drag.
        if self._mode == "select" and target is not None:
            ctrl = bool(
                event.modifiers()
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )
            shift_toggle = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if target in self._groups:
                gid = self._groups[target]
                members = {
                    i
                    for i, g in self._groups.items()
                    if g == gid
                    and i < len(self._entities)
                    and i not in self._hidden_polys
                }
                if ctrl or shift_toggle:
                    # Toggle the whole group as one unit.
                    if members <= self._sel:
                        self._sel -= members
                    else:
                        self._sel |= members
                elif target not in self._sel:
                    self._sel = members
                # else: already selected — preserve current selection for group move
            elif ctrl or shift_toggle:
                self._sel.add(target)
            elif target not in self._sel:
                self._sel = {target}
            self._notify()
            hit = self._find_nearest_vertex(pos.x(), pos.y())
            target_kind = (
                self._entities[target].kind
            )
            if (
                hit is not None
                and hit[0] == target
                and (
                    was_selected_before
                    or target_kind in {"arc", "circle", "ellipse", "rectangle"}
                )
                and target not in self._locked_polys
            ):
                pi, vi = hit
                if pi < 0 or pi >= len(self._entities):
                    return
                if vi < 0 or vi >= len(self._entities[pi].points):
                    return
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._edit_drag_moved = False
                self._edit_undo_pushed = False
                self._edit_drag_anchor = self._entities[pi].points[vi]
                self._edit_selected_verts = {hit}
                self._edit_drag_targets = self._linked_vertices(pi, vi)
                self._edit_linked_verts = set(self._edit_drag_targets)
                self._redraw()
                return
        # Prepare for move if clicking on an already-selected poly
        if target is not None and target in self._sel:
            wx, wy = self._c2w(pos.x(), pos.y())
            self._move_origin = (wx, wy)
            self._move_dragging = False
            self._move_undo_pushed = False
            self._move_snap_exclude_vertices = set()
            self._move_snap_exclude_segments = set()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        wx, wy = self._c2w(pos.x(), pos.y())
        self._cursor_wx = wx
        self._cursor_wy = wy
        self._hover_snap = None
        self._hover_snap_type = None

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
            self._apply_gizmo_drag(wx, wy)
            self._redraw()
            return

        if self._measure_mode:
            if self._measure_locked:
                return
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap_result = self._resolve_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
            )
            if self._measure_anchor is None:
                # Pre-first-click: just track snap indicator
                self._measure_hover_pre = (
                    (snap_result[0], snap_result[1]) if snap_result else None
                )
                if snap_result is not None:
                    self._cursor_wx, self._cursor_wy = snap_result[0], snap_result[1]
                    self._hover_snap = (snap_result[0], snap_result[1])
                    self._hover_snap_type = snap_result[2]
                self._redraw()
                return
            # After anchor placed — compute hover with snap + optional angle snap
            if snap_result is not None:
                mx, my = snap_result[0], snap_result[1]
                self._hover_snap = (mx, my)
                self._hover_snap_type = snap_result[2]
            else:
                mx, my = wx, wy
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                mx, my = self._angle_snap(*self._measure_anchor, mx, my)
            self._measure_hover = (mx, my)
            self._cursor_wx, self._cursor_wy = mx, my
            self._redraw()
            return

        if (
            self._mode in ("edit", "select")
            and self._edit_dragging
            and self._edit_poly is not None
            and self._edit_vert is not None
        ):
            if self._shift_drag and self._band_start:
                self._lmb_prev = pos
                self._redraw()
                return
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            drag_snap_result = self._resolve_drag_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
                exclude_vertices=self._edit_drag_targets,
                exclude_segments=self._immediate_segments_for_vertices(
                    self._edit_drag_targets
                ),
                reference_point=self._entities[self._edit_poly].points[self._edit_vert],
            )
            snap_wx, snap_wy = wx, wy
            snap_type = ""
            if drag_snap_result is not None:
                snap_wx, snap_wy, snap_type = drag_snap_result

            anchor_for_constraint = self._edit_drag_anchor

            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                anchor_pt = anchor_for_constraint
                if anchor_pt is not None:
                    dx = snap_wx - anchor_pt[0]
                    dy = snap_wy - anchor_pt[1]
                    if abs(dx) >= abs(dy):
                        snap_wy = anchor_pt[1]
                        snap_type = "horizontal"
                    else:
                        snap_wx = anchor_pt[0]
                        snap_type = "vertical"

            cur_pt = self._entities[self._edit_poly].points[self._edit_vert]
            if abs(cur_pt[0] - snap_wx) > 1e-9 or abs(cur_pt[1] - snap_wy) > 1e-9:
                if not self._edit_undo_pushed:
                    self._push_undo()
                    self._edit_undo_pushed = True
                self._edit_drag_moved = True

            self._apply_edit_vertex_position(snap_wx, snap_wy)
            self._cursor_wx, self._cursor_wy = snap_wx, snap_wy
            if snap_type:
                self._hover_snap = (snap_wx, snap_wy)
                self._hover_snap_type = snap_type
            self._redraw()
            return

        if self._mode == "edit":
            if self._shift_drag and self._band_start:
                self._lmb_prev = pos
                self._redraw()
                return
            old_hover = self._hover_vert
            self._hover_vert = self._find_nearest_vertex(pos.x(), pos.y())
            if self._hover_vert != old_hover:
                self._update_cursor()
                self._redraw()
            return

        if self._mode == "select" and self._sel:
            old_hover = self._hover_vert
            hit = self._find_nearest_vertex(pos.x(), pos.y())
            if hit is not None and hit[0] in self._sel:
                self._hover_vert = hit
            else:
                self._hover_vert = None
            if self._hover_vert != old_hover:
                self._update_cursor()
                self._redraw()
                return
        elif self._mode == "select" and self._hover_vert is not None:
            self._hover_vert = None
            self._update_cursor()

        if self._mode == "draw":
            if self._draw_shape_preview_active:
                self._draw_shape_cursor_w = (wx, wy)
                self._cursor_wx = wx
                self._cursor_wy = wy
                self._update_shape_size_fields_from_preview()
                self._redraw()
                return
            # 1. Resolve snap target
            allow_snap = not bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            snap_result = self._resolve_snap(
                pos.x(),
                pos.y(),
                wx,
                wy,
                allow_polyline=allow_snap,
                allow_grid=allow_snap,
            )
            if snap_result is not None:
                self._draw_snap = (snap_result[0], snap_result[1])
                self._draw_snap_type = snap_result[2]
            else:
                self._draw_snap = None
                self._draw_snap_type = None

            # 2. Determine effective position (snap or raw cursor)
            eff_x = self._draw_snap[0] if self._draw_snap else wx
            eff_y = self._draw_snap[1] if self._draw_snap else wy

            # 3. Angle snap with Shift
            shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift_held and self._draw_pts:
                anchor = self._draw_pts[-1]
                eff_x, eff_y = self._angle_snap(anchor[0], anchor[1], eff_x, eff_y)
                self._draw_snap = (eff_x, eff_y)
                self._angle_snap_active = True
            else:
                self._angle_snap_active = False

            # 4. Explicit draw constraint locks, then fallback auto-detection.
            #    A user-typed angle takes highest precedence: the segment locks
            #    to that ray with live feedback, and the pointer sets the length.
            self._draw_constraint = None
            typed_angle = self._typed_draw_angle()
            if self._draw_pts and typed_angle is not None:
                last_wx, last_wy = self._draw_pts[-1]
                ar = math.radians(typed_angle)
                dirx, diry = math.cos(ar), math.sin(ar)
                typed_dist = self._typed_draw_distance()
                if typed_dist is not None:
                    length = typed_dist
                else:
                    proj = (eff_x - last_wx) * dirx + (eff_y - last_wy) * diry
                    length = max(0.0, proj)
                eff_x = last_wx + dirx * length
                eff_y = last_wy + diry * length
                self._draw_snap = (eff_x, eff_y)
                self._angle_snap_active = True
                self._draw_constraint = f"∠{typed_angle:g}°"
            elif self._draw_pts:
                last_wx, last_wy = self._draw_pts[-1]
                if self._draw_constraint_lock == "H":
                    self._draw_constraint = "H"
                    eff_y = last_wy
                    if self._draw_snap is not None:
                        self._draw_snap = (self._draw_snap[0], last_wy)
                elif self._draw_constraint_lock == "V":
                    self._draw_constraint = "V"
                    eff_x = last_wx
                    if self._draw_snap is not None:
                        self._draw_snap = (last_wx, self._draw_snap[1])
                elif self._draw_constraint_lock == "45":
                    self._draw_constraint = "45"
                    eff_x, eff_y = self._angle_snap(last_wx, last_wy, eff_x, eff_y)
                    self._draw_snap = (eff_x, eff_y)
                    self._angle_snap_active = True
                else:
                    seg_dx = eff_x - last_wx
                    seg_dy = eff_y - last_wy
                    seg_dist = math.hypot(seg_dx, seg_dy)
                    if seg_dist > 1e-9:
                        seg_angle = math.degrees(math.atan2(seg_dy, seg_dx)) % 360
                        if seg_angle < 3 or seg_angle > 357 or (177 < seg_angle < 183):
                            self._draw_constraint = "H"
                            eff_y = last_wy
                            if self._draw_snap is not None:
                                self._draw_snap = (self._draw_snap[0], last_wy)
                        elif 87 < seg_angle < 93 or 267 < seg_angle < 273:
                            self._draw_constraint = "V"
                            eff_x = last_wx
                            if self._draw_snap is not None:
                                self._draw_snap = (last_wx, self._draw_snap[1])

            # 5. Update cursor to final effective position (all modifications applied)
            self._cursor_wx = eff_x
            self._cursor_wy = eff_y

            # 6. Update dimension HUD position and values
            if self._draw_pts:
                last_wx, last_wy = self._draw_pts[-1]
                eff_wx = self._cursor_wx if self._cursor_wx is not None else wx
                eff_wy = self._cursor_wy if self._cursor_wy is not None else wy
                seg_dist = math.hypot(eff_wx - last_wx, eff_wy - last_wy)
                seg_angle = math.degrees(math.atan2(eff_wy - last_wy, eff_wx - last_wx))
                cur_cx, cur_cy = self._w2c(eff_wx, eff_wy)
                self._update_dim_positions(cur_cx, cur_cy)
                self._update_dim_values(seg_dist, seg_angle)

            self._redraw()
            return

        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._shift_drag and self._band_start:
                self._lmb_prev = pos
                self._redraw()
                return
            # Move selected shapes
            if self._move_origin is not None and self._lmb_press is not None:
                dx_px = pos.x() - self._lmb_press.x()
                dy_px = pos.y() - self._lmb_press.y()
                if not self._move_dragging and (
                    abs(dx_px) > DRAG_THRESH or abs(dy_px) > DRAG_THRESH
                ):
                    self._move_dragging = True
                    self._nudge_undo_pushed = False
                    self._move_snap_exclude_vertices = self._vertices_for_polylines(
                        set(self._sel)
                    )
                    self._move_snap_exclude_segments = self._segments_for_polylines(
                        set(self._sel)
                    )
                if self._move_dragging:
                    if not self._move_undo_pushed:
                        self._push_undo()
                        self._move_undo_pushed = True
                    new_wx, new_wy = self._c2w(pos.x(), pos.y())
                    move_snap_type = ""
                    allow_snap = not bool(
                        event.modifiers() & Qt.KeyboardModifier.AltModifier
                    )
                    if allow_snap:
                        move_snap = self._resolve_drag_snap(
                            pos.x(),
                            pos.y(),
                            new_wx,
                            new_wy,
                            allow_polyline=True,
                            allow_grid=True,
                            exclude_vertices=self._move_snap_exclude_vertices,
                            exclude_segments=self._move_snap_exclude_segments,
                        )
                        if move_snap is not None:
                            new_wx, new_wy, move_snap_type = move_snap
                    dx_w = new_wx - self._move_origin[0]
                    dy_w = new_wy - self._move_origin[1]
                    for idx in self._sel:
                        if idx in self._locked_polys:
                            continue
                        if idx < 0 or idx >= len(self._entities):
                            continue
                        self._entities[idx].points = [
                            (x + dx_w, y + dy_w) for x, y in self._entities[idx].points
                        ]
                        self._transform_entity_meta(
                            idx,
                            center=(0.0, 0.0),
                            kind=self._entities[idx].kind,
                            meta=self._entities[idx].meta,
                            transform="translate",
                            dx=dx_w,
                            dy=dy_w,
                        )
                    self._move_origin = (new_wx, new_wy)
                    self._cursor_wx, self._cursor_wy = new_wx, new_wy
                    if move_snap_type:
                        self._hover_snap = (new_wx, new_wy)
                        self._hover_snap_type = move_snap_type
                    self._redraw()
                    return
            if self._lmb_prev:
                self._ox += pos.x() - self._lmb_prev.x()
                self._oy += pos.y() - self._lmb_prev.y()
                self._lmb_prev = pos
                self._redraw()
        else:
            # Passive hover in select mode: only repaint if the displayed
            # cursor-position text (2 decimal places) actually changed.
            if self._mode == "select":
                _prev_cx = getattr(self, "_prev_cursor_display", None)
                _cur_cx = (round(wx, 2), round(wy, 2))
                if _prev_cx == _cur_cx:
                    return
                self._prev_cursor_display = _cur_cx
            self._redraw()

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

        if self._measure_mode:
            return

        if self._mode in ("edit", "select") and self._edit_dragging:
            self._edit_dragging = False
            self._edit_linked_verts = set()
            self._edit_drag_targets = set()
            self._edit_drag_anchor = None
            self._redraw()
            self._notify()
            if self._edit_drag_moved:
                self._fire_poly_change()
            self._edit_drag_moved = False
            self._edit_undo_pushed = False
            return

        if self._mode == "edit" and self._shift_drag and self._band_start:
            bx, by = self._band_start.x(), self._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            self._select_edit_vertices_in_rect(x1c, y1c, x2c, y2c, additive=True)
            self._shift_drag = False
            self._band_start = None
            self._lmb_prev = None
            self._redraw()
            return

        if self._mode == "draw":
            return

        if self._shift_drag and self._band_start and self._selectable:
            bx, by = self._band_start.x(), self._band_start.y()
            x1c, x2c = min(bx, pos.x()), max(bx, pos.x())
            y1c, y2c = min(by, pos.y()), max(by, pos.y())
            if not self._band_additive:
                self._sel.clear()
            for idx, poly in enumerate(e.points for e in self._entities):
                pts_c = [self._w2c(x, y) for x, y in poly]
                if any(x1c <= cx <= x2c and y1c <= cy <= y2c for cx, cy in pts_c):
                    self._sel.add(idx)
            self._redraw()
            self._notify()
            self._shift_drag = False
            self._band_start = None
            self._band_additive = False
            return

        if self._move_dragging:
            # Move completed — already applied incrementally
            self._move_dragging = False
            self._move_origin = None
            self._move_undo_pushed = False
            self._move_snap_exclude_vertices = set()
            self._move_snap_exclude_segments = set()
            self._lmb_press = None
            self._lmb_prev = None
            self._lmb_target = None
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return

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
        self._move_snap_exclude_vertices = set()
        self._move_snap_exclude_segments = set()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if self._mode == "select" and self._selectable:
            hit = self._find_poly_at(pos.x(), pos.y())
            if hit is not None:
                shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if shift:
                    self._sel = self._connected_poly_indices(hit)
                    self._show_flash(f"Object selected ({len(self._sel)})", 800)
                else:
                    self._sel = {hit}
                self._redraw()
                self._notify()
            return
        if self._mode == "draw":
            # Double-click finishes and closes the polygon (Fusion 360 behavior)
            if len(self._draw_pts) >= 3:
                self._finish_draw(close=True)
            else:
                self._finish_draw()
            return
        if self._mode == "edit":
            hit = self._find_nearest_edge(pos.x(), pos.y())
            if hit is not None:
                pi, seg_idx, pt = hit
                if pi < 0 or pi >= len(self._entities):
                    return
                poly = self._entities[pi].points
                if seg_idx + 1 > len(poly):
                    return
                self._push_undo()
                poly.insert(seg_idx + 1, pt)
                self._redraw()
                self._notify()
                self._fire_poly_change()

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

        def _carry_flags(src_idx: int, ni: int) -> None:
            if src_idx in self._construction_polys:
                new_construction.add(ni)
            if src_idx in self._hidden_polys:
                new_hidden.add(ni)
            if src_idx in self._locked_polys:
                new_locked.add(ni)

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
            if src_idx in self._groups:
                new_groups[ni] = self._groups[src_idx]

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
            EntityRecord(points=p, kind=k, meta=m)
            for p, k, m in zip(result_polys, result_kinds, result_meta)
        ]
        self._construction_polys = new_construction
        self._hidden_polys = new_hidden
        self._locked_polys = new_locked
        self._groups = new_groups
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

    def _shape_snap_candidate(self, cx: float, cy: float) -> tuple[float, float, str] | None:
        best: tuple[float, float, str] | None = None
        best_dist = float("inf")
        for shape in self._snap_shapes():
            if not getattr(shape, "visible", True):
                continue
            for sx, sy, snap_type in ShapeSnapEngine.get_snap_candidates(shape):
                pcx, pcy = self._w2c(sx, sy)
                dist = math.hypot(cx - pcx, cy - pcy)
                if dist <= ShapeSnapEngine.SNAP_RADIUS and dist < best_dist:
                    best_dist = dist
                    best = (sx, sy, snap_type)
        return best

    def _pick_better_snap(
        self,
        cx: float,
        cy: float,
        first: tuple[float, float, str] | None,
        second: tuple[float, float, str] | None,
    ) -> tuple[float, float, str] | None:
        if first is None:
            return second
        if second is None:
            return first
        fcx, fcy = self._w2c(first[0], first[1])
        scx, scy = self._w2c(second[0], second[1])
        fd = math.hypot(cx - fcx, cy - fcy)
        sd = math.hypot(cx - scx, cy - scy)
        return second if sd < fd else first

    def _snap_to_polyline(
        self,
        cx: float,
        cy: float,
        *,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return _legacy_snap_to_polyline(
            cx, cy, [e.points for e in self._entities], self._hidden_polys, self._scale,
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
        legacy = _legacy_resolve_snap(
            cx, cy, wx, wy, allow_polyline=allow_polyline, allow_grid=allow_grid,
            grid_snap_enabled=self._grid_snap, grid_spacing=self._grid_spacing,
            polylines=[e.points for e in self._entities], hidden_polys=self._hidden_polys, scale=self._scale,
            w2c=self._w2c, c2w=self._c2w, poly_bounds=self._poly_bounds,
            is_poly_closed=self._is_poly_closed, segment_intersection_point=self._segment_intersection_point,
            mode=self._mode, reference_point=reference_point, draw_points=self._draw_pts,
        )
        shape_candidate = self._shape_snap_candidate(cx, cy)
        return self._pick_better_snap(cx, cy, legacy, shape_candidate)

    def _resolve_drag_snap(
        self,
        cx: float, cy: float, wx: float, wy: float,
        *,
        allow_polyline: bool = True, allow_grid: bool = True, allow_vertex: bool = True,
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        legacy = _legacy_resolve_drag_snap(
            cx, cy, wx, wy, allow_polyline=allow_polyline, allow_grid=allow_grid,
            allow_vertex=allow_vertex, grid_snap_enabled=self._grid_snap,
            grid_spacing=self._grid_spacing, polylines=[e.points for e in self._entities], hidden_polys=self._hidden_polys,
            scale=self._scale, w2c=self._w2c, c2w=self._c2w, poly_bounds=self._poly_bounds,
            is_poly_closed=self._is_poly_closed, segment_intersection_point=self._segment_intersection_point,
            mode=self._mode, exclude_vertices=exclude_vertices, exclude_segments=exclude_segments,
            reference_point=reference_point, draw_points=self._draw_pts,
        )
        shape_candidate = self._shape_snap_candidate(cx, cy)
        return self._pick_better_snap(cx, cy, legacy, shape_candidate)

    def _angle_snap(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        return _legacy_angle_snap(ax, ay, wx, wy)

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
            created.append((offset_poly, idx in self._construction_polys))
        if not created:
            return 0

        self._push_undo()
        new_sel: set[int] = set()
        for poly, is_construction in created:
            # _append_entity keeps _entity_kinds/_entity_meta in sync — a bare
            # _polys.append desyncs them and corrupts later DXF export.
            new_idx = self._append_entity(poly)
            if is_construction:
                self._construction_polys.add(new_idx)
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
            self._construction_polys.add(new_idx)
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

    def align_selected(self, mode: str) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if len(indices) < 2 or bounds is None:
            return False
        bx0, by0, bx1, by1 = bounds
        center_x = (bx0 + bx1) / 2.0
        center_y = (by0 + by1) / 2.0
        self._push_undo()
        for idx in indices:
            px0, py0, px1, py1 = self._poly_bounds(self._entities[idx].points)
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
            else:
                return False
            self._entities[idx].points = [(x + dx, y + dy) for x, y in self._entities[idx].points]
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
            created.append((offset_poly, idx in self._construction_polys))
        if not created:
            return 0

        self._push_undo()
        new_sel: set[int] = set()
        for poly, is_construction in created:
            # _append_entity keeps _entity_kinds/_entity_meta in sync — a bare
            # _polys.append desyncs them and corrupts later DXF export.
            new_idx = self._append_entity(poly)
            if is_construction:
                self._construction_polys.add(new_idx)
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
            parent=self.viewport(),
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
        self._tool_picker_dialog = ToolPickerDialog(parent=self.viewport())

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
        y = 8
        target_h = max(260, self.viewport().height() - 16)
        self._draw_sidebar.setFixedHeight(min(430, target_h))
        x = 8 if self._draw_sidebar_visible else -self._draw_sidebar.width() + 20
        self._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._draw_sidebar is None or self._draw_sidebar_anim is None:
            return
        if self._draw_sidebar_visible == visible and self._draw_sidebar.isVisible():
            self._refresh_draw_sidebar_state()
            return

        self._draw_sidebar_visible = visible
        self._refresh_draw_sidebar_state()
        y = 8
        hidden_x = -self._draw_sidebar.width() + 20
        shown_x = 8
        self._draw_sidebar.setFixedHeight(
            min(430, max(260, self.viewport().height() - 16))
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
        ):
            if self._tool_picker_dialog.exec() == 1:  # QDialog.Accepted
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
            self._groups[idx] = gid
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
        ungrouped = {self._groups.pop(idx) for idx in self._sel if idx in self._groups}
        if not ungrouped:
            return
        # Also remove other group members if their whole group is being dissolved
        stale = {idx for idx, gid in list(self._groups.items()) if gid in ungrouped}
        for idx in stale:
            self._groups.pop(idx, None)
        for gid in ungrouped:
            self._group_labels.pop(gid, None)
        self._show_flash("Ungrouped", 700)
        self._notify()
        self._fire_poly_change()

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
            is_construction = i in self._construction_polys
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
            EntityRecord(points=p, kind=k, meta=m)
            for p, k, m in zip(new_polys, new_kinds, new_meta)
        ]
        self._construction_polys = new_construction
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
            is_construction = i in self._construction_polys
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
                self._construction_polys.add(ni)
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
                    radius, ok = QInputDialog.getDouble(
                        self,
                        "Round Corner",
                        "Radius (mm):",
                        1.0,
                        0.01,
                        1000000.0,
                        3,
                    )
                    if ok:
                        self._round_vertex(pi, vi, radius)

                def _prompt_chamfer_corner() -> None:
                    distance, ok = QInputDialog.getDouble(
                        self,
                        "Chamfer Corner",
                        "Distance (mm):",
                        1.0,
                        0.01,
                        1000000.0,
                        3,
                    )
                    if ok:
                        self._chamfer_vertex(pi, vi, distance)

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

