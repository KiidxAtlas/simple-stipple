"""PolylineView — interactive pan/zoom QGraphicsView with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math

from PIL import Image as PILImage
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QLineEdit, QMenu, QWidget

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import split as shapely_split

from src.constants import DIM, DRAG_THRESH, POLY, SEL, Q_BG

# Edit-mode visual constants
_HANDLE = QColor("#4a9eff")  # vertex handle — matches poly accent
_HANDLE_HOVER = QColor("#00c8aa")  # hover — teal
_HANDLE_ACTIVE = QColor("#f5a623")  # active drag — amber
_SNAP_CLOSE = QColor("#00c8aa")  # snap ring — teal
_DRAW_COLOR = QColor("#f5a623")  # draw mode in-progress — amber
_MEASURE_COLOR = QColor("#22d3ee")  # measure — cyan
_HANDLE_R = 4
_SNAP_DIST = 14
_CLOSE_SNAP_DIST = 14  # same as snap so visual indicator matches click behavior
_VERT_HIT = 8
_EDGE_HIT = 6
_DRAW_VERT_R = 5  # vertex dot radius in draw mode
_DRAW_LINE_W = 2.0  # placed segment line width
_RUBBER_W = 1.5  # rubber-band line width
_MIN_SCALE = 1e-6
_GRID_MINOR = QColor("#1a2432")
_GRID_MAJOR = QColor("#243244")
_GRID_AXIS = QColor("#31516e")
_CONSTRUCTION_COLOR = QColor("#9933cc")
_ORTHO_COLOR = QColor("#334466")
_BADGE_BG = QColor(20, 24, 36, 200)
_BADGE_TEXT = QColor("#ffffff")
_BADGE_DIM = QColor("#aabbcc")


def _pil_to_qpixmap(pil_img: PILImage.Image) -> QPixmap:
    """Convert a PIL Image to QPixmap."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class PolylineView(QGraphicsView):
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
        self._on_change = on_change
        self._on_mode_change = on_mode_change
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        self._polys: list[list[tuple[float, float]]] = []
        self._sel: set[int] = set()

        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0

        # LMB interaction state
        self._lmb_press: QPointF | None = None
        self._lmb_prev: QPointF | None = None
        self._lmb_target: int | None = None

        # MMB pan state
        self._mmb_prev: QPointF | None = None

        # Cursor world position
        self._cursor_wx: float | None = None
        self._cursor_wy: float | None = None

        # Rubber-band select
        self._shift_drag: bool = False
        self._band_start: QPointF | None = None

        # Undo / redo stacks
        self._undo_stack: list[list] = []
        self._redo_stack: list[list] = []

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

        # Edit mode state
        self._edit_poly: int | None = None
        self._edit_vert: int | None = None
        self._edit_dragging: bool = False
        self._hover_vert: tuple[int, int] | None = None

        # Move state (select mode drag-to-move)
        self._move_dragging: bool = False
        self._move_origin: tuple[float, float] | None = None
        self._move_undo_pushed: bool = False

        # Clipboard
        self._clipboard: list[list[tuple[float, float]]] = []

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
        # Measure pre-anchor hover snap point
        self._measure_hover_pre: tuple[float, float] | None = None

        # Precision aids
        self._grid_visible: bool = False
        self._grid_snap: bool = False
        self._grid_spacing: float = 1.0

        # Construction / reference lines: list of ("h", y_world) or ("v", x_world)
        self._construction_lines: list[tuple[str, float]] = []

        # Auto-dimension HUD inputs (Fusion 360 style)
        self._dim_distance_edit: QLineEdit | None = None
        self._dim_angle_edit: QLineEdit | None = None
        self._dim_distance_dirty: bool = False
        self._dim_angle_dirty: bool = False

        # Auto-constraint detection (H/V)
        self._draw_constraint: str | None = None

        # Flash indicator for transient messages
        self._flash_text: str | None = None
        self._flash_timer: QTimer | None = None

        # Angle snap active flag (for ortho display)
        self._angle_snap_active: bool = False

        self._needs_fit = True
        self.setMouseTracking(True)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, polys: list[list[tuple[float, float]]]) -> None:
        self._polys = self._clone_polys(polys)
        self._sel.clear()
        self._needs_fit = True
        self._fit()
        self._notify()

    def reload(self, polys: list[list[tuple[float, float]]]) -> None:
        self._polys = self._clone_polys(polys)
        self._sel &= set(range(len(self._polys)))
        self._redraw()
        self._notify()

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return self._clone_polys(self._polys)

    def set_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        self._polys = self._clone_polys(polys)
        self._sel.clear()
        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()

    def get_view_state(self) -> dict[str, float | str]:
        return {
            "scale": self._scale,
            "ox": self._ox,
            "oy": self._oy,
            "fit_scale": self._fit_scale,
            "mode": self._mode,
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
        }

    def set_view_state(self, state: dict[str, float | str]) -> None:
        self._scale = max(_MIN_SCALE, float(state.get("scale", self._scale)))
        self._ox = float(state.get("ox", self._ox))
        self._oy = float(state.get("oy", self._oy))
        self._fit_scale = max(
            _MIN_SCALE, float(state.get("fit_scale", self._fit_scale))
        )
        mode = str(state.get("mode", self._mode))
        if mode in ("select", "draw", "edit"):
            self.set_mode(mode)
        self._grid_visible = bool(state.get("grid_visible", self._grid_visible))
        self._grid_snap = bool(state.get("grid_snap", self._grid_snap))
        self._grid_spacing = max(
            0.001, float(state.get("grid_spacing", self._grid_spacing))
        )
        self._redraw()

    def get_active(self) -> list[list[tuple[float, float]]]:
        return [p for i, p in enumerate(self._polys) if i not in self._sel]

    def get_selected(self) -> list[list[tuple[float, float]]]:
        return [p for i, p in enumerate(self._polys) if i in self._sel]

    def get_selection_indices(self) -> list[int]:
        return self._selected_indices()

    def set_selection(self, indices: list[int]) -> None:
        self._sel = {idx for idx in indices if 0 <= idx < len(self._polys)}
        self._redraw()
        self._notify()

    def get_status_summary(self) -> dict[str, object]:
        precision = []
        if self._grid_visible:
            precision.append(f"Grid {self._grid_spacing:g}mm")
        if self._grid_snap:
            precision.append("Snap")
        if self._measure_mode:
            precision.append("Measure")
        return {
            "mode": self._mode,
            "selected_count": len(self._sel),
            "object_count": len(self._polys),
            "precision": " · ".join(precision) if precision else "Free move",
        }

    def delete_selected(self) -> int:
        n = len(self._sel)
        if n:
            self._push_undo()
        self._polys = [p for i, p in enumerate(self._polys) if i not in self._sel]
        self._sel.clear()
        self._redraw()
        self._notify()
        if n:
            self._fire_poly_change()
        return n

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append([list(p) for p in self._polys])
        if len(self._redo_stack) > 30:
            self._redo_stack.pop(0)
        self._polys = self._undo_stack.pop()
        self._sel.clear()
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._hover_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append([list(p) for p in self._polys])
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)
        self._polys = self._redo_stack.pop()
        self._sel.clear()
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._hover_vert = None
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def undo_delete(self) -> bool:
        return self.undo()

    def invert_selection(self) -> None:
        self._sel = set(range(len(self._polys))) - self._sel
        self._redraw()
        self._notify()

    def select_all(self) -> None:
        self._sel = set(range(len(self._polys)))
        self._redraw()
        self._notify()

    def deselect_all(self) -> None:
        self._sel.clear()
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
            self._draw_snap = None
            self._draw_snap_type = None
            self._angle_snap_active = False
            self._draw_constraint = None
            self._dismiss_dim_inputs()
        elif self._mode == "edit":
            self._edit_poly = None
            self._edit_vert = None
            self._edit_dragging = False
            self._hover_vert = None
        self._mode = mode
        if mode in ("draw", "edit"):
            self._measure_mode = False
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

    def paste_clipboard(self) -> bool:
        if not self._clipboard:
            return False
        self._paste_clipboard()
        return True

    def duplicate_selected(self) -> bool:
        if not self._sel:
            return False
        self._duplicate_selected()
        return True

    def cut_selected(self) -> bool:
        if not self._sel:
            return False
        self._cut_selected()
        return True

    def rotate_selected(self, angle_deg: float) -> bool:
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        angle = math.radians(angle_deg)
        ca, sa = math.cos(angle), math.sin(angle)
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [
                (
                    cx + (x - cx) * ca - (y - cy) * sa,
                    cy + (x - cx) * sa + (y - cy) * ca,
                )
                for x, y in self._polys[idx]
            ]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def scale_selected(self, factor: float) -> bool:
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or factor <= 0:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [
                (cx + (x - cx) * factor, cy + (y - cy) * factor)
                for x, y in self._polys[idx]
            ]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def mirror_selected(self, axis: str) -> bool:
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._push_undo()
        for idx in indices:
            if axis == "horizontal":
                self._polys[idx] = [(2 * cx - x, y) for x, y in self._polys[idx]]
            elif axis == "vertical":
                self._polys[idx] = [(x, 2 * cy - y) for x, y in self._polys[idx]]
            else:
                return False
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def align_selected(self, mode: str) -> bool:
        indices = self._selected_indices()
        bounds = self._selection_bounds(indices)
        if len(indices) < 2 or bounds is None:
            return False
        bx0, by0, bx1, by1 = bounds
        center_x = (bx0 + bx1) / 2.0
        center_y = (by0 + by1) / 2.0
        self._push_undo()
        for idx in indices:
            px0, py0, px1, py1 = self._poly_bounds(self._polys[idx])
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
            self._polys[idx] = [(x + dx, y + dy) for x, y in self._polys[idx]]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

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
        if self._measure_mode or self._mode in ("draw", "edit"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    def _push_undo(self) -> None:
        self._undo_stack.append([list(p) for p in self._polys])
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

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
    def _poly_bounds(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
        if not poly:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = zip(*poly)
        return min(xs), min(ys), max(xs), max(ys)

    def _selected_indices(self) -> list[int]:
        return [idx for idx in sorted(self._sel) if idx < len(self._polys)]

    def _selection_bounds(
        self, indices: list[int] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = indices if indices is not None else self._selected_indices()
        pts = [pt for idx in items for pt in self._polys[idx]]
        if not pts:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

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

    def _snap_to_grid(self, wx: float, wy: float) -> tuple[float, float]:
        spacing = max(self._grid_spacing, 0.001)
        return (round(wx / spacing) * spacing, round(wy / spacing) * spacing)

    def _resolve_snap(
        self,
        cx: float,
        cy: float,
        wx: float,
        wy: float,
        *,
        allow_polyline: bool = True,
        allow_grid: bool = True,
    ) -> tuple[float, float, str] | None:
        candidates: list[tuple[float, tuple[float, float, str]]] = []
        if allow_polyline:
            poly_snap = self._snap_to_polyline(cx, cy)
            if poly_snap is not None:
                sx, sy = self._w2c(poly_snap[0], poly_snap[1])
                candidates.append((math.hypot(cx - sx, cy - sy), poly_snap))
        if allow_grid and self._grid_snap:
            grid_snap = self._snap_to_grid(wx, wy)
            sx, sy = self._w2c(*grid_snap)
            candidates.append((math.hypot(cx - sx, cy - sy), (grid_snap[0], grid_snap[1], "grid")))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _escape_cb(self) -> None:
        if self._mode == "draw":
            if len(self._draw_pts) >= 2:
                # Commit the placed points as an open polyline, stay in draw mode
                self._finish_draw(close=False)
            elif self._draw_pts:
                # Only 1 point placed — discard it
                self._draw_pts.clear()
                self._draw_constraint = None
                self._dismiss_dim_inputs()
                self._redraw()
            else:
                self.set_mode("select")
        elif self._mode == "edit":
            if self._edit_dragging:
                self._edit_dragging = False
                self._redraw()
            else:
                self.set_mode("select")
        elif self._measure_mode:
            self.toggle_measure()
        else:
            self.deselect_all()

    def _key_delete(self) -> None:
        if self._mode == "select":
            self.delete_selected()

    def _key_backspace(self) -> None:
        if self._mode == "draw" and self._draw_pts:
            self._draw_pts.pop()
            if not self._draw_pts:
                self._dismiss_dim_inputs()
                self._draw_constraint = None
            self._redraw()
        elif self._mode == "select":
            self.delete_selected()

    def _finish_draw(self, *, close: bool = False) -> None:
        if self._mode != "draw" or len(self._draw_pts) < 2:
            return
        self._push_undo()
        if close and self._draw_pts[0] != self._draw_pts[-1]:
            self._draw_pts.append(self._draw_pts[0])
        drawn = list(self._draw_pts)

        # Try to split existing geometry with the drawn line
        # (Fusion 360 behavior: drawing a line across a shape splits it)
        if not close and len(drawn) >= 2:
            did_split = self._split_geometry_with_line(drawn)
            if did_split:
                # Cutting line consumed — don't keep it as a separate poly
                self._notify()
                self._fire_poly_change()
                self._draw_pts.clear()
                self._draw_constraint = None
                self._dismiss_dim_inputs()
                self._show_flash("Geometry split", 800)
                self._redraw()
                return

        # No split occurred — add drawn polyline normally
        self._polys.append(drawn)
        self._notify()
        self._fire_poly_change()
        self._draw_pts.clear()
        self._draw_constraint = None
        self._dismiss_dim_inputs()
        self._show_flash("Polyline created", 800)
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

    def _snap_to_polyline(self, cx: float, cy: float) -> tuple[float, float, str] | None:
        """Return the nearest world-space point on any polyline within _SNAP_DIST pixels.

        Checks vertices first, then midpoints, then perpendicular (in draw mode),
        then edges. Returns (wx, wy, snap_type) or None.
        """
        best_dist = _SNAP_DIST
        best_pt: tuple[float, float] | None = None
        best_type: str = "edge"
        # Check vertices
        for poly in self._polys:
            for pt in poly:
                sx, sy = self._w2c(*pt)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
                    best_type = "vertex"
        # Check midpoints (only real segments, not wrap-around for open polys)
        for poly in self._polys:
            n = len(poly)
            is_closed = (
                n >= 3
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
            )
            seg_count = n if is_closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
                sx, sy = self._w2c(mx, my)
                d = math.hypot(cx - sx, cy - sy)
                if d < best_dist:
                    best_dist = d
                    best_pt = (mx, my)
                    best_type = "midpoint"
        # Check perpendicular snap (draw mode only, when there are placed points)
        if self._mode == "draw" and self._draw_pts:
            last_wx, last_wy = self._draw_pts[-1]
            for poly in self._polys:
                n = len(poly)
                is_closed = (
                    n >= 3
                    and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
                )
                seg_count = n if is_closed else n - 1
                for vi in range(seg_count):
                    eax, eay = poly[vi]
                    ebx, eby = poly[(vi + 1) % n]
                    edx, edy = ebx - eax, eby - eay
                    seg_len_sq = edx * edx + edy * edy
                    if seg_len_sq < 1e-12:
                        continue
                    # Find perpendicular foot from last_draw_pt onto this edge
                    t_perp = ((last_wx - eax) * edx + (last_wy - eay) * edy) / seg_len_sq
                    if 0.0 <= t_perp <= 1.0:
                        foot_x = eax + t_perp * edx
                        foot_y = eay + t_perp * edy
                        sx, sy = self._w2c(foot_x, foot_y)
                        d = math.hypot(cx - sx, cy - sy)
                        if d < best_dist:
                            best_dist = d
                            best_pt = (foot_x, foot_y)
                            best_type = "perpendicular"
        # Check edges
        for poly in self._polys:
            n = len(poly)
            is_closed = (
                n >= 3
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
            )
            seg_count = n if is_closed else n - 1
            for vi in range(seg_count):
                ax, ay = poly[vi]
                bx, by = poly[(vi + 1) % n]
                dx, dy = bx - ax, by - ay
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-12:
                    continue
                wwx, wwy = self._c2w(cx, cy)
                t = max(
                    0.0,
                    min(
                        1.0,
                        ((wwx - ax) * dx + (wwy - ay) * dy) / seg_len_sq,
                    ),
                )
                px, py_ = ax + t * dx, ay + t * dy
                scx, scy = self._w2c(px, py_)
                d = math.hypot(cx - scx, cy - scy)
                if d < best_dist:
                    best_dist = d
                    best_pt = (px, py_)
                    best_type = "edge"
        if best_pt is not None:
            return (best_pt[0], best_pt[1], best_type)
        return None

    @staticmethod
    def _angle_snap(ax: float, ay: float, wx: float, wy: float) -> tuple[float, float]:
        """Snap (wx, wy) to the nearest 45-degree ray from (ax, ay)."""
        dxx = wx - ax
        dyy = wy - ay
        dist = math.hypot(dxx, dyy)
        if dist < 1e-9:
            return (wx, wy)
        angle = math.atan2(dyy, dxx)
        # Round to nearest 45° (pi/4)
        snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
        return (ax + dist * math.cos(snapped), ay + dist * math.sin(snapped))

    def _find_nearest_endpoint(self, cx: float, cy: float) -> tuple[float, float] | None:
        """Find the nearest start/end point of existing polylines within snap distance.

        Used to connect new drawings to existing polyline endpoints (Fusion 360 behavior).
        """
        best_dist = _SNAP_DIST
        best_pt: tuple[float, float] | None = None
        for poly in self._polys:
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
            n = len(poly)
            for vi in range(n):
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
            n = len(poly)
            for vi in range(n):
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
        if (self._mode == "draw" or self._measure_mode) and self._cursor_wx is not None:
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

        # Polylines
        for idx, poly in enumerate(self._polys):
            if len(poly) < 2:
                continue
            # Frustum culling: skip polylines entirely outside the viewport
            _pxs = [p[0] for p in poly]
            _pys = [p[1] for p in poly]
            _poly_rect = QRectF(
                QPointF(min(_pxs), min(_pys)),
                QPointF(max(_pxs), max(_pys)),
            )
            if not _visible_world.intersects(_poly_rect):
                continue
            sel = idx in self._sel
            color = QColor(SEL) if sel else QColor(POLY)
            lw = 2.0 if sel else 1.5
            pen = QPen(color, lw)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            sx, sy = self._w2c(*poly[0])
            path.moveTo(sx, sy)
            for pt in poly[1:]:
                px, py_ = self._w2c(*pt)
                path.lineTo(px, py_)
            if (
                len(poly) >= 3
                and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) < 0.5
            ):
                path.closeSubpath()
            painter.drawPath(path)

        # Selection bounding box
        if self._sel and self._mode == "select":
            sel_pts = [
                pt for i in self._sel if i < len(self._polys) for pt in self._polys[i]
            ]
            if sel_pts:
                xs, ys = zip(*sel_pts)
                bx0, by0 = self._w2c(min(xs), max(ys))
                bx1, by1 = self._w2c(max(xs), min(ys))
                pad = 4
                pen = QPen(QColor(SEL), 1.0, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(
                    QRectF(
                        bx0 - pad, by0 - pad, bx1 - bx0 + 2 * pad, by1 - by0 + 2 * pad
                    )
                )

        # Edit mode: vertex handles
        if self._mode == "edit":
            self._paint_edit_handles(painter)

        # Construction lines (Feature 15)
        if self._construction_lines:
            con_pen = QPen(_CONSTRUCTION_COLOR, 0.5, Qt.PenStyle.DashLine)
            painter.setPen(con_pen)
            for cl_dir, cl_val in self._construction_lines:
                if cl_dir == "h":
                    _, cy_con = self._w2c(0.0, cl_val)
                    painter.drawLine(QPointF(0.0, cy_con), QPointF(float(w), cy_con))
                else:
                    cx_con, _ = self._w2c(cl_val, 0.0)
                    painter.drawLine(QPointF(cx_con, 0.0), QPointF(cx_con, float(h)))

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
                    nx, ny = dx_o / d_o, dy_o / d_o
                    p1x = anchor_cx - nx * ext
                    p1y = anchor_cy + ny * ext  # Note: canvas Y is inverted
                    p2x = anchor_cx + nx * ext
                    p2y = anchor_cy - ny * ext
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
            for _dpoly in self._polys:
                for _dpt in _dpoly:
                    _dcx, _dcy = self._w2c(*_dpt)
                    painter.setPen(QPen(QColor("#3a5a6a"), 1.0))
                    painter.setBrush(QBrush(_dim_dot))
                    painter.drawEllipse(QPointF(_dcx, _dcy), 3, 3)
            # Highlight endpoints of existing polylines (connection targets)
            _ep_color = QColor("#5a8aaa")
            for _dpoly in self._polys:
                if len(_dpoly) >= 2:
                    for _ept in (_dpoly[0], _dpoly[-1]):
                        _ecx, _ecy = self._w2c(*_ept)
                        painter.setPen(QPen(_ep_color, 1.5))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawEllipse(QPointF(_ecx, _ecy), 5, 5)

        # C. Inference / alignment lines
        if self._mode == "draw" and self._draw_pts and self._cursor_wx is not None:
            self._paint_inference_lines(painter, w, h)

        # In-progress draw polygon (BEFORE snap indicators so snaps render on top)
        if self._draw_pts:
            self._paint_in_progress_poly(painter)

        # Snap indicator — drawn LAST so it's always visible on top
        if self._mode == "draw" and self._draw_snap is not None:
            self._paint_snap_overlay(painter)

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
            hint = "[click=add  dbl-click=close  right-click=finish open  \u21e7=ortho  ⌫=undo pt  Esc=cancel  D=exit]"
        elif self._mode == "edit":
            hint = "[drag vert  dbl-click edge=insert  right-click vert=delete  E=exit]"
        else:
            hint = "[F=fit  \u2318Z=undo  \u2318A=all  \u2318C/V/D/X=clip  S=snap  [/]=grid  H/V=cline  M D E=modes]"
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

    def _paint_bg_image(self, painter: QPainter) -> None:
        target_w = max(1, int(self._bg_w_mm * self._scale))
        target_h = max(1, int(self._bg_h_mm * self._scale))
        max_dim = 1200
        if max(target_w, target_h) > max_dim:
            ratio = max_dim / max(target_w, target_h)
            target_w = max(1, int(target_w * ratio))
            target_h = max(1, int(target_h * ratio))
        if (
            self._bg_pixmap is None
            or abs(self._scale - self._bg_cached_scale) > self._bg_cached_scale * 0.01
        ):
            try:
                assert self._bg_pil is not None
                resized = self._bg_pil.resize(
                    (target_w, target_h), PILImage.Resampling.LANCZOS
                )
                self._bg_pixmap = _pil_to_qpixmap(resized)
                self._bg_cached_scale = self._scale
            except (AssertionError, OSError, ValueError):
                return
        cx, cy = self._w2c(0.0, self._bg_h_mm)
        painter.drawPixmap(QPointF(cx, cy), self._bg_pixmap)

    def _paint_grid(self, painter: QPainter, canvas_w: int, canvas_h: int) -> None:
        spacing = max(self._grid_spacing, 0.001)
        if spacing * self._scale < 6:
            return
        wx0, wy_top = self._c2w(0.0, 0.0)
        wx1, wy_bottom = self._c2w(float(canvas_w), float(canvas_h))
        minx, maxx = sorted((wx0, wx1))
        miny, maxy = sorted((wy_bottom, wy_top))
        major_every = 5

        x = math.floor(minx / spacing) * spacing
        while x <= maxx + spacing:
            is_major = round(x / spacing) % major_every == 0
            color = (
                _GRID_AXIS
                if abs(x) < spacing * 0.25
                else (_GRID_MAJOR if is_major else _GRID_MINOR)
            )
            painter.setPen(QPen(color, 1))
            cx, _ = self._w2c(x, 0.0)
            painter.drawLine(QPointF(cx, 0.0), QPointF(cx, float(canvas_h)))
            x += spacing

        y = math.floor(miny / spacing) * spacing
        while y <= maxy + spacing:
            is_major = round(y / spacing) % major_every == 0
            color = (
                _GRID_AXIS
                if abs(y) < spacing * 0.25
                else (_GRID_MAJOR if is_major else _GRID_MINOR)
            )
            painter.setPen(QPen(color, 1))
            _, cy = self._w2c(0.0, y)
            painter.drawLine(QPointF(0.0, cy), QPointF(float(canvas_w), cy))
            y += spacing

    def _paint_edit_handles(self, painter: QPainter) -> None:
        for pi, poly in enumerate(self._polys):
            for vi, pt in enumerate(poly):
                cx, cy = self._w2c(*pt)
                is_hover = self._hover_vert == (pi, vi)
                is_active = (
                    self._edit_dragging
                    and self._edit_poly == pi
                    and self._edit_vert == vi
                )
                if is_active:
                    color = _HANDLE_ACTIVE
                    r = _HANDLE_R + 2
                elif is_hover:
                    color = _HANDLE_HOVER
                    r = _HANDLE_R + 1
                else:
                    color = _HANDLE
                    r = _HANDLE_R
                pen = QPen(color, 1.5)
                painter.setPen(pen)
                if is_active or is_hover:
                    painter.setBrush(QBrush(color))
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), r, r)

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

    def _paint_in_progress_poly(self, painter: QPainter) -> None:
        pts_screen = [self._w2c(*pt) for pt in self._draw_pts]
        near_close = self._is_near_start()

        # ── Placed segments (solid, thick) ──
        if len(pts_screen) >= 2:
            pen = QPen(_DRAW_COLOR, _DRAW_LINE_W)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(*pts_screen[0])
            for px, py_ in pts_screen[1:]:
                path.lineTo(px, py_)
            painter.drawPath(path)

        # ── Close-polygon preview ──
        if near_close:
            start_cx, start_cy = self._w2c(*self._draw_pts[0])
            last_cx, last_cy = self._w2c(*self._draw_pts[-1])

            # Draw filled translucent preview of the closed shape
            preview_path = QPainterPath()
            preview_path.moveTo(*pts_screen[0])
            for px, py_ in pts_screen[1:]:
                preview_path.lineTo(px, py_)
            preview_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 200, 170, 30)))
            painter.drawPath(preview_path)

            # Closing segment — solid teal line
            pen = QPen(_SNAP_CLOSE, _DRAW_LINE_W)
            painter.setPen(pen)
            painter.drawLine(QPointF(last_cx, last_cy), QPointF(start_cx, start_cy))

            # Prominent close ring — double ring with glow
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 200, 170, 60), 4))
            painter.drawEllipse(QPointF(start_cx, start_cy), 14, 14)
            painter.setPen(QPen(_SNAP_CLOSE, 2.5))
            painter.drawEllipse(QPointF(start_cx, start_cy), 10, 10)

            # "Close" label
            painter.setPen(_SNAP_CLOSE)
            painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
            painter.drawText(QPointF(start_cx + 16, start_cy + 5), "Close")

        # ── Vertex dots (larger, with outline ring) ──
        for i, pt in enumerate(self._draw_pts):
            cx, cy = self._w2c(*pt)
            # Outer ring
            painter.setPen(QPen(QColor(245, 166, 35, 120), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), _DRAW_VERT_R + 2, _DRAW_VERT_R + 2)
            # Filled center
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_DRAW_COLOR))
            painter.drawEllipse(QPointF(cx, cy), _DRAW_VERT_R, _DRAW_VERT_R)

        # ── Rubber-band line to cursor ──
        if self._cursor_wx is not None and self._cursor_wy is not None and self._draw_pts:
            last = self._w2c(*self._draw_pts[-1])

            # Use effective snap position for rubber-band (visual cursor jump)
            if self._draw_snap is not None and not near_close:
                eff_wx, eff_wy = self._draw_snap
            elif near_close:
                eff_wx, eff_wy = self._draw_pts[0]
            else:
                eff_wx, eff_wy = self._cursor_wx, self._cursor_wy
            cur_c = self._w2c(eff_wx, eff_wy)

            if not near_close:
                # Constraint color: blue when H/V constrained, amber otherwise
                if self._draw_constraint is not None:
                    rub_color = QColor("#4a9eff")
                else:
                    rub_color = _DRAW_COLOR
                pen = QPen(rub_color, _RUBBER_W)
                painter.setPen(pen)
                painter.drawLine(QPointF(*last), QPointF(*cur_c))

                # H/V constraint icon near midpoint
                if self._draw_constraint is not None:
                    mid_cx = (last[0] + cur_c[0]) / 2
                    mid_cy = (last[1] + cur_c[1]) / 2
                    painter.setPen(QColor("#4a9eff"))
                    painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
                    painter.drawText(QPointF(mid_cx + 8, mid_cy - 6), self._draw_constraint)

        # ── Segment length badge on rubber-band ──
        if self._cursor_wx is not None and self._draw_pts and not near_close:
            last_w = self._draw_pts[-1]
            eff_wx2 = self._draw_snap[0] if self._draw_snap else self._cursor_wx
            eff_wy2 = self._draw_snap[1] if self._draw_snap else self._cursor_wy
            seg_len = math.hypot(eff_wx2 - last_w[0], eff_wy2 - last_w[1])
            if seg_len > 0.01:
                last_c = self._w2c(*last_w)
                cur_c2 = self._w2c(eff_wx2, eff_wy2)
                mid_x = (last_c[0] + cur_c2[0]) / 2
                mid_y = (last_c[1] + cur_c2[1]) / 2
                seg_text = f"{seg_len:.2f}"
                painter.setFont(QFont("Helvetica", 9))
                fm = QFontMetrics(painter.font())
                tw = fm.horizontalAdvance(seg_text)
                # Background pill
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(_BADGE_BG))
                painter.drawRoundedRect(
                    QRectF(mid_x - tw / 2 - 4, mid_y - 8 - 12, tw + 8, 16), 3, 3
                )
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QPointF(mid_x - tw / 2, mid_y - 8 - 1), seg_text)

        # ── Cumulative polyline length and point count (top-right badge) ──
        if self._draw_pts:
            total_len = 0.0
            for i in range(1, len(self._draw_pts)):
                px0, py0 = self._draw_pts[i - 1]
                px1, py1 = self._draw_pts[i]
                total_len += math.hypot(px1 - px0, py1 - py0)
            if self._cursor_wx is not None and self._cursor_wy is not None:
                eff_wx3 = self._draw_snap[0] if self._draw_snap else self._cursor_wx
                eff_wy3 = self._draw_snap[1] if self._draw_snap else self._cursor_wy
                total_len += math.hypot(
                    eff_wx3 - self._draw_pts[-1][0],
                    eff_wy3 - self._draw_pts[-1][1],
                )
            vp = self.viewport()
            vw = max(vp.width(), 100)
            summary_text = f"Total: {total_len:.2f} mm  |  {len(self._draw_pts)} pts"
            self._draw_badge(painter, vw - 100, 50, summary_text, 10)

    def _paint_snap_overlay(self, painter: QPainter) -> None:
        """Draw snap ring, type indicator, and label — rendered LAST so always visible."""
        if self._draw_snap is None:
            return
        _dsx, _dsy = self._w2c(*self._draw_snap)
        snap_t = self._draw_snap_type or ""

        # Outer glow ring
        painter.setPen(QPen(QColor(0, 200, 170, 60), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(_dsx, _dsy), 11, 11)
        # Inner teal snap ring
        painter.setPen(QPen(_SNAP_CLOSE, 2.0))
        painter.drawEllipse(QPointF(_dsx, _dsy), 7, 7)

        # Snap type indicator shape
        painter.setPen(QPen(_SNAP_CLOSE, 2.0))
        painter.setBrush(QBrush(_SNAP_CLOSE))
        if snap_t == "vertex":
            path = QPainterPath()
            path.moveTo(_dsx, _dsy - 6)
            path.lineTo(_dsx + 6, _dsy)
            path.lineTo(_dsx, _dsy + 6)
            path.lineTo(_dsx - 6, _dsy)
            path.closeSubpath()
            painter.drawPath(path)
        elif snap_t == "midpoint":
            path = QPainterPath()
            path.moveTo(_dsx, _dsy - 6)
            path.lineTo(_dsx + 5, _dsy + 4)
            path.lineTo(_dsx - 5, _dsy + 4)
            path.closeSubpath()
            painter.drawPath(path)
        elif snap_t == "grid":
            painter.drawLine(QPointF(_dsx - 5, _dsy), QPointF(_dsx + 5, _dsy))
            painter.drawLine(QPointF(_dsx, _dsy - 5), QPointF(_dsx, _dsy + 5))
        elif snap_t == "perpendicular":
            painter.drawLine(QPointF(_dsx, _dsy + 5), QPointF(_dsx, _dsy - 4))
            painter.drawLine(QPointF(_dsx - 5, _dsy + 5), QPointF(_dsx + 5, _dsy + 5))
        elif snap_t == "edge":
            painter.setPen(QPen(_SNAP_CLOSE, 1.5))
            painter.drawLine(QPointF(_dsx - 4, _dsy - 4), QPointF(_dsx + 4, _dsy + 4))
            painter.drawLine(QPointF(_dsx - 4, _dsy + 4), QPointF(_dsx + 4, _dsy - 4))

        # Snap label pill — above the snap point
        _snap_labels = {
            "vertex": "Endpoint",
            "midpoint": "Midpoint",
            "edge": "On Edge",
            "grid": "Grid",
            "perpendicular": "Perpendicular",
        }
        _label = _snap_labels.get(snap_t, "")
        if _label:
            painter.setFont(QFont("Helvetica", 9, QFont.Weight.DemiBold))
            _fm = QFontMetrics(painter.font())
            _ltw = _fm.horizontalAdvance(_label)
            _lx = _dsx - _ltw / 2 - 4
            _ly = _dsy - 24
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 30, 40, 210)))
            painter.drawRoundedRect(QRectF(_lx, _ly, _ltw + 8, 16), 3, 3)
            painter.setPen(_SNAP_CLOSE)
            painter.drawText(QPointF(_lx + 4, _ly + 12), _label)

    def _draw_badge(
        self, painter: QPainter, cx: float, cy: float, text: str, font_size: int
    ) -> None:
        """Draw a text badge with semi-transparent background centered at (cx, cy)."""
        font = QFont("Helvetica", font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        pad = 4
        rx = cx - tw / 2 - pad
        ry = cy - th / 2 - pad / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BADGE_BG))
        painter.drawRoundedRect(QRectF(rx, ry, tw + 2 * pad, th + pad), 3, 3)
        painter.setPen(_BADGE_TEXT if font_size >= 10 else _BADGE_DIM)
        painter.drawText(
            QRectF(rx, ry, tw + 2 * pad, th + pad),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

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
        self._dim_angle_edit = angle_edit
        self._dim_angle_dirty = False

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
            dist_text = self._dim_distance_edit.text().strip() if self._dim_distance_edit else ""
            angle_text = self._dim_angle_edit.text().strip() if self._dim_angle_edit else ""
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
            self._redraw()
        except ValueError:
            pass

    # ── Inference / alignment lines ──────────────────────────────────────────

    def _paint_inference_lines(self, painter: QPainter, vp_w: int, vp_h: int) -> None:
        """Draw dotted inference lines showing H/V alignment with existing endpoints."""
        if self._cursor_wx is None or self._cursor_wy is None or not self._draw_pts:
            return
        cur_wx, cur_wy = self._cursor_wx, self._cursor_wy
        cur_cx, cur_cy = self._w2c(cur_wx, cur_wy)

        # Collect all candidate endpoints (existing polyline endpoints + last draw point)
        candidates: list[tuple[float, float]] = []
        for poly in self._polys:
            for pt in poly:
                candidates.append(pt)
        # Also include all placed draw points
        for pt in self._draw_pts:
            candidates.append(pt)

        # Threshold: 3 degrees expressed as a ratio for quick check
        _ANGLE_THRESH = math.tan(math.radians(3.0))

        hits: list[tuple[float, tuple[float, float]]] = []  # (distance, endpoint)

        for ep in candidates:
            ex, ey = ep
            dx = abs(cur_wx - ex)
            dy = abs(cur_wy - ey)
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                continue

            # Check near-horizontal alignment (dy/dx < tan(3deg))
            if dx > 1e-9 and dy / dx < _ANGLE_THRESH:
                hits.append((dist, ep))
                continue
            # Check near-vertical alignment (dx/dy < tan(3deg))
            if dy > 1e-9 and dx / dy < _ANGLE_THRESH:
                hits.append((dist, ep))

        # Limit to 3 nearest
        hits.sort(key=lambda h: h[0])
        shown = 0
        seen: set[tuple[float, float]] = set()
        inf_pen = QPen(QColor("#4a9eff30"), 0.5, Qt.PenStyle.DashLine)
        painter.setPen(inf_pen)

        for _d, ep in hits:
            if ep in seen:
                continue
            seen.add(ep)
            if shown >= 3:
                break
            ex, ey = ep
            ecx, ecy = self._w2c(ex, ey)
            dx = abs(cur_wx - ex)
            dy = abs(cur_wy - ey)
            if dx > 1e-9 and dy / dx < _ANGLE_THRESH:
                # Horizontal inference line
                painter.drawLine(QPointF(ecx, ecy), QPointF(cur_cx, ecy))
                shown += 1
            elif dy > 1e-9 and dx / dy < _ANGLE_THRESH:
                # Vertical inference line
                painter.drawLine(QPointF(ecx, ecy), QPointF(ecx, cur_cy))
                shown += 1

    def _paint_measure_button(self, painter: QPainter, canvas_w: int) -> None:
        pad, bh, bw = 6, 22, 114
        label = "\u2715 Measure [M]" if self._measure_mode else "\u2295 Measure [M]"
        color = _MEASURE_COLOR if self._measure_mode else QColor(DIM)
        bg = QColor("#002233") if self._measure_mode else QColor("#14141e")
        x1, y1 = canvas_w - bw - pad, pad
        x2, y2 = canvas_w - pad, pad + bh
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRect(QRectF(x1, y1, bw, bh))
        painter.setFont(QFont("Helvetica", 10))
        painter.setPen(color)
        painter.drawText(QRectF(x1, y1, bw, bh), Qt.AlignmentFlag.AlignCenter, label)
        self._mbtn_rect = (x1, y1, x2, y2)

    def _hit_measure_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._mbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    def _paint_measure_overlay(self, painter: QPainter) -> None:
        assert self._measure_anchor is not None
        assert self._measure_hover is not None
        ax, ay = self._measure_anchor
        hx, hy = self._measure_hover
        cax, cay = self._w2c(ax, ay)
        chx, chy = self._w2c(hx, hy)
        dist = math.hypot(hx - ax, hy - ay)
        dx = abs(hx - ax)
        dy = abs(hy - ay)
        angle_deg = math.degrees(math.atan2(hy - ay, hx - ax)) if dist > 1e-9 else 0.0

        pen = QPen(_MEASURE_COLOR, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(cax, cay), QPointF(chx, chy))

        # Snap rings on anchor
        if self._measure_snapped_a:
            painter.setPen(QPen(_SNAP_CLOSE, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cax, cay), 8, 8)

        r = 5
        painter.setPen(QPen(_MEASURE_COLOR, 2))
        painter.setBrush(QBrush(QColor("#001522")))
        painter.drawEllipse(QPointF(cax, cay), r, r)
        painter.setPen(QPen(_MEASURE_COLOR, 1))
        painter.drawLine(QPointF(cax - 8, cay), QPointF(cax + 8, cay))
        painter.drawLine(QPointF(cax, cay - 8), QPointF(cax, cay + 8))

        # Snap ring on hover/end
        if self._measure_snapped_b or (
            not self._measure_locked and self._snap_to_polyline(chx, chy) is not None
        ):
            painter.setPen(QPen(_SNAP_CLOSE, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(chx, chy), 8, 8)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_MEASURE_COLOR))
        painter.drawEllipse(QPointF(chx, chy), 3, 3)

        mx, my = (cax + chx) / 2, (cay + chy) / 2
        badge_y = my - 28

        if not self._measure_locked:
            painter.setPen(QPen(_MEASURE_COLOR, 1))
            painter.setBrush(QBrush(QColor("#001522")))
            painter.drawRect(QRectF(mx - 100, badge_y - 14, 200, 32))
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
            painter.drawText(
                QRectF(mx - 100, badge_y - 14, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"{dist:.2f} mm  {angle_deg:.1f}\u00b0",
            )
            painter.setPen(_MEASURE_COLOR)
            painter.setFont(QFont("Helvetica", 9))
            painter.drawText(
                QRectF(mx - 100, badge_y, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"\u0394x {dx:.2f}  \u0394y {dy:.2f}  [\u21e7=snap angle]",
            )
        else:
            # When locked, draw the delta info below the badge
            painter.setPen(QPen(_MEASURE_COLOR, 1))
            painter.setBrush(QBrush(QColor("#001522")))
            painter.drawRect(QRectF(mx - 120, badge_y + 8, 240, 18))
            painter.setPen(_MEASURE_COLOR)
            painter.setFont(QFont("Helvetica", 9))
            painter.drawText(
                QRectF(mx - 120, badge_y + 8, 240, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"\u0394x {dx:.2f}  \u0394y {dy:.2f}  {angle_deg:.1f}°  ·  click to reset",
            )

    # ── Events ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
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
            elif key == Qt.Key.Key_H and shift_mod:
                # Ctrl+Shift+H: clear all construction lines (Feature 15)
                self._construction_lines.clear()
                self._show_flash("Construction lines cleared")
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
        elif key == Qt.Key.Key_Escape:
            # If a dim field has focus or is dirty, blur and reset it first
            has_dim_focus = (
                (self._dim_distance_edit is not None and self._dim_distance_edit.hasFocus())
                or (self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus())
            )
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
        elif key == Qt.Key.Key_H and not ctrl:
            # H = horizontal construction line (Feature 15)
            if self._cursor_wy is not None:
                self._construction_lines.append(("h", self._cursor_wy))
                self._show_flash(f"H-line at Y={self._cursor_wy:.2f}")
            return
        elif key == Qt.Key.Key_V and not ctrl:
            # V = vertical construction line (Feature 15)
            if self._cursor_wx is not None:
                self._construction_lines.append(("v", self._cursor_wx))
                self._show_flash(f"V-line at X={self._cursor_wx:.2f}")
            return
        elif self._selectable:
            # B. Dimension HUD key interception — digits/period/minus go to distance field
            if (
                self._dim_distance_edit is not None
                and key in (
                    Qt.Key.Key_0, Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3,
                    Qt.Key.Key_4, Qt.Key.Key_5, Qt.Key.Key_6, Qt.Key.Key_7,
                    Qt.Key.Key_8, Qt.Key.Key_9, Qt.Key.Key_Period, Qt.Key.Key_Minus,
                )
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
                if self._dim_distance_edit is not None and self._dim_distance_dirty:
                    if self._dim_distance_edit.hasFocus():
                        self._dim_distance_edit.backspace()
                        if not self._dim_distance_edit.text():
                            self._dim_distance_dirty = False
                        event.accept()
                        return
                if self._dim_angle_edit is not None and self._dim_angle_dirty:
                    if self._dim_angle_edit.hasFocus():
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
                # If dim inputs are dirty, apply them; otherwise finish draw
                if self._dim_distance_dirty or self._dim_angle_dirty:
                    self._apply_dim_input()
                else:
                    self._finish_draw()
            elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                # Tab cycles focus between distance and angle fields
                if self._dim_distance_edit is not None and self._dim_angle_edit is not None:
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

        if btn == Qt.MouseButton.RightButton:
            if self._selectable:
                self._rightclick_cb(pos.x(), pos.y())
            return

        if btn != Qt.MouseButton.LeftButton:
            return

        if self._hit_measure_button(pos.x(), pos.y()):
            self.toggle_measure()
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
            snap_result = self._resolve_snap(pos.x(), pos.y(), wx, wy)
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
            hit = self._find_nearest_vertex(pos.x(), pos.y())
            if hit is not None:
                pi, vi = hit
                self._push_undo()
                self._edit_poly = pi
                self._edit_vert = vi
                self._edit_dragging = True
                self._redraw()
                return
            self._lmb_press = pos
            self._lmb_prev = pos
            return

        if self._mode == "draw":
            wx, wy = self._c2w(pos.x(), pos.y())
            if self._draw_snap is not None:
                wx, wy = self._draw_snap
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
            self._draw_pts.append((wx, wy))
            # B. Show dim inputs after first point is placed
            if len(self._draw_pts) == 1:
                self._show_dim_inputs()
            # Reset dirty flags for the new segment
            self._dim_distance_dirty = False
            self._dim_angle_dirty = False
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
            self._lmb_target = target
            # Prepare for move if clicking on an already-selected poly
            if target is not None and target in self._sel:
                wx, wy = self._c2w(pos.x(), pos.y())
                self._move_origin = (wx, wy)
                self._move_dragging = False
                self._move_undo_pushed = False

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        wx, wy = self._c2w(pos.x(), pos.y())
        self._cursor_wx = wx
        self._cursor_wy = wy

        if self._mmb_prev is not None and event.buttons() & Qt.MouseButton.MiddleButton:
            self._ox += pos.x() - self._mmb_prev.x()
            self._oy += pos.y() - self._mmb_prev.y()
            self._mmb_prev = pos
            self._redraw()
            return

        if self._measure_mode:
            if self._measure_locked:
                return
            snap_result = self._resolve_snap(pos.x(), pos.y(), wx, wy)
            if self._measure_anchor is None:
                # Pre-first-click: just track snap indicator
                self._measure_hover_pre = (snap_result[0], snap_result[1]) if snap_result else None
                self._redraw()
                return
            # After anchor placed — compute hover with snap + optional angle snap
            if snap_result is not None:
                mx, my = snap_result[0], snap_result[1]
            else:
                mx, my = wx, wy
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                mx, my = self._angle_snap(*self._measure_anchor, mx, my)
            self._measure_hover = (mx, my)
            self._redraw()
            return

        if (
            self._mode == "edit"
            and self._edit_dragging
            and self._edit_poly is not None
            and self._edit_vert is not None
        ):
            if self._grid_snap:
                wx, wy = self._snap_to_grid(wx, wy)
            self._polys[self._edit_poly][self._edit_vert] = (wx, wy)
            self._redraw()
            return

        if self._mode == "edit":
            old_hover = self._hover_vert
            self._hover_vert = self._find_nearest_vertex(pos.x(), pos.y())
            if self._hover_vert != old_hover:
                self._redraw()
            return

        if self._mode == "draw":
            # 1. Resolve snap target
            snap_result = self._resolve_snap(pos.x(), pos.y(), wx, wy)
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

            # 4. Auto-constraint detection (H/V) — applied to effective position
            #    so snap point, cursor, and crosshair all stay in sync
            self._draw_constraint = None
            if self._draw_pts:
                last_wx, last_wy = self._draw_pts[-1]
                seg_dx = eff_x - last_wx
                seg_dy = eff_y - last_wy
                seg_dist = math.hypot(seg_dx, seg_dy)
                if seg_dist > 1e-9:
                    seg_angle = math.degrees(math.atan2(seg_dy, seg_dx)) % 360
                    if (seg_angle < 3 or seg_angle > 357
                            or (177 < seg_angle < 183)):
                        self._draw_constraint = "H"
                        eff_y = last_wy
                        if self._draw_snap is not None:
                            self._draw_snap = (self._draw_snap[0], last_wy)
                    elif (87 < seg_angle < 93 or 267 < seg_angle < 273):
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
                if self._move_dragging:
                    if not self._move_undo_pushed:
                        self._push_undo()
                        self._move_undo_pushed = True
                    new_wx, new_wy = self._c2w(pos.x(), pos.y())
                    if self._grid_snap:
                        new_wx, new_wy = self._snap_to_grid(new_wx, new_wy)
                    dx_w = new_wx - self._move_origin[0]
                    dy_w = new_wy - self._move_origin[1]
                    for idx in self._sel:
                        self._polys[idx] = [
                            (x + dx_w, y + dy_w) for x, y in self._polys[idx]
                        ]
                    self._move_origin = (new_wx, new_wy)
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

        if self._measure_mode:
            return

        if self._mode == "edit" and self._edit_dragging:
            self._edit_dragging = False
            self._redraw()
            self._notify()
            self._fire_poly_change()
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
            self._lmb_press = None
            self._lmb_prev = None
            self._lmb_target = None
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return

        if (
            self._selectable
            and self._lmb_press is not None
            and self._lmb_target is not None
        ):
            dx = pos.x() - self._lmb_press.x()
            dy = pos.y() - self._lmb_press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH:
                idx = self._lmb_target
                if idx in self._sel:
                    self._sel.discard(idx)
                else:
                    self._sel.add(idx)
                self._redraw()
                self._notify()
        self._lmb_press = None
        self._lmb_prev = None
        self._lmb_target = None
        self._shift_drag = False
        self._band_start = None
        self._move_origin = None
        self._move_undo_pushed = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
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
        self._clipboard = [
            list(self._polys[i]) for i in sorted(self._sel) if i < len(self._polys)
        ]

    def _paste_clipboard(self) -> None:
        if not self._clipboard:
            return
        self._push_undo()
        offset = 1.0  # mm
        new_indices = []
        for poly in self._clipboard:
            new_poly = [(x + offset, y + offset) for x, y in poly]
            self._polys.append(new_poly)
            new_indices.append(len(self._polys) - 1)
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
        self._copy_selected()
        self._push_undo()
        self._polys = [p for i, p in enumerate(self._polys) if i not in self._sel]
        self._sel.clear()
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._sel:
            return
        if not self._nudge_undo_pushed:
            self._push_undo()
            self._nudge_undo_pushed = True
            QTimer.singleShot(500, self._reset_nudge_undo)
        for idx in self._sel:
            if idx < len(self._polys):
                self._polys[idx] = [(x + dx, y + dy) for x, y in self._polys[idx]]
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
        self._redraw()
        self._notify()
        self._fire_poly_change()

    @staticmethod
    def _is_poly_closed(poly: list[tuple[float, float]]) -> bool:
        """Check if a polyline is geometrically closed (first ≈ last)."""
        if len(poly) < 3:
            return False
        return math.hypot(
            poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]
        ) < 0.01

    def _split_geometry_with_line(
        self, new_poly: list[tuple[float, float]]
    ) -> bool:
        """Split existing polylines using a drawn cutting line (Shapely-based).

        Handles both closed polygons (split into sub-polygons) and open
        polylines (split at intersection points).  The cutting line itself
        is consumed (not kept) when at least one closed split succeeds.

        Returns True if any geometry was split.
        """
        if len(new_poly) < 2:
            return False
        try:
            cutter = LineString(new_poly)
            if cutter.is_empty or cutter.length < 1e-9:
                return False
        except Exception:
            return False

        any_split = False
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

                    # Extend cutting line beyond polygon bounds to ensure
                    # clean entry/exit (avoids tangent-touch issues)
                    bounds = shapely_poly.bounds  # (minx, miny, maxx, maxy)
                    diag = math.hypot(
                        bounds[2] - bounds[0], bounds[3] - bounds[1]
                    )
                    ext_cutter = self._extend_line(cutter, diag * 0.1)

                    pieces = shapely_split(shapely_poly, ext_cutter)
                    geoms = list(pieces.geoms) if hasattr(pieces, "geoms") else []

                    if len(geoms) >= 2:
                        for g in geoms:
                            if isinstance(g, Polygon) and not g.is_empty:
                                coords_out = list(g.exterior.coords)
                                if len(coords_out) >= 3:
                                    result_polys.append(
                                        [(x, y) for x, y in coords_out]
                                    )
                        any_split = True
                    else:
                        # Split failed (line tangent, partial, etc.) — keep original
                        result_polys.append(poly)
                except Exception:
                    # Any Shapely error — keep original geometry untouched
                    result_polys.append(poly)
            else:
                # ── Split open polyline ───────────────────────────────────
                try:
                    target_line = LineString(poly)
                    if target_line.is_empty or not cutter.intersects(target_line):
                        result_polys.append(poly)
                        continue

                    pieces = shapely_split(target_line, cutter)
                    geoms = list(pieces.geoms) if hasattr(pieces, "geoms") else []

                    if len(geoms) >= 2:
                        for g in geoms:
                            if isinstance(g, LineString) and not g.is_empty:
                                coords_out = list(g.coords)
                                if len(coords_out) >= 2:
                                    result_polys.append(
                                        [(x, y) for x, y in coords_out]
                                    )
                        any_split = True
                    else:
                        result_polys.append(poly)
                except Exception:
                    result_polys.append(poly)

        self._polys = result_polys
        return any_split

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
            # Right-click = finish open polyline (no close), stay in draw mode
            self._finish_draw(close=False)
            return

        if self._mode == "edit":
            hit = self._find_nearest_vertex(cx, cy)
            if hit is not None:
                pi, vi = hit
                menu = QMenu(self)
                if len(self._polys[pi]) > 2:
                    menu.addAction("Delete vertex", lambda: self._delete_vertex(pi, vi))
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
        if self._sel:
            menu.addAction(f"Delete selected ({len(self._sel)})", self.delete_selected)
            menu.addAction("Invert selection", self.invert_selection)
            menu.addAction("Deselect all", self.deselect_all)
            menu.addAction("Duplicate  [⌘D]", self.duplicate_selected)
            menu.addAction("Fit selection", self.fit_selection)
            transform_menu = menu.addMenu("Transform")
            transform_menu.addAction("Rotate +90°", lambda: self.rotate_selected(90.0))
            transform_menu.addAction("Rotate -90°", lambda: self.rotate_selected(-90.0))
            transform_menu.addAction(
                "Mirror horizontal",
                lambda: self.mirror_selected("horizontal"),
            )
            transform_menu.addAction(
                "Mirror vertical",
                lambda: self.mirror_selected("vertical"),
            )
            align_menu = transform_menu.addMenu("Align")
            align_menu.addAction("Left", lambda: self.align_selected("left"))
            align_menu.addAction("Center X", lambda: self.align_selected("center-x"))
            align_menu.addAction("Right", lambda: self.align_selected("right"))
            align_menu.addAction("Top", lambda: self.align_selected("top"))
            align_menu.addAction("Center Y", lambda: self.align_selected("center-y"))
            align_menu.addAction("Bottom", lambda: self.align_selected("bottom"))
        else:
            menu.addAction("Select all", self.select_all)
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

    def _delete_vertex(self, pi: int, vi: int) -> None:
        self._push_undo()
        self._polys[pi].pop(vi)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _delete_poly(self, pi: int) -> None:
        self._push_undo()
        self._polys.pop(pi)
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
        self._sel.discard(idx)
        self._sel = {i if i < idx else i - 1 for i in self._sel if i != idx}
        self._redraw()
        self._notify()
        self._fire_poly_change()


# Backward-compat alias
DxfCanvas = PolylineView
