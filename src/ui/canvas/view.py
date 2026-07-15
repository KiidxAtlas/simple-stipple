"""PolylineView — interactive pan/zoom canvas widget with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any, cast

from PIL import Image as PILImage
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QSpinBox, QWidget
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

from src.backend.constraints import GeometricConstraint, solve_constraints
from src.backend.document import OperationResult
from src.backend.shapes import ShapeFactory, transform_meta
from src.backend.snapping import polygon_centroid as _polygon_centroid
from src.backend.snapping import (
    snap_to_polyline as _snap_to_polyline_candidates,
)
from src.infra.constants import DRAG_THRESH
from src.infra.settings import (
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_DRAW_SIDEBAR_WIDTH,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
)
from src.ui.canvas.constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas.document import CanvasDocument, EntityRecord, new_entity_id
from src.ui.canvas.geometry_model import (
    CanvasGeometry,
    entity_shows_point_handles,
    geometry_for_entity,
    move_entity_control_point,
    shape_for_entity,
    synchronize_entity_control_points,
    transform_entity_metadata,
    update_entity_parameter,
)
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.canvas.interaction import tools as canvas_tools
from src.ui.canvas.mixins.canvas_math import HitTestMixin, LayerMixin, SnapGlueMixin
from src.ui.canvas.mixins.draw_ops import DrawSidebarMixin, SmoothingMixin
from src.ui.canvas.mixins.hud_text import HudMixin, TextOpsMixin
from src.ui.canvas.mixins.render import CanvasRenderer
from src.ui.canvas.mixins.selection_ops import (
    ClipboardMixin,
    GizmoDragMixin,
    GroupingMixin,
)
from src.ui.canvas.snap import SnapEngine
from src.ui.canvas.undo import HistoryState, UndoStore
from src.ui.components import blur_focused_line_edit
from src.ui.util import DEFAULT_UNIT_SYSTEM
from src.ui.widgets.draw_sidebar import DrawSidebar

_MAX_SCALE = 20000.0  # px per mm — deep zoom for tiny features


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
            (float(p[0]), float(p[1])) for p in cps if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
    return out


class PolylineView(
    QWidget,
    CanvasRenderer,
    ClipboardMixin,
    GizmoDragMixin,
    TextOpsMixin,
    HudMixin,
    GroupingMixin,
    SmoothingMixin,
    LayerMixin,
    HitTestMixin,
    DrawSidebarMixin,
    SnapGlueMixin,
):
    """
    Displays polyline lists with Select / Draw / Edit modes.

    Modes:
    - ``select`` — click polylines to select/deselect, Shift+drag rubber-band
    - ``draw``   — click to place vertices, finish with dbl-click/Enter/right-click
    - ``edit``   — drag vertices, double-click edge to insert, right-click vertex to delete

    Set ``selectable=False`` for a display-only preview (no mode switching).
    """

    @property
    def _entities(self) -> list[EntityRecord]:
        return self._document.entities

    @_entities.setter
    def _entities(self, entities: list[EntityRecord]) -> None:
        document = self.__dict__.get("_document")
        if document is None:
            self._document = CanvasDocument(list(entities))
        else:
            document.entities = list(entities)
            document.ensure_unique_ids()

    @property
    def _sel(self) -> set[int]:
        return self._document.selection

    @_sel.setter
    def _sel(self, selection: set[int]) -> None:
        self._document.selection = set(selection)

    @property
    def _layer_order(self) -> list[str]:
        return self._document.layer_order

    @_layer_order.setter
    def _layer_order(self, value: list[str]) -> None:
        self._document.layer_order = list(value)

    @property
    def _active_layer(self) -> str | None:
        return self._document.active_layer

    @_active_layer.setter
    def _active_layer(self, value: str | None) -> None:
        self._document.active_layer = value

    @property
    def _layer_colors(self) -> dict[str, str]:
        return self._document.layer_colors

    @_layer_colors.setter
    def _layer_colors(self, value: dict[str, str]) -> None:
        self._document.layer_colors = dict(value)

    @property
    def _group_labels(self) -> dict[int, str]:
        return self._document.group_labels

    @_group_labels.setter
    def _group_labels(self, value: dict[int, str]) -> None:
        self._document.group_labels = dict(value)

    @property
    def _next_group_id(self) -> int:
        return self._document.next_group_id

    @_next_group_id.setter
    def _next_group_id(self, value: int) -> None:
        self._document.next_group_id = int(value)

    selectionChanged = Signal(int)  # type: ignore[assignment]
    geometryChanged = Signal()
    modeChanged = Signal(str)
    drawSidebarWidthChanged = Signal(int)
    drawSidebarHeightChanged = Signal(int)
    smoothingMethodChanged = Signal(str)
    smoothIterationsChanged = Signal(int)
    simplifyToleranceChanged = Signal(float)

    def _flagged(self, attr: str) -> set[int]:
        """Indices of entities whose boolean ``attr`` is set."""
        return self._document.flagged_indices(attr)

    def _set_flagged(self, attr: str, indices) -> None:
        """Set boolean ``attr`` to exactly ``indices`` (wholesale assignment)."""
        self._document.set_flagged_indices(attr, indices)

    def _is_locked(self, idx: int) -> bool:
        return 0 <= idx < len(self._entities) and self._entities[idx].locked

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
        self._document = CanvasDocument()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._selectable = selectable
        self._empty_message = "No polylines loaded"
        self._show_selection_bbox: bool = False
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        # Layer model. ``_active_layer is None`` = single-layer mode: every
        # entity is interactive and ``EntityRecord.layer`` is ignored.
        # Multi-layer pages (Draft) install an ordered layer list + active
        # layer; entities on non-active layers render dimmed and are not
        # selectable/editable until their layer is activated.
        # Optional per-layer color (hex string), shown in the layer tree
        # swatch and tinting that layer's canvas outlines.

        # Lazily-built Shape objects for the snap engine (invalidated on
        # any structural/geometry change).
        self._snap_shapes_cache: list | None = None

        # construction/hidden/locked/group flags live on EntityRecord.
        self._accent_polys: dict[int, str] = {}  # index → color hex for role overlays
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
        self._lasso_select_enabled: bool = False
        self._lasso_active: bool = False
        self._lasso_points: list[QPointF] = []
        self._lasso_additive: bool = False
        self._context_menu_sections: set[str] = set(DEFAULT_CONTEXT_MENU_SECTIONS)
        # Named reusable geometry snippets. Definitions live in view state so
        # a workspace carries its own small symbol library without introducing
        # a second document format or global asset database.
        self._symbol_library: dict[str, list[dict[str, Any]]] = {}

        # Knife tool: a transient screen gesture whose world-space line is
        # fed through the same robust splitter used by draw-time splitting.
        self._knife_start_w: tuple[float, float] | None = None
        self._knife_end_w: tuple[float, float] | None = None
        self._last_split_result_indices: set[int] = set()
        self._last_operation_result = OperationResult.unchanged("")
        self._last_repeat_action: tuple[str, Any] | None = None
        self._operation_preview_polys: list[list[tuple[float, float]]] = []

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
        # Display-only unit — all internal storage/geometry stays mm.
        self._unit_system: str = DEFAULT_UNIT_SYSTEM
        # Algorithm smooth_selected() runs: "chaikin" | "gaussian" | "catmull_rom".
        self._smoothing_method: str = DEFAULT_SMOOTHING_METHOD
        # Seed values for the Smooth/Simplify HUD prompts — remembers the
        # last value typed so the user doesn't retype it every time.
        self._smooth_iterations: int = DEFAULT_SMOOTH_ITERATIONS
        self._simplify_tolerance: float = DEFAULT_SIMPLIFY_TOLERANCE

        # Fit scale for zoom-% display
        self._fit_scale: float = 1.0
        self._view_back: list[tuple[float, float, float]] = []
        self._view_forward: list[tuple[float, float, float]] = []
        self._last_view_record_time = 0.0
        self._restoring_view = False

        # Measure tool
        self._measure_mode: bool = False
        self._measure_anchor: tuple[float, float] | None = None
        self._measure_hover: tuple[float, float] | None = None
        self._measure_locked: bool = False
        self._measure_end: tuple[float, float] | None = None
        self._measure_snapped_a: bool = False
        self._measure_snapped_b: bool = False
        self._measure_edit: QLineEdit | None = None

        # Persistent dimension/annotation tool — reference-only overlay, like
        # ruler guides: view-state (saved/loaded with the workspace) but not
        # undo-tracked and never written to DXF, so a dimension line can
        # never accidentally get cut/engraved.
        self._dimension_mode: bool = False
        self._dimensions: list[dict] = []
        self._dim_pending_p1: tuple[float, float] | None = None
        self._dim_pending_p2: tuple[float, float] | None = None
        self._dim_pending_offset: float = 5.0
        self._selected_dimension: int | None = None
        self._dimension_drag: int | None = None

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
            "knife": canvas_tools.KnifeTool(self),
        }
        self._measure_tool = canvas_tools.MeasureTool(self)
        self._dimension_tool = canvas_tools.DimensionTool(self)

        # Draw mode state
        self._draw_pts: list[tuple[float, float]] = []
        self._draw_point_snap_types: list[str | None] = []
        self._draw_primitive: str = (
            "polyline"  # polyline|line|arc|rectangle|circle|ellipse|polygon|slot
        )
        self._draw_shape_preview_active: bool = False
        self._draw_shape_anchor_w: tuple[float, float] | None = None
        self._draw_shape_cursor_w: tuple[float, float] | None = None
        # Side count used the next time a polygon is drawn; adjustable via
        # a HUD prompt when the Shapes picker lands on "polygon".
        self._draw_polygon_sides: int = 6
        self._draw_star_points: int = 5
        self._draw_arc_pts: list[tuple[float, float]] = []
        self._draw_arc_mode: str = "3point"
        self._draw_constraint_lock: str | None = None

        # Pen tool (bezier curves): plain click = corner anchor; click-drag
        # = smooth anchor with a symmetric tangent handle sized by the drag.
        self._pen_pts: list[tuple[float, float]] = []
        self._pen_tangents: list[tuple[float, float]] = []
        self._pen_dragging: bool = False
        self._pen_press_screen: tuple[float, float] | None = None

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
        self._hover_bezier_handle: tuple[int, int, str] | None = None
        self._bezier_handle_drag: tuple[int, int, str] | None = None
        self._bezier_handle_drag_moved: bool = False
        self._bezier_handle_undo_pushed: bool = False
        # Select-mode hover pre-highlight: which polyline a click would pick
        self._hover_poly: int | None = None
        # Last displayed cursor position (rounded), to skip redundant repaints
        self._prev_cursor_display: tuple[float, float] | None = None

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

        # Clipboard is process-wide (see _clipboard property below) — do not
        # reset it here, or opening a new window/tab mid-session would wipe
        # whatever the user just copied elsewhere.

        # Image bounds reference rectangle
        self._img_bounds: tuple[float, float] | None = None

        # Background image overlay
        self._bg_pil: PILImage.Image | None = None
        self._bg_w_mm: float = 0.0
        self._bg_h_mm: float = 0.0
        self._bg_pixmap: QPixmap | None = None
        self._bg_cached_scale: float = 0.0

        # Measure / Dimension button rects
        self._mbtn_rect: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._dbtn_rect: tuple[float, float, float, float] = (0, 0, 0, 0)

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
        self._hover_snap_multi: list[tuple[tuple[float, float], str, tuple[float, float]]] = []
        # Measure pre-anchor hover snap point
        self._measure_hover_pre: tuple[float, float] | None = None

        # Precision aids
        self._grid_visible: bool = False
        self._grid_snap: bool = False
        self._grid_spacing: float = 5.0
        self._geometry_health_visible: bool = False
        self._curvature_visible: bool = False
        self._constraints: list[GeometricConstraint] = []

        # Independent snap-category toggles (all default on, matching prior
        # unconditional behavior). Master is a hard kill-switch for every
        # snap source; vertex/edge gate the two SnapEngine candidate
        # families; angle gates the Shift-held 45-degree snap.
        self._snap_master_enabled: bool = True
        self._snap_vertex_enabled: bool = True
        self._snap_edge_enabled: bool = True
        self._snap_tangent_enabled: bool = True
        self._snap_extension_enabled: bool = True
        self._snap_angle_enabled: bool = True

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
        self._gizmo_local_shape: dict[str, Any] | None = None
        self._gizmo_drag_moved: bool = False
        self._gizmo_undo_pushed: bool = False
        # Persistent aspect-ratio lock (properties panel toggle) — unlike
        # the existing Shift-to-constrain gizmo behavior, this stays on
        # across both gizmo drags and typed width/height edits until
        # explicitly turned off.
        self._aspect_ratio_locked: bool = False
        self._property_highlight: str | None = None

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
        self._draw_shape_sides_spin: QSpinBox | None = None
        self._draw_sidebar_width: int = DEFAULT_DRAW_SIDEBAR_WIDTH
        self._draw_sidebar_height: int | None = None  # None => auto-fit available space
        self._draw_sidebar_sections: list[str] = list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        self._draw_sidebar_path_tools: list[str] = list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
        self._draw_sidebar_shape_tools: list[str] = list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
        self._draw_sidebar_always_visible: bool = DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE

        self._needs_fit = True
        self.setMouseTracking(True)
        self._build_draw_sidebar()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, polys: list[list[tuple[float, float]]]) -> None:
        self._entities = [EntityRecord(points=list(p), layer=self._active_layer) for p in polys]
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

    def _flattened_points(self, idx: int) -> list[tuple[float, float]]:
        """This entity's points, ready to hand to code that only
        understands "a plain polygon" (another page, a fill pattern,
        DXF export): re-tessellates curve/parametric kinds instead of
        handing out their sparse control points or a stale fixed-resolution
        sampling, so a curve used as an outline elsewhere never comes out
        faceted/jagged. ``.points`` itself is left untouched — it stays the
        sparse, editable control-point representation used by Edit mode,
        undo, and workspace save/load.
        """
        if not (0 <= idx < len(self._entities)):
            return []
        return self._geometry_for_entity(idx).tessellate()

    def _geometry_for_entity(self, idx: int) -> CanvasGeometry:
        """Return the geometry behavior object for a document entity."""
        return geometry_for_entity(self._entities[idx])

    def _entity_shows_point_handles(self, idx: int) -> bool:
        return entity_shows_point_handles(self._entities[idx])

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [self._flattened_points(i) for i in range(len(self._entities))]

    def set_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        self._entities = [EntityRecord(points=list(p), layer=self._active_layer) for p in polys]
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

    def add_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        """Append polylines as new entities alongside whatever is already on
        the canvas — unlike ``set_polylines_state``, which replaces
        everything. Used for cross-tab "send selection to Draft", where the
        existing draft content must be preserved. Newly added entities end
        up selected."""
        if not polys:
            return
        self._push_undo()
        new_indices = {self._append_entity(list(p)) for p in polys if len(p) >= 2}
        self._sel = new_indices
        self._sync_shape_storage_from_entities()

        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()
        self._fire_poly_change()

    def get_view_state(self) -> dict[str, Any]:
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
            "dimensions": [dict(d) for d in self._dimensions],
            "rulers_visible": self._rulers_visible,
            "geometry_health_visible": self._geometry_health_visible,
            "curvature_visible": self._curvature_visible,
            "constraints": [constraint.to_dict() for constraint in self._constraints],
            "layer_colors": dict(self._layer_colors),
            "hidden_indices": sorted(self._flagged("hidden")),
            "locked_indices": sorted(self._flagged("locked")),
            "groups": {str(i): g for i, g in self._group_map().items()},
            "group_labels": {str(k): v for k, v in self._group_labels.items()},
            "symbols": deepcopy(self._symbol_library),
        }

    def set_view_state(self, state: dict[str, Any]) -> None:
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
        self._grid_spacing = max(0.001, _to_float(grid_spacing_state, self._grid_spacing))
        hidden_state = state.get("hidden_indices", [])
        if not isinstance(hidden_state, list):
            hidden_state = []
        locked_state = state.get("locked_indices", [])
        if not isinstance(locked_state, list):
            locked_state = []
        raw_guides = state.get("guides", [])
        if isinstance(raw_guides, list):
            self._guides = [(str(o), float(c)) for o, c in raw_guides if str(o) in ("h", "v")]
        raw_dimensions = state.get("dimensions", [])
        if isinstance(raw_dimensions, list):
            restored: list[dict] = []
            for d in raw_dimensions:
                if not isinstance(d, dict):
                    continue
                try:
                    p1 = tuple(float(v) for v in d["p1"])
                    p2 = tuple(float(v) for v in d["p2"])
                    offset = float(d["offset"])
                except (KeyError, TypeError, ValueError):
                    continue
                if len(p1) == 2 and len(p2) == 2:
                    restored.append({"p1": p1, "p2": p2, "offset": offset})
            self._dimensions = restored
        if "rulers_visible" in state:
            self._rulers_visible = bool(state.get("rulers_visible"))
        if "geometry_health_visible" in state:
            self._geometry_health_visible = bool(state.get("geometry_health_visible"))
        if "curvature_visible" in state:
            self._curvature_visible = bool(state.get("curvature_visible"))
        raw_constraints = state.get("constraints", [])
        if isinstance(raw_constraints, list):
            self._constraints = [
                parsed
                for item in raw_constraints
                if isinstance(item, dict)
                and (parsed := GeometricConstraint.from_dict(item)) is not None
            ]
        raw_colors = state.get("layer_colors", {})
        if isinstance(raw_colors, dict):
            self._layer_colors = {str(k): str(v) for k, v in raw_colors.items() if v}
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
        raw_symbols = state.get("symbols", {})
        if isinstance(raw_symbols, dict):
            self._symbol_library = {
                str(name): deepcopy(records)
                for name, records in raw_symbols.items()
                if str(name).strip() and isinstance(records, list)
            }
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
        return [self._flattened_points(i) for i in sorted(self._sel) if i < len(self._entities)]

    def _append_entity(
        self,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> int:
        index = self._document.append(
            EntityRecord(
                points=list(poly),
                kind=kind,
                meta=deepcopy(meta) if meta is not None else None,
                layer=self._active_layer,
            )
        )
        self._sync_shape_storage_from_entities()
        return index

    def _restore_history_state(
        self,
        entities: list[EntityRecord],
        sel: set[int],
        layers: HistoryState | None = None,
    ) -> None:
        self._entities = entities
        if layers is not None:
            self._layer_order = list(layers.layer_order)
            self._active_layer = layers.active_layer
            self._constraints = [
                parsed
                for item in layers.constraints
                if (parsed := GeometricConstraint.from_dict(item)) is not None
            ]
        self._sel = {i for i in sel if i < len(self._entities) and self._entity_selectable(i)}
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

    def _cancel_active_drag(self) -> bool:
        """Abort an in-progress move/gizmo/vertex drag (e.g. on Escape),
        restoring pre-drag geometry instead of leaving the shape stuck at
        its half-dragged position. Each drag kind pushes its undo shadow
        lazily on the first actual mutation (see ``_move_undo_pushed`` /
        ``_gizmo_undo_pushed`` / ``_edit_undo_pushed``), so popping that
        shadow — only if it was actually pushed — reverts the mutation.
        Returns True if a drag was cancelled.
        """
        if self._lasso_active:
            self._lasso_active = False
            self._lasso_points.clear()
            self._lasso_additive = False
            self._redraw()
            return True
        if self._knife_start_w is not None:
            self._knife_start_w = None
            self._knife_end_w = None
            self.set_mode("select")
            return True
        if self._shift_drag and self._band_start is not None:
            self._shift_drag = False
            self._band_start = None
            self._band_additive = False
            self._redraw()
            return True
        if self._move_dragging:
            if self._move_undo_pushed:
                self.undo()
            self._move_dragging = False
            self._move_origin = None
            self._move_undo_pushed = False
            self._move_anchor_w = None
            self._move_applied_w = (0.0, 0.0)
            self._move_start_pts = []
            self._move_snap_exclude_vertices = set()
            self._move_snap_exclude_segments = set()
            self._lmb_press = None
            self._lmb_prev = None
            self._lmb_target = None
            self._redraw()
            return True
        if self._gizmo_drag_mode is not None:
            if self._gizmo_undo_pushed:
                self.undo()
            self._end_gizmo_drag()
            self._redraw()
            return True
        if self._bezier_handle_drag is not None:
            if self._bezier_handle_undo_pushed:
                self.undo()
            self._bezier_handle_drag = None
            self._bezier_handle_drag_moved = False
            self._bezier_handle_undo_pushed = False
            self._redraw()
            return True
        if self._edit_dragging:
            if self._edit_undo_pushed:
                self.undo()
            self._reset_edit_interaction_state()
            self._redraw()
            return True
        return False

    def _sync_shape_storage_from_entities(self) -> None:
        """Invalidate the lazily-built snap-shape cache."""
        self._snap_shapes_cache = None

    def _snap_shapes(self) -> list:
        """Shape objects for the snap engine, rebuilt from entities on demand."""
        if self._snap_shapes_cache is None:
            ShapeFactory.reset_id_counter(0)
            self._snap_shapes_cache = [shape_for_entity(entity) for entity in self._entities]
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
            "topology": (f"{topo['closed']} closed · {topo['open']} open · {topo['points']} pts"),
        }

    def get_command_guidance(self) -> tuple[str, str]:
        """Persistent next-step guidance for the active canvas command."""
        if self._dimension_mode:
            step = len(self._dim_pending)
            return (("Pick first point" if step == 0 else "Pick second point"), "accent")
        if self._measure_mode:
            if self._measure_anchor is None:
                return "Measure: pick first point · Esc exits", "accent"
            if not self._measure_locked:
                return "Measure: pick second point · Shift snaps angle", "accent"
            return "Measurement locked · Enter edits scale · Esc exits", "success"
        if self._mode == "draw":
            if not self._draw_pts and not self._draw_shape_preview_active:
                return f"{self._draw_primitive.title()}: pick first point · Esc exits", "accent"
            return f"{self._draw_primitive.title()}: pick next point · Enter finishes · Esc cancels", "accent"
        if self._mode == "edit":
            return "Edit vertices: drag points · double-click an edge to insert · Esc exits", "accent"
        if self._mode == "trim":
            return "Trim: hover a segment to preview removal · click to apply · Esc exits", "accent"
        if self._mode == "extend":
            return "Extend: hover an open end to preview · click to apply · Esc exits", "accent"
        if self._sel:
            return f"{len(self._sel)} selected · use contextual actions or drag the gizmo", "success"
        return "Select geometry · drag empty space for a selection window", "neutral"

    def get_precision_state(self) -> dict[str, object]:
        """Public snapshot consumed by the shared precision bar."""
        return {
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
            "construction_mode": self._draw_construction_mode,
            "measure_mode": self._measure_mode,
            "snap_master": self._snap_master_enabled,
            "snap_vertex": self._snap_vertex_enabled,
            "snap_edge": self._snap_edge_enabled,
            "snap_tangent": self._snap_tangent_enabled,
            "snap_extension": self._snap_extension_enabled,
            "snap_angle": self._snap_angle_enabled,
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
        self._entities = [e for i, e in enumerate(self._entities) if i not in drop]
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
        drop = {i for i in indices if 0 <= i < len(self._entities) and not self._entities[i].locked}
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
        result = self._undo_store.undo(self._entities, self._sel, self._layer_state())
        if result is None:
            return False
        self._restore_history_state(*result)
        self._reset_edit_interaction_state()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def redo(self) -> bool:
        result = self._undo_store.redo(self._entities, self._sel, self._layer_state())
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

    def select_open_paths(self) -> None:
        """Select every interactive path whose endpoints do not close."""
        blocked = self._noninteractive_indices()
        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and not self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def select_closed_paths(self) -> None:
        """Select every interactive closed path."""
        blocked = self._noninteractive_indices()
        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def select_geometry_category(self, category: str) -> int:
        """Select an interactive semantic category without exposing kind switches to UI code."""
        blocked = self._noninteractive_indices()
        parametric = {
            "arc",
            "circle",
            "ellipse",
            "rectangle",
            "rounded_rectangle",
            "polygon",
            "star",
            "slot",
            "spline",
            "bezier",
        }

        def matches(entity) -> bool:
            if category == "construction":
                return entity.construction
            if category == "text":
                return entity.kind == "text"
            if category == "parametric":
                return entity.kind in parametric
            if category == "generic_paths":
                return entity.kind in {"polyline", "line"}
            return entity.kind == category

        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and matches(entity)
        }
        self._redraw()
        self._notify()
        self._show_flash(f"Selected {len(self._sel)} {category.replace('_', ' ')}", 900)
        return len(self._sel)

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

    def toggle_dimension_mode(self) -> None:
        self._dimension_mode = not self._dimension_mode
        self._dim_pending_p1 = None
        self._dim_pending_p2 = None
        self._update_cursor()
        self._redraw()

    def _dimension_line_points(
        self, dim: dict
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """World-space endpoints of a dimension's offset line (parallel to
        p1-p2, shifted by ``offset`` mm along the segment's normal)."""
        ax, ay = dim["p1"]
        bx, by = dim["p2"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        nx, ny = -dy / length, dx / length  # world-space unit normal
        offset = dim["offset"]
        return (ax + nx * offset, ay + ny * offset), (bx + nx * offset, by + ny * offset)

    def _dimension_offset_at(self, dim: dict, wx: float, wy: float) -> float:
        """Signed perpendicular distance (mm) from (wx, wy) to the p1-p2
        line — used to compute a new offset while dragging."""
        ax, ay = dim["p1"]
        bx, by = dim["p2"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return dim["offset"]
        nx, ny = -dy / length, dx / length
        return (wx - ax) * nx + (wy - ay) * ny

    def _find_dimension_at(self, cx: float, cy: float) -> int | None:
        """Placed-dimension index within grab distance of the cursor
        (screen px) — hit-tests the offset dimension line, not p1/p2."""
        best: int | None = None
        best_d = 6.0
        for i, dim in enumerate(self._dimensions):
            line = self._dimension_line_points(dim)
            if line is None:
                continue
            (lax_w, lay_w), (lbx_w, lby_w) = line
            lax, lay = self._w2c(lax_w, lay_w)
            lbx, lby = self._w2c(lbx_w, lby_w)
            ldx, ldy = lbx - lax, lby - lay
            llen = math.hypot(ldx, ldy)
            if llen < 1e-9:
                continue
            t = max(0.0, min(1.0, ((cx - lax) * ldx + (cy - lay) * ldy) / (llen * llen)))
            px, py = lax + t * ldx, lay + t * ldy
            d = math.hypot(cx - px, cy - py)
            if d < best_d:
                best_d = d
                best = i
        return best

    def _delete_selected_dimension(self) -> None:
        di = self._selected_dimension
        if di is None or not (0 <= di < len(self._dimensions)):
            self._selected_dimension = None
            return
        del self._dimensions[di]
        self._selected_dimension = None
        self._dimension_drag = None
        self._redraw()
        self._notify()

    def set_image_bounds(self, w_mm: float, h_mm: float) -> None:
        self._img_bounds = (w_mm, h_mm)
        self._redraw()

    def set_background_image(self, pil_img: PILImage.Image, w_mm: float, h_mm: float) -> None:
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
            self._pen_pts.clear()
            self._pen_tangents.clear()
            self._pen_dragging = False
            self._pen_press_screen = None
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

    def arm_lasso_selection(self) -> None:
        """Make the next empty-canvas drag a freehand crossing selection."""
        self.set_mode("select")
        self._lasso_select_enabled = True
        self._show_flash("Lasso selection · drag around shapes · Shift adds", 1600)
        self._update_cursor()

    def create_symbol_from_selection(self) -> None:
        if not self._sel:
            self._show_flash("Select geometry for the symbol", 1100)
            return

        def _save(name: str) -> None:
            clean = name.strip()
            if not clean:
                raise ValueError("Symbol name cannot be empty")
            points = [
                point
                for index in self._sel
                if 0 <= index < len(self._entities)
                for point in self._entities[index].points
            ]
            if not points:
                raise ValueError("Selection has no geometry")
            origin_x = min(x for x, _y in points)
            origin_y = min(y for _x, y in points)
            self._copy_selected()
            records = deepcopy(self._clipboard)
            for record in records:
                record["polyline"] = [
                    (x - origin_x, y - origin_y) for x, y in record.get("polyline", [])
                ]
                record["meta"] = self._translated_entity_meta(
                    str(record.get("kind", "polyline")),
                    record.get("meta"),
                    -origin_x,
                    -origin_y,
                )
            self._symbol_library[clean] = records
            self._show_flash(f"Symbol saved: {clean}", 1000)
            self._notify()

        self._show_text_hud_prompt("Symbol name", _save)

    def insert_symbol(self) -> None:
        if not self._symbol_library:
            self._show_flash("No symbols in this workspace", 1100)
            return

        def _insert(name: str) -> None:
            if not self.insert_symbol_named(name):
                choices = ", ".join(sorted(self._symbol_library))
                raise ValueError(f"Choose: {choices}")

        self._show_text_hud_prompt(
            f"Symbol: {', '.join(sorted(self._symbol_library))}",
            _insert,
        )

    def insert_symbol_named(self, name: str) -> bool:
        match = next(
            (key for key in self._symbol_library if key.casefold() == name.strip().casefold()),
            None,
        )
        if match is None:
            return False
        old_clipboard = deepcopy(self._clipboard)
        self._clipboard = deepcopy(self._symbol_library[match])
        x = self._cursor_wx if self._cursor_wx is not None else 0.0
        y = self._cursor_wy if self._cursor_wy is not None else 0.0
        self._push_undo()
        created = self._paste_records(x, y)
        self._clipboard = old_clipboard
        created_ids = tuple(self._entities[index].id for index in created)
        self._apply_operation_result(
            OperationResult(
                changed=bool(created),
                message=f"Inserted symbol: {match}" if created else "Symbol contains no geometry",
                created_ids=created_ids,
                selected_ids=created_ids,
                metadata={"symbol": match},
            )
        )
        return bool(created)

    def rename_symbol(self, old_name: str, new_name: str) -> bool:
        clean = new_name.strip()
        if old_name not in self._symbol_library or not clean:
            return False
        conflict = next(
            (name for name in self._symbol_library if name.casefold() == clean.casefold()), None
        )
        if conflict is not None and conflict != old_name:
            self._show_flash(f"A symbol named {clean} already exists", 1200)
            return False
        records = self._symbol_library.pop(old_name)
        self._symbol_library[clean] = records
        self._notify()
        self._show_flash(f"Renamed symbol to {clean}", 900)
        return True

    def prompt_rename_symbol(self, old_name: str) -> None:
        self._show_text_hud_prompt(
            f"Rename {old_name}", lambda new_name: self.rename_symbol(old_name, new_name)
        )

    def delete_symbol(self, name: str) -> bool:
        if name not in self._symbol_library:
            return False
        del self._symbol_library[name]
        self._notify()
        self._show_flash(f"Deleted symbol: {name}", 900)
        return True

    def knife_cut(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        """Split intersected geometry with a two-point knife stroke."""
        if math.dist(start, end) < 1e-9:
            self._last_operation_result = OperationResult.unchanged("Knife stroke is too short")
            return False
        before_ids = {entity.id for entity in self._entities}
        self._push_undo()
        changed, closed_count, open_count = self._split_geometry_with_line([start, end])
        if not changed:
            self._apply_operation_result(
                OperationResult.unchanged("Knife did not cross any geometry")
            )
            return False
        pieces = closed_count + open_count
        selected_ids = tuple(
            self._entities[index].id for index in sorted(self._last_split_result_indices)
        )
        self._apply_operation_result(
            OperationResult(
                changed=True,
                message=f"Knife split {pieces} shape{'s' if pieces != 1 else ''}",
                created_ids=tuple(
                    entity_id for entity_id in selected_ids if entity_id not in before_ids
                ),
                selected_ids=selected_ids,
                metadata={"closed_splits": closed_count, "open_splits": open_count},
            )
        )
        return True

    def _apply_operation_result(self, result: OperationResult) -> OperationResult:
        """Publish one operation outcome and select its outputs by stable ID."""
        self._last_operation_result = result
        if result.selected_ids:
            wanted = set(result.selected_ids)
            self._sel = {
                index for index, entity in enumerate(self._entities) if entity.id in wanted
            }
        if result.changed:
            self._sync_shape_storage_from_entities()
            self._redraw()
            self._notify()
            self._fire_poly_change()
        elif result.message:
            self._redraw()
        text = result.message
        if result.warnings:
            warning_text = "; ".join(result.warnings)
            text = f"{text} — {warning_text}" if text else warning_text
        if text:
            self._show_flash(text, 1200 if result.warnings or not result.changed else 900)
        return result

    def prompt_morph_selected_paths(self) -> None:
        if len(self._mutable_selected_indices()) != 2:
            self._show_flash("Select exactly two paths to morph", 1200)
            return

        def _apply(percent: float) -> None:
            self._morph_selected_paths(percent)

        self._show_hud_prompt(
            "Morph amount (%)",
            50.0,
            _apply,
            minimum=0.0,
            is_length=False,
            preview=self._preview_morph_selected,
        )

    def _preview_morph_selected(self, percent: float) -> None:
        from src.backend.path_ops import morph_paths

        indices = self._mutable_selected_indices()
        if len(indices) != 2:
            self._clear_operation_preview()
            return
        try:
            points = morph_paths(
                self._entities[indices[0]].points,
                self._entities[indices[1]].points,
                percent / 100.0,
            )
        except ValueError:
            self._clear_operation_preview()
            return
        self._set_operation_preview([points])

    def _morph_selected_paths(self, percent: float) -> bool:
        from src.backend.path_ops import morph_paths

        indices = self._mutable_selected_indices()
        if len(indices) != 2:
            self._apply_operation_result(
                OperationResult.unchanged("Select exactly two paths to morph")
            )
            return False
        try:
            points = morph_paths(
                self._entities[indices[0]].points,
                self._entities[indices[1]].points,
                percent / 100.0,
            )
        except ValueError as exc:
            self._apply_operation_result(OperationResult.unchanged(str(exc)))
            return False
        self._push_undo()
        index = self._append_entity(points)
        entity_id = self._entities[index].id
        self._apply_operation_result(
            OperationResult(
                changed=True,
                message=f"Created {percent:g}% path morph",
                created_ids=(entity_id,),
                selected_ids=(entity_id,),
                metadata={"amount": percent / 100.0},
            )
        )
        self._set_repeat_action(
            f"Morph {percent:g}%", lambda value=percent: self._morph_selected_paths(value)
        )
        return True

    def _set_repeat_action(self, label: str, callback) -> None:
        self._last_repeat_action = (str(label), callback)

    def _set_operation_preview(self, polys: list[list[tuple[float, float]]]) -> None:
        self._operation_preview_polys = [list(poly) for poly in polys]
        self._redraw()

    def _clear_operation_preview(self) -> None:
        if self._operation_preview_polys:
            self._operation_preview_polys = []
            self._redraw()

    def get_mode(self) -> str:
        return self._mode

    def fit(self) -> None:
        self._record_view_history(force=True)
        self._fit()

    def fit_selection(self) -> bool:
        bounds = self._selection_bounds()
        if bounds is None:
            return False
        self._record_view_history(force=True)
        self._fit_to_bounds(bounds)
        return True

    def _view_transform(self) -> tuple[float, float, float]:
        return (self._scale, self._ox, self._oy)

    def _record_view_history(self, *, force: bool = False) -> None:
        if self._restoring_view:
            return
        now = time.monotonic()
        if not force and now - self._last_view_record_time < 0.3:
            return
        current = self._view_transform()
        if not self._view_back or self._view_back[-1] != current:
            self._view_back.append(current)
            del self._view_back[:-50]
        self._view_forward.clear()
        self._last_view_record_time = now

    def previous_view(self) -> bool:
        if not self._view_back:
            self._show_flash("No previous view", 800)
            return False
        self._view_forward.append(self._view_transform())
        transform = self._view_back.pop()
        self._restoring_view = True
        self._scale, self._ox, self._oy = transform
        self._restoring_view = False
        self._redraw()
        return True

    def next_view(self) -> bool:
        if not self._view_forward:
            self._show_flash("No next view", 800)
            return False
        self._view_back.append(self._view_transform())
        transform = self._view_forward.pop()
        self._restoring_view = True
        self._scale, self._ox, self._oy = transform
        self._restoring_view = False
        self._redraw()
        return True

    def set_empty_message(self, message: str) -> None:
        """Set the hint shown on an empty canvas ("Title\\nhint line")."""
        self._empty_message = message

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._redraw()

    def set_context_menu_sections(self, sections: list[str]) -> None:
        from src.infra.settings import normalize_context_menu_sections

        self._context_menu_sections = set(normalize_context_menu_sections(sections))

    def _context_menu_section_enabled(self, section: str) -> bool:
        return section in self._context_menu_sections

    def set_grid_snap(self, enabled: bool) -> None:
        self._grid_snap = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_grid_spacing(self, spacing: float) -> None:
        self._grid_spacing = max(0.001, float(spacing))
        self._redraw()

    def set_snap_master(self, enabled: bool) -> None:
        self._snap_master_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_snap_vertex(self, enabled: bool) -> None:
        self._snap_vertex_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_edge(self, enabled: bool) -> None:
        self._snap_edge_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_tangent(self, enabled: bool) -> None:
        self._snap_tangent_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_extension(self, enabled: bool) -> None:
        self._snap_extension_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_angle(self, enabled: bool) -> None:
        self._snap_angle_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

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
        entity = self._entities[idx]
        transform_entity_metadata(
            entity,
            transform=transform,
            center=center,
            factor=factor,
            angle_degrees=angle_deg,
            axis=axis,
            dx=dx,
            dy=dy,
        )

    @staticmethod
    def _translated_entity_meta(
        kind: str,
        meta: dict[str, Any] | None,
        dx: float,
        dy: float,
    ) -> dict[str, Any] | None:
        return transform_meta(kind, meta, transform="translate", dx=dx, dy=dy)

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
        if self._selected_dimension is not None:
            self._delete_selected_dimension()
            return
        if self._mode == "edit":
            if self._edit_selected_verts:
                self._delete_edit_vertices(set(self._edit_selected_verts))
                return
            if self._hover_vert is not None:
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
        if self._selected_dimension is not None:
            self._delete_selected_dimension()
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
        if self._edit_poly is None or self._edit_vert is None:
            return
        entity = self._entities[self._edit_poly]
        if move_entity_control_point(
            entity,
            self._edit_vert,
            (wx, wy),
            displayed_point_count=len(entity.points),
        ):
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
            synchronize_entity_control_points(entity)

    def _bezier_handles(self, entity_index: int) -> list[tuple[int, str, tuple[float, float]]]:
        """Return editable incoming/outgoing handle tips for one Bézier."""
        if not 0 <= entity_index < len(self._entities):
            return []
        entity = self._entities[entity_index]
        if entity.kind != "bezier" or not entity.meta:
            return []
        count = len(entity.points)
        legacy = [tuple(value) for value in entity.meta.get("tangents", [])]
        outgoing = [tuple(value) for value in entity.meta.get("handles_out", legacy)]
        incoming = [
            tuple(value) for value in entity.meta.get("handles_in", [(-x, -y) for x, y in legacy])
        ]
        outgoing.extend([(0.0, 0.0)] * (count - len(outgoing)))
        incoming.extend([(0.0, 0.0)] * (count - len(incoming)))
        handles: list[tuple[int, str, tuple[float, float]]] = []
        for index, anchor in enumerate(entity.points):
            for side, vector in (("in", incoming[index]), ("out", outgoing[index])):
                handles.append(
                    (index, side, (anchor[0] + float(vector[0]), anchor[1] + float(vector[1])))
                )
        return handles

    def _find_bezier_handle(self, cx: float, cy: float) -> tuple[int, int, str] | None:
        best: tuple[float, int, int, str] | None = None
        candidates = self._sel if self._mode == "select" else range(len(self._entities))
        for entity_index in candidates:
            for anchor_index, side, point in self._bezier_handles(entity_index):
                hx, hy = self._w2c(*point)
                distance = math.hypot(cx - hx, cy - hy)
                if distance <= 9.0 and (best is None or distance < best[0]):
                    best = (distance, entity_index, anchor_index, side)
        return None if best is None else (best[1], best[2], best[3])

    def _set_bezier_handle(
        self,
        entity_index: int,
        anchor_index: int,
        side: str,
        point: tuple[float, float],
        *,
        break_pair: bool = False,
    ) -> bool:
        entity = self._entities[entity_index]
        if entity.kind != "bezier" or not entity.meta or not 0 <= anchor_index < len(entity.points):
            return False
        anchor = entity.points[anchor_index]
        vector = (point[0] - anchor[0], point[1] - anchor[1])
        count = len(entity.points)
        legacy = [tuple(value) for value in entity.meta.get("tangents", [])]
        outgoing = [tuple(value) for value in entity.meta.get("handles_out", legacy)]
        incoming = [
            tuple(value) for value in entity.meta.get("handles_in", [(-x, -y) for x, y in legacy])
        ]
        outgoing.extend([(0.0, 0.0)] * (count - len(outgoing)))
        incoming.extend([(0.0, 0.0)] * (count - len(incoming)))
        node_types = [str(value) for value in entity.meta.get("node_types", [])]
        node_types.extend(["symmetric"] * (count - len(node_types)))
        if break_pair:
            node_types[anchor_index] = "corner"
        mode = node_types[anchor_index]
        target = incoming if side == "in" else outgoing
        other = outgoing if side == "in" else incoming
        target[anchor_index] = vector
        if mode == "symmetric":
            other[anchor_index] = (-vector[0], -vector[1])
        elif mode == "smooth":
            old_length = math.hypot(*other[anchor_index])
            length = math.hypot(*vector)
            if length > 1e-12:
                paired_length = old_length if old_length > 1e-12 else length
                other[anchor_index] = (
                    -vector[0] / length * paired_length,
                    -vector[1] / length * paired_length,
                )
        entity.meta["handles_in"] = incoming
        entity.meta["handles_out"] = outgoing
        entity.meta["node_types"] = node_types
        entity.meta["tangents"] = outgoing
        self._sync_shape_storage_from_entities()
        return True

    def set_bezier_node_type(self, entity_index: int, anchor_index: int, mode: str) -> bool:
        """Convert an anchor to corner, smooth, or symmetric behavior."""
        if mode not in {"corner", "smooth", "symmetric"}:
            return False
        entity = self._entities[entity_index]
        if entity.kind != "bezier" or not entity.meta or not 0 <= anchor_index < len(entity.points):
            return False
        self._push_undo()
        node_types = [str(value) for value in entity.meta.get("node_types", [])]
        node_types.extend(["symmetric"] * (len(entity.points) - len(node_types)))
        node_types[anchor_index] = mode
        entity.meta["node_types"] = node_types
        if mode != "corner":
            handles = {
                side: tip
                for vi, side, tip in self._bezier_handles(entity_index)
                if vi == anchor_index
            }
            anchor = entity.points[anchor_index]
            out = handles.get("out", anchor)
            vector = (out[0] - anchor[0], out[1] - anchor[1])
            self._set_bezier_handle(entity_index, anchor_index, "out", out)
            if math.hypot(*vector) <= 1e-12:
                self._set_bezier_handle(
                    entity_index, anchor_index, "out", (anchor[0] + 1.0, anchor[1])
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

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

    def _shape_primitive_active(self) -> bool:
        return getattr(self, "_draw_primitive", "polyline") in {
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
        }

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
        self._commit_drawn_polyline(
            drawn, primitive=primitive, close=close, created_flash="Polyline created"
        )

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
        can_cut_split = getattr(self, "_draw_split_enabled", True) and primitive in {
            "line",
            "polyline",
            "arc",
            "spline",
        }
        if can_cut_split and not close and len(poly) >= 2:
            split_happened, split_closed, split_open = self._split_geometry_with_line(poly)

        kind = "polyline"
        meta: dict[str, Any] | None = None
        if primitive == "line" and len(poly) >= 2:
            kind = "line"
            meta = {"start": tuple(poly[0]), "end": tuple(poly[-1])}
        elif primitive == "arc" and len(poly) >= 3:
            from src.backend.geometry import (
                arc_spec_from_center_start_end,
                arc_spec_from_three_points,
            )

            if getattr(self, "_draw_arc_mode", "center-start-end") == "center-start-end":
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
            meta = {
                "segments": 24,
                "closed": close,
                "control_points": [tuple(pt) for pt in poly],
                "degree": 3,
            }

        rec = EntityRecord(points=list(poly), kind=kind, meta=meta, layer=self._active_layer)
        self._entities.append(rec)
        new_idx = len(self._entities) - 1
        if getattr(self, "_draw_construction_mode", False):
            self._entities[new_idx].construction = True

        merged_idx: int | None = None
        if (
            primitive in {"line", "polyline"}
            and not getattr(self, "_draw_construction_mode", False)
            and not split_happened
            and any(
                snap_type == "vertex" for snap_type in getattr(self, "_draw_point_snap_types", [])
            )
        ):
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

    def _finish_pen(self) -> bool:
        """Commit the in-progress pen-tool curve as a ``kind="bezier"``
        entity (anchors on ``.points``, tangent offsets in ``meta``)."""
        if len(self._pen_pts) < 2:
            self._cancel_pen()
            return False
        self._push_undo()
        idx = self._append_entity(
            list(self._pen_pts),
            kind="bezier",
            meta={
                "tangents": list(self._pen_tangents),
                "handles_out": list(self._pen_tangents),
                "handles_in": [(-x, -y) for x, y in self._pen_tangents],
                "node_types": [
                    "smooth" if math.hypot(x, y) > 1e-9 else "corner" for x, y in self._pen_tangents
                ],
                "segments": 16,
                "closed": False,
            },
        )
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
        self._sel = {idx}
        self._show_flash("Curve created", 800)
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _cancel_pen(self) -> None:
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
        self._redraw()

    def _close_selected_polylines(self, *, record_undo: bool = True) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        changed = 0
        if record_undo:
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

    def close_selection_as_path(self) -> None:
        """Join the selected segments into one path (when several are
        selected) and close it — the context-menu "Close path" action."""
        if not self._sel:
            return
        # One push covers both steps below — otherwise merge-then-close
        # (a single user-visible action) costs two separate Ctrl+Z presses.
        self._push_undo()
        if len(self._sel) > 1:
            self.merge_selected_segments_to_objects(record_undo=False)
        closed = self._close_selected_polylines(record_undo=False)
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
        # Group requested vertices per polygon first, then clamp each group
        # to however many can actually be removed while keeping at least a
        # triangle (closed) / 3 points (open) — checking each vertex against
        # the *original* length independently (as this used to) lets a
        # multi-vertex band-delete strip a small polygon down to 1-2 points,
        # leaving a degenerate entity that breaks rendering/hit-testing.
        requested: dict[int, set[int]] = {}
        for pi, vi in verts:
            if self._is_locked(pi):
                continue
            poly = self._entities[pi].points
            if not (0 <= vi < len(poly)):
                continue
            requested.setdefault(pi, set()).add(vi)

        grouped: dict[int, set[int]] = {}
        for pi, vis in requested.items():
            poly = self._entities[pi].points
            closed = self._is_poly_closed(poly)
            available = (len(poly) - 1) if closed else len(poly)
            max_removable = max(0, available - 3)
            if max_removable <= 0:
                continue
            # The duplicated closing vertex is kept in sync with index 0
            # after deletion rather than removed directly.
            candidates = sorted(vi for vi in vis if not (closed and vi == len(poly) - 1))
            keep = set(candidates[:max_removable])
            if keep:
                grouped[pi] = keep

        if not grouped:
            return 0
        self._push_undo()
        deleted = 0
        for pi in sorted(grouped.keys(), reverse=True):
            if not (0 <= pi < len(self._entities)):
                continue
            poly = self._entities[pi].points
            closed = self._is_poly_closed(poly)
            for vi in sorted(grouped[pi], reverse=True):
                if 0 <= vi < len(poly):
                    poly.pop(vi)
                    deleted += 1
            # Only closed polygons need the closing point re-stitched to the
            # (possibly now different) first point — doing this for open
            # polylines too used to force-close them on every deletion.
            if closed and len(poly) >= 4:
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
            "Offset distance (mm)",
            1.0,
            self.offset_selected,
            preview=self._preview_offset_selected,
        )

    def _preview_offset_selected(self, distance: float) -> None:
        preview = []
        for index in self._mutable_selected_indices():
            poly = self._offset_polyline(self._entities[index].points, distance)
            if poly is not None and len(poly) >= 2:
                preview.append(poly)
        self._set_operation_preview(preview)

    def set_construction_mode(self, enabled: bool) -> None:
        self._draw_construction_mode = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_aspect_ratio_locked(self, enabled: bool) -> None:
        """Keep width/height proportional for both the properties panel's
        typed W/H fields and gizmo-handle drags, until turned off again."""
        self._aspect_ratio_locked = bool(enabled)

    def set_property_highlight(self, key: str | None) -> None:
        """Highlight the geometry controlled by a focused inspector field."""
        self._property_highlight = str(key) if key else None
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

    def array_duplicate_grid(self) -> None:
        self._array_duplicate_grid()

    def array_duplicate_radial(self) -> None:
        self._array_duplicate_radial()

    def attach_selected_text_to_path(self) -> None:
        """Run the "Attach Text to Path" command against the current
        selection (exactly one text object + one path)."""
        canvas_commands.run(self, "text.attach_to_path")

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

    def _layer_state(self) -> HistoryState:
        return HistoryState(
            tuple(self._layer_order),
            self._active_layer,
            tuple(constraint.to_dict() for constraint in self._constraints),
        )

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
                    "id": e.id,
                    "points": [[float(x), float(y)] for x, y in e.points],
                    "kind": e.kind,
                    "meta": deepcopy(e.meta) if e.meta is not None else None,
                    "construction": e.construction,
                    "hidden": e.hidden,
                    "locked": e.locked,
                    "group": e.group,
                    "layer": e.layer,
                    "group_label": (
                        self._group_labels.get(e.group) if e.group is not None else None
                    ),
                }
            )
        return out

    def set_entity_records(self, records: list[dict[str, Any]], *, fit: bool = False) -> None:
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
                    id=str(r.get("id") or ""),
                    kind=str(r.get("kind", "polyline")),
                    meta=meta,
                    construction=bool(r.get("construction", False)),
                    hidden=bool(r.get("hidden", False)),
                    locked=bool(r.get("locked", False)),
                    group=gid,
                    layer=(str(r["layer"]) if r.get("layer") is not None else None),
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
            kind = self._entities[idx].kind
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
            elif kind == "bezier" and len(poly) >= 2:
                # No native DXF bezier entity — export the flattened curve
                # as a plain polyline so the file looks right in any viewer.
                tessellated = self._flattened_points(idx)
                if len(tessellated) >= 2:
                    poly = tessellated
                    kind = "polyline"
                    export_meta = {}
                    if default_name:
                        export_meta["name"] = default_name
            result.append(
                {
                    "index": idx,
                    "polyline": list(poly),
                    "kind": kind,
                    "meta": export_meta,
                    "layer": self._entities[idx].layer,
                }
            )
        return result

    # Default work-area shown before anything is drawn — without this, the
    # canvas keeps its raw __init__ scale (1 px/mm) until the first fit,
    # so an empty document's rulers show a meaningless 0-800mm span instead
    # of a plausible small work area.
    _EMPTY_BBOX = (0.0, 0.0, 100.0, 100.0)

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [
            point
            for index, entity in enumerate(self._entities)
            if entity.kind not in {"xline", "ray"}
            for point in self._flattened_points(index)
        ]
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
        return [idx for idx in self._selected_indices() if not self._entities[idx].locked]

    def _selection_bounds(
        self, indices: list[int] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = indices if indices is not None else self._selected_indices()
        pts = [
            point
            for idx in items
            if self._entities[idx].kind not in {"xline", "ray"}
            for point in self._flattened_points(idx)
        ]
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
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
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

        self._dimension_mode = False
        self._dim_pending_p1 = None
        self._dim_pending_p2 = None

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

    RULER_PX = 22

    def set_rulers_visible(self, visible: bool) -> None:
        self._rulers_visible = bool(visible)
        self._layout_draw_sidebar()
        self._redraw()

    def set_geometry_health_visible(self, visible: bool) -> None:
        self._geometry_health_visible = bool(visible)
        self._show_flash(
            "Geometry health overlay: ON" if visible else "Geometry health overlay: OFF",
            900,
        )
        self._redraw()

    def set_curvature_visible(self, visible: bool) -> None:
        self._curvature_visible = bool(visible)
        self._show_flash("Curvature view: ON" if visible else "Curvature view: OFF", 900)
        self._redraw()

    def show_coordinate_entry(self, initial: str = "") -> None:
        """Place a draw point or selection using CAD coordinate notation."""
        from src.backend.coordinates import parse_coordinate

        origin = self._draw_pts[-1] if self._mode == "draw" and self._draw_pts else (0.0, 0.0)
        unit_scale = 25.4 if self._unit_system == "in" else 1.0

        def _apply(text: str) -> None:
            point = parse_coordinate(text, origin=origin, scale=unit_scale)
            if self._mode == "draw" and self._draw_primitive in {"line", "polyline"}:
                self._draw_pts.append(point)
                self._draw_point_snap_types.append("coordinate")
                self._cursor_wx, self._cursor_wy = point
                self._show_dim_inputs()
                self._redraw()
                return
            if self._sel:
                if not self.move_selection_to(*point):
                    self._show_flash("Selection is already at that coordinate", 900)
                return
            raise ValueError("Start a line/polyline or select geometry first")

        self._show_text_hud_prompt(
            "Coordinate: x,y · @dx,dy · @distance<angle",
            _apply,
            initial=initial,
        )

    def set_unit_system(self, unit: str) -> None:
        """Set the display unit ("mm" or "in"). Storage/geometry stay mm."""
        if unit not in ("mm", "in"):
            return
        self._unit_system = unit
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
        poly = self._geometry_for_entity(idx).tessellate()
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

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        """Bridge Qt paint dispatch to CanvasRenderer mixin implementation."""
        CanvasRenderer.paintEvent(self, event)
        tool = self._measure_tool if self._measure_mode else self._tools.get(self._mode)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if tool is not None:
            tool.paint_overlay(painter)
        self._paint_chrome_rulers(painter)
        painter.end()

    def eventFilter(self, obj, event) -> bool:
        """Intercept Tab/Backtab on the draw-mode dim-input QLineEdits."""
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                reverse = key == Qt.Key.Key_Backtab
                if (
                    self._draw_shape_w_edit is not None
                    and self._draw_shape_h_edit is not None
                    and (obj is self._draw_shape_w_edit or obj is self._draw_shape_h_edit)
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
        field_x, field_y = self._hud_position_near(
            cx, cy, 86, 80 if self._draw_primitive in {"polygon", "star"} else 52,
            offset_x=16, offset_y=12,
        )

        w_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
        w_edit.setText(f"{w:.2f}")
        w_edit.setProperty("shape_hud_temp", True)
        w_edit.move(field_x, field_y)
        w_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        h_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
        h_edit.setText(f"{h:.2f}")
        h_edit.setProperty("shape_hud_temp", True)
        h_edit.move(field_x, field_y + 28)
        h_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        self._draw_shape_w_edit = w_edit
        self._draw_shape_h_edit = h_edit
        self._draw_shape_w_edit.setFocus()
        self._draw_shape_w_edit.selectAll()

        if self._draw_primitive in {"polygon", "star"}:
            count = (
                self._draw_polygon_sides
                if self._draw_primitive == "polygon"
                else self._draw_star_points
            )
            sides_spin = self._make_hud_spinbox(minimum=3, maximum=64, value=count)
            sides_spin.setProperty("shape_hud_temp", True)
            sides_spin.move(field_x, field_y + 56)
            sides_spin.valueChanged.connect(self._on_polygon_sides_spin_changed)
            self._draw_shape_sides_spin = sides_spin

    def _on_polygon_sides_spin_changed(self, value: int) -> None:
        """Live-update the polygon/star ghost's point-count control."""
        if self._draw_primitive == "star":
            self._draw_star_points = value
        else:
            self._draw_polygon_sides = value
        self._redraw()

    def _dismiss_shape_dim_inputs(self) -> None:
        for edit in (
            self._draw_shape_w_edit,
            self._draw_shape_h_edit,
            self._draw_shape_sides_spin,
        ):
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
        if self._draw_shape_sides_spin is not None and bool(
            self._draw_shape_sides_spin.property("shape_hud_temp")
        ):
            self._draw_shape_sides_spin = None

    def _apply_and_commit_shape_preview(self) -> None:
        if not (self._mode == "draw" and self._draw_shape_preview_active):
            return
        self._apply_shape_size_inputs()
        self._dismiss_shape_dim_inputs()
        self._commit_shape_preview()

    def _hit_measure_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._mbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    def _hit_dimension_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._dbtn_rect
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

        if event.text() == "@" and not isinstance(QApplication.focusWidget(), QLineEdit):
            self.show_coordinate_entry("@")
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
            and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
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
                self._dim_distance_edit is not None and self._dim_distance_edit.hasFocus()
            ) or (self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus())
            if has_dim_focus or self._dim_distance_dirty or self._dim_angle_dirty:
                self._dim_distance_dirty = False
                self._dim_angle_dirty = False
                self.setFocus()  # return focus to canvas
                return
            # Cancel an in-progress dimension placement and exit dimension
            # mode outright — Escape should back all the way out, not just
            # drop the pending point and leave the tool armed.
            if self._dim_pending_p1 is not None:
                self._dim_pending_p1 = None
                self._dim_pending_p2 = None
                self._dimension_mode = False
                self._update_cursor()
                self._redraw()
                return
            # Cancel a live move/gizmo/vertex drag before it can be
            # mistaken for a plain "clear selection" — otherwise the drag
            # keeps applying to a selection that was just emptied out from
            # under it, freezing the shape at its half-dragged position.
            if self._cancel_active_drag():
                return
            # Idle in dimension/measure mode (no pending point, no drag) —
            # Escape exits the mode rather than doing nothing.
            if self._dimension_mode or self._measure_mode:
                self._dimension_mode = False
                self._measure_mode = False
                self._measure_anchor = None
                self._measure_hover = None
                self._measure_locked = False
                self._measure_end = None
                self._measure_snapped_a = False
                self._measure_snapped_b = False
                self._dismiss_measure_edit()
                self._update_cursor()
                self._redraw()
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
                        self._draw_shape_w_edit is not None and self._draw_shape_w_edit.hasFocus()
                    ) or (
                        self._draw_shape_h_edit is not None and self._draw_shape_h_edit.hasFocus()
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
                    and (self._dim_distance_edit is None or self._dim_angle_edit is None)
                ):
                    self._show_dim_inputs()
                if (
                    self._mode == "draw"
                    and self._shape_primitive_active()
                    and self._draw_shape_preview_active
                ):
                    if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
                        self._show_shape_dim_inputs()
                    if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
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
                if self._dim_distance_edit is not None and self._dim_angle_edit is not None:
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
        self._record_view_history()
        wx, wy = self._c2w(cx, cy)
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        self._ox = cx - wx * self._scale
        self._oy = cy + wy * self._scale
        self._redraw()

    def event(self, ev) -> bool:
        # macOS trackpad pinch zoom.
        if (
            ev.type() == QEvent.Type.NativeGesture
            and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
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

        if self._hit_dimension_button(pos.x(), pos.y()):
            self.toggle_dimension_mode()
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

        # Grab an existing placed dimension the same way guides work — click
        # selects it (Delete removes it); dragging moves it perpendicular.
        if (
            self._selectable
            and self._mode == "select"
            and self._dimensions
            and self._find_poly_at(pos.x(), pos.y()) is None
        ):
            di = self._find_dimension_at(pos.x(), pos.y())
            if di is not None:
                self._dimension_drag = di
                self._selected_dimension = di
                self._redraw()
                return
        if self._selected_dimension is not None:
            self._selected_dimension = None
            self._redraw()

        # Selection badges / transform gizmo take priority over tools.
        select_tool = cast(canvas_tools.SelectTool, self._tools["select"])
        if self._mode == "select" and self._sel and select_tool.press_overlays(event):
            return

        if self._dimension_mode:
            self._dimension_tool.press(event)
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

        if self._gizmo_drag_mode is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_gizmo_drag(wx, wy, event.modifiers())
            self._redraw()
            return

        if self._guide_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            orient, _ = self._guides[self._guide_drag]
            self._guides[self._guide_drag] = (
                orient,
                wy if orient == "h" else wx,
            )
            self._guide_drag_moved = True
            self._redraw()
            return

        if self._dimension_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dim = self._dimensions[self._dimension_drag]
            dim["offset"] = self._dimension_offset_at(dim, wx, wy)
            self._redraw()
            return

        if self._dimension_mode:
            self._dimension_tool.move(event)
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
            if self._guide_drag_moved and (pos.x() <= self.RULER_PX or pos.y() <= self.RULER_PX):
                del self._guides[self._guide_drag]
                self._selected_guide = None
            self._guide_drag = None
            self._guide_drag_moved = False
            self._redraw()
            return

        if self._dimension_drag is not None:
            self._dimension_drag = None
            self._redraw()
            self._notify()
            return

        if self._dimension_mode:
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
                if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
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
        result_ids: list[str] = []
        new_construction: set[int] = set()
        new_hidden: set[int] = set()
        new_locked: set[int] = set()
        new_groups: dict[int, int] = {}
        new_layers: dict[int, str | None] = {}
        changed_result_indices: set[int] = set()
        emitted_by_source: dict[int, int] = {}

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
            result_kinds.append(self._entities[src_idx].kind)
            m = self._entities[src_idx].meta
            result_meta.append(deepcopy(m) if m is not None else None)
            result_ids.append(self._entities[src_idx].id)
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
            piece_number = emitted_by_source.get(src_idx, 0)
            result_ids.append(self._entities[src_idx].id if piece_number == 0 else new_entity_id())
            emitted_by_source[src_idx] = piece_number + 1
            _carry_flags(src_idx, ni)
            changed_result_indices.add(ni)

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
                            trial = list(pieces.geoms) if hasattr(pieces, "geoms") else []
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
                        pts = list(poly[:-1] if self._points_equal(poly[0], poly[-1]) else poly)
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

        if not any_split:
            self._last_split_result_indices = set()
            return False, 0, 0

        self._entities = [
            EntityRecord(
                points=p,
                id=result_ids[i],
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
        self._last_split_result_indices = changed_result_indices
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
            cx,
            cy,
            [e.points for e in self._entities],
            self._noninteractive_indices(),
            self._scale,
            self._w2c,
            self._c2w,
            self._poly_bounds,
            self._is_poly_closed,
            self._segment_intersection_point,
            reference_point=reference_point,
            draw_points=self._draw_pts,
            mode=self._mode,
        )

    def _resolve_snap(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return self._snap_engine.query(
            cx,
            cy,
            wx,
            wy,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            reference_point=reference_point,
        )

    def _resolve_drag_snap(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        allow_polyline: bool = True,
        allow_grid: bool = True,
        allow_vertex: bool = True,
        exclude_vertices: set[tuple[int, int]] | None = None,
        exclude_segments: set[tuple[int, int]] | None = None,
        exclude_polys: set[int] | None = None,
        reference_point: tuple[float, float] | None = None,
    ) -> tuple[float, float, str] | None:
        return self._snap_engine.query(
            cx,
            cy,
            wx,
            wy,
            drag=True,
            allow_polyline=allow_polyline,
            allow_grid=allow_grid,
            allow_vertex=allow_vertex,
            exclude_vertices=exclude_vertices,
            exclude_segments=exclude_segments,
            exclude_polys=exclude_polys,
            reference_point=reference_point,
        )

    def _angle_snap(self, ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        return self._snap_engine.angle(ax, ay, wx, wy)

    # ---- Shape preview helpers (inlined from _DrawModeMixin) ----

    def _offset_selected(self, distance: float) -> int:
        indices = self._mutable_selected_indices()
        if not indices or abs(distance) <= 1e-9:
            self._last_operation_result = OperationResult.unchanged(
                "Select editable geometry and use a non-zero offset"
            )
            return 0

        created: list[tuple[list[tuple[float, float]], bool]] = []
        for idx in indices:
            poly = self._entities[idx].points
            offset_poly = self._offset_polyline(poly, distance)
            if offset_poly is None or len(offset_poly) < 2:
                continue
            created.append((offset_poly, self._entities[idx].construction))
        if not created:
            self._apply_operation_result(
                OperationResult.unchanged(
                    "Offset produced no geometry",
                    "Try the opposite direction or a smaller distance",
                )
            )
            return 0

        self._push_undo()
        created_ids: list[str] = []
        for poly, is_construction in created:
            # _append_entity keeps _entity_kinds/_entity_meta in sync — a bare
            # _polys.append desyncs them and corrupts later DXF export.
            new_idx = self._append_entity(poly)
            if is_construction:
                self._entities[new_idx].construction = True
            created_ids.append(self._entities[new_idx].id)
        self._apply_operation_result(
            OperationResult(
                changed=True,
                message=f"Offset created {len(created_ids)} shape(s)",
                created_ids=tuple(created_ids),
                selected_ids=tuple(created_ids),
                metadata={"distance": distance},
            )
        )
        self._set_repeat_action(
            f"Offset {distance:g}", lambda value=distance: self.offset_selected(value)
        )
        return len(created)

    def _fire_poly_change(self) -> None:
        """Notify the on_poly_change callback when polylines are structurally modified."""
        self._solve_geometric_constraints()
        self._sync_shape_storage_from_entities()
        if callable(self._on_poly_change):
            self._on_poly_change()

    def _solve_geometric_constraints(self) -> int:
        """Re-solve persistent constraints and prune references to deleted entities."""
        entities_by_id = {entity.id: entity for entity in self._entities}
        self._constraints = [
            constraint
            for constraint in self._constraints
            if all(entity_id in entities_by_id for entity_id in constraint.entity_ids)
        ]
        if not self._constraints:
            return 0
        solved = solve_constraints(
            {entity_id: list(entity.points) for entity_id, entity in entities_by_id.items()},
            self._constraints,
        )
        changed = 0
        for entity_id, points in solved.items():
            entity = entities_by_id[entity_id]
            if entity.points == points:
                continue
            entity.points = points
            if entity.kind == "line" and len(points) == 2:
                entity.meta = {"start": points[0], "end": points[1]}
            else:
                entity.kind = "polyline"
                entity.meta = None
            changed += 1
        return changed

    def add_geometric_constraint(self, kind: str) -> int:
        """Attach an explicit persistent constraint to selected line geometry."""
        line_indices = [
            index
            for index in self._mutable_selected_indices()
            if len(self._entities[index].points) == 2
        ]
        unary = {"horizontal", "vertical", "fixed"}
        binary = {"parallel", "perpendicular", "equal_length", "coincident"}
        if kind in unary and not line_indices:
            self._show_flash("Select one or more line segments", 1200)
            return 0
        if kind in binary and len(line_indices) != 2:
            self._show_flash("Select exactly two line segments", 1200)
            return 0
        self._push_undo()
        additions: list[GeometricConstraint] = []
        if kind in {"horizontal", "vertical"}:
            additions = [
                GeometricConstraint(kind=kind, entity_ids=(self._entities[index].id,))
                for index in line_indices
            ]
        elif kind == "fixed":
            additions = [
                GeometricConstraint(
                    kind="fixed",
                    entity_ids=(self._entities[index].id,),
                    parameters={"points": [list(point) for point in self._entities[index].points]},
                )
                for index in line_indices
            ]
        elif kind in binary:
            first, second = (self._entities[index] for index in line_indices)
            parameters: dict[str, Any] = {}
            if kind == "coincident":
                choice = min(
                    (
                        (math.dist(first.points[a], second.points[b]), a, b)
                        for a in (0, 1)
                        for b in (0, 1)
                    ),
                    key=lambda item: item[0],
                )
                parameters = {"first_endpoint": choice[1], "second_endpoint": choice[2]}
            additions = [
                GeometricConstraint(
                    kind=kind,
                    entity_ids=(first.id, second.id),
                    parameters=parameters,
                )
            ]
        else:
            return 0
        self._constraints.extend(additions)
        self._solve_geometric_constraints()
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"Added {kind.replace('_', ' ')} constraint", 1000)
        return len(additions)

    def remove_constraints_for_selection(self) -> int:
        selected_ids = {
            self._entities[index].id
            for index in self._selected_indices()
            if 0 <= index < len(self._entities)
        }
        removed = [
            constraint
            for constraint in self._constraints
            if selected_ids.intersection(constraint.entity_ids)
        ]
        if not removed:
            self._show_flash("Selection has no constraints", 900)
            return 0
        self._push_undo()
        self._constraints = [
            constraint for constraint in self._constraints if constraint not in removed
        ]
        self._fire_poly_change()
        self._redraw()
        self._notify()
        self._show_flash(f"Removed {len(removed)} constraint(s)", 1000)
        return len(removed)

    def _commit_construction_entities(
        self, records: list[tuple[list[tuple[float, float]], str, dict[str, Any] | None]]
    ) -> int:
        if not records:
            return 0
        self._push_undo()
        selected = set()
        for points, kind, metadata in records:
            index = self._append_entity(points, kind=kind, meta=metadata)
            self._entities[index].construction = True
            selected.add(index)
        self._sel = selected
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(records)

    @staticmethod
    def _infinite_line_points(
        origin: tuple[float, float], direction: tuple[float, float], *, ray: bool = False
    ) -> list[tuple[float, float]]:
        length = math.hypot(*direction)
        if length <= 1e-12:
            return []
        ux, uy = direction[0] / length, direction[1] / length
        reach = 1_000_000.0
        if ray:
            return [origin, (origin[0] + ux * reach, origin[1] + uy * reach)]
        return [
            (origin[0] - ux * reach, origin[1] - uy * reach),
            (origin[0] + ux * reach, origin[1] + uy * reach),
        ]

    def construction_line_from_selection(self, *, ray: bool = False) -> int:
        indices = [
            index
            for index in self._mutable_selected_indices()
            if len(self._entities[index].points) == 2
        ]
        if len(indices) != 1:
            self._show_flash("Select exactly one line segment", 1100)
            return 0
        start, end = self._entities[indices[0]].points
        origin = start if ray else ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        kind = "ray" if ray else "xline"
        points = self._infinite_line_points(origin, (end[0] - start[0], end[1] - start[1]), ray=ray)
        count = self._commit_construction_entities(
            [
                (
                    points,
                    kind,
                    {"origin": origin, "direction": (end[0] - start[0], end[1] - start[1])},
                )
            ]
        )
        if count:
            self._show_flash(
                "Construction ray created" if ray else "Construction line created", 900
            )
        return count

    def create_angle_bisector(self) -> int:
        from src.backend.construction import angle_bisector

        lines = [
            self._entities[index].points
            for index in self._mutable_selected_indices()
            if len(self._entities[index].points) == 2
        ]
        if len(lines) != 2:
            self._show_flash("Select exactly two intersecting lines", 1200)
            return 0
        result = angle_bisector((lines[0][0], lines[0][1]), (lines[1][0], lines[1][1]))
        if result is None:
            self._show_flash("Parallel lines have no unique angle bisector", 1300)
            return 0
        origin, direction = result
        points = self._infinite_line_points(origin, direction)
        return self._commit_construction_entities(
            [(points, "xline", {"origin": origin, "direction": direction})]
        )

    def create_centerline(self) -> int:
        from src.backend.construction import centerline

        lines = [
            self._entities[index].points
            for index in self._mutable_selected_indices()
            if len(self._entities[index].points) == 2
        ]
        if len(lines) != 2:
            self._show_flash("Select exactly two edges", 1100)
            return 0
        result = centerline((lines[0][0], lines[0][1]), (lines[1][0], lines[1][1]))
        return self._commit_construction_entities([(list(result), "line", None)])

    def create_circle_through_three_points(self) -> int:
        from src.backend.construction import circumcircle

        selected = self._mutable_selected_indices()
        candidates: list[tuple[float, float]] = []
        if len(selected) == 1:
            candidates = list(self._entities[selected[0]].points[:3])
        elif len(selected) == 3:
            candidates = [
                self._entities[index].points[0]
                for index in selected
                if self._entities[index].points
            ]
        if len(candidates) != 3:
            self._show_flash("Select one 3+ point path or three point-bearing objects", 1500)
            return 0
        result = circumcircle(*candidates)
        if result is None:
            self._show_flash("Those points are collinear", 1000)
            return 0
        center, radius = result
        shape = ShapeFactory.circle(center, radius)
        return self._commit_construction_entities(
            [(list(shape.points), "circle", {"center": center, "radius": radius})]
        )

    def create_tangents_from_point(self) -> int:
        from src.backend.construction import tangents_from_point

        selected = [self._entities[index] for index in self._mutable_selected_indices()]
        circles = [entity for entity in selected if entity.kind == "circle" and entity.meta]
        others = [entity for entity in selected if entity not in circles and entity.points]
        if len(circles) != 1 or len(others) != 1:
            self._show_flash("Select one circle and one point-bearing object", 1400)
            return 0
        center = tuple(circles[0].meta["center"])
        point = max(others[0].points, key=lambda value: math.dist(value, center))
        lines = tangents_from_point(point, center, float(circles[0].meta["radius"]))
        if not lines:
            self._show_flash("Point must be outside the circle", 1100)
            return 0
        return self._commit_construction_entities([(list(line), "line", None) for line in lines])

    def create_common_circle_tangents(self) -> int:
        from src.backend.construction import common_circle_tangents

        circles = [
            self._entities[index]
            for index in self._mutable_selected_indices()
            if self._entities[index].kind == "circle" and self._entities[index].meta
        ]
        if len(circles) != 2:
            self._show_flash("Select exactly two circles", 1100)
            return 0
        first, second = circles
        lines = common_circle_tangents(
            tuple(first.meta["center"]),
            float(first.meta["radius"]),
            tuple(second.meta["center"]),
            float(second.meta["radius"]),
        )
        if not lines:
            self._show_flash("No real common tangents", 1000)
            return 0
        return self._commit_construction_entities([(list(line), "line", None) for line in lines])

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

    def _distribute_selected(self, axis: str, spacing: float, *, mode: str = "gap") -> bool:
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
        if extent <= 1e-6:
            self._show_flash(
                "Line has no {} — change its angle first".format(
                    "width" if axis == "w" else "height"
                ),
                1100,
            )
            return False
        f = max(1e-4, min(1e4, target / extent))
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
        if len(indices) == 1:
            entity = self._entities[indices[0]]
            parameter = {
                "rectangle": ("height", height),
                "rounded_rectangle": ("height", height),
                "ellipse": ("ry", height / 2.0),
                "circle": ("radius", height / 2.0),
                "slot": ("width", height),
            }.get(entity.kind)
            if parameter is not None:
                return self.set_shape_param(indices[0], *parameter)
        cur_w = bounds[2] - bounds[0]
        cur_h = bounds[3] - bounds[1]
        if cur_h <= 1e-6:
            return False
        fy = max(1e-4, min(1e4, height / cur_h))
        fx = fy if (self._aspect_ratio_locked and cur_w > 1e-6) else 1.0
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [
                (cx + (x - cx) * fx, cy + (y - cy) * fy) for x, y in self._entities[idx].points
            ]
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
        if len(indices) == 1:
            entity = self._entities[indices[0]]
            parameter = {
                "rectangle": ("width", width),
                "rounded_rectangle": ("width", width),
                "ellipse": ("rx", width / 2.0),
                "circle": ("radius", width / 2.0),
                "slot": ("length", width),
            }.get(entity.kind)
            if parameter is not None:
                return self.set_shape_param(indices[0], *parameter)
        cur_w = bounds[2] - bounds[0]
        cur_h = bounds[3] - bounds[1]
        if cur_w <= 1e-6:
            return False
        fx = max(1e-4, min(1e4, width / cur_w))
        fy = fx if (self._aspect_ratio_locked and cur_h > 1e-6) else 1.0
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._entities[idx].points = [
                (cx + (x - cx) * fx, cy + (y - cy) * fy) for x, y in self._entities[idx].points
            ]
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
            self._apply_operation_result(OperationResult.unchanged("Click a segment to trim"))
            return False
        if self._is_locked(idx):
            self._apply_operation_result(OperationResult.unchanged("Shape is locked"))
            return False
        wx, wy = self._c2w(cx, cy)
        pts = self._entities[idx].points
        if len(pts) < 2:
            return False
        cutters = self._other_linework(idx)
        if cutters is None or cutters.is_empty:
            self._apply_operation_result(
                OperationResult.unchanged(
                    "Nothing to trim against",
                    "Add or reveal intersecting geometry",
                )
            )
            return False
        target = LineString(pts)
        try:
            pieces = [
                g
                for g in shapely_split(target, cutters).geoms
                if isinstance(g, LineString) and len(g.coords) >= 2
            ]
        except GEOSException:
            self._apply_operation_result(
                OperationResult.unchanged(
                    "Trim failed", "The target or cutter geometry may be invalid"
                )
            )
            return False
        if len(pieces) < 2:
            self._apply_operation_result(
                OperationResult.unchanged(
                    "No intersection to trim to", "Extend a cutter across the target"
                )
            )
            return False
        click = Point(wx, wy)
        drop = min(pieces, key=lambda g: g.distance(click))
        kept = [g for g in pieces if g is not drop]
        merged = linemerge(kept) if len(kept) > 1 else kept[0]
        out = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
        self._push_undo()
        first, *rest = out
        e = self._entities[idx]
        e.points = [(float(x), float(y)) for x, y in first.coords]
        e.kind = "polyline"
        e.meta = None
        selected_ids = [e.id]
        created_ids: list[str] = []
        for piece in rest:
            new_index = self._append_entity([(float(x), float(y)) for x, y in piece.coords])
            new_id = self._entities[new_index].id
            selected_ids.append(new_id)
            created_ids.append(new_id)
        self._apply_operation_result(
            OperationResult(
                changed=True,
                message="Trimmed",
                created_ids=tuple(created_ids),
                selected_ids=tuple(selected_ids),
            )
        )
        return True

    def preview_trim_at(self, cx: float, cy: float) -> None:
        """Preview the exact segment that a trim click would remove."""
        idx = self._find_poly_at(cx, cy)
        if idx is None:
            self._clear_operation_preview()
            return
        cutters = self._other_linework(idx)
        if cutters is None or cutters.is_empty:
            self._clear_operation_preview()
            return
        try:
            pieces = [
                g for g in shapely_split(LineString(self._entities[idx].points), cutters).geoms
                if isinstance(g, LineString) and len(g.coords) >= 2
            ]
        except GEOSException:
            pieces = []
        if len(pieces) < 2:
            self._clear_operation_preview()
            return
        wx, wy = self._c2w(cx, cy)
        drop = min(pieces, key=lambda geometry: geometry.distance(Point(wx, wy)))
        self._set_operation_preview([[(float(x), float(y)) for x, y in drop.coords]])

    def preview_extend_at(self, cx: float, cy: float) -> None:
        """Preview extension from the nearest open endpoint to its first target."""
        best: tuple[int, int, float] | None = None
        for index, entity in enumerate(self._entities):
            if len(entity.points) < 2 or self._is_poly_closed(entity.points):
                continue
            for endsel in (0, -1):
                ex, ey = self._w2c(*entity.points[endsel])
                distance = math.hypot(cx - ex, cy - ey)
                if distance < 18 and (best is None or distance < best[2]):
                    best = (index, endsel, distance)
        if best is None:
            self._clear_operation_preview()
            return
        index, endsel, _distance = best
        points = self._entities[index].points
        tip = points[endsel]
        neighbor = points[1] if endsel == 0 else points[-2]
        dx, dy = tip[0] - neighbor[0], tip[1] - neighbor[1]
        length = math.hypot(dx, dy)
        others = self._other_linework(index)
        if length < 1e-9 or others is None or others.is_empty:
            self._clear_operation_preview()
            return
        reach = max(self._bbox()[2] - self._bbox()[0], self._bbox()[3] - self._bbox()[1], 1.0) * 3
        ray = LineString([tip, (tip[0] + dx / length * reach, tip[1] + dy / length * reach)])
        try:
            intersection = ray.intersection(others)
        except GEOSException:
            self._clear_operation_preview()
            return
        candidates = []
        for geometry in getattr(intersection, "geoms", [intersection]):
            if isinstance(geometry, Point):
                distance = math.dist(tip, (geometry.x, geometry.y))
                if distance > 1e-6:
                    candidates.append((distance, (float(geometry.x), float(geometry.y))))
        if candidates:
            hit = min(candidates)[1]
            self._set_operation_preview([[tip, hit]])
        else:
            self._clear_operation_preview()

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
        ray = LineString([tip, (tip[0] + dx * reach, tip[1] + dy * reach)])
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
            if len(self._entities[i].points) >= 4 and self._is_poly_closed(self._entities[i].points)
        ]
        if len(indices) < 2:
            self._apply_operation_result(OperationResult.unchanged("Select 2+ closed shapes"))
            return 0
        try:
            shapes = []
            for i in indices:
                pg = Polygon(self._entities[i].points[:-1]).buffer(0)
                if not pg.is_empty:
                    shapes.append(pg)
            if len(shapes) < 2:
                self._apply_operation_result(
                    OperationResult.unchanged(
                        "Shapes are degenerate",
                        "Repair self-intersections or zero-area outlines first",
                    )
                )
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
            self._apply_operation_result(
                OperationResult.unchanged(
                    "Boolean operation failed",
                    "Check for overlapping edges or invalid outlines",
                )
            )
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
            self._apply_operation_result(
                OperationResult.unchanged(
                    "No area left after operation",
                    "The selected outlines may not overlap for this operation",
                )
            )
            return 0

        removed_ids = tuple(self._entities[index].id for index in indices)
        self._push_undo()
        self._compact_entities(set(indices))
        created_ids: list[str] = []
        for ring in rings:
            new_index = self._append_entity(ring)
            created_ids.append(self._entities[new_index].id)
        self._apply_operation_result(
            OperationResult(
                changed=True,
                message=f"{op.capitalize()}: {len(rings)} shape(s)",
                created_ids=tuple(created_ids),
                removed_ids=removed_ids,
                selected_ids=tuple(created_ids),
                metadata={"operation": op},
            )
        )
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
        total_length = 0.0
        total_area = 0.0
        for index in indices:
            points = self._entities[index].points
            total_length += sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            if self._is_poly_closed(points) and len(points) >= 4:
                total_area += (
                    abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))) / 2.0
                )
        info["length"] = total_length
        info["area"] = total_area
        if 2 <= len(indices) <= 100:
            geometries = []
            for index in indices:
                points = self._entities[index].points
                if len(points) < 2:
                    continue
                try:
                    geometry = (
                        Polygon(points)
                        if self._is_poly_closed(points) and len(points) >= 4
                        else LineString(points)
                    )
                    if not geometry.is_empty:
                        geometries.append(geometry)
                except (TypeError, ValueError, GEOSException):
                    continue
            if len(geometries) >= 2:
                info["clearance"] = min(
                    geometries[i].distance(geometries[j])
                    for i in range(len(geometries))
                    for j in range(i + 1, len(geometries))
                )
        if len(indices) == 1:
            e = self._entities[indices[0]]
            info["kind"] = e.kind
            info["meta"] = deepcopy(e.meta) if e.meta else {}
            info["index"] = indices[0]
            display_kind = e.kind
            display_meta = info["meta"]
            if e.kind == "polyline":
                from src.backend.recognition import recognize_polyline

                recognized = recognize_polyline(e.points)
                if recognized is not None:
                    display_kind = recognized.kind
                    display_meta = dict(recognized.metadata)
                    sides = int(display_meta.get("sides", 0) or 0)
                    if display_kind == "polygon" and sides == 3:
                        display_kind = "triangle"
            info["display_kind"] = display_kind
            rotation = display_meta.get("rotation")
            if rotation is None and len(e.points) >= 2:
                for first, second in zip(e.points, e.points[1:]):
                    dx, dy = second[0] - first[0], second[1] - first[1]
                    if math.hypot(dx, dy) > 1e-9:
                        rotation = math.degrees(math.atan2(dy, dx))
                        break
            info["rotation"] = float(rotation or 0.0) % 360.0
            if e.meta:
                if e.kind in {"rectangle", "rounded_rectangle"}:
                    info["w"] = float(e.meta.get("width", info["w"]))
                    info["h"] = float(e.meta.get("height", info["h"]))
                elif e.kind == "ellipse":
                    info["w"] = 2.0 * float(e.meta.get("rx", info["w"] / 2.0))
                    info["h"] = 2.0 * float(e.meta.get("ry", info["h"] / 2.0))
                elif e.kind == "circle":
                    diameter = 2.0 * float(e.meta.get("radius", info["w"] / 2.0))
                    info["w"] = info["h"] = diameter
                elif e.kind == "slot":
                    info["w"] = float(e.meta.get("length", info["w"]))
                    info["h"] = float(e.meta.get("width", info["h"]))
            if e.kind == "circle" and e.meta and e.meta.get("radius") is not None:
                info["diameter"] = 2.0 * float(e.meta["radius"])
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
        candidate = deepcopy(e)
        if not update_entity_parameter(candidate, key, value):
            return False
        self._push_undo()
        e.points = candidate.points
        e.kind = candidate.kind
        e.meta = candidate.meta
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
                self._entities[idx].points = [
                    (2 * cx - x, y) for x, y in self._entities[idx].points
                ]
            elif axis == "vertical":
                self._entities[idx].points = [
                    (x, 2 * cy - y) for x, y in self._entities[idx].points
                ]
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
            ent.points = [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in ent.points]
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
        enabled = self._shape_primitive_active() and self._draw_shape_anchor_w is not None
        self._draw_shape_w_edit.setEnabled(enabled)
        self._draw_shape_h_edit.setEnabled(enabled)
        if self._draw_shape_sides_spin is not None:
            self._draw_shape_sides_spin.setEnabled(enabled)
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
        """Public command/API wrapper for the canonical offset operation."""
        return self._offset_selected(distance)

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

    def _demote_selected_entities_to_polylines(self, indices: list[int] | None = None) -> None:
        if indices is None:
            indices = self._selected_indices()
        for idx in indices:
            if 0 <= idx < len(self._entities):
                self._entities[idx].kind = "polyline"
                self._entities[idx].meta = None

    # ── Second restoration pass: methods referenced as callbacks
    #    (menu actions) that the call-only audit missed. ──

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
        self._show_flash("Sent to Pattern", 900)

    def _use_selected_as_custom_tile(self) -> None:
        cb = getattr(self, "_use_selected_as_custom_tile_cb", None)
        if not callable(cb):
            return
        selected = self.get_selected()
        if not selected:
            self._show_flash("Select shape(s) first", 1000)
            return
        cb([[(x, y) for x, y in poly] for poly in selected])
        self._show_flash("Custom tile set", 900)

    def _show_geometry_preflight(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from src.backend.preflight import analyze_geometry

        polys = self.get_selected() or self.get_polylines_state()
        report = analyze_geometry(polys)
        minimum = "—" if report.minimum_segment is None else f"{report.minimum_segment:.4g} mm"
        QMessageBox.information(
            self,
            "Geometry Preflight",
            f"{report.summary()}\n\n"
            f"Analysis tolerance: {report.tolerance:.4g} mm\n"
            f"Minimum segment: {minimum}\n\n"
            "Open paths may be intentional engraving strokes. Invalid, duplicate, "
            "zero-length, and tiny geometry should be repaired before fabrication.",
        )

    def recognize_selected_shapes(self) -> int:
        """Convert conservative imported-polyline matches to parametric shapes."""
        from src.backend.recognition import recognize_polyline

        matches = []
        for index in self._mutable_selected_indices():
            entity = self._entities[index]
            if entity.kind != "polyline":
                continue
            recognized = recognize_polyline(entity.points)
            if recognized is not None:
                matches.append((entity, recognized))
        if not matches:
            self._show_flash("No unambiguous circles, rectangles, or regular polygons", 1600)
            return 0
        self._push_undo()
        for entity, recognized in matches:
            entity.kind = recognized.kind
            entity.meta = dict(recognized.metadata)
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"Recognized {len(matches)} shape(s)", 1000)
        return len(matches)

    def reverse_selected_paths(self) -> int:
        from src.backend.path_ops import reverse_path

        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        self._push_undo()
        for index in indices:
            entity = self._entities[index]
            entity.points = reverse_path(entity.points)
            if entity.kind == "line" and len(entity.points) == 2:
                entity.meta = {"start": entity.points[0], "end": entity.points[1]}
            elif entity.kind == "bezier" and entity.meta:
                old_in = list(entity.meta.get("handles_in", []))
                old_out = list(entity.meta.get("handles_out", []))
                if old_in or old_out:
                    entity.meta["handles_in"] = list(reversed(old_out))
                    entity.meta["handles_out"] = list(reversed(old_in))
                    entity.meta["node_types"] = list(reversed(entity.meta.get("node_types", [])))
                tangents = list(reversed(entity.meta.get("tangents", [])))
                entity.meta["tangents"] = [(-float(x), -float(y)) for x, y in tangents]
            elif entity.kind == "spline" and entity.meta:
                entity.meta["control_points"] = list(entity.points)
            elif entity.kind != "polyline":
                entity.kind = "polyline"
                entity.meta = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"Reversed {len(indices)} path(s)", 900)
        return len(indices)

    def set_selected_path_start(self) -> bool:
        from src.backend.path_ops import set_closed_start

        indices = self._mutable_selected_indices()
        if len(indices) != 1:
            self._show_flash("Select exactly one closed path", 1000)
            return False
        index = indices[0]
        points = self._entities[index].points
        if not self._is_poly_closed(points):
            self._show_flash("Path is open", 800)
            return False
        if self._hover_vert is not None and self._hover_vert[0] == index:
            vertex = self._hover_vert[1]
        elif self._cursor_wx is not None and self._cursor_wy is not None:
            vertex = min(
                range(len(points) - 1),
                key=lambda item: math.dist(points[item], (self._cursor_wx, self._cursor_wy)),
            )
        else:
            self._show_flash("Hover the desired start vertex", 1000)
            return False
        self._push_undo()
        self._entities[index].points = set_closed_start(points, vertex)
        self._entities[index].kind = "polyline"
        self._entities[index].meta = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash("Path start updated", 800)
        return True

    def resample_selected_paths(self, value: float, *, by_count: bool = False) -> int:
        from src.backend.path_ops import resample_by_count, resample_by_spacing

        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        replacements: dict[int, list[tuple[float, float]]] = {}
        for index in indices:
            try:
                replacements[index] = (
                    resample_by_count(self._entities[index].points, int(round(value)))
                    if by_count
                    else resample_by_spacing(self._entities[index].points, value)
                )
            except ValueError:
                continue
        if not replacements:
            self._show_flash("No selected path could be resampled", 1100)
            return 0
        self._push_undo()
        for index, points in replacements.items():
            self._entities[index].points = points
            self._entities[index].kind = "polyline"
            self._entities[index].meta = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"Resampled {len(replacements)} path(s)", 900)
        return len(replacements)

    def prompt_resample_spacing(self) -> None:
        self._show_hud_prompt(
            "Point spacing (mm)", 1.0, self.resample_selected_paths, minimum=0.001
        )

    def prompt_resample_count(self) -> None:
        self._show_hud_prompt(
            "Point count",
            32.0,
            lambda value: self.resample_selected_paths(value, by_count=True),
            minimum=2.0,
            is_length=False,
        )

    def fit_selected_to_primitive(self, primitive: str) -> int:
        from src.backend.path_ops import fit_circle, fit_line

        indices = self._mutable_selected_indices()
        replacements: dict[int, tuple[list[tuple[float, float]], str, dict[str, Any]]] = {}
        for index in indices:
            points = self._entities[index].points
            if primitive == "line":
                result = fit_line(points)
                if result is not None:
                    replacements[index] = (
                        list(result),
                        "line",
                        {"start": result[0], "end": result[1]},
                    )
            elif primitive in {"circle", "arc"}:
                result = fit_circle(points)
                if result is None:
                    continue
                center, radius = result
                if primitive == "circle":
                    shape = ShapeFactory.circle(center, radius)
                    replacements[index] = (
                        list(shape.points),
                        "circle",
                        {"center": center, "radius": radius},
                    )
                elif len(points) >= 2:
                    start = (
                        math.degrees(math.atan2(points[0][1] - center[1], points[0][0] - center[0]))
                        % 360
                    )
                    end = (
                        math.degrees(
                            math.atan2(points[-1][1] - center[1], points[-1][0] - center[0])
                        )
                        % 360
                    )
                    middle = (
                        math.degrees(
                            math.atan2(
                                points[len(points) // 2][1] - center[1],
                                points[len(points) // 2][0] - center[0],
                            )
                        )
                        % 360
                    )
                    if (middle - start) % 360 > (end - start) % 360:
                        start, end = end, start
                    shape = ShapeFactory.arc(center, radius, start, end, segments=48)
                    replacements[index] = (
                        list(shape.points),
                        "arc",
                        {
                            "center": center,
                            "radius": radius,
                            "start_angle": start,
                            "end_angle": end,
                        },
                    )
        if not replacements:
            self._show_flash(f"Could not fit selection to {primitive}", 1100)
            return 0
        self._push_undo()
        for index, (points, kind, metadata) in replacements.items():
            entity = self._entities[index]
            entity.points, entity.kind, entity.meta = points, kind, metadata
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"Fitted {len(replacements)} path(s) to {primitive}", 1000)
        return len(replacements)

    def create_procedural_primitive(self, primitive: str) -> int:
        """Create an advanced primitive at the cursor using conservative defaults."""
        from src.backend.primitives import (
            chamfered_star,
            dovetail_box,
            finger_joint_box,
            gear,
            keyhole,
            ring,
            rounded_star,
            spiral,
            superellipse,
            tabbed_panel,
            teardrop,
        )

        center = (
            (self._cursor_wx, self._cursor_wy)
            if self._cursor_wx is not None and self._cursor_wy is not None
            else (0.0, 0.0)
        )
        generators = {
            "gear": lambda: [gear()],
            "spiral": lambda: [spiral()],
            "superellipse": lambda: [superellipse()],
            "teardrop": lambda: [teardrop()],
            "keyhole": lambda: [keyhole()],
            "ring": lambda: list(ring()),
            "rounded_star": lambda: [rounded_star()],
            "chamfered_star": lambda: [chamfered_star()],
            "finger_joint_box": lambda: [finger_joint_box()],
            "dovetail_box": lambda: [dovetail_box()],
            "tabbed_panel": lambda: [tabbed_panel()],
        }
        generator = generators.get(primitive)
        if generator is None:
            return 0
        try:
            paths = generator()
        except ValueError as exc:
            self._show_flash(str(exc), 1200)
            return 0
        records = [
            (
                [(point[0] + center[0], point[1] + center[1]) for point in path],
                primitive,
                {"generator": primitive, "center": center},
            )
            for path in paths
            if len(path) >= 2
        ]
        if not records:
            return 0
        self._push_undo()
        created = set()
        for points, kind, metadata in records:
            created.add(self._append_entity(points, kind=kind, meta=metadata))
        if len(created) > 1:
            group = self._next_group_id
            self._next_group_id += 1
            for index in created:
                self._entities[index].group = group
            self._group_labels[group] = "Ring"
        self._sel = created
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash(f"{primitive.replace('_', ' ').title()} created", 900)
        return len(created)

    def create_polygon_from_selected_edge(self, sides: float = 6.0) -> int:
        from src.backend.primitives import regular_polygon_from_edge

        indices = [
            index
            for index in self._mutable_selected_indices()
            if len(self._entities[index].points) == 2
        ]
        if len(indices) != 1:
            self._show_flash("Select exactly one edge", 900)
            return 0
        start, end = self._entities[indices[0]].points
        points = regular_polygon_from_edge(start, end, int(round(sides)))
        vertices = points[:-1]
        center = (
            sum(point[0] for point in vertices) / len(vertices),
            sum(point[1] for point in vertices) / len(vertices),
        )
        radius = math.dist(center, vertices[0])
        rotation = (
            math.degrees(math.atan2(vertices[0][1] - center[1], vertices[0][0] - center[0])) + 90.0
        )
        self._push_undo()
        index = self._append_entity(
            points,
            kind="polygon",
            meta={
                "source": "edge",
                "center": center,
                "radius": radius,
                "rotation": rotation,
                "sides": int(round(sides)),
            },
        )
        self._sel = {index}
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return 1

    def prompt_polygon_from_edge(self) -> None:
        self._show_hud_prompt(
            "Polygon sides",
            6.0,
            self.create_polygon_from_selected_edge,
            minimum=3.0,
            is_length=False,
        )

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
                    and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6
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

    def merge_selected_segments_to_objects(self, *, record_undo: bool = True) -> int:
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
            if len(pts) >= 3 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6:
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

        if record_undo:
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
            merged_polys.append(
                (
                    self._normalize_merged_chain(chain),
                    chain_construction,
                )
            )

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
                    and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
                )
                unique_count = len(poly) - 1 if is_closed else len(poly)
                if unique_count > 3:
                    menu.addAction("Delete vertex", lambda: self._delete_vertex(pi, vi))
                if (is_closed and unique_count >= 3) or (not is_closed and 0 < vi < len(poly) - 1):
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
            len(poly) >= 4 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
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
        # Corner surgery changes topology and can no longer be represented by
        # the source rectangle/polygon's old parametric metadata.
        self._entities[pi].kind = "polyline"
        self._entities[pi].meta = None
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
        self._entities[pi].kind = "polyline"
        self._entities[pi].meta = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True
