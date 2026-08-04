# Interaction handlers extracted from view.py

from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.canvas.constants import DRAG_THRESH
from simple_stipple.canvas.tools import tools as canvas_tools
from simple_stipple.ui.components.focus import blur_focused_line_edit


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
        # Scale and Dimension are modal canvas tools: one Escape always
        # exits the mode completely, even when a target-distance field
        # has focus or a multi-click placement is in progress.
        if self._dimension_mode or self._measure_mode:
            self._dimension_mode = False
            self._dim_pending_p1 = None
            self._dim_pending_p2 = None
            self._dim_selected_segments.clear()
            self._dim_hover_segment = None
            self._dimension_tool.reset()
            self._measure_mode = False
            self._measure_anchor = None
            self._measure_hover = None
            self._measure_locked = False
            self._measure_end = None
            self._measure_snapped_a = False
            self._measure_snapped_b = False
            self._dismiss_measure_edit()
            self.setFocus()
            if self._mode != "select":
                self.set_mode("select")
            else:
                self._update_cursor()
                self._redraw()
                self.modeChanged.emit("select")
            return
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
        # Cancel a live move/gizmo/vertex drag before it can be
        # mistaken for a plain "clear selection" — otherwise the drag
        # keeps applying to a selection that was just emptied out from
        # under it, freezing the shape at its half-dragged position.
        if self._cancel_active_drag():
            return
        # First Escape mid-draw drops the unfinished path but keeps the
        # tool armed; the fall-through below exits the tool entirely.
        if self._cancel_draw_in_progress():
            return
        if self._bg_selected:
            self.select_background_image(False)
            return
        # In select mode, Escape clears selection
        if self._mode == "select" and self._sel:
            self.deselect_all()
            return
        self._escape_cb()
        return

    # An editable image is a first-class canvas target.  Give it the same
    # keyboard affordances as geometry: Delete removes the workspace image,
    # and Tab moves into its precise placement fields instead of being eaten
    # by the canvas' shape-dimension HUD.
    if self._bg_selected and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        if callable(self._bg_key_callback):
            self._bg_key_callback("remove")
            event.accept()
            return
    if self._bg_selected and key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        if callable(self._bg_key_callback):
            self._bg_key_callback("tab", key == Qt.Key.Key_Backtab)
            event.accept()
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
                if (self._draw_shape_w_edit is not None and self._draw_shape_w_edit.hasFocus()) or (
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

    # Declarative command shortcuts — see src/simple_stipple/canvas/interaction/commands.py.
    cmd = canvas_commands.match_key(key, mods)
    if cmd is not None and canvas_commands.can_run(self, cmd):
        cmd.run(self)
        event.accept()
        return

    QWidget.keyPressEvent(self, event)


def keyReleaseEvent(self, event: QKeyEvent) -> None:
    if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
        self._space_pan_active = False
        self._space_pan_dragging = False
        self._lmb_prev = None
        self._update_cursor()
        event.accept()
        return
    QWidget.keyReleaseEvent(self, event)


def mousePressEvent(self, event: QMouseEvent):
    pos = event.position()
    btn = event.button()

    if btn == Qt.MouseButton.LeftButton:
        bg_hit = self._background_edit_hit(pos.x(), pos.y())
        if bg_hit is not None:
            wx, wy = self._c2w(pos.x(), pos.y())
            self._bg_drag = (
                bg_hit,
                wx,
                wy,
                self._bg_x_mm,
                self._bg_y_mm,
                self._bg_w_mm,
                self._bg_h_mm,
                self._bg_rotation_deg,
            )
            return
        if self._bg_selected:
            self.select_background_image(False)
        elif (
            self._background_contains(pos.x(), pos.y())
            and self._find_poly_at(pos.x(), pos.y()) is None
        ):
            self.select_background_image(True)
            self._sel.clear()
            self._notify()
            return

    if btn == Qt.MouseButton.MiddleButton:
        self._mmb_prev = pos
        return

    if btn == Qt.MouseButton.LeftButton and self._space_pan_active:
        self._space_pan_dragging = True
        self._lmb_prev = pos
        self._update_cursor()
        return

    if btn == Qt.MouseButton.LeftButton and self._mode == "pan":
        self._lmb_prev = pos
        self._update_cursor()
        return

    if btn == Qt.MouseButton.RightButton:
        if self._selectable:
            self._rightclick_cb(pos.x(), pos.y())
        return

    if btn != Qt.MouseButton.LeftButton:
        return

    # Persistent, visible tool buttons. These used to be painted by dead
    # renderer helpers and had no event routing, so they were effectively
    # invisible and unclickable.
    if self._hit_measure_button(pos.x(), pos.y()):
        self.toggle_measure()
        return
    if self._hit_dimension_button(pos.x(), pos.y()):
        self.toggle_dimension_mode("linear")
        return
    if self._hit_angle_dimension_button(pos.x(), pos.y()):
        self.toggle_dimension_mode("angle")
        return

    # Rulers: press inside a ruler strip drags out a new guide.
    if self._rulers_visible and self._selectable:
        r = self.RULER_PX
        wx0, wy0 = self._c2w(pos.x(), pos.y())
        if pos.x() <= r and pos.y() <= r:
            return  # corner box
        if pos.y() <= r:
            self._guide_preview = self._canvas_service.begin_preview()
            self._guides.append(("h", wy0))
            self._guide_drag = len(self._guides) - 1
            self._selected_guide = self._guide_drag
            self._guide_drag_moved = False
            self._redraw()
            return
        if pos.x() <= r:
            self._guide_preview = self._canvas_service.begin_preview()
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
            self._guide_preview = self._canvas_service.begin_preview()
            self._guide_drag = gi
            self._selected_guide = gi
            self._guide_drag_moved = False
            self._redraw()
            return
    # Clicking elsewhere clears any selected guide.
    if self._selected_guide is not None:
        self._selected_guide = None
        self._redraw()

    # Existing dimensions take priority even while the Dimension tool is
    # armed, so clicking a value edits/selects it instead of starting a
    # new placement. Offset dragging remains a Select-mode interaction.
    if self._selectable and self._dimensions:
        di = self._find_dimension_at(pos.x(), pos.y())
        if di is not None:
            self._selected_dimension = di
            self._all_dimensions_selected = False
            self._sel.clear()
            if self._mode == "select" and self._dimensions[di].get("type") != "angle":
                # Offset dragging mutates the live document; snapshot first so
                # the whole gesture commits as one undoable command on release.
                self._dimension_drag_preview = self._canvas_service.begin_preview()
                self._dimension_drag = di
            else:
                self._dimension_drag = None
            self._notify()
            self._redraw()
            return
    if self._selected_dimension is not None:
        self._selected_dimension = None
        self._notify()
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
    # Cursor updates are intentionally separate from the page-wide status
    # refresh.  Rebuilding a layer tree on each mouse move made the bottom
    # readout stale unless another operation happened to refresh the page.
    self._queue_cursor_position_update()
    self._hover_snap = None
    self._hover_snap_type = None
    self._hover_snap_multi = []

    if self._bg_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
        mode, sx, sy, ox, oy, ow, oh, rotation = self._bg_drag
        if mode == "move":
            self._bg_x_mm, self._bg_y_mm = ox + wx - sx, oy + wy - sy
        elif mode == "rotate":
            center_x, center_y = ox + ow / 2.0, oy + oh / 2.0
            start_angle = math.degrees(math.atan2(sy - center_y, sx - center_x))
            current_angle = math.degrees(math.atan2(wy - center_y, wx - center_x))
            self._bg_rotation_deg = rotation + current_angle - start_angle
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                increment = self._rotation_snap_increment
                self._bg_rotation_deg = round(self._bg_rotation_deg / increment) * increment
        else:
            wx, wy = self._background_unrotate(wx, wy)
            left, right, bottom, top = ox, ox + ow, oy, oy + oh
            if "w" in mode:
                left = min(wx, right - 0.01)
            if "e" in mode:
                right = max(wx, left + 0.01)
            if "s" in mode:
                bottom = min(wy, top - 0.01)
            if "n" in mode:
                top = max(wy, bottom + 0.01)
            self._bg_x_mm, self._bg_y_mm = left, bottom
            self._bg_w_mm, self._bg_h_mm = right - left, top - bottom
        if callable(self._bg_edit_callback):
            self._bg_edit_callback(
                self._bg_x_mm,
                self._bg_y_mm,
                self._bg_w_mm,
                self._bg_h_mm,
                self._bg_rotation_deg,
            )
        self._bg_pixmap = None
        self._redraw()
        return

    if self._mmb_prev is not None and event.buttons() & Qt.MouseButton.MiddleButton:
        self._ox += pos.x() - self._mmb_prev.x()
        self._oy += pos.y() - self._mmb_prev.y()
        self._mmb_prev = pos
        self._redraw()
        return

    if (
        (self._space_pan_active or self._mode == "pan")
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

    if self._bg_drag is not None:
        self._bg_drag = None
        return

    if self._space_pan_active or self._mode == "pan":
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
        # Commit the whole gesture (add / move / delete) as one undoable
        # command; a click that changed nothing commits as a no-op.
        self._canvas_service.commit_preview(self._guide_preview)
        self._guide_preview = None
        self._redraw()
        return

    if self._dimension_drag is not None:
        self._dimension_drag = None
        # A click that changed nothing commits as a no-op.
        self._canvas_service.commit_preview(self._dimension_drag_preview)
        self._dimension_drag_preview = None
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
            eid = self._lmb_target
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                if eid in self._sel:
                    self._sel = self._sel - {eid}
                else:
                    self._sel = self._sel | {eid}
            else:
                self._sel = {eid}
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
    dimension = self._find_dimension_at(event.position().x(), event.position().y())
    if dimension is not None:
        self._selected_dimension = dimension

        if isinstance(self._dimensions[dimension].get("driving"), dict):
            self._edit_driving_dimension(dimension)
            return

        def set_precision(value: float) -> None:
            self._set_dimension_precision_value(dimension, int(round(value)))
            self._notify()

        self._show_hud_prompt(
            "Dimension decimals",
            float(self._dimensions[dimension].get("precision", 2)),
            set_precision,
            minimum=0,
            maximum=6,
        )
        return
    tool = self._tools.get(self._mode)
    if tool is not None:
        tool.double_click(event)


def _refresh_driving_dimensions(self) -> None:
    for dimension in self._dimensions:
        self._dimension_tool.refresh_driving_dimension(dimension)


# ── Undoable annotation edits ──────────────────────────────────────────
# Every guide/dimension mutation funnels through these so it becomes one
# entry on the same undo stack as geometry. They apply the change to a copy
# via the document command boundary (update_document → ReplaceDocumentCommand),
# which records a concrete inverse; there is no path that mutates annotations
# without producing a reversible command.


def _commit_annotation_edit(self, mutate) -> bool:
    result = self._canvas_service.update_document(mutate)
    if result.changed:
        self._refresh_driving_dimensions()
        self._redraw()
    return result.changed


def _append_dimension(self, dimension: dict) -> int:
    payload = deepcopy(dimension)
    self._commit_annotation_edit(lambda document: document.dimensions.append(deepcopy(payload)))
    return len(self._dimensions) - 1


def _remove_dimension(self, index: int) -> bool:
    if not (0 <= index < len(self._dimensions)):
        return False
    return self._commit_annotation_edit(lambda document: document.dimensions.pop(index))


def _clear_dimensions(self) -> bool:
    if not self._dimensions:
        return False
    return self._commit_annotation_edit(lambda document: document.dimensions.clear())


def _set_dimension_precision_value(self, index: int, precision: int) -> bool:
    if not (0 <= index < len(self._dimensions)):
        return False
    value = max(0, min(6, int(precision)))

    def mutate(document) -> None:
        document.dimensions[index]["precision"] = value

    return self._commit_annotation_edit(mutate)


# Adding a guide is not a discrete command: it is the start of a drag-out
# gesture (see the ruler press handler), committed as one preview
# transaction on mouse release so add-then-adjust is a single undo step.


def _remove_guide(self, index: int) -> bool:
    if not (0 <= index < len(self._guides)):
        return False
    return self._commit_annotation_edit(lambda document: document.guides.pop(index))


def _edit_driving_dimension(self, index: int) -> None:
    if not (0 <= index < len(self._dimensions)):
        return
    angular = self._dimensions[index].get("type") == "angle"

    def set_driving_value(value: float) -> None:
        if not self._dimension_tool.set_value(index, value):
            self._show_flash("This driving dimension could not update its geometry", 1800)

    self._show_hud_prompt(
        "Target angle" if angular else "Target measurement",
        self._dimension_tool.value(self._dimensions[index]),
        set_driving_value,
        minimum=0.001,
        is_length=not angular,
    )


def _set_dimension_precision(self, index: int, precision: int) -> None:
    if self._set_dimension_precision_value(index, precision):
        self._notify()
