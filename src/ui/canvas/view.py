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
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
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
from shapely.ops import unary_union

from src.backend.behaviors import snapping as snap_behaviors
from src.ui.canvas._snap_mixin import _SnapMixin
from src.ui.canvas._geom_ops_mixin import _GeomOpsMixin
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
from src.backend.geometry.spline import build_spline_poly
from src.constants import DIM, DRAG_THRESH, POLY, Q_BG, SEL
from src.ui.canvas._constants import CLOSE_SNAP_DIST as _CLOSE_SNAP_DIST
from src.ui.canvas._constants import EDGE_HIT as _EDGE_HIT
from src.ui.canvas._constants import GRID_AXIS as _GRID_AXIS
from src.ui.canvas._constants import GUIDE_COLOR as _GUIDE_COLOR
from src.ui.canvas._constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas._constants import ORTHO_COLOR as _ORTHO_COLOR
from src.ui.canvas._constants import SNAP_CLOSE as _SNAP_CLOSE
from src.ui.canvas._constants import SNAP_DIST as _SNAP_DIST
from src.ui.canvas._constants import VERT_HIT as _VERT_HIT
from src.ui.canvas.render import CanvasRenderer
from src.ui.sidebars.canvas_sidebar import DrawSidebar

CanvasState: TypeAlias = tuple[
    list[list[tuple[float, float]]],
    set[int],
    set[int],
    list[str],
    list[dict[str, Any] | None],
]


