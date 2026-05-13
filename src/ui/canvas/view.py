"""PolylineView — interactive pan/zoom QGraphicsView with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, TypeAlias

from PIL import Image as PILImage
from PySide6.QtCore import (
    QEvent,
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
    QLineEdit,
    QWidget,
)
from shapely.errors import GEOSException
from shapely.geometry import (
    LineString,
    Polygon,
)
from shapely.ops import split as shapely_split

from src.backend.geometry.arc import (
    arc_from_center_start_end,
    arc_from_three_points,
)
from src.constants import DRAG_THRESH, Q_BG
from src.ui.canvas._constants import EDGE_HIT as _EDGE_HIT
from src.ui.canvas._constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas._constants import SNAP_DIST as _SNAP_DIST
from src.ui.canvas._constants import VERT_HIT as _VERT_HIT
from src.ui.canvas._geom_ops_mixin import _GeomOpsMixin
from src.ui.canvas._snap_mixin import _SnapMixin
from src.ui.canvas.modes._draw_mixin import _DrawModeMixin
from src.ui.canvas.modes._edit_mixin import _EditModeMixin
from src.ui.canvas.modes._select_mixin import _SelectModeMixin
from src.ui.canvas.render import CanvasRenderer
from src.ui.canvas.shape_storage import ShapeStorage
from src.ui.core.focus_policy import blur_focused_line_edit
from src.ui.sidebars.canvas_sidebar import DrawSidebar

CanvasState: TypeAlias = tuple[
    list[list[tuple[float, float]]],
    set[int],
    set[int],
    list[str],
    list[dict[str, Any] | None],
]


class PolylineView(
    QGraphicsView,
    CanvasRenderer,
    _SnapMixin,
    _GeomOpsMixin,
    _SelectModeMixin,
    _DrawModeMixin,
    _EditModeMixin,
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

        # Phase 2: New shape storage (Phase 2+)
        self._shape_storage = ShapeStorage()

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
        self._construction_polys.clear()

        # Phase 2: Migrate to new shape storage
        self._shape_storage.migrate_from_polylines(
            self._polys, self._entity_kinds, self._entity_meta
        )

        self._needs_fit = True
        self._fit()
        self._notify()

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

        # Phase 2: Migrate to new shape storage
        self._shape_storage.migrate_from_polylines(
            self._polys, self._entity_kinds, self._entity_meta
        )

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

        # Phase 2: Migrate to new shape storage
        self._shape_storage.migrate_from_polylines(
            self._polys, self._entity_kinds, self._entity_meta
        )

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
        self._sync_shape_storage_from_entities()
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
        self._sync_shape_storage_from_entities()

    def _remove_entity(self, idx: int) -> None:
        self._polys.pop(idx)
        if idx < len(self._entity_kinds):
            self._entity_kinds.pop(idx)
        if idx < len(self._entity_meta):
            self._entity_meta.pop(idx)
        self._sync_shape_storage_from_entities()

    def _copy_entities(self) -> tuple[list[str], list[dict[str, Any] | None]]:
        return (
            list(self._entity_kinds),
            self._copy_entity_meta_list(self._entity_meta),
        )

    @staticmethod
    def _copy_entity_meta_list(
        meta_items: list[dict[str, Any] | None],
    ) -> list[dict[str, Any] | None]:
        return [deepcopy(meta) if meta is not None else None for meta in meta_items]

    def _snapshot_state(self) -> CanvasState:
        return (
            [list(p) for p in self._polys],
            set(self._sel),
            set(self._construction_polys),
            list(self._entity_kinds),
            self._copy_entity_meta_list(self._entity_meta),
        )

    def _restore_state_snapshot(self, snapshot: CanvasState) -> None:
        polys, sel, construction, kinds, meta = snapshot
        self._polys = polys
        self._sel = {i for i in sel if i < len(self._polys)}
        self._construction_polys = {i for i in construction if i < len(self._polys)}
        self._set_entities_from_copy(kinds, meta)
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

    def _set_entities_from_copy(
        self,
        kinds: list[str],
        meta: list[dict[str, Any] | None],
    ) -> None:
        self._entity_kinds = list(kinds)
        self._entity_meta = [deepcopy(m) if m is not None else None for m in meta]

    def _sync_shape_storage_from_entities(self) -> None:
        """Keep Phase-2 shape storage synchronized with current entity arrays."""
        self._shape_storage.migrate_from_polylines(
            self._polys,
            self._entity_kinds,
            self._entity_meta,
        )

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
        kept_kinds: list[str] = []
        kept_meta: list[dict[str, Any] | None] = []
        new_construction: set[int] = set()
        new_hidden: set[int] = set()
        new_locked: set[int] = set()
        new_groups: dict[int, int] = {}
        for i, p in enumerate(self._polys):
            if i in delete_set:
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
            if i in self._groups:
                new_groups[new_idx] = self._groups[i]
        self._polys = kept
        self._entity_kinds = kept_kinds
        self._entity_meta = kept_meta
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

    def _invert_selection(self) -> None:
        """Invert selection: select all unselected, deselect all selected."""
        all_indices = set(range(len(self._polys))) - self._hidden_polys
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

    def _push_undo(self) -> None:
        self._push_stack_capped(self._undo_stack, self._snapshot_state())
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

    def get_export_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [
            list(poly)
            for idx, poly in enumerate(self._polys)
            if idx not in self._construction_polys
        ]

    def get_export_dxf_state(self) -> list[dict[str, Any]]:
        self._sync_shape_storage_from_entities()
        result: list[dict[str, Any]] = []
        for idx, poly in enumerate(self._polys):
            if idx in self._construction_polys:
                continue
            kind = (
                self._entity_kinds[idx] if idx < len(self._entity_kinds) else "polyline"
            )
            meta = self._entity_meta[idx] if idx < len(self._entity_meta) else None
            if meta is None:
                export_meta: dict[str, Any] = {"name": f"shape_{idx + 1}"}
            else:
                export_meta = deepcopy(meta)
                export_meta.setdefault("name", f"shape_{idx + 1}")
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
                    if (obj is self._dim_distance_edit and not reverse) or (
                        obj is self._dim_angle_edit and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                        self._dim_angle_dirty = True
                    elif (obj is self._dim_angle_edit and not reverse) or (
                        obj is self._dim_distance_edit and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                        self._dim_distance_dirty = True
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
                    if self._sel_dim_edit is None:
                        if reverse and self._sel_badge_h_rect is not None:
                            self._show_sel_dim_editor("h", self._sel_badge_h_rect)
                        elif self._sel_badge_w_rect is not None:
                            self._show_sel_dim_editor("w", self._sel_badge_w_rect)
                        elif self._sel_badge_h_rect is not None:
                            self._show_sel_dim_editor("h", self._sel_badge_h_rect)
                    else:
                        axis = self._sel_dim_axis
                        self._apply_sel_dim_editor()
                        if reverse:
                            if axis == "h" and self._sel_badge_w_rect is not None:
                                self._show_sel_dim_editor("w", self._sel_badge_w_rect)
                            elif self._sel_badge_h_rect is not None:
                                self._show_sel_dim_editor("h", self._sel_badge_h_rect)
                        else:
                            if axis == "w" and self._sel_badge_h_rect is not None:
                                self._show_sel_dim_editor("h", self._sel_badge_h_rect)
                            elif self._sel_badge_w_rect is not None:
                                self._show_sel_dim_editor("w", self._sel_badge_w_rect)
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
                    if (self._dim_distance_edit.hasFocus() and not reverse) or (
                        self._dim_angle_edit.hasFocus() and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                        self._dim_angle_dirty = True
                    elif (self._dim_angle_edit.hasFocus() and not reverse) or (
                        self._dim_distance_edit.hasFocus() and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                        self._dim_distance_dirty = True
                    else:
                        # Neither field has focus — give focus to distance
                        if reverse:
                            self._dim_angle_edit.setFocus()
                            self._dim_angle_edit.selectAll()
                            self._dim_angle_dirty = True
                        else:
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
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._edit_drag_moved = False
                self._edit_undo_pushed = False
                self._edit_drag_anchor = self._polys[pi][vi]
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
            target_kind = (
                self._entity_kinds[target]
                if target < len(self._entity_kinds)
                else "polyline"
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
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._edit_drag_moved = False
                self._edit_undo_pushed = False
                self._edit_drag_anchor = self._polys[pi][vi]
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
            for idx, poly in enumerate(self._polys):
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
                self._push_undo()
                self._polys[pi].insert(seg_idx + 1, pt)
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
        self._sync_shape_storage_from_entities()
        if callable(self._on_poly_change):
            self._on_poly_change()
