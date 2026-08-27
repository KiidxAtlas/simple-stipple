"""Configuration and initialization helpers for CanvasView."""

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from simple_stipple.canvas.hit_testing import HitTestService
from simple_stipple.canvas.objects import (
    CanvasModel,
    CanvasModelPort,
    CanvasService,
)
from simple_stipple.canvas.operations.clipboard import ClipboardService
from simple_stipple.canvas.operations.construction import ConstructionService
from simple_stipple.canvas.operations.draw_ops import DrawOpsService
from simple_stipple.canvas.operations.editing import EditingService
from simple_stipple.canvas.operations.gizmo import GizmoService
from simple_stipple.canvas.operations.hud_text import HudTextService
from simple_stipple.canvas.operations.select import SelectionService
from simple_stipple.canvas.operations.smoothing import SmoothingService
from simple_stipple.canvas.operations.text import TextService
from simple_stipple.canvas.renderer import CanvasRenderer
from simple_stipple.canvas.snap import SnapEngine
from simple_stipple.canvas.tools import tools as canvas_tools
from simple_stipple.canvas.tools.dimension_tool import DimensionTool as SketchDimensionTool
from simple_stipple.canvas.tools.selection import EditTool, SelectTool
from simple_stipple.core.document.model import OperationResult
from simple_stipple.core.document.organization import GroupingService, LayerService
from simple_stipple.platform.settings import (
    DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS,
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
from simple_stipple.ui.components.focus import CanvasEscapeRouter
from simple_stipple.ui.components.units import DEFAULT_UNIT_SYSTEM


def _initialize_view(
    self,
    parent: QWidget | None = None,
    selectable: bool = True,
    on_change=None,
    on_mode_change=None,
    on_poly_change=None,
) -> None:
    """Initialize CanvasView instance attributes and services.

    Extracted from __init__ to reduce view.py line count. Called at the
    end of CanvasView.__init__ after super().__init__().
    """
    self._model = CanvasModel(parent=self)
    self._canvas_service = CanvasService(cast(CanvasModelPort, self._model))
    self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    # Route Escape to an active canvas tool even when a numeric HUD or
    # properties-panel field has focus. The router ignores modal dialogs.
    self._escape_router = CanvasEscapeRouter(self)
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(self._escape_router)

    self._selectable = selectable
    self._empty_message = "No polylines loaded"
    self._show_selection_bbox = False
    self._selection_follows_geometry = False
    self._selection_drag_edits = True
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
    self._snap_shapes_cache = None

    # Cached entity lookup by ID. The view's document service replaces the
    # entity list for committed edits, while bulk preview loads append to it
    # directly, so the accessor keys the cache by list identity and length.
    self._CanvasView__entities_by_id = {}
    self._CanvasView__entities_by_id_key = None

    # construction/hidden/locked/group flags live on EntityRecord.
    self._accent_polys = {}  # entity_id → color hex for role overlays
    self._region_tint = cast(dict[str, str], {})  # entity_id → color hex, filled translucently
    self._region_picking = False  # click inside a closed shape selects it
    # Solved pattern/fill, rendered beneath the editable outlines. This is a
    # render channel, not an entity set — the canvas never stops holding the
    # real outlines, so editing is always editing the document.
    # Preflight findings, drawn where they are rather than summarised in a
    # collapsed panel. Set via ``set_issue_markers``.
    self._issue_markers = cast(tuple[Any, ...], ())
    self._result_polys = []
    self._result_visible = True
    self._result_pattern_span = (0, 0)  # slice of _result_polys that is cells
    # Optional owner-supplied undo/redo that runs before the canvas history.
    self._undo_hook = None
    self._redo_hook = None
    # Generated preview strokes may be rendered faithfully without becoming
    # selectable editor geometry (for example, dense pattern-fill hatch rows).
    self._render_only_entity_ids = set()
    self._draw_construction_mode = False
    self._draw_split_enabled = True

    # Ghost polylines: a non-interactive secondary set rendered beneath the
    # main polys (faded, dashed). Used for showing context layers — e.g.
    # the source outline beneath a generated pattern preview — without
    # putting them into the editable poly list.
    self._ghost_polys = []
    self._ghost_visible = True
    # Dense preview fills are visually identical when their strokes are
    # submitted to Qt in batches instead of one draw call per segment.
    self._dense_preview_render = False
    self._context_menu_transform_items = []

    self._scale = 1.0
    self._ox = 0.0
    self._oy = 0.0

    # LMB interaction state
    self._lmb_press = None
    self._lmb_prev = None
    self._lmb_target = None

    # MMB pan state
    self._mmb_prev = None
    self._space_pan_active = False
    self._space_pan_dragging = False

    # Cursor world position
    self._cursor_wx = None
    self._cursor_wy = None

    # Rubber-band select
    self._shift_drag = False
    self._band_start = None
    self._band_additive = False
    self._lasso_select_enabled = False
    self._lasso_active = False
    self._lasso_points = []
    self._lasso_additive = False
    self._context_menu_sections = set(DEFAULT_CONTEXT_MENU_SECTIONS)
    self._context_menu_section_order = list(DEFAULT_CONTEXT_MENU_SECTIONS)
    self._context_menu_overflow_sections = set(DEFAULT_CONTEXT_MENU_OVERFLOW_SECTIONS)
    # New profiles store leaf actions individually. This flag distinguishes
    # an intentionally empty action list from an unsaved legacy profile.
    self._context_menu_item_order = []
    self._context_menu_overflow_items = set()
    self._context_menu_actions_configured = False
    self._context_menu_profile = "draft"
    # Named reusable geometry snippets. Definitions live in view state so
    # a workspace carries its own small symbol library without introducing
    # a second document format or global asset database.
    self._symbol_library = {}

    # Knife tool: a transient screen gesture whose world-space line is
    # fed through the same robust splitter used by draw-time splitting.
    self._knife_start_w = None
    self._knife_end_w = None
    self._last_split_result_ids = set()
    self._last_operation_result = OperationResult.unchanged("")
    self._last_repeat_action = None
    self._operation_preview_polys = []

    # Undo / redo history (delta-based; see src/simple_stipple/core/document/history.py)

    # Unified snap engine (src/simple_stipple/canvas/snap.py) and guide lines
    # (("h", y_world) or ("v", x_world)); guides participate in snapping.
    self._snap_engine = SnapEngine(self)
    # _guides / _dimensions are document-backed properties (defined above);
    # the document already starts with empty lists, so no init assignment.
    self._guide_drag = None
    self._guide_drag_moved = False
    # Snapshot captured at the start of a guide gesture (drag out a new
    # guide, move one, or drag one back onto a ruler to delete it); the
    # whole gesture commits as one undoable command on release.
    self._guide_preview = None
    self._selected_guide = None
    # mm rulers along the top/left edges; drag out of a ruler to create
    # a guide, drop a guide back onto a ruler to delete it.
    self._rulers_visible = False
    # Display-only unit — all internal storage/geometry stays mm.
    self._unit_system = DEFAULT_UNIT_SYSTEM
    # Algorithm smooth_selected() runs: "chaikin" | "gaussian" | "catmull_rom".
    self._smoothing_method = DEFAULT_SMOOTHING_METHOD
    # Seed values for the Smooth/Simplify HUD prompts — remembers the
    # last value typed so the user doesn't retype it every time.
    self._smooth_iterations = DEFAULT_SMOOTH_ITERATIONS
    self._simplify_tolerance = DEFAULT_SIMPLIFY_TOLERANCE
    self._smoothing_service = SmoothingService(cast(Any, self))
    self._grouping_service = GroupingService(cast(Any, self))
    self._layer_service = LayerService(self)
    self._clipboard_service = ClipboardService(self)
    self._hit_test = HitTestService(self)
    self._gizmo_service = GizmoService(self)
    self._hud_service = HudTextService(self)
    self._text_service = TextService(self)
    self._draw_ops = DrawOpsService(self)
    self._construction_service = ConstructionService(self)
    self._selection_service = SelectionService(self)
    self._renderer = CanvasRenderer(self)
    # The renderer retains dense preview geometry in world coordinates. The
    # reactive model is the canonical invalidation boundary for document and
    # selection changes, so the cache can never outlive rendered state.
    self._model.geometry_changed.connect(self._renderer.invalidate_dense_preview_cache)
    self._model.selection_changed.connect(self._renderer.invalidate_dense_preview_cache)
    self._editing = EditingService(self)

    # Fit scale for zoom-% display
    self._fit_scale = 1.0
    self._view_back = []
    self._view_forward = []
    self._last_view_record_time = 0.0
    self._restoring_view = False

    # Scale-by-reference tool (legacy internal "measure" identifiers are
    # retained for workspace and shortcut compatibility).
    self._measure_mode = False
    self._measure_anchor = None
    self._measure_hover = None
    self._measure_locked = False
    self._measure_end = None
    self._measure_snapped_a = False
    self._measure_snapped_b = False
    self._measure_edit = None

    # Persistent dimension/annotation tool. Dimensions are saved in view
    # state and emitted as real DXF dimension entities during export.
    self._dimension_mode = False
    self._dimension_kind = "linear"
    # _dimensions is a document-backed property (see above); starts empty.
    self._dim_pending_p1 = None
    self._dim_pending_p2 = None
    self._dim_pending_offset = 5.0
    self._dim_selected_segments = []
    self._dim_hover_segment = None
    self._selected_dimension = None
    self._all_dimensions_selected = False
    self._dimension_drag = None
    self._dimension_drag_preview = None

    # Mode: "select" | "draw" | "edit"
    self._mode = "select"

    # Interaction tools (src/simple_stipple/canvas/tools/tools.py): per-mode strategy
    # objects dispatched by the mouse event handlers. All interaction
    # state stays on the view; tools are stateless.
    trim_tool = canvas_tools.TrimExtendTool(self)
    self._tools = {
        "select": SelectTool(self),
        "draw": canvas_tools.DrawTool(self),
        "edit": EditTool(self),
        "trim": trim_tool,
        "extend": trim_tool,
        "knife": canvas_tools.KnifeTool(self),
    }
    self._measure_tool = canvas_tools.ScaleTool(self)
    self._dimension_tool = SketchDimensionTool(self)

    # Draw mode state
    self._draw_pts = []
    self._draw_point_snap_types = []
    self._draw_primitive = "polyline"  # polyline|line|arc|rectangle|circle|ellipse|polygon|slot
    self._draw_shape_preview_active = False
    self._draw_shape_anchor_w = None
    self._draw_shape_cursor_w = None
    # Side count used the next time a polygon is drawn; adjustable via
    # a HUD prompt when the Shapes picker lands on "polygon".
    self._draw_polygon_sides = 6
    self._draw_star_points = 5
    self._draw_arc_pts = []
    self._draw_arc_mode = "3point"
    self._draw_constraint_lock = None

    # Pen tool (bezier curves): plain click = corner anchor; click-drag
    # = smooth anchor with a symmetric tangent handle sized by the drag.
    self._pen_pts = []
    self._pen_tangents = []
    self._pen_dragging = False
    self._pen_press_screen = None

    # Edit mode state
    self._edit_poly = None
    self._edit_vert = None
    self._edit_dragging = False
    self._edit_linked_verts = set()
    self._edit_selected_verts = set()
    self._edit_drag_targets = set()
    self._edit_drag_anchor = None
    self._edit_drag_moved = False
    self._edit_undo_pushed = False
    self._edit_command_snapshot = None
    self._hover_vert = None
    self._hover_bezier_handle = None
    self._bezier_handle_drag = None
    self._bezier_handle_drag_moved = False
    self._bezier_handle_undo_pushed = False
    self._bezier_command_snapshot = None
    # Select-mode hover pre-highlight: which polyline a click would pick
    self._hover_poly = None
    # Last displayed cursor position (rounded), to skip redundant repaints
    self._prev_cursor_display = None

    # Move state (select mode drag-to-move). Object snapping works on
    # absolute deltas from the drag anchor: the selection's own vertices
    # (sampled at drag start) snap against static vertices/edges/grid/
    # guides regardless of where the user grabbed the shape.
    self._move_dragging = False
    self._move_origin = None
    self._move_undo_pushed = False
    self._move_command_snapshot = None
    self._move_anchor_w = None
    self._move_applied_w = (0.0, 0.0)
    self._move_start_pts = []
    self._move_snap_exclude_vertices = set()
    self._move_snap_exclude_segments = set()

    # Clipboard is process-wide (see _clipboard property below) — do not
    # reset it here, or opening a new window/tab mid-session would wipe
    # whatever the user just copied elsewhere.

    # Image bounds reference rectangle
    self._img_bounds = None

    # Background image overlay
    self._bg_pil = None
    self._bg_w_mm = 0.0
    self._bg_h_mm = 0.0
    self._bg_pixmap = None
    self._bg_cached_scale = 0.0
    self._bg_x_mm = 0.0
    self._bg_y_mm = 0.0
    self._bg_rotation_deg = 0.0
    self._bg_editable = False
    self._bg_selected = False
    self._bg_edit_callback = None
    self._bg_key_callback = None
    self._bg_drag = None

    # Scale / Dimension button rects
    self._mbtn_rect = (0, 0, 0, 0)
    self._dbtn_rect = (0, 0, 0, 0)
    self._adbtn_rect = (0, 0, 0, 0)

    # Draw mode snap (world-space snap point under cursor)
    self._draw_snap = None
    self._draw_snap_type = None
    # Cross-mode hover snap indicator (select/edit/move/measure)
    self._hover_snap = None
    self._hover_snap_type = None
    # Independent X/Y axis snap indicators for whole-shape drag (up to
    # two entries — lets one axis align to a different feature than the
    # other, e.g. left edge to shape A while top edge aligns to shape B).
    # Each entry is (target_point, kind, dragged_point) so the renderer
    # can draw a dashed guide line connecting the two — without it, a
    # match that's only aligned on one axis can appear at a point that's
    # visually far from the shape, looking like a snapping glitch.
    self._hover_snap_multi = []
    # Scale pre-anchor hover snap point
    self._measure_hover_pre = None

    # Precision aids
    self._grid_visible = False
    self._grid_snap = False
    self._grid_spacing = 5.0
    self._geometry_health_visible = False
    self._curvature_visible = False
    self._constraints = []
    # The two most recently selected edge references. Constraint commands use
    # these so a user can constrain edges of a polyline, not only standalone
    # two-point line entities.
    self._constraint_segment_refs = []

    # Independent snap-category toggles (all default on, matching prior
    # unconditional behavior). Master is a hard kill-switch for every
    # snap source; vertex/edge gate the two SnapEngine candidate
    # families; angle gates the Shift-held 45-degree snap.
    self._snap_master_enabled = True
    self._snap_vertex_enabled = True
    self._snap_midpoint_enabled = True
    self._snap_intersection_enabled = True
    self._snap_edge_enabled = True
    self._snap_tangent_enabled = True
    self._snap_extension_enabled = True
    self._snap_angle_enabled = True
    self._snap_parallel_enabled = True
    self._snap_perpendicular_enabled = True
    self._snap_equal_length_enabled = True
    self._snap_axis_alignment_enabled = True
    self._snap_align_x_enabled = True
    self._snap_align_y_enabled = True
    # Magnetic capture radius multiplier (0%–200%), controlled from the
    # Snap dropdown. It changes snapping tolerance, never stored geometry.
    self._snap_strength = 0.5
    self._rotation_snap_increment = 15.0

    # Construction / reference lines: list of ("h", y_world) or ("v", x_world)

    # Auto-dimension HUD inputs (Fusion 360 style)
    self._dim_distance_edit = None
    self._dim_angle_edit = None
    self._dim_distance_label = None
    self._dim_angle_label = None
    self._dim_distance_dirty = False
    self._dim_angle_dirty = False

    # Selection dimension badge hit rects (for inline editing)
    self._sel_badge_w_rect = None
    self._sel_badge_h_rect = None
    # Single-line selection: length / angle badge hit rects
    self._sel_badge_l_rect = None
    self._sel_badge_a_rect = None
    # Inline single-dimension editor
    self._sel_dim_edit = None
    self._sel_dim_axis = None  # "w" or "h"

    # Transform gizmo (select mode)
    self._gizmo_scale_rect = None
    self._gizmo_rotate_rect = None
    self._gizmo_move_rect = None
    # 8-handle selection frame: [(handle name, hit rect), ...] where the
    # name is a compass direction ("nw", "n", …) in world orientation.
    self._gizmo_handle_rects = []
    self._gizmo_anchor_w = None
    self._gizmo_handle_w = None
    self._gizmo_drag_mode = None  # "scale" | "rotate"
    self._gizmo_center_w = None
    self._gizmo_start_vec = None
    self._gizmo_snapshot = {}
    # Parallel snapshot of each entity's meta ("center" etc.) at drag
    # start — needed so scale/rotate can recompute e.g. a circle's true
    # center from its ORIGINAL (pre-drag) value every mouse-move event,
    # instead of compounding the transform onto an already-updated value.
    self._gizmo_meta_snapshot = {}
    # Parallel snapshot of each entity's kind at drag start — a non-uniform
    # scale can change kind mid-drag (arc -> elliptical_arc), so later
    # mouse-move events must keep reconstructing from the drag-start kind,
    # not the live one, or it no longer matches _gizmo_meta_snapshot.
    self._gizmo_kind_snapshot = {}
    self._gizmo_local_shape = None
    self._gizmo_drag_moved = False
    self._gizmo_undo_pushed = False
    self._gizmo_command_snapshot = None
    # Persistent aspect-ratio lock (properties panel toggle) — unlike
    # the existing Shift-to-constrain gizmo behavior, this stays on
    # across both gizmo drags and typed width/height edits until
    # explicitly turned off.
    self._aspect_ratio_locked = False
    self._property_highlight = None

    # Auto-constraint detection (H/V)
    self._draw_constraint = None

    # Flash indicator for transient messages
    self._flash_text = None
    self._flash_timer = None

    # Angle snap active flag (for ortho display)
    self._angle_snap_active = False

    # Draw-mode slide-in sidebar
    self._draw_sidebar = None
    self._draw_sidebar_anim = None
    self._draw_sidebar_visible = False
    self._draw_shape_w_edit = None
    self._draw_shape_h_edit = None
    self._draw_shape_sides_spin = None
    self._draw_sidebar_width = DEFAULT_DRAW_SIDEBAR_WIDTH
    self._draw_sidebar_height = None  # None => auto-fit available space
    self._draw_sidebar_sections = list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
    self._draw_sidebar_path_tools = list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
    self._draw_sidebar_shape_tools = list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
    self._draw_sidebar_always_visible = DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE

    self._needs_fit = True
    self.setMouseTracking(True)
    self._build_draw_sidebar()
