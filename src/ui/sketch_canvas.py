"""Sketch-aware canvas.

Thin subclass of PolylineView that integrates with SketchGraph.
Drawing decomposes multi-point polylines into shared-endpoint segments.
Double-click in select mode selects all segments of a closed object.
Undo/redo operates on graph snapshots.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

from src.constants import DRAG_THRESH
from src.core.sketch import SketchGraph
from src.ui.canvas import PolylineView


class SketchCanvas(PolylineView):
    """PolylineView with graph-backed segment management."""

    graphChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, selectable=True)
        self._graph = SketchGraph()
        self._graph_undo: list[dict] = []
        self._graph_redo: list[dict] = []
        self._syncing = False
        self._pending_component_hit: int | None = None
        self._pending_component_press: QPointF | None = None

    @property
    def graph(self) -> SketchGraph:
        return self._graph

    # ── Draw completion override ──────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self._mode == "select"
            and event.button() == Qt.MouseButton.LeftButton
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            pos = event.position()
            self._pending_component_hit = self._find_poly_at(pos.x(), pos.y())
            self._pending_component_press = pos
        else:
            self._pending_component_hit = None
            self._pending_component_press = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        hit = self._pending_component_hit
        press = self._pending_component_press
        super().mouseReleaseEvent(event)

        if (
            self._mode == "select"
            and event.button() == Qt.MouseButton.LeftButton
            and hit is not None
            and press is not None
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            pos = event.position()
            dx = pos.x() - press.x()
            dy = pos.y() - press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH:
                seg = self._graph.segment_at_index(hit)
                if seg is not None:
                    indices = self._graph.segments_in_component(seg)
                    if indices:
                        self._sel = indices
                        self._redraw()
                        self._notify()

        self._pending_component_hit = None
        self._pending_component_press = None

    def _finish_draw(self, *, close: bool = False) -> None:
        """Intercept drawn polyline → decompose into graph segments."""
        if self._mode != "draw" or len(self._draw_pts) < 2:
            return

        pts = list(self._draw_pts)
        if close and pts[0] != pts[-1]:
            pts.append(pts[0])

        # Push graph undo
        self._push_graph_undo()

        # Decompose into segments with explicit topology.
        # No broad tolerance-based implicit merges: only near-exact matches.
        snap_tol = 1e-6
        prev_point = None
        created_segment_ids: list[int] = []
        for x, y in pts:
            p = self._graph.find_point_near(x, y, snap_tol)
            if p is None:
                p = self._graph.add_point(x, y)
            if prev_point is not None and prev_point.id != p.id:
                seg = self._graph.add_segment(prev_point, p)
                created_segment_ids.append(seg.id)
            prev_point = p

        # Clean up draw state (same fields parent would clear)
        self._draw_pts.clear()
        self._draw_constraint = None
        self._dismiss_dim_inputs()

        # Sync canvas display from graph
        self._sync_from_graph()
        if created_segment_ids:
            seed_seg = next(
                (
                    self._graph.segments[sid]
                    for sid in created_segment_ids
                    if sid in self._graph.segments
                ),
                None,
            )
            if seed_seg is not None:
                geo = self._graph.geometry_for_segment(seed_seg)
                if geo is not None and geo.is_closed:
                    self._sel = self._graph.segments_in_geometry(geo)
                    self.set_mode("select")
                    self._show_flash("Closed object created", 800)
                    self._redraw()
                    self._notify()
                    return
                self._sel = self._graph.segments_in_component(seed_seg)
        self._show_flash("Segment created", 800)
        self._redraw()
        self._notify()

    # ── Double-click: select whole object in select mode ──────────────────

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._mode == "select":
            # Find which segment was clicked
            pos = event.position()
            hit = self._find_poly_at(pos.x(), pos.y())
            if hit is not None:
                seg = self._graph.segment_at_index(hit)
                if seg is not None:
                    geo = self._graph.geometry_for_segment(seg)
                    if geo is not None:
                        # Select all segments in this object
                        indices = self._graph.segments_in_geometry(geo)
                        self._sel = indices
                        self._redraw()
                        self._notify()
                        return
                    indices = self._graph.segments_in_component(seg)
                    if indices:
                        self._sel = indices
                        self._redraw()
                        self._notify()
                        return
            return

        # Delegate to parent for draw/edit mode double-click
        super().mouseDoubleClickEvent(event)

    # ── Delete override ───────────────────────────────────────────────────

    def delete_selected(self) -> int:
        n = len(self._sel)
        if not n:
            return 0
        self._push_graph_undo()
        self._graph.remove_segments(set(self._sel))
        # Clean up orphaned points (points with no segments)
        orphans = [
            p
            for p in list(self._graph.points.values())
            if not any(
                s.p0.id == p.id or s.p1.id == p.id
                for s in self._graph.segments.values()
            )
        ]
        for p in orphans:
            self._graph.points.pop(p.id, None)
        self._sync_from_graph()
        return n

    # ── Undo/redo override ────────────────────────────────────────────────

    def undo(self) -> bool:
        if not self._graph_undo:
            return False
        self._graph_redo.append(self._graph.snapshot())
        if len(self._graph_redo) > 30:
            self._graph_redo.pop(0)
        state = self._graph_undo.pop()
        self._graph.restore(state)
        self._sync_from_graph()
        return True

    def redo(self) -> bool:
        if not self._graph_redo:
            return False
        self._graph_undo.append(self._graph.snapshot())
        if len(self._graph_undo) > 30:
            self._graph_undo.pop(0)
        state = self._graph_redo.pop()
        self._graph.restore(state)
        self._sync_from_graph()
        return True

    # ── Internal helpers ──────────────────────────────────────────────────

    def _push_graph_undo(self) -> None:
        self._graph_undo.append(self._graph.snapshot())
        if len(self._graph_undo) > 30:
            self._graph_undo.pop(0)
        self._graph_redo.clear()

    def _sync_from_graph(self) -> None:
        """Rebuild _polys from graph and refresh display."""
        self._syncing = True
        polys = self._graph.to_polys()
        self._polys = polys
        self._sel = self._sel & set(range(len(polys)))
        self._redraw()
        self._notify()
        self.graphChanged.emit()
        self._syncing = False

    def clear_graph(self) -> None:
        """Reset the graph and canvas."""
        self._graph = SketchGraph()
        self._graph_undo.clear()
        self._graph_redo.clear()
        self._sync_from_graph()