class PolylineView(QGraphicsView, CanvasRenderer, _SnapMixin, _GeomOpsMixin):
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
        self._show_selection_bbox: bool = False
        self._on_change = on_change
        self._on_mode_change = on_mode_change
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        self._polys: list[list[tuple[float, float]]] = []
        self._entity_kinds: list[str] = []
        self._entity_meta: list[dict[str, Any] | None] = []
        self._sel: set[int] = set()
        self._construction_polys: set[int] = set()
        self._hidden_polys: set[int] = set()
        self._locked_polys: set[int] = set()
        self._accent_polys: dict[int, str] = {}  # index → color hex for role overlays
        self._groups: dict[int, int] = {}  # poly_idx → group_id
        self._next_group_id: int = 0
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
        self._polys = self._clone_polys(polys)
        self._entity_kinds = ["polyline" for _ in self._polys]
        self._entity_meta = [None for _ in self._polys]
        self._sel.clear()
        self._hidden_polys.clear()
        self._locked_polys.clear()
        self._groups.clear()
        self._needs_fit = True
        self._fit()
        self._notify()
        self._construction_polys.clear()

    def set_accent_polys(self, accent: dict[int, str]) -> None:
        """Override render color for specific poly indices (e.g. cutout shapes).

        Pass an empty dict to clear all accents.
        """
        self._accent_polys = dict(accent)
        self._redraw()

    def reload(self, polys: list[list[tuple[float, float]]]) -> None:
        self._polys = self._clone_polys(polys)
        if len(self._entity_kinds) != len(self._polys):
            self._entity_kinds = ["polyline" for _ in self._polys]
        if len(self._entity_meta) != len(self._polys):
            self._entity_meta = [None for _ in self._polys]
        valid = set(range(len(self._polys)))
        self._sel &= valid
        self._hidden_polys &= valid
        self._locked_polys &= valid
        self._groups = {k: v for k, v in self._groups.items() if k in valid}
        self._redraw()

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return self._clone_polys(self._polys)

    def set_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        self._polys = self._clone_polys(polys)
        self._entity_kinds = ["polyline" for _ in self._polys]
        self._entity_meta = [None for _ in self._polys]
        self._sel.clear()
        self._construction_polys.clear()
        self._hidden_polys.clear()
        self._locked_polys.clear()
        self._groups.clear()
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
            i for i in hidden_state if isinstance(i, int) and 0 <= i < len(self._polys)
        }
        self._locked_polys = {
            i for i in locked_state if isinstance(i, int) and 0 <= i < len(self._polys)
        }
        raw_groups = state.get("groups", {})
        if isinstance(raw_groups, dict):
            self._groups = {
                int(k): int(v)
                for k, v in raw_groups.items()
                if str(k).lstrip("-").isdigit()
                and str(v).lstrip("-").isdigit()
                and 0 <= int(k) < len(self._polys)
            }
            self._next_group_id = max(self._groups.values(), default=0) + 1
        self._sel -= self._hidden_polys
        self._redraw()

    def get_active(self) -> list[list[tuple[float, float]]]:
        return [p for i, p in enumerate(self._polys) if i not in self._sel]

    def get_selected(self) -> list[list[tuple[float, float]]]:
        return [p for i, p in enumerate(self._polys) if i in self._sel]

    def _append_entity(
        self,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> int:
        self._polys.append(list(poly))
        self._entity_kinds.append(kind)
        self._entity_meta.append(deepcopy(meta) if meta is not None else None)
        return len(self._polys) - 1

    def _insert_entity(
        self,
        idx: int,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._polys.insert(idx, list(poly))
        self._entity_kinds.insert(idx, kind)
        self._entity_meta.insert(idx, deepcopy(meta) if meta is not None else None)

    def _remove_entity(self, idx: int) -> None:
        self._polys.pop(idx)
        if idx < len(self._entity_kinds):
            self._entity_kinds.pop(idx)
        if idx < len(self._entity_meta):
            self._entity_meta.pop(idx)

    def _copy_entities(self) -> tuple[list[str], list[dict[str, Any] | None]]:
        return (
            list(self._entity_kinds),
            [
                deepcopy(meta) if meta is not None else None
                for meta in self._entity_meta
            ],
        )

    def _set_entities_from_copy(
        self,
        kinds: list[str],
        meta: list[dict[str, Any] | None],
    ) -> None:
        self._entity_kinds = list(kinds)
        self._entity_meta = [deepcopy(m) if m is not None else None for m in meta]

    def _demote_selected_entities_to_polylines(
        self, indices: list[int] | None = None
    ) -> None:
        if indices is None:
            indices = self._selected_indices()
        for idx in indices:
            if 0 <= idx < len(self._entity_kinds):
                self._entity_kinds[idx] = "polyline"
                self._entity_meta[idx] = None

    def get_selection_indices(self) -> list[int]:
        return self._selected_indices()

    def set_selection(self, indices: list[int]) -> None:
        new_sel = {
            idx
            for idx in indices
            if 0 <= idx < len(self._polys) and idx not in self._hidden_polys
        }
        if new_sel == self._sel:
            return
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_hidden_indices(self, indices: list[int]) -> None:
        new_hidden = {idx for idx in indices if 0 <= idx < len(self._polys)}
        new_sel = self._sel - new_hidden
        if new_hidden == self._hidden_polys and new_sel == self._sel:
            return
        self._hidden_polys = new_hidden
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_locked_indices(self, indices: list[int]) -> None:
        new_locked = {idx for idx in indices if 0 <= idx < len(self._polys)}
        if new_locked == self._locked_polys:
            return
        self._locked_polys = new_locked
        self._redraw()

    def get_hidden_indices(self) -> list[int]:
        return sorted(self._hidden_polys)

    def get_locked_indices(self) -> list[int]:
        return sorted(self._locked_polys)

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

    def set_ghost_visible(self, visible: bool) -> None:
        if bool(visible) == self._ghost_visible:
            return
        self._ghost_visible = bool(visible)
        self._redraw()

    def has_ghost_polylines(self) -> bool:
        return bool(self._ghost_polys)

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
            "object_count": len(self._polys),
            "precision": " · ".join(precision) if precision else "Free move",
            "topology": (
                f"{topo['closed']} closed · {topo['open']} open · {topo['points']} pts"
            ),
        }

    def get_topology_summary(self) -> dict[str, int]:
        closed = 0
        logical_points = 0
        for poly in self._polys:
            if self._is_poly_closed(poly):
                closed += 1
                logical_points += max(0, len(poly) - 1)
            else:
                logical_points += len(poly)
        total = len(self._polys)
        return {
            "total": total,
            "closed": closed,
            "open": max(0, total - closed),
            "points": logical_points,
        }

    def delete_selected(self) -> int:
        delete_set = {idx for idx in self._sel if idx not in self._locked_polys}
        n = len(delete_set)
        if n:
            self._push_undo()
        kept: list[list[tuple[float, float]]] = []
        new_construction: set[int] = set()
        new_hidden: set[int] = set()
        new_locked: set[int] = set()
        new_groups: dict[int, int] = {}
        for i, p in enumerate(self._polys):
            if i in delete_set:
                continue
            new_idx = len(kept)
            kept.append(p)
            if i in self._construction_polys:
                new_construction.add(new_idx)
            if i in self._hidden_polys:
                new_hidden.add(new_idx)
            if i in self._locked_polys:
                new_locked.add(new_idx)
            if i in self._groups:
                new_groups[new_idx] = self._groups[i]
        self._polys = kept
        self._construction_polys = new_construction
        self._hidden_polys = new_hidden
        self._locked_polys = new_locked
        self._groups = new_groups
        self._sel.clear()
        self._redraw()
        self._notify()
        if n:
            self._fire_poly_change()
        return n

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append((
            [list(p) for p in self._polys],
            set(self._sel),
            set(self._construction_polys),
            list(self._entity_kinds),
            [
                deepcopy(meta) if meta is not None else None
                for meta in self._entity_meta
            ],
        ))
        if len(self._redo_stack) > 30:
            self._redo_stack.pop(0)
        polys, sel, construction, kinds, meta = self._undo_stack.pop()
        self._polys = polys
        self._sel = {i for i in sel if i < len(self._polys)}
        self._construction_polys = {i for i in construction if i < len(self._polys)}
        self._set_entities_from_copy(kinds, meta)
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append((
            [list(p) for p in self._polys],
            set(self._sel),
            set(self._construction_polys),
            list(self._entity_kinds),
            [
                deepcopy(meta) if meta is not None else None
                for meta in self._entity_meta
            ],
        ))
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)
        polys, sel, construction, kinds, meta = self._redo_stack.pop()
        self._polys = polys
        self._sel = {i for i in sel if i < len(self._polys)}
        self._construction_polys = {i for i in construction if i < len(self._polys)}
        self._set_entities_from_copy(kinds, meta)
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def undo_delete(self) -> bool:
        return self.undo()

    def invert_selection(self) -> None:
        visible = set(range(len(self._polys))) - self._hidden_polys
        self._sel = visible - self._sel
        self._redraw()
        self._notify()

    def select_all(self) -> None:
        self._sel = set(range(len(self._polys))) - self._hidden_polys
        self._redraw()
        self._notify()

    def deselect_all(self) -> None:
        self._sel.clear()
        self._redraw()
        self._notify()

    def explode_selected_to_segments(self) -> int:
        """Split each selected multi-vertex polyline into individual 2-pt line segments.

        Returns the number of new segments created.
        """
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        # Only explode polylines with more than 2 points (1 segment is already atomic).
        to_explode = [i for i in indices if len(self._polys[i]) > 2]
        if not to_explode:
            return 0
        self._push_undo()
        new_polys: list[list[tuple[float, float]]] = []
        new_construction: set[int] = set()
        new_sel: set[int] = set()
        for i, poly in enumerate(self._polys):
            is_construction = i in self._construction_polys
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
                    if is_construction:
                        new_construction.add(si)
                    new_sel.add(si)
            else:
                ni = len(new_polys)
                new_polys.append(poly)
                if is_construction:
                    new_construction.add(ni)
        self._polys = new_polys
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

        def _eq(a: tuple[float, float], b: tuple[float, float]) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        segs: list[tuple[tuple[float, float], tuple[float, float], bool]] = []
        for i in indices:
            poly = self._polys[i]
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

        kept_polys: list[list[tuple[float, float]]] = []
        kept_construction: set[int] = set()
        for i, poly in enumerate(self._polys):
            if i in sel_set:
                continue
            ni = len(kept_polys)
            kept_polys.append(poly)
            if i in self._construction_polys:
                kept_construction.add(ni)

        new_sel: set[int] = set()
        for poly, is_construction in merged_polys:
            ni = len(kept_polys)
            kept_polys.append(poly)
            new_sel.add(ni)
            if is_construction:
                kept_construction.add(ni)

        self._polys = kept_polys
        self._construction_polys = kept_construction
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

    def toggle_draw_mode(self) -> None:
        self.set_mode("draw" if self._mode != "draw" else "select")

    def get_draw_mode(self) -> bool:
        return self._mode == "draw"

    def fit(self) -> None:
        self._fit()

    def fit_selection(self) -> bool:
        bounds = self._selection_bounds()
        if bounds is None:
            return False
        self._fit_to_bounds(bounds)
        return True

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._redraw()

    def set_grid_snap(self, enabled: bool) -> None:
        self._grid_snap = bool(enabled)
        self._redraw()

    def set_grid_spacing(self, spacing: float) -> None:
        self._grid_spacing = max(0.001, float(spacing))
        self._redraw()

    def get_precision_state(self) -> dict[str, float | bool]:
        return {
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
            "construction_mode": self._draw_construction_mode,
            "measure_mode": self._measure_mode,
        }

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

    def copy_selected(self) -> bool:
        if not self._sel:
            return False
        self._copy_selected()
        return True

    def paste_selected(self) -> bool:
        if not self._clipboard:
            return False
        self._paste_clipboard()
        return True

    def duplicate_selected(self) -> None:
        self._duplicate_selected()

    def cut_selected(self) -> bool:
        if not self._sel:
            return False
        self._cut_selected()
        return True

    def close_selected_polylines(self) -> int:
        return self._close_selected_polylines()

    def open_selected_polylines(self) -> int:
        return self._open_selected_polylines()

    def toggle_selected_polyline_topology(self) -> int:
        return self._toggle_selected_polyline_topology()

    @property
    def poly_count(self) -> int:
        return len(self._polys)

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

    def _build_draw_sidebar(self) -> None:
        panel = DrawSidebar(
            parent=self.viewport(),
            on_tool_selected=self._set_draw_primitive,
            on_apply_shape_size=self._apply_shape_size_inputs,
            on_finish_open=lambda: self._finish_draw(close=False),
            on_close_edit=lambda: self._finish_draw(close=True),
            on_undo_point=self._key_backspace,
            on_toggle_snap=self._toggle_sidebar_snap,
            on_toggle_construction=self._toggle_sidebar_construction,
            on_toggle_split=self._toggle_sidebar_split,
            on_cycle_arc_mode=self._cycle_arc_mode,
            on_cycle_constraint_mode=self._cycle_constraint_mode,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self.set_mode("select"),
        )
        panel.hide()

        self._draw_shape_w_edit = panel.shape_width_edit
        self._draw_shape_h_edit = panel.shape_height_edit
        if self._draw_shape_w_edit is not None:
            self._draw_shape_w_edit.installEventFilter(self)
        if self._draw_shape_h_edit is not None:
            self._draw_shape_h_edit.installEventFilter(self)

        anim = QPropertyAnimation(panel, b"pos", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._draw_sidebar = panel
        self._draw_sidebar_anim = anim
        self._refresh_draw_sidebar_state()

    def _toggle_sidebar_snap(self) -> None:
        self._grid_snap = not self._grid_snap
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _toggle_sidebar_construction(self) -> None:
        self._draw_construction_mode = not self._draw_construction_mode
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _toggle_sidebar_split(self) -> None:
        self._draw_split_enabled = not self._draw_split_enabled
        self._refresh_draw_sidebar_state()
        self._show_flash("Split: on" if self._draw_split_enabled else "Split: off", 800)

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
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._draw_sidebar is None:
            return
        if not self._draw_sidebar_visible:
            self._draw_sidebar.hide()

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
        self._draw_sidebar.set_construction_label(self._draw_construction_mode)
        self._draw_sidebar.set_split_label(self._draw_split_enabled)
        self._draw_sidebar.set_active_tool(self._draw_primitive)
        self._draw_sidebar.set_arc_mode(self._draw_arc_mode)
        self._draw_sidebar.set_arc_mode_enabled(self._draw_primitive == "arc")
        self._draw_sidebar.set_constraint_mode(self._draw_constraint_lock)
        self._draw_sidebar.set_constraint_mode_enabled(
            self._draw_primitive in {"line", "polyline"}
        )
        self._update_shape_size_fields_from_preview()

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

    def _shape_primitive_active(self) -> bool:
        return self._draw_primitive in {"rectangle", "circle", "ellipse", "polygon"}

    def _update_shape_size_fields_from_preview(self) -> None:
        if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
            return
        enabled = (
            self._shape_primitive_active() and self._draw_shape_anchor_w is not None
        )
        if isinstance(self._draw_sidebar, DrawSidebar):
            self._draw_sidebar.set_shape_size_enabled(enabled)
        else:
            self._draw_shape_w_edit.setEnabled(enabled)
            self._draw_shape_h_edit.setEnabled(enabled)
        if not enabled:
            return
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        if isinstance(self._draw_sidebar, DrawSidebar):
            self._draw_sidebar.set_shape_size_values(
                f"{abs(ex - sx):.2f}",
                f"{abs(ey - sy):.2f}",
            )
        else:
            self._draw_shape_w_edit.setText(f"{abs(ex - sx):.2f}")
            self._draw_shape_h_edit.setText(f"{abs(ey - sy):.2f}")

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

    def _commit_drawn_polyline(
        self,
        poly: list[tuple[float, float]],
        *,
        primitive: str,
        close: bool = False,
        created_flash: str = "Polyline created",
    ) -> bool:
        if len(poly) < 2:
            return False

        self._push_undo()
        split_happened = False
        split_closed = 0
        split_open = 0
        can_cut_split = self._draw_split_enabled and (
            primitive in {"line", "polyline", "arc", "spline"}
            or self._draw_construction_mode
        )
        if can_cut_split and not close and len(poly) >= 2:
            split_happened, split_closed, split_open = self._split_geometry_with_line(
                poly
            )

        kind = "polyline"
        meta: dict[str, Any] | None = None
        if primitive == "line" and len(poly) >= 2:
            kind = "line"
            meta = {"start": tuple(poly[0]), "end": tuple(poly[-1])}
        elif primitive == "arc" and len(poly) >= 3:
            kind = "arc"
            from src.backend.geometry.arc import (
                arc_spec_from_center_start_end,
                arc_spec_from_three_points,
            )

            if self._draw_arc_mode == "center-start-end":
                spec = arc_spec_from_center_start_end(poly[0], poly[1], poly[2])
            else:
                spec = arc_spec_from_three_points(poly[0], poly[1], poly[2])
            if spec is not None:
                meta = {
                    "center": spec.center,
                    "radius": spec.radius,
                    "start_angle": spec.start_angle,
                    "end_angle": spec.end_angle,
                }
        elif primitive == "spline" and len(poly) >= 2:
            kind = "spline"
            meta = {"segments": 24, "closed": close}
        self._entity_kinds.append(kind)
        self._entity_meta.append(meta)
        self._polys.append(list(poly))
        new_idx = len(self._polys) - 1
        if self._draw_construction_mode:
            self._construction_polys.add(new_idx)

        merged_idx: int | None = None
        if (
            primitive in {"line", "polyline"}
            and not self._draw_construction_mode
            and not split_happened
            and any(snap_type == "vertex" for snap_type in self._draw_point_snap_types)
        ):
            merged_idx = self._try_merge_endpoints()
            if merged_idx is not None:
                new_idx = merged_idx

        self._sel.clear()
        self._sel.add(new_idx)
        self._notify()
        self._fire_poly_change()
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
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
        elif merged_idx is not None and self._is_poly_closed(self._polys[new_idx]):
            self._show_flash("Polyline closed", 800)
        elif merged_idx is not None:
            self._show_flash("Segments merged", 800)
        else:
            self._show_flash(created_flash, 800)
        self._redraw()
        if close or (
            merged_idx is not None and self._is_poly_closed(self._polys[new_idx])
        ):
            self.set_mode("edit")
        return True

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
        elif self._draw_primitive == "circle":
            poly = build_circle_poly(cx, cy, min(w, h) / 2.0)
            kind = "circle"
            meta = {"center": (cx, cy), "radius": min(w, h) / 2.0}
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

    def _push_undo(self) -> None:
        self._undo_stack.append((
            [list(p) for p in self._polys],
            set(self._sel),
            set(self._construction_polys),
            list(self._entity_kinds),
            [
                deepcopy(meta) if meta is not None else None
                for meta in self._entity_meta
            ],
        ))
        # Hard cap on entry count.
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)
        # Soft cap on total vertices retained across the stack so a few
        # huge snapshots do not balloon process memory. A scene with
        # ~200k vertices roughly equals ~3 MB of float pairs; we keep
        # the budget generous but bounded.
        _UNDO_VERTEX_BUDGET = 200_000
        total = sum(sum(len(p) for p in entry[0]) for entry in self._undo_stack)
        while total > _UNDO_VERTEX_BUDGET and len(self._undo_stack) > 1:
            dropped = self._undo_stack.pop(0)
            total -= sum(len(p) for p in dropped[0])
        self._redo_stack.clear()

    def _toggle_selected_construction(self) -> None:
        if not self._sel:
            return
        self._push_undo()
        for idx in list(self._sel):
            if idx in self._construction_polys:
                self._construction_polys.discard(idx)
            else:
                self._construction_polys.add(idx)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def get_export_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [
            list(poly)
            for idx, poly in enumerate(self._polys)
            if idx not in self._construction_polys
        ]

    def get_export_dxf_state(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for idx, poly in enumerate(self._polys):
            if idx in self._construction_polys:
                continue
            kind = (
                self._entity_kinds[idx] if idx < len(self._entity_kinds) else "polyline"
            )
            meta = self._entity_meta[idx] if idx < len(self._entity_meta) else None
            result.append({
                "index": idx,
                "polyline": list(poly),
                "kind": kind,
                "meta": deepcopy(meta) if meta is not None else None,
            })
        return result

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [pt for p in self._polys for pt in p]
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
            if idx < len(self._polys) and idx not in self._hidden_polys
        ]

    def _mutable_selected_indices(self) -> list[int]:
        return [
            idx for idx in self._selected_indices() if idx not in self._locked_polys
        ]

    def _selection_bounds(
        self, indices: list[int] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = indices if indices is not None else self._selected_indices()
        pts = [pt for idx in items for pt in self._polys[idx]]
        if not pts:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    def _connected_poly_indices(self, start_idx: int) -> set[int]:
        """Return polylines connected to start_idx via shared vertices."""
        if not (0 <= start_idx < len(self._polys)):
            return set()

        def _key(pt: tuple[float, float]) -> tuple[int, int]:
            return (round(pt[0] * 1_000_000), round(pt[1] * 1_000_000))

        graph: dict[int, set[int]] = {i: set() for i in range(len(self._polys))}
        point_to_polys: dict[tuple[int, int], set[int]] = {}
        for pi, poly in enumerate(self._polys):
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

    def _key_delete(self) -> None:
        if self._mode == "edit":
            if self._edit_selected_verts:
                self._delete_edit_vertices(set(self._edit_selected_verts))
                return
            if self._hover_vert is not None:
                self._delete_edit_vertices({self._hover_vert})
                return
        if self._mode == "select":
            self.delete_selected()

    def _key_backspace(self) -> None:
        if self._mode == "draw" and self._draw_pts:
            self._draw_pts.pop()
            if self._draw_point_snap_types:
                self._draw_point_snap_types.pop()
            if not self._draw_pts:
                self._dismiss_dim_inputs()
                self._draw_constraint = None
            self._refresh_draw_sidebar_state()
            self._redraw()
        elif self._mode == "edit":
            self._key_delete()
        elif self._mode == "select":
            self.delete_selected()

    def _can_delete_vertex(self, pi: int, vi: int) -> bool:
        if not (0 <= pi < len(self._polys)):
            return False
        poly = self._polys[pi]
        if not (0 <= vi < len(poly)):
            return False
        is_closed = (
            len(poly) >= 4
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 1e-6
        )
        unique_count = len(poly) - 1 if is_closed else len(poly)
        return unique_count > 3

    def _delete_edit_vertices(self, verts: set[tuple[int, int]]) -> int:
        if not verts:
            return 0

        grouped: dict[int, set[int]] = {}
        for pi, vi in verts:
            if pi in self._locked_polys:
                continue
            if self._can_delete_vertex(pi, vi):
                grouped.setdefault(pi, set()).add(vi)
        if not grouped:
            return 0

        self._push_undo()
        deleted = 0

        for pi in sorted(grouped.keys(), reverse=True):
            if not (0 <= pi < len(self._polys)):
                continue
            poly = self._polys[pi]
            is_closed = (
                len(poly) >= 4
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                < 1e-6
            )

            for vi in sorted(grouped[pi], reverse=True):
                if 0 <= vi < len(poly):
                    poly.pop(vi)
                    deleted += 1

            if is_closed and len(poly) >= 4:
                poly[-1] = poly[0]

        self._edit_selected_verts.clear()
        self._edit_drag_targets = set()
        self._edit_linked_verts = set()
        self._edit_poly = None
        self._edit_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return deleted

    def _finish_draw(self, *, close: bool = False) -> None:
        if self._mode != "draw" or len(self._draw_pts) < 2:
            return
        if close and self._draw_pts[0] != self._draw_pts[-1]:
            self._draw_pts.append(self._draw_pts[0])
        drawn = list(self._draw_pts)
        self._commit_drawn_polyline(
            drawn,
            primitive=self._draw_primitive,
            close=close,
            created_flash="Polyline created",
        )

    def _close_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if len(poly) < 3:
                continue
            if self._is_poly_closed(poly):
                continue
            self._polys[idx] = [*poly, poly[0]]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _open_selected_polylines(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if not self._is_poly_closed(poly):
                continue
            if len(poly) < 2:
                continue
            self._polys[idx] = poly[:-1]
            changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _toggle_selected_polyline_topology(self) -> int:
        indices = self._selected_indices()
        if not indices:
            return 0
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if self._is_poly_closed(poly):
                self._polys[idx] = poly[:-1]
                changed += 1
            elif len(poly) >= 3:
                self._polys[idx] = [*poly, poly[0]]
                changed += 1
        if changed:
            self._redraw()
            self._notify()
            self._fire_poly_change()
        return changed

    def _try_merge_endpoints(self) -> int | None:
        """Merge endpoint-touching polylines. Returns survivor index or None."""
        if len(self._polys) < 2:
            return None
        survivor_idx = len(self._polys) - 1
        if len(self._polys[survivor_idx]) < 2:
            return None

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        merged_any = False
        changed = True
        while changed:
            changed = False
            survivor = self._polys[survivor_idx]
            if len(survivor) < 2:
                break
            survivor_start, survivor_end = survivor[0], survivor[-1]
            for i, poly in enumerate(self._polys):
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
                popped_was_construction = i in self._construction_polys
                survivor_was_construction = survivor_idx in self._construction_polys
                self._polys[survivor_idx] = merged
                if survivor_idx < len(self._entity_kinds):
                    self._entity_kinds[survivor_idx] = "polyline"
                if survivor_idx < len(self._entity_meta):
                    self._entity_meta[survivor_idx] = None
                self._polys.pop(i)
                if i < len(self._entity_kinds):
                    self._entity_kinds.pop(i)
                if i < len(self._entity_meta):
                    self._entity_meta.pop(i)
                remapped: set[int] = set()
                for ci in self._construction_polys:
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

    def _linked_vertices(self, poly_idx: int, vert_idx: int) -> set[tuple[int, int]]:
        """Find all vertices linked to the given vertex (same point, across polylines)."""
        if poly_idx >= len(self._polys) or vert_idx >= len(self._polys[poly_idx]):
            return set()

        target_pt = self._polys[poly_idx][vert_idx]
        linked = {(poly_idx, vert_idx)}

        def _eq(a: tuple, b: tuple) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        for i, poly in enumerate(self._polys):
            if i == poly_idx:
                # For same polyline, check if it's a closed shape
                is_closed = len(poly) >= 4 and _eq(poly[0], poly[-1])
                if is_closed and (vert_idx == 0 or vert_idx == len(poly) - 1):
                    # First and last vertices of closed shape are linked
                    linked.add((i, 0))
                    linked.add((i, len(poly) - 1))
            else:
                # Check other polylines for matching endpoints
                for j, pt in enumerate(poly):
                    if _eq(target_pt, pt):
                        linked.add((i, j))

        return linked

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        """Move active edit vertex and all linked coincident vertices together."""
        if self._edit_poly is None or self._edit_vert is None:
            return
        targets = (
            self._edit_drag_targets
            or self._edit_linked_verts
            or {(self._edit_poly, self._edit_vert)}
        )
        for pi, vi in targets:
            if pi in self._locked_polys:
                continue
            if 0 <= pi < len(self._polys) and 0 <= vi < len(self._polys[pi]):
                self._polys[pi][vi] = (wx, wy)

    def _select_edit_vertices_in_rect(
        self,
        x1c: float,
        y1c: float,
        x2c: float,
        y2c: float,
        *,
        additive: bool = True,
    ) -> int:
        """Select edit vertices whose canvas coordinates are inside a rectangle."""
        if not additive:
            self._edit_selected_verts.clear()
        added = 0
        for pi, poly in enumerate(self._polys):
            for vi, (vx, vy) in enumerate(poly):
                cx, cy = self._w2c(vx, vy)
                if x1c <= cx <= x2c and y1c <= cy <= y2c:
                    key = (pi, vi)
                    if key not in self._edit_selected_verts:
                        added += 1
                    self._edit_selected_verts.add(key)
        return added

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
        for pi, poly in enumerate(self._polys):
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
        for pi, poly in enumerate(self._polys):
            if pi in self._hidden_polys:
                continue
            for vi, pt in enumerate(poly):
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best = (pi, vi)
        return best

    def _find_nearest_edge(
        self, cx: float, cy: float
    ) -> tuple[int, int, tuple[float, float]] | None:
        best_dist = _EDGE_HIT
        best = None
        wx, wy = self._c2w(cx, cy)
        for pi, poly in enumerate(self._polys):
            if pi in self._hidden_polys:
                continue
            n = len(poly)
            is_closed = (
                n >= 3
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                < 0.01
            )
            seg_count = n if is_closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                dx, dy = bx - ax, by - ay
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-12:
                    continue
                t = max(
                    0.0,
                    min(
                        1.0,
                        ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq,
                    ),
                )
                px, py_ = ax + t * dx, ay + t * dy
                scx, scy = self._w2c(px, py_)
                d = math.hypot(cx - scx, cy - scy)
                if d < best_dist:
                    best_dist = d
                    best = (pi, vi, (px, py_))
        return best

    def _find_poly_at(self, cx: float, cy: float) -> int | None:
        best_dist = 8.0
        best = None
        wx, wy = self._c2w(cx, cy)
        for pi, poly in enumerate(self._polys):
            if pi in self._hidden_polys:
                continue
            n = len(poly)
            is_closed = (
                n >= 3
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                < 0.01
            )
            seg_count = n if is_closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                dx, dy = bx - ax, by - ay
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-12:
                    d = math.hypot(cx - self._w2c(ax, ay)[0], cy - self._w2c(ax, ay)[1])
                else:
                    t = max(
                        0.0,
                        min(
                            1.0,
                            ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq,
                        ),
                    )
                    px, py_ = ax + t * dx, ay + t * dy
                    scx, scy = self._w2c(px, py_)
                    d = math.hypot(cx - scx, cy - scy)
                if d < best_dist:
                    best_dist = d
                    best = pi
        return best

    def _find_ghost_poly_at(self, cx: float, cy: float) -> int | None:
        """Hit-test the ghost overlay polys; returns ghost-list index or None."""
        if not self._ghost_polys or not self._ghost_visible:
            return None
        best_dist = 8.0
        best = None
        wx, wy = self._c2w(cx, cy)
        for pi, poly in enumerate(self._ghost_polys):
            n = len(poly)
            is_closed = (
                n >= 3
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])
                < 0.01
            )
            seg_count = n if is_closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                dx, dy = bx - ax, by - ay
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-12:
                    d = math.hypot(cx - self._w2c(ax, ay)[0], cy - self._w2c(ax, ay)[1])
                else:
                    t = max(
                        0.0,
                        min(
                            1.0,
                            ((wx - ax) * dx + (wy - ay) * dy) / seg_len_sq,
                        ),
                    )
                    px, py_ = ax + t * dx, ay + t * dy
                    scx, scy = self._w2c(px, py_)
                    d = math.hypot(cx - scx, cy - scy)
                if d < best_dist:
                    best_dist = d
                    best = pi
        return best

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        vp = self.viewport()
        w = max(vp.width(), 100)
        h = max(vp.height(), 100)

        if self._grid_visible:
            self._paint_grid(painter, w, h)
            # H. Origin crosshair at world (0,0)
            ox_cx, ox_cy = self._w2c(0.0, 0.0)
            o_pen = QPen(_GRID_AXIS, 1)
            painter.setPen(o_pen)
            painter.drawLine(QPointF(ox_cx - 10, ox_cy), QPointF(ox_cx + 10, ox_cy))
            painter.drawLine(QPointF(ox_cx, ox_cy - 10), QPointF(ox_cx, ox_cy + 10))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(ox_cx, ox_cy), 3, 3)

        # A. Full-viewport cursor crosshair (draw/measure mode)
        if (
            (self._mode == "draw" or self._measure_mode)
            and self._cursor_wx is not None
            and self._cursor_wy is not None
        ):
            _ch_cx, _ch_cy = self._w2c(self._cursor_wx, self._cursor_wy)
            _ch_pen = QPen(QColor("#2a3a4a"), 0.5)
            painter.setPen(_ch_pen)
            painter.drawLine(QPointF(_ch_cx, 0.0), QPointF(_ch_cx, float(h)))
            painter.drawLine(QPointF(0.0, _ch_cy), QPointF(float(w), _ch_cy))

        # Background image overlay
        if self._bg_pil and self._bg_w_mm > 0 and self._bg_h_mm > 0:
            self._paint_bg_image(painter)

        # Image bounds reference rectangle
        if self._img_bounds:
            bw, bh = self._img_bounds
            cx0, cy0 = self._w2c(0.0, 0.0)
            cx1, cy1 = self._w2c(bw, bh)
            pen = QPen(QColor("#334466"), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(QPointF(cx0, cy0), QPointF(cx1, cy1)))

        # Compute visible world-space rectangle for frustum culling
        _tl_wx, _tl_wy = self._c2w(0.0, 0.0)
        _br_wx, _br_wy = self._c2w(float(w), float(h))
        _visible_world = QRectF(
            QPointF(min(_tl_wx, _br_wx), min(_tl_wy, _br_wy)),
            QPointF(max(_tl_wx, _br_wx), max(_tl_wy, _br_wy)),
        )

        # Ghost polylines (context-only overlay, drawn beneath the main polys).
        if self._ghost_polys and self._ghost_visible:
            ghost_color = QColor(POLY)
            ghost_color.setAlpha(90)
            ghost_pen = QPen(ghost_color, 1.0)
            ghost_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(ghost_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for poly in self._ghost_polys:
                if len(poly) < 2:
                    continue
                _gp_rect = self._poly_rect_for_culling(poly)
                if not _visible_world.intersects(_gp_rect):
                    continue
                gpath = QPainterPath()
                gx, gy = self._w2c(*poly[0])
                gpath.moveTo(gx, gy)
                for pt in poly[1:]:
                    px, py_ = self._w2c(*pt)
                    gpath.lineTo(px, py_)
                if (
                    len(poly) >= 3
                    and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1])
                    < 0.5
                ):
                    gpath.closeSubpath()
                painter.drawPath(gpath)

        # Polylines
        for idx, poly in enumerate(self._polys):
            if idx in self._hidden_polys:
                continue
            if len(poly) < 2:
                continue
            # Frustum culling: skip polylines entirely outside the viewport
            _poly_rect = self._poly_rect_for_culling(poly)
            if not _visible_world.intersects(_poly_rect):
                continue
            sel = idx in self._sel
            is_construction = idx in self._construction_polys
            is_locked = idx in self._locked_polys
            if sel:
                color = QColor(SEL)
            elif idx in self._accent_polys:
                color = QColor(self._accent_polys[idx])
            elif is_construction:
                color = QColor(_GUIDE_COLOR)
            else:
                color = QColor(POLY)
            if is_locked:
                color = QColor("#8b949e")
            lw = 2.0 if sel else (1.2 if is_construction else 1.5)
            pen = QPen(color, lw)
            if is_construction or is_locked:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            render_poly = poly
            if (
                idx < len(self._entity_kinds)
                and self._entity_kinds[idx] == "spline"
                and len(poly) >= 2
            ):
                meta = self._entity_meta[idx] if idx < len(self._entity_meta) else None
                render_poly = build_spline_poly(
                    poly,
                    segments=int(meta.get("segments", 24)) if meta else 24,
                    closed=bool(meta.get("closed", False)) if meta else False,
                )
                if len(render_poly) < 2:
                    continue
            path = QPainterPath()
            sx, sy = self._w2c(*render_poly[0])
            path.moveTo(sx, sy)
            for pt in render_poly[1:]:
                px, py_ = self._w2c(*pt)
                path.lineTo(px, py_)
            if (
                len(poly) >= 3
                and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) < 0.5
            ):
                path.closeSubpath()
            painter.drawPath(path)

        # Selection bounding box + transform gizmo
        if self._sel and self._mode == "select":
            sel_pts = [
                pt for i in self._sel if i < len(self._polys) for pt in self._polys[i]
            ]
            if sel_pts:
                xs, ys = zip(*sel_pts)
                bx0, by0 = self._w2c(min(xs), max(ys))
                bx1, by1 = self._w2c(max(xs), min(ys))
                if self._show_selection_bbox:
                    pad = 4
                    pen = QPen(QColor(SEL), 1.0, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(
                        QRectF(
                            bx0 - pad,
                            by0 - pad,
                            bx1 - bx0 + 2 * pad,
                            by1 - by0 + 2 * pad,
                        )
                    )
                self._paint_transform_gizmo(painter, bx0, by0, bx1, by1)
            else:
                self._gizmo_scale_rect = None
                self._gizmo_rotate_rect = None
        else:
            self._gizmo_scale_rect = None
            self._gizmo_rotate_rect = None

        # Select-mode dimensions (Fusion-like quick readout)
        if self._mode == "select" and self._sel:
            sel_bounds = self._selection_bounds()
            if sel_bounds is not None:
                x0, y0, x1, y1 = sel_bounds
                width = x1 - x0
                height = y1 - y0
                cx0, cy0 = self._w2c(x0, y0)
                cx1, cy1 = self._w2c(x1, y1)
                mx = (cx0 + cx1) / 2.0
                my = (cy0 + cy1) / 2.0
                self._sel_badge_w_rect = self._draw_badge(
                    painter, mx, min(cy0, cy1) - 14, f"W {width:.2f}", 9
                )
                self._sel_badge_h_rect = self._draw_badge(
                    painter, max(cx0, cx1) + 26, my, f"H {height:.2f}", 9
                )
                if len(self._sel) == 1:
                    idx = next(iter(self._sel))
                    if 0 <= idx < len(self._polys):
                        poly = self._polys[idx]
                        if len(poly) == 2:
                            (ax, ay), (bx, by) = poly
                            llen = math.hypot(bx - ax, by - ay)
                            ang = math.degrees(math.atan2(by - ay, bx - ax))
                            self._draw_badge(
                                painter,
                                mx,
                                max(cy0, cy1) + 16,
                                f"L {llen:.2f}  ∠ {ang:.1f}°",
                                9,
                            )
            else:
                self._sel_badge_w_rect = None
                self._sel_badge_h_rect = None
        else:
            self._sel_badge_w_rect = None
            self._sel_badge_h_rect = None

        # Edit mode: vertex handles
        if self._mode == "edit":
            self._paint_edit_handles(painter)
        elif self._mode == "select" and self._sel:
            self._paint_select_handles(painter)

        # Construction lines (Feature 15)
        # Guide/measure lines (non-exported)
        # Ortho constraint line (Feature 6)
        if self._mode == "draw" and self._angle_snap_active and self._draw_pts:
            ortho_pen = QPen(_ORTHO_COLOR, 0.5, Qt.PenStyle.DashLine)
            painter.setPen(ortho_pen)
            anchor_cx, anchor_cy = self._w2c(*self._draw_pts[-1])
            if self._cursor_wx is not None and self._cursor_wy is not None:
                dx_o = self._cursor_wx - self._draw_pts[-1][0]
                dy_o = self._cursor_wy - self._draw_pts[-1][1]
                d_o = math.hypot(dx_o, dy_o)
                if d_o > 1e-9:
                    # Extend construction line across viewport
                    ext = max(w, h) * 2.0
                    # Actually use canvas coords directly
                    cur_cx, cur_cy = self._w2c(self._cursor_wx, self._cursor_wy)
                    cdx = cur_cx - anchor_cx
                    cdy = cur_cy - anchor_cy
                    cd = math.hypot(cdx, cdy)
                    if cd > 1e-9:
                        cnx, cny = cdx / cd, cdy / cd
                        painter.drawLine(
                            QPointF(anchor_cx - cnx * ext, anchor_cy - cny * ext),
                            QPointF(anchor_cx + cnx * ext, anchor_cy + cny * ext),
                        )

        # Draw mode: dim vertex guides + endpoint highlights
        if self._mode == "draw":
            _dim_dot = QColor("#4a5a6a")
            _helper_count = 0
            _helper_cap = 1800
            for _dpoly in self._polys:
                if not _visible_world.intersects(self._poly_rect_for_culling(_dpoly)):
                    continue
                for _dpt in _dpoly:
                    if _helper_count >= _helper_cap:
                        break
                    _dcx, _dcy = self._w2c(*_dpt)
                    painter.setPen(QPen(QColor("#3a5a6a"), 1.0))
                    painter.setBrush(QBrush(_dim_dot))
                    painter.drawEllipse(QPointF(_dcx, _dcy), 3, 3)
                    _helper_count += 1
                if _helper_count >= _helper_cap:
                    break
            # Highlight endpoints of existing polylines (connection targets)
            _ep_color = QColor("#5a8aaa")
            for _dpoly in self._polys:
                if not _visible_world.intersects(self._poly_rect_for_culling(_dpoly)):
                    continue
                if len(_dpoly) >= 2:
                    for _ept in (_dpoly[0], _dpoly[-1]):
                        _ecx, _ecy = self._w2c(*_ept)
                        painter.setPen(QPen(_ep_color, 1.5))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawEllipse(QPointF(_ecx, _ecy), 5, 5)

        # C. Inference / alignment lines
        if self._mode == "draw" and self._draw_pts and self._cursor_wx is not None:
            self._paint_inference_lines(painter, w, h)

        if self._mode == "draw":
            self._paint_draw_shape_preview(painter)
            self._paint_arc_preview(painter)
            self._paint_spline_preview(painter)
            self._paint_draw_preview_badges(painter)

        # In-progress draw polygon (BEFORE snap indicators so snaps render on top)
        if self._draw_pts:
            self._paint_in_progress_poly(painter)

        # Snap indicator — drawn LAST so it's always visible on top
        if self._mode == "draw" and self._draw_snap is not None:
            self._paint_snap_overlay(painter)
        elif self._hover_snap is not None and self._hover_snap_type is not None:
            self._paint_snap_overlay(
                painter,
                snap_point=self._hover_snap,
                snap_type=self._hover_snap_type,
            )

        # Rubber-band
        if self._shift_drag and self._band_start and self._lmb_prev:
            pen = QPen(QColor("#ff8800"), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            bx, by = self._band_start.x(), self._band_start.y()
            painter.drawRect(
                QRectF(
                    QPointF(bx, by),
                    QPointF(self._lmb_prev.x(), self._lmb_prev.y()),
                )
            )

        # Measure overlay
        if self._measure_mode and self._measure_anchor and self._measure_hover:
            self._paint_measure_overlay(painter)

        # Pre-anchor measure snap indicator
        if (
            self._measure_mode
            and self._measure_anchor is None
            and self._measure_hover_pre is not None
        ):
            _mpx, _mpy = self._w2c(*self._measure_hover_pre)
            painter.setPen(QPen(_SNAP_CLOSE, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_mpx, _mpy), 6, 6)

        # Info overlay
        n, s = len(self._polys), len(self._sel)
        info = f"{n} polylines" + (f"  ·  {s} selected" if s else "")
        if self._mode == "draw":
            pts_hint = f"  {len(self._draw_pts)} pt(s)" if self._draw_pts else ""
            info += f"  ·  DRAW{pts_hint}"
        elif self._mode == "edit":
            info += "  ·  EDIT"

        painter.setPen(QColor(DIM))
        painter.setFont(QFont("Helvetica", 10))
        painter.drawText(8, 18, info)

        zoom_pct = round(self._scale / max(self._fit_scale, 1e-9) * 100)
        if self._mode == "draw":
            hint = f"[{self._draw_primitive}: click points, Enter=finish, dbl-click=close, Esc=cancel]"
        elif self._mode == "edit":
            hint = "[drag vertex, dbl-click edge=insert, right-click vertex=delete, E=exit]"
        else:
            hint = "[Pan: Space/MMB · F=fit · Cmd/Ctrl+Z=undo · D/E mode · use Precision bar for snap/grid]"
        precision = []
        if self._grid_visible:
            precision.append(f"grid {self._grid_spacing:g}mm")
        if self._grid_snap:
            precision.append("snap")
        if precision:
            hint += "  ·  " + " / ".join(precision)
        painter.setFont(QFont("Helvetica", 9))
        painter.drawText(8, h - 8, f"{zoom_pct}%  {hint}")

        if not self._polys and not self._draw_pts:
            painter.setPen(QColor("#3b4a6a"))
            painter.setFont(QFont("Helvetica", 12))
            painter.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "No polylines loaded",
            )

        # Cursor position
        if self._cursor_wx is not None:
            painter.setPen(QColor(DIM))
            painter.setFont(QFont("Helvetica", 10))
            text = f"{self._cursor_wx:.2f}, {self._cursor_wy:.2f} mm"
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(text)
            painter.drawText(w - tw - 8, h - 8, text)

        # Flash indicator
        if self._flash_text:
            painter.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
            fm = QFontMetrics(painter.font())
            ftw = fm.horizontalAdvance(self._flash_text)
            fth = fm.height()
            fpad = 8
            frx = w / 2 - ftw / 2 - fpad
            fry = 40
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(20, 24, 36, 220)))
            painter.drawRoundedRect(QRectF(frx, fry, ftw + 2 * fpad, fth + fpad), 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(frx, fry, ftw + 2 * fpad, fth + fpad),
                Qt.AlignmentFlag.AlignCenter,
                self._flash_text,
            )

        # Measure button
        self._paint_measure_button(painter, w)

        painter.end()

    def _is_near_start(self) -> bool:
        """Check if cursor is near the first draw point (close-polygon zone)."""
        if (
            len(self._draw_pts) < 3
            or self._cursor_wx is None
            or self._cursor_wy is None
        ):
            return False
        start_cx, start_cy = self._w2c(*self._draw_pts[0])
        cur_cx, cur_cy = self._w2c(self._cursor_wx, self._cursor_wy)
        return math.hypot(cur_cx - start_cx, cur_cy - start_cy) < _CLOSE_SNAP_DIST

    def _draw_preview_outcomes(self) -> list[str]:
        if self._mode != "draw" or self._cursor_wx is None or self._cursor_wy is None:
            return []
        outcomes: list[str] = []

        if self._is_near_start():
            outcomes.append("Close")
            return outcomes

        if self._draw_primitive in {"line", "polyline"} and self._draw_pts:
            start = self._draw_pts[0]
            end = (self._cursor_wx, self._cursor_wy)
            if self._would_close_existing_polyline(start, end):
                outcomes.append("Close")
            elif self._would_merge_existing_polyline(start, end):
                outcomes.append("Merge")
            preview = list(self._draw_pts) + [end]
            if len(preview) >= 2 and self._would_split_existing_geometry(preview):
                outcomes.append("Split")
        elif self._draw_construction_mode and self._draw_pts:
            preview = list(self._draw_pts) + [(self._cursor_wx, self._cursor_wy)]
            if len(preview) >= 2 and self._would_split_existing_geometry(preview):
                outcomes.append("Split")

        return outcomes

    @staticmethod
    def _points_equal(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6

    def _would_close_existing_polyline(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        for poly in self._polys:
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            if (
                self._points_equal(start, poly[0]) and self._points_equal(end, poly[-1])
            ) or (
                self._points_equal(start, poly[-1]) and self._points_equal(end, poly[0])
            ):
                return True
        return False

    def _would_merge_existing_polyline(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        touched = 0
        for poly in self._polys:
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            endpoints = (poly[0], poly[-1])
            if any(self._points_equal(start, pt) for pt in endpoints):
                touched += 1
            if any(self._points_equal(end, pt) for pt in endpoints):
                touched += 1
        return touched > 0

    def _would_split_existing_geometry(
        self,
        preview_poly: list[tuple[float, float]],
    ) -> bool:
        if len(preview_poly) < 2:
            return False
        try:
            cutter = LineString(preview_poly)
            if cutter.is_empty or cutter.length < 1e-9:
                return False
        except (TypeError, ValueError, GEOSException):
            return False

        for poly in self._polys:
            if len(poly) < 2:
                continue
            if self._is_poly_closed(poly):
                try:
                    coords = list(poly)
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    shp = Polygon(coords)
                    if not shp.is_valid:
                        shp = shp.buffer(0)
                    if shp.is_empty or not cutter.intersects(shp):
                        continue
                    if self._would_split_closed_polygon(shp, cutter):
                        return True
                    pts = list(
                        poly[:-1] if self._points_equal(poly[0], poly[-1]) else poly
                    )
                    edge_count = len(pts)
                    for j in range(edge_count):
                        a = pts[j]
                        b = pts[(j + 1) % edge_count]
                        if len(self._split_segment_by_cutter_points(a, b, cutter)) >= 2:
                            return True
                except (TypeError, ValueError, GEOSException):
                    continue
            else:
                try:
                    for j in range(len(poly) - 1):
                        a = poly[j]
                        b = poly[j + 1]
                        if len(self._split_segment_by_cutter_points(a, b, cutter)) >= 2:
                            return True
                except (TypeError, ValueError, GEOSException):
                    continue
        return False

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
            idx: list(self._polys[idx]) for idx in self._mutable_selected_indices()
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
            self._polys[idx] = out_poly

    def _end_gizmo_drag(self) -> bool:
        moved = self._gizmo_drag_moved
        self._gizmo_drag_mode = None
        self._gizmo_center_w = None
        self._gizmo_start_vec = None
        self._gizmo_snapshot = {}
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        return moved

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        """Show a brief flash indicator on the canvas."""
        self._flash_text = text
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)
        self._flash_timer.start(duration_ms)
        self._redraw()

    def _clear_flash(self) -> None:
        self._flash_text = None
        self._flash_timer = None
        self._redraw()

    # ── Auto-dimension HUD (Fusion 360 style) ──────────────────────────────

    _DIM_STYLE = (
        "background: #1a1f2e; color: #ffffff; border: 1px solid #4a9eff;"
        "border-radius: 2px; font-size: 11px; font-family: 'Menlo';"
        "padding: 2px 4px;"
    )

    def _show_dim_inputs(self) -> None:
        """Create both distance and angle QLineEdits that float near the cursor."""
        self._dismiss_dim_inputs()
        if not self._draw_pts:
            return

        dist_edit = QLineEdit(self.viewport())
        dist_edit.setFixedWidth(70)
        dist_edit.setFixedHeight(20)
        dist_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        dist_edit.setStyleSheet(self._DIM_STYLE)
        dist_edit.setPlaceholderText("d:")
        dist_edit.show()
        dist_edit.returnPressed.connect(self._apply_dim_input)
        dist_edit.installEventFilter(self)
        self._dim_distance_edit = dist_edit
        self._dim_distance_dirty = False

        angle_edit = QLineEdit(self.viewport())
        angle_edit.setFixedWidth(55)
        angle_edit.setFixedHeight(20)
        angle_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        angle_edit.setStyleSheet(self._DIM_STYLE)
        angle_edit.setPlaceholderText("\u2220:")
        angle_edit.show()
        angle_edit.returnPressed.connect(self._apply_dim_input)
        angle_edit.installEventFilter(self)
        self._dim_angle_edit = angle_edit
        self._dim_angle_dirty = False

    def eventFilter(self, obj, event) -> bool:
        """Intercept Tab/Backtab on the draw-mode dim-input QLineEdits."""
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                if (
                    self._draw_shape_w_edit is not None
                    and self._draw_shape_h_edit is not None
                    and (
                        obj is self._draw_shape_w_edit or obj is self._draw_shape_h_edit
                    )
                ):
                    if obj is self._draw_shape_w_edit:
                        self._draw_shape_h_edit.setFocus()
                        self._draw_shape_h_edit.selectAll()
                    else:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                    return True
                if (
                    self._dim_distance_edit is not None
                    and self._dim_angle_edit is not None
                ):
                    if obj is self._dim_distance_edit:
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                        self._dim_angle_dirty = True
                    elif obj is self._dim_angle_edit:
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                        self._dim_distance_dirty = True
                return True  # Consume the Tab — prevent Qt focus chain
        return super().eventFilter(obj, event)

    def _dismiss_dim_inputs(self) -> None:
        """Remove the auto-dimension HUD widgets."""
        if self._dim_distance_edit is not None:
            self._dim_distance_edit.hide()
            self._dim_distance_edit.deleteLater()
            self._dim_distance_edit = None
        if self._dim_angle_edit is not None:
            self._dim_angle_edit.hide()
            self._dim_angle_edit.deleteLater()
            self._dim_angle_edit = None
        self._dim_distance_dirty = False
        self._dim_angle_dirty = False

    # ── Inline selection-badge dimension editor ───────────────────────────────

    def _show_sel_dim_editor(self, axis: str, rect: QRectF) -> None:
        """Show a floating QLineEdit over the W or H badge for direct editing."""
        self._dismiss_sel_dim_editor()
        bounds = self._selection_bounds()
        if bounds is None:
            return
        x0, y0, x1, y1 = bounds
        cur_val = (x1 - x0) if axis == "w" else (y1 - y0)

        edit = QLineEdit(self.viewport())
        edit.setFixedWidth(max(int(rect.width()) + 10, 70))
        edit.setFixedHeight(22)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setStyleSheet(self._DIM_STYLE)
        edit.setText(f"{cur_val:.3f}")
        edit.selectAll()
        edit.move(int(rect.x()), int(rect.y()))
        edit.show()
        edit.setFocus()
        edit.returnPressed.connect(lambda: self._apply_sel_dim_editor())
        edit.editingFinished.connect(lambda: self._dismiss_sel_dim_editor())
        self._sel_dim_edit = edit
        self._sel_dim_axis = axis

    def _apply_sel_dim_editor(self) -> None:
        if self._sel_dim_edit is None or self._sel_dim_axis is None:
            return
        text = self._sel_dim_edit.text().strip()
        axis = self._sel_dim_axis
        # Disconnect editingFinished before dismissing to avoid double-trigger
        try:
            self._sel_dim_edit.editingFinished.disconnect()
        except RuntimeError:
            pass
        self._dismiss_sel_dim_editor()
        try:
            val = float(text)
        except ValueError:
            return
        if val <= 0:
            return
        if axis == "w":
            self._set_selected_width(val)
        else:
            self._set_selected_height(val)
        self._show_flash("Dimension updated", 900)

    def _dismiss_sel_dim_editor(self) -> None:
        if self._sel_dim_edit is not None:
            self._sel_dim_edit.hide()
            self._sel_dim_edit.deleteLater()
            self._sel_dim_edit = None
        self._sel_dim_axis = None

    def _update_dim_positions(self, cx: float, cy: float) -> None:
        """Move the dim input widgets near cursor, avoiding snap label overlap.

        Positions the fields below-right of cursor with enough clearance so
        snap indicator icons and labels (drawn at +18, +4 from snap point)
        never get covered.
        """
        vp = self.viewport()
        vw = max(vp.width(), 100)
        vh = max(vp.height(), 100)
        # Default: below-right of cursor
        dx, dy = 28, 22
        # If near right edge, flip to left side
        if cx + dx + 80 > vw:
            dx = -100
        # If near bottom edge, flip above
        if cy + dy + 50 > vh:
            dy = -50
        if self._dim_distance_edit is not None:
            self._dim_distance_edit.move(int(cx + dx), int(cy + dy))
        if self._dim_angle_edit is not None:
            self._dim_angle_edit.move(int(cx + dx), int(cy + dy + 24))

    def _update_dim_values(self, distance: float, angle: float) -> None:
        """Update displayed values in the dim inputs, unless user has typed."""
        if self._dim_distance_edit is not None and not self._dim_distance_dirty:
            self._dim_distance_edit.setText(f"{distance:.2f}")
        if self._dim_angle_edit is not None and not self._dim_angle_dirty:
            self._dim_angle_edit.setText(f"{angle:.1f}")

    def _apply_dim_input(self) -> None:
        """Read distance/angle from the HUD fields and place a point."""
        if not self._draw_pts:
            return
        last_wx, last_wy = self._draw_pts[-1]
        try:
            dist_text = (
                self._dim_distance_edit.text().strip()
                if self._dim_distance_edit
                else ""
            )
            angle_text = (
                self._dim_angle_edit.text().strip() if self._dim_angle_edit else ""
            )
            if not dist_text:
                return
            dist = float(dist_text)
            if angle_text:
                angle_deg = float(angle_text)
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                angle_deg = math.degrees(
                    math.atan2(
                        self._cursor_wy - last_wy,
                        self._cursor_wx - last_wx,
                    )
                )
            else:
                angle_deg = 0.0
            angle_rad = math.radians(angle_deg)
            new_x = last_wx + dist * math.cos(angle_rad)
            new_y = last_wy + dist * math.sin(angle_rad)
            self._draw_pts.append((new_x, new_y))
            # Reset dirty flags so fields resume auto-updating
            self._dim_distance_dirty = False
            self._dim_angle_dirty = False
            self._refresh_draw_sidebar_state()
            self._redraw()
        except ValueError:
            pass

    # ── Inference / alignment lines ──────────────────────────────────────────

    def _hit_measure_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._mbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    # ── Events ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_draw_sidebar()
        if self._needs_fit and self._polys:
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
            elif key == Qt.Key.Key_C:
                self._copy_selected()
                return
            elif key == Qt.Key.Key_V:
                self._paste_clipboard()
                return
            elif key == Qt.Key.Key_D:
                self._duplicate_selected()
                return
            elif key == Qt.Key.Key_X:
                self._cut_selected()
                return
            elif key == Qt.Key.Key_D and shift_mod:
                self._prompt_edit_dimensions()
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
        elif key == Qt.Key.Key_R and not ctrl and not shift_mod:
            if self._mode in ("select", "edit"):
                self._prompt_round_shortcut()
                return
        elif key == Qt.Key.Key_C and not ctrl and not shift_mod:
            if self._mode in ("select", "edit"):
                self._prompt_chamfer_shortcut()
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
                        self._apply_shape_size_inputs()
                    else:
                        self._commit_shape_preview()
                    return
                # If dim inputs are dirty, apply them; otherwise finish draw
                if self._dim_distance_dirty or self._dim_angle_dirty:
                    self._apply_dim_input()
                else:
                    self._finish_draw()
            elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                if (
                    self._mode == "draw"
                    and self._shape_primitive_active()
                    and self._draw_shape_preview_active
                    and self._draw_shape_w_edit is not None
                    and self._draw_shape_h_edit is not None
                ):
                    if self._draw_shape_w_edit.hasFocus():
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
                    if self._dim_distance_edit.hasFocus():
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                        self._dim_angle_dirty = True
                    elif self._dim_angle_edit.hasFocus():
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                        self._dim_distance_dirty = True
                    else:
                        # Neither field has focus — give focus to distance
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                        self._dim_distance_dirty = True
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

        # Clicking directly on a W/H selection badge opens inline dimension editor
        if self._mode == "select" and self._sel:
            pt = QPointF(pos.x(), pos.y())
            if self._sel_badge_w_rect is not None and self._sel_badge_w_rect.contains(
                pt
            ):
                self._show_sel_dim_editor("w", self._sel_badge_w_rect)
                return
            if self._sel_badge_h_rect is not None and self._sel_badge_h_rect.contains(
                pt
            ):
                self._show_sel_dim_editor("h", self._sel_badge_h_rect)
                return
            wx0, wy0 = self._c2w(pos.x(), pos.y())
            if (
                self._gizmo_scale_rect is not None
                and self._gizmo_scale_rect.contains(pt)
                and self._start_gizmo_drag("scale", wx0, wy0)
            ):
                self._redraw()
                return
            if (
                self._gizmo_rotate_rect is not None
                and self._gizmo_rotate_rect.contains(pt)
                and self._start_gizmo_drag("rotate", wx0, wy0)
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
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._edit_drag_moved = False
                self._edit_undo_pushed = False
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
        if self._selectable and shift:
            self._shift_drag = True
            self._band_start = pos
            self._lmb_press = None
            self._lmb_prev = pos
            self._lmb_target = None
        else:
            self._shift_drag = False
            self._band_start = None
            self._lmb_press = pos
            self._lmb_prev = pos
            target = self._find_poly_at(pos.x(), pos.y())
            was_selected_before = target in self._sel if target is not None else False
            self._lmb_target = target
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
                shift_toggle = bool(
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                )
                if target in self._groups:
                    gid = self._groups[target]
                    members = {
                        i
                        for i, g in self._groups.items()
                        if g == gid and i < len(self._polys)
                    }
                    if ctrl or shift_toggle:
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
                if (
                    hit is not None
                    and hit[0] == target
                    and was_selected_before
                    and target not in self._locked_polys
                ):
                    pi, vi = hit
                    self._edit_poly = pi
                    self._edit_vert = vi
                    self._edit_dragging = True
                    self._edit_drag_moved = False
                    self._edit_undo_pushed = False
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
                reference_point=self._polys[self._edit_poly][self._edit_vert],
            )
            snap_wx, snap_wy = wx, wy
            snap_type = ""
            if drag_snap_result is not None:
                snap_wx, snap_wy, snap_type = drag_snap_result

            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                poly = self._polys[self._edit_poly]
                anchor_pt = None
                if self._edit_vert > 0:
                    anchor_pt = poly[self._edit_vert - 1]
                elif len(poly) > 1:
                    anchor_pt = poly[1]
                if anchor_pt is not None:
                    snap_wx, snap_wy = self._angle_snap(
                        anchor_pt[0], anchor_pt[1], snap_wx, snap_wy
                    )
                    snap_type = snap_type or "angle"

            cur_pt = self._polys[self._edit_poly][self._edit_vert]
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
            self._draw_constraint = None
            if self._draw_pts:
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
                        self._polys[idx] = [
                            (x + dx_w, y + dy_w) for x, y in self._polys[idx]
                        ]
                        self._transform_entity_meta(
                            idx,
                            center=(0.0, 0.0),
                            kind=self._entity_kinds[idx]
                            if idx < len(self._entity_kinds)
                            else "polyline",
                            meta=self._entity_meta[idx]
                            if idx < len(self._entity_meta)
                            else None,
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
            for idx, poly in enumerate(self._polys):
                pts_c = [self._w2c(x, y) for x, y in poly]
                if any(x1c <= cx <= x2c and y1c <= cy <= y2c for cx, cy in pts_c):
                    self._sel.add(idx)
            self._redraw()
            self._notify()
            self._shift_drag = False
            self._band_start = None
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
                self._push_undo()
                self._polys[pi].insert(seg_idx + 1, pt)
                self._redraw()
                self._notify()
                self._fire_poly_change()

    def _show_measure_edit(self) -> None:
        """Show a QLineEdit overlay for editing the measured distance."""
        self._dismiss_measure_edit()
        if not self._measure_anchor or not self._measure_end:
            return
        ax, ay = self._measure_anchor
        hx, hy = self._measure_end
        dist = math.hypot(hx - ax, hy - ay)
        cax, cay = self._w2c(ax, ay)
        chx, chy = self._w2c(hx, hy)
        mx, my = (cax + chx) / 2, (cay + chy) / 2

        le = QLineEdit(self.viewport())
        le.setText(f"{dist:.2f}")
        le.setFixedWidth(100)
        le.setFixedHeight(24)
        le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        le.setStyleSheet(
            "background: #001522; color: #ffffff; border: 1px solid #00d8ff;"
            "border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        le.move(int(mx - 50), int(my - 40))
        le.show()
        le.setFocus()
        le.selectAll()
        le.returnPressed.connect(self._apply_measure_scale)
        self._measure_edit = le

    def _dismiss_measure_edit(self) -> None:
        """Remove the measure distance QLineEdit overlay."""
        if self._measure_edit is not None:
            self._measure_edit.hide()
            self._measure_edit.deleteLater()
            self._measure_edit = None

    def _apply_measure_scale(self) -> None:
        """Read new distance from the edit overlay and scale all polylines."""
        if not self._measure_edit or not self._measure_anchor or not self._measure_end:
            self._dismiss_measure_edit()
            return
        try:
            new_dist = float(self._measure_edit.text())
        except ValueError:
            self._dismiss_measure_edit()
            return
        ax, ay = self._measure_anchor
        hx, hy = self._measure_end
        old_dist = math.hypot(hx - ax, hy - ay)
        if old_dist < 1e-9 or new_dist <= 0:
            self._dismiss_measure_edit()
            return
        factor = new_dist / old_dist
        self._scale_all(factor)
        self._dismiss_measure_edit()
        self._measure_locked = False
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._redraw()

    # ── Clipboard & nudge helpers ─────────────────────────────────────────────

    def _copy_selected(self) -> None:
        if not self._sel:
            return
        self._clipboard = []
        for i in sorted(self._sel):
            if i >= len(self._polys):
                continue
            self._clipboard.append({
                "polyline": list(self._polys[i]),
                "kind": self._entity_kinds[i]
                if i < len(self._entity_kinds)
                else "polyline",
                "meta": deepcopy(self._entity_meta[i])
                if i < len(self._entity_meta) and self._entity_meta[i] is not None
                else None,
                "construction": i in self._construction_polys,
            })

    def _paste_clipboard(self) -> None:
        if not self._clipboard:
            return
        self._push_undo()
        offset = 1.0  # mm
        new_indices = []
        for record in self._clipboard:
            poly = list(record.get("polyline", []))
            new_poly = [(x + offset, y + offset) for x, y in poly]
            kind = str(record.get("kind", "polyline"))
            meta = self._translated_entity_meta(
                kind,
                record.get("meta"),
                offset,
                offset,
            )
            new_idx = self._append_entity(new_poly, kind=kind, meta=meta)
            if record.get("construction"):
                self._construction_polys.add(new_idx)
            new_indices.append(new_idx)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _duplicate_selected(self) -> None:
        if not self._sel:
            return
        self._copy_selected()
        self._paste_clipboard()

    def _cut_selected(self) -> None:
        if not self._sel:
            return
        cut_set = {idx for idx in self._sel if idx not in self._locked_polys}
        if not cut_set:
            return
        self._copy_selected()
        self._push_undo()
        kept: list[list[tuple[float, float]]] = []
        kept_kinds: list[str] = []
        kept_meta: list[dict[str, Any] | None] = []
        new_construction: set[int] = set()
        new_hidden: set[int] = set()
        new_locked: set[int] = set()
        for i, p in enumerate(self._polys):
            if i in cut_set:
                continue
            new_idx = len(kept)
            kept.append(p)
            kept_kinds.append(
                self._entity_kinds[i] if i < len(self._entity_kinds) else "polyline"
            )
            kept_meta.append(
                deepcopy(self._entity_meta[i])
                if i < len(self._entity_meta) and self._entity_meta[i] is not None
                else None
            )
            if i in self._construction_polys:
                new_construction.add(new_idx)
            if i in self._hidden_polys:
                new_hidden.add(new_idx)
            if i in self._locked_polys:
                new_locked.add(new_idx)
        self._polys = kept
        self._entity_kinds = kept_kinds
        self._entity_meta = kept_meta
        self._construction_polys = new_construction
        self._hidden_polys = new_hidden
        self._locked_polys = new_locked
        self._sel.clear()
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._sel:
            return
        mutable = [idx for idx in self._sel if idx not in self._locked_polys]
        if not mutable:
            return
        if not self._nudge_undo_pushed:
            self._push_undo()
            self._nudge_undo_pushed = True
            QTimer.singleShot(500, self._reset_nudge_undo)
        for idx in mutable:
            if idx < len(self._polys):
                self._polys[idx] = [(x + dx, y + dy) for x, y in self._polys[idx]]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entity_kinds[idx]
                    if idx < len(self._entity_kinds)
                    else "polyline",
                    meta=self._entity_meta[idx]
                    if idx < len(self._entity_meta)
                    else None,
                    transform="translate",
                    dx=dx,
                    dy=dy,
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _reset_nudge_undo(self) -> None:
        self._nudge_undo_pushed = False

    def _scale_all(self, factor: float) -> None:
        """Scale all polylines uniformly around their bounding box center."""
        if not self._polys:
            return
        self._push_undo()
        all_pts = [pt for p in self._polys for pt in p]
        xs, ys = zip(*all_pts)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        self._polys = [
            [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in poly]
            for poly in self._polys
        ]
        for idx in range(len(self._polys)):
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="scale",
                factor=factor,
            )
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

        for poly in self._polys:
            if len(poly) < 2:
                result_polys.append(poly)
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
                        result_polys.append(poly)
                        continue

                    # Check if cutting line actually intersects
                    if not cutter.intersects(shapely_poly):
                        result_polys.append(poly)
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
                                    result_polys.append([(x, y) for x, y in coords_out])
                        any_split = True
                        closed_splits += 1
                    else:
                        # Boundary-only cut: split the impacted edge(s) but keep one closed shape.
                        pts = list(
                            poly[:-1] if self._points_equal(poly[0], poly[-1]) else poly
                        )
                        if len(pts) < 3:
                            result_polys.append(poly)
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
                                result_polys.append(rebuilt)
                                any_split = True
                                closed_splits += 1
                            else:
                                result_polys.append(poly)
                        else:
                            result_polys.append(poly)
                except (TypeError, ValueError, GEOSException):
                    # Any Shapely error — keep original geometry untouched
                    result_polys.append(poly)
            else:
                # ── Split open geometry segment-by-segment ────────────────
                try:
                    pts = list(poly)
                    if len(pts) < 2:
                        result_polys.append(poly)
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
                        result_polys.extend(c for c in chains if len(c) >= 2)
                        any_split = True
                        open_splits += 1
                    else:
                        result_polys.append(poly)
                except (TypeError, ValueError, GEOSException):
                    result_polys.append(poly)

        self._polys = result_polys
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

    def _fire_poly_change(self) -> None:
        """Notify the on_poly_change callback when polylines are structurally modified."""
        if callable(self._on_poly_change):
            self._on_poly_change()

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
                menu = QMenu(self)

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

                poly = self._polys[pi]
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

        # Select mode context menu
        menu = QMenu(self)
        poly_hit = self._find_poly_at(cx, cy)
        if poly_hit is not None:
            idx = poly_hit
            is_sel = idx in self._sel
            if not is_sel:
                menu.addAction("Select", lambda: self._ctx_select(idx))
            else:
                menu.addAction("Deselect", lambda: self._ctx_deselect(idx))
            menu.addAction("Delete", lambda: self._ctx_delete_poly(idx))
            menu.addSeparator()

        context_idx = poly_hit

        def _ensure_context_selection() -> bool:
            if self._sel:
                return True
            if context_idx is None:
                return False
            self._sel = {context_idx}
            self._redraw()
            self._notify()
            return True

        def _run_transform(action) -> None:
            if _ensure_context_selection():
                action()
            else:
                self._show_flash("Select shape(s) first", 1000)

        def _run_prompted_transform(
            title: str,
            label: str,
            default: float,
            minimum: float,
            callback,
        ) -> None:
            value, ok = QInputDialog.getDouble(
                self,
                title,
                label,
                default,
                minimum,
                1_000_000.0,
                3,
            )
            if ok:
                callback(value)

        def _show_topology_feedback(
            count: int, success_message: str, empty_message: str
        ) -> None:
            if count:
                self._show_flash(success_message.format(count=count), 900)
            else:
                self._show_flash(empty_message, 900)

        if self._sel:
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            if context_idx is not None:
                menu.addAction(
                    "Select connected object",
                    lambda: self._ctx_select_connected(context_idx),
                )
            menu.addAction("Invert selection", self.invert_selection)
            menu.addAction("Deselect all", self.deselect_all)
            menu.addAction("Duplicate  [⌘D]", self.duplicate_selected)
            menu.addAction("Fit selection", self.fit_selection)
            menu.addAction(
                "Explode to segments",
                lambda: _run_transform(self.explode_selected_to_segments),
            )
            menu.addAction(
                "Merge segments to object",
                lambda: _run_transform(self.merge_selected_segments_to_objects),
            )
        else:
            menu.addAction("Select all", self.select_all)
            if context_idx is not None:
                menu.addAction(
                    "Select connected object",
                    lambda: self._ctx_select_connected(context_idx),
                )

        if callable(getattr(self, "_send_selected_to_draft_cb", None)):
            menu.addAction(
                "Send selected to Draft",
                lambda: _run_transform(self._send_selected_to_draft),
            )
        if callable(getattr(self, "_send_selected_to_pattern_cb", None)):
            menu.addAction(
                "Use as outline",
                lambda: _run_transform(self._send_selected_to_pattern),
            )
        if callable(getattr(self, "_use_selected_as_fill_pattern_cb", None)):
            menu.addAction(
                "Use as pattern fill",
                lambda: _run_transform(self._use_selected_as_fill_pattern),
            )

        transform_menu = menu.addMenu("Transform")
        transform_menu.addAction(
            "Rotate +90°", lambda: _run_transform(lambda: self.rotate_selected(90.0))
        )
        transform_menu.addAction(
            "Rotate -90°", lambda: _run_transform(lambda: self.rotate_selected(-90.0))
        )
        transform_menu.addAction(
            "Mirror horizontal",
            lambda: _run_transform(lambda: self.mirror_selected("horizontal")),
        )
        transform_menu.addAction(
            "Mirror vertical",
            lambda: _run_transform(lambda: self.mirror_selected("vertical")),
        )

        dim_menu = transform_menu.addMenu("Dimensions / Spacing")
        dim_menu.addAction(
            "Edit width + height…  [⌘⇧D]",
            lambda: _run_transform(self._prompt_edit_dimensions),
        )
        dim_menu.addAction(
            "Set line length…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Set Line Length",
                    "Line length (mm):",
                    10.0,
                    0.001,
                    self._set_selected_line_length,
                )
            ),
        )
        dim_menu.addAction(
            "Distribute horizontal spacing…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Distribute Horizontal",
                    "Spacing (mm):",
                    1.0,
                    0.0,
                    lambda value: self._distribute_selected("horizontal", value),
                )
            ),
        )
        dim_menu.addAction(
            "Distribute vertical spacing…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Distribute Vertical",
                    "Spacing (mm):",
                    1.0,
                    0.0,
                    lambda value: self._distribute_selected("vertical", value),
                )
            ),
        )
        dim_menu.addAction(
            "Offset selected…",
            lambda: _run_transform(
                lambda: _run_prompted_transform(
                    "Offset Geometry",
                    "Offset distance (mm):",
                    1.0,
                    -1_000_000.0,
                    lambda value: self._offset_selected_with_feedback(value),
                )
            ),
        )

        align_menu = transform_menu.addMenu("Align")
        align_menu.addAction(
            "Left", lambda: _run_transform(lambda: self.align_selected("left"))
        )
        align_menu.addAction(
            "Center X", lambda: _run_transform(lambda: self.align_selected("center-x"))
        )
        align_menu.addAction(
            "Right", lambda: _run_transform(lambda: self.align_selected("right"))
        )
        align_menu.addAction(
            "Top", lambda: _run_transform(lambda: self.align_selected("top"))
        )
        align_menu.addAction(
            "Center Y", lambda: _run_transform(lambda: self.align_selected("center-y"))
        )
        align_menu.addAction(
            "Bottom", lambda: _run_transform(lambda: self.align_selected("bottom"))
        )

        # Group / Ungroup
        if len(self._sel) >= 2:
            menu.addAction("Group", self._group_selected)
        if any(i in self._groups for i in self._sel):
            menu.addAction("Ungroup", self._ungroup_selected)

        topology_menu = menu.addMenu("Polyline topology")
        topology_menu.addAction(
            "Close selected  [⇧C]",
            lambda: _run_transform(
                lambda: _show_topology_feedback(
                    self._close_selected_polylines(),
                    "Closed {count} polyline(s)",
                    "No open polyline selected",
                )
            ),
        )
        topology_menu.addAction(
            "Open selected  [⇧O]",
            lambda: _run_transform(
                lambda: _show_topology_feedback(
                    self._open_selected_polylines(),
                    "Opened {count} polyline(s)",
                    "No closed polyline selected",
                )
            ),
        )
        topology_menu.addAction(
            "Toggle open/closed",
            lambda: _run_transform(
                lambda: _show_topology_feedback(
                    self._toggle_selected_polyline_topology(),
                    "Updated {count} polyline(s)",
                    "No polyline updated",
                )
            ),
        )

        edit_menu = menu.addMenu("Edit geometry")
        edit_menu.addAction(
            "Trim to intersections",
            lambda: _run_transform(
                lambda: _show_topology_feedback(
                    self.trim_selected_to_intersections(),
                    "Trimmed {count} polyline(s)",
                    "No trim intersections found",
                )
            ),
        )
        edit_menu.addAction(
            "Extend to intersections",
            lambda: _run_transform(
                lambda: _show_topology_feedback(
                    self.extend_selected_to_intersections(),
                    "Extended {count} polyline(s)",
                    "No extension intersections found",
                )
            ),
        )

        menu.addSeparator()
        menu.addAction("Fit view  [F]", self.fit)
        grid_action = menu.addAction("Show grid")
        grid_action.setCheckable(True)
        grid_action.setChecked(self._grid_visible)
        grid_action.triggered.connect(self.set_grid_visible)
        snap_action = menu.addAction("Snap to grid")
        snap_action.setCheckable(True)
        snap_action.setChecked(self._grid_snap)
        snap_action.triggered.connect(self.set_grid_snap)
        mode_menu = menu.addMenu("Mode")
        mode_menu.addAction("Select  [Esc]", lambda: self.set_mode("select"))
        mode_menu.addAction("Draw  [D]", lambda: self.set_mode("draw"))
        mode_menu.addAction("Edit  [E]", lambda: self.set_mode("edit"))
        menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))

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

    def _ungroup_selected(self) -> None:
        ungrouped = {self._groups.pop(idx) for idx in self._sel if idx in self._groups}
        if not ungrouped:
            return
        # Also remove other group members if their whole group is being dissolved
        stale = {idx for idx, gid in list(self._groups.items()) if gid in ungrouped}
        for idx in stale:
            self._groups.pop(idx, None)
        self._show_flash("Ungrouped", 700)
        self._notify()

    def _delete_vertex(self, pi: int, vi: int) -> None:
        # Check if shape is currently closed BEFORE deletion
        poly = self._polys[pi]
        is_closed = (
            len(poly) >= 4
            and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        )

        self._push_undo()
        self._polys[pi].pop(vi)
        self._redraw()

        # Re-close shape if it was closed before deletion
        if is_closed and len(self._polys[pi]) >= 4:
            self._polys[pi][-1] = self._polys[pi][0]
        self._notify()
        self._fire_poly_change()

    def _chamfer_vertex(self, pi: int, vi: int, dist: float) -> bool:
        if not (0 <= pi < len(self._polys)) or dist <= 0:
            return False
        poly = self._polys[pi]
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
        self._polys[pi] = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _round_vertex(self, pi: int, vi: int, radius: float) -> bool:
        if not (0 <= pi < len(self._polys)) or radius <= 0:
            return False
        poly = self._polys[pi]
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
        self._polys[pi] = new_poly
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _delete_poly(self, pi: int) -> None:
        self._push_undo()
        self._polys.pop(pi)
        remapped: set[int] = set()
        for idx in self._construction_polys:
            if idx == pi:
                continue
            remapped.add(idx - 1 if idx > pi else idx)
        self._construction_polys = remapped
        self._sel.discard(pi)
        self._sel = {i if i < pi else i - 1 for i in self._sel if i != pi}
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _ctx_select(self, idx: int) -> None:
        self._sel.add(idx)
        self._redraw()
        self._notify()

    def _ctx_deselect(self, idx: int) -> None:
        self._sel.discard(idx)
        self._redraw()
        self._notify()

    def _ctx_delete_poly(self, idx: int) -> None:
        self._push_undo()
        self._polys.pop(idx)
        remapped: set[int] = set()
        for pi in self._construction_polys:
            if pi == idx:
                continue
            remapped.add(pi - 1 if pi > idx else pi)
        self._construction_polys = remapped
        self._sel.discard(idx)
        self._sel = {i if i < idx else i - 1 for i in self._sel if i != idx}
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _ctx_select_connected(self, idx: int | None) -> None:
        if idx is None:
            return
        self._sel = self._connected_poly_indices(idx)
        self._redraw()
        self._notify()

    def _offset_selected_with_feedback(self, distance: float) -> None:
        created = self.offset_selected(distance)
        if created:
            self._show_flash(f"Offset {created} polyline(s)", 900)
        else:
            self._show_flash("Offset failed", 900)

    def _prompt_offset_selected(self) -> None:
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return
        value, ok = QInputDialog.getDouble(
            self,
            "Offset Geometry",
            "Offset distance (mm):",
            1.0,
            -1_000_000.0,
            1_000_000.0,
            3,
        )
        if ok:
            self._offset_selected_with_feedback(value)

    def _active_vertex_for_shortcuts(self) -> tuple[int, int] | None:
        """Return the best vertex target for round/chamfer keyboard shortcuts."""
        if self._edit_poly is not None and self._edit_vert is not None:
            return (self._edit_poly, self._edit_vert)
        if self._hover_vert is not None:
            return self._hover_vert
        if self._cursor_wx is not None and self._cursor_wy is not None:
            cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
            return self._find_nearest_vertex(cx, cy)
        return None

    def _normalized_corner_vertex(self, pi: int, vi: int) -> tuple[int, int] | None:
        """Return a valid interior-corner vertex for round/chamfer, if possible."""
        if not (0 <= pi < len(self._polys)):
            return None
        if pi in self._locked_polys:
            return None
        poly = self._polys[pi]
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return None
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return None
        if not closed and (vi == 0 or vi == n - 1):
            return None
        return (pi, vi)

    def _corner_vertex_for_shortcuts(self) -> tuple[int, int] | None:
        """Resolve keyboard round/chamfer target to the nearest valid corner."""
        active = self._active_vertex_for_shortcuts()
        if active is not None:
            normalized = self._normalized_corner_vertex(*active)
            if normalized is not None:
                return normalized

        if self._cursor_wx is None or self._cursor_wy is None:
            return None
        ccx, ccy = self._w2c(self._cursor_wx, self._cursor_wy)

        # Prefer currently selected polylines when available.
        if self._sel:
            poly_indices = [
                pi for pi in sorted(self._sel) if 0 <= pi < len(self._polys)
            ]
        else:
            poly_indices = list(range(len(self._polys)))

        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for pi in poly_indices:
            poly = self._polys[pi]
            for vi, pt in enumerate(poly):
                normalized = self._normalized_corner_vertex(pi, vi)
                if normalized is None:
                    continue
                cx, cy = self._w2c(*pt)
                d = math.hypot(ccx - cx, ccy - cy)
                if d < best_dist:
                    best_dist = d
                    best = normalized
        return best

    def _prompt_round_shortcut(self) -> None:
        target = self._corner_vertex_for_shortcuts()
        if target is None:
            self._show_flash("Pick a valid corner vertex first", 1000)
            return
        pi, vi = target
        radius, ok = QInputDialog.getDouble(
            self,
            "Round Corner",
            "Radius (mm):",
            1.0,
            0.01,
            1_000_000.0,
            3,
        )
        if not ok:
            return
        if self._round_vertex(pi, vi, radius):
            self._show_flash("Rounded corner", 900)
        else:
            self._show_flash("Round failed", 900)

    def _prompt_chamfer_shortcut(self) -> None:
        target = self._corner_vertex_for_shortcuts()
        if target is None:
            self._show_flash("Pick a valid corner vertex first", 1000)
            return
        pi, vi = target
        distance, ok = QInputDialog.getDouble(
            self,
            "Chamfer Corner",
            "Distance (mm):",
            1.0,
            0.01,
            1_000_000.0,
            3,
        )
        if not ok:
            return
        if self._chamfer_vertex(pi, vi, distance):
            self._show_flash("Chamfered corner", 900)
        else:
            self._show_flash("Chamfer failed", 900)

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
