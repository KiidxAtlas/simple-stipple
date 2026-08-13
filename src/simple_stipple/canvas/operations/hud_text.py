"""HUD widget and text operation services composed by the canvas view."""

from __future__ import annotations

import logging
import math
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QLabel, QLineEdit, QSpinBox, QWidget

from simple_stipple.core.document.model import EntityRecord
from simple_stipple.platform.settings import user_data_dir
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.units import (
    parse_numeric_expression as _parse_expression,
)
from simple_stipple.ui.components.units import (
    suffix as _unit_suffix,
)
from simple_stipple.ui.components.units import (
    to_display as _to_display,
)

LOGGER = logging.getLogger(__name__)


class HudTextService:
    """Own transient HUD widgets and dimension input state."""

    def __init__(self, host) -> None:
        self._host = host

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        """Show a brief flash indicator on the canvas."""
        from simple_stipple.ui.components.feedback import record_notification

        record_notification(text)
        settings = getattr(self._host, "_settings", {})
        if settings.get("persistent_notifications"):
            duration_ms = max(duration_ms, 5000)
        elif settings.get("reduced_motion"):
            duration_ms = min(duration_ms, 700)
        self._host._flash_text = text
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            self._host._flash_anchor_c = self._host._w2c(
                self._host._cursor_wx, self._host._cursor_wy
            )
        else:
            bounds = self._host._selection_bounds()
            self._host._flash_anchor_c = (
                self._host._w2c((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
                if bounds is not None
                else None
            )
        if self._host._flash_timer is not None:
            self._host._flash_timer.stop()
        self._host._flash_timer = QTimer(cast("QWidget", self._host))
        self._host._flash_timer.setSingleShot(True)
        self._host._flash_timer.timeout.connect(self._clear_flash)
        self._host._flash_timer.start(duration_ms)
        self._host._redraw()

    def _clear_flash(self) -> None:
        self._host._flash_text = None
        self._host._flash_anchor_c = None
        self._host._flash_timer = None
        self._host._redraw()

    # ── Auto-dimension HUD (Fusion 360 style) ──────────────────────────────

    def _make_hud_edit(
        self,
        placeholder: str = "",
        width: int = 80,
        height: int = 26,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter,
    ) -> QLineEdit:
        """Create a styled HUD QLineEdit parented to the canvas.

        Redesigned for better visibility and usability:
        - Larger, more touchable targets (26px height)
        - Modern dark theme with subtle borders
        - Monospace font for precise number reading
        """
        edit = QLineEdit(cast("QWidget", self._host))
        edit.setFixedWidth(max(width, 76))
        edit.setFixedHeight(max(height, 30))
        edit.setAlignment(align)
        edit.setProperty("role", "canvas-hud-input")
        edit.setAccessibleName(placeholder or "Canvas numeric input")
        edit.setAccessibleDescription("Type a value, press Enter to apply, or Escape to cancel")

        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.textEdited.connect(lambda _text: self._clear_hud_error(edit))
        edit.installEventFilter(cast("QWidget", self._host))
        edit.show()
        return edit

    def _make_hud_spinbox(
        self,
        *,
        minimum: int,
        maximum: int,
        value: int,
        width: int = 86,
        height: int = 24,
    ) -> QSpinBox:
        """Create a styled HUD QSpinBox (native up/down arrows + typing +
        Up/Down keys) parented to the canvas, matching _make_hud_edit's
        look. Used for live-adjustable integer parameters like polygon
        side count, where the built-in valueChanged signal gives a
        step-and-see-it-update interaction for free."""
        spin = QSpinBox(cast("QWidget", self._host))
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setFixedWidth(max(width, 76))
        spin.setFixedHeight(max(height, 30))
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setProperty("role", "canvas-hud-input")
        spin.setAccessibleName("Canvas numeric stepper")
        spin.setAccessibleDescription("Type a value or use the arrow keys to adjust it")
        spin.installEventFilter(cast("QWidget", self._host))
        spin.show()
        return spin

    @staticmethod
    def _clear_hud_error(widget: QLineEdit) -> None:
        """Clear stale validation chrome as soon as the user corrects input."""
        if widget.property("error"):
            widget.setProperty("error", False)
            refresh_style(widget)

    def _show_hud_prompt(
        self,
        label: str,
        default: float,
        callback,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        is_length: bool = True,
        preview=None,
    ) -> None:
        """Inline numeric prompt anchored to the active drawing context: Enter commits,
        Escape dismisses. Replaces modal QInputDialog for canvas ops.

        ``is_length`` marks *default*/the parsed value as an mm length that
        should round-trip through the active display unit (mm/in) — pass
        ``False`` for non-length values (angles, counts) so they're shown
        and returned as-is.
        """
        self._dismiss_hud_prompt()
        unit = self._host._unit_system if is_length else None
        display_label = label.replace("mm", _unit_suffix(unit)) if unit else label
        display_default = _to_display(default, unit) if unit else default
        edit = self._make_hud_edit(placeholder="Value", width=180, height=32)
        edit.setText(f"{display_default:g}")
        edit.selectAll()
        edit.setToolTip(display_label)
        x, y = self._context_hud_position(180, 52)
        label_widget = QLabel(display_label, cast("QWidget", self._host))
        label_widget.setProperty("role", "canvas-hud-label")
        label_widget.setFixedSize(180, 18)
        label_widget.move(x, y)
        label_widget.show()
        edit.move(x, y + 20)
        self._host._hud_prompt_edit = edit
        self._host._hud_prompt_label = label_widget
        self._host._show_flash(display_label, 1600)

        def _preview(text: str) -> None:
            if preview is None:
                return
            try:
                value = _parse_expression(text, unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                self._host._clear_operation_preview()
                return
            if minimum is not None and value < minimum:
                self._host._clear_operation_preview()
                return
            if maximum is not None and value > maximum:
                self._host._clear_operation_preview()
                return
            preview(value)

        if preview is not None:
            edit.textChanged.connect(_preview)
            _preview(edit.text())

        def _reject(message: str) -> None:
            # Keep the prompt open and flag it — silently vanishing made bad
            # input indistinguishable from success.
            edit.setProperty("error", True)
            edit.setToolTip(message)
            refresh_style(edit)
            self._host._show_flash(message, 1400)
            edit.selectAll()

        def _commit() -> None:
            try:
                value = _parse_expression(edit.text(), unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                _reject("Enter a valid number or expression")
                return
            if minimum is not None and value < minimum:
                _reject(
                    f"Value must be at least {_to_display(minimum, unit) if unit else minimum:g}"
                )
                return
            if maximum is not None and value > maximum:
                _reject(
                    f"Value must be at most {_to_display(maximum, unit) if unit else maximum:g}"
                )
                return
            self._dismiss_hud_prompt()
            callback(value)

        edit.returnPressed.connect(_commit)
        edit.setFocus()

    def _show_text_hud_prompt(
        self,
        label: str,
        callback,
        *,
        initial: str = "",
        width: int = 190,
    ) -> None:
        """Inline non-numeric prompt whose callback may raise ValueError."""
        self._dismiss_hud_prompt()
        edit = self._make_hud_edit(placeholder="Value", width=width, height=24)
        edit.setText(initial)
        x, y = self._context_hud_position(width, 44)
        label_widget = QLabel(label, cast("QWidget", self._host))
        label_widget.setProperty("role", "canvas-hud-label")
        label_widget.setFixedSize(width, 18)
        label_widget.move(x, y)
        label_widget.show()
        edit.move(x, y + 20)
        edit.setToolTip(label)
        self._host._hud_prompt_edit = edit
        self._host._hud_prompt_label = label_widget
        self._host._show_flash(label, 1800)

        def _commit() -> None:
            try:
                callback(edit.text().strip())
            except ValueError as exc:
                edit.setProperty("error", True)
                edit.setToolTip(str(exc))
                refresh_style(edit)
                self._host._show_flash(str(exc), 1400)
                edit.selectAll()
                return
            self._dismiss_hud_prompt()

        edit.returnPressed.connect(_commit)
        edit.setFocus()

    def _context_hud_position(self, width: int, height: int) -> tuple[int, int]:
        """Anchor prompts near cursor/selection while keeping them on canvas."""
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            anchor_x, anchor_y = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
        else:
            bounds = self._host._selection_bounds()
            if bounds is not None:
                anchor_x, anchor_y = self._host._w2c(
                    (bounds[0] + bounds[2]) / 2.0,
                    (bounds[1] + bounds[3]) / 2.0,
                )
            else:
                anchor_x, anchor_y = self._host.width() / 2.0, self._host.height() / 2.0
        return self._hud_position_near(anchor_x, anchor_y, width, height)

    def _hud_position_near(
        self,
        anchor_x: float,
        anchor_y: float,
        width: int,
        height: int,
        *,
        offset_x: int = 20,
        offset_y: int = 18,
    ) -> tuple[int, int]:
        """Place a world-context control near its anchor without clipping."""
        x = max(8, min(int(anchor_x + offset_x), max(8, self._host.width() - width - 8)))
        y = max(8, min(int(anchor_y + offset_y), max(8, self._host.height() - height - 8)))
        return x, y

    def _dismiss_hud_prompt(self) -> None:
        edit = getattr(self._host, "_hud_prompt_edit", None)
        if edit is not None:
            edit.deleteLater()
        self._host._hud_prompt_edit = None
        label_widget = getattr(self._host, "_hud_prompt_label", None)
        if label_widget is not None:
            label_widget.deleteLater()
        self._host._hud_prompt_label = None
        self._host._clear_operation_preview()

    def _show_dim_inputs(self) -> None:
        """Create both distance and angle QLineEdits that float near the cursor."""
        self._dismiss_dim_inputs()
        if not self._host._draw_pts:
            return

        dist_label = QLabel(
            f"Length ({_unit_suffix(self._host._unit_system)})",
            cast("QWidget", self._host),
        )
        dist_label.setProperty("role", "canvas-hud-label")
        dist_label.setFixedSize(92, 16)
        dist_label.show()
        self._host._dim_distance_label = dist_label

        dist_edit = self._make_hud_edit("Length", 92)
        dist_edit.setAccessibleDescription("Next segment length in the active unit")
        dist_edit.returnPressed.connect(self._apply_dim_input)
        # textEdited fires only on user keystrokes (not setText), so the dirty
        # flag tracks genuine typing; clearing the field resumes live updates.
        dist_edit.textEdited.connect(
            lambda t: setattr(self._host, "_dim_distance_dirty", bool(t.strip()))
        )
        self._host._dim_distance_edit = dist_edit
        self._host._dim_distance_dirty = False

        angle_label = QLabel("Angle (°)", cast("QWidget", self._host))
        angle_label.setProperty("role", "canvas-hud-label")
        angle_label.setFixedSize(92, 16)
        angle_label.show()
        self._host._dim_angle_label = angle_label

        angle_edit = self._make_hud_edit("Angle", 92)
        angle_edit.setAccessibleDescription("Next segment angle in degrees")
        angle_edit.returnPressed.connect(self._apply_dim_input)
        angle_edit.textEdited.connect(
            lambda t: setattr(self._host, "_dim_angle_dirty", bool(t.strip()))
        )
        self._host._dim_angle_edit = angle_edit
        self._host._dim_angle_dirty = False

        # Position immediately at the current cursor — otherwise the fields
        # flash at the canvas origin (0, 0) until the next mouse-move event.
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            cx, cy = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
            self._update_dim_positions(cx, cy)

    def _dismiss_dim_inputs(self) -> None:
        """Remove the auto-dimension HUD widgets."""
        if self._host._dim_distance_edit is not None:
            self._host._dim_distance_edit.hide()
            self._host._dim_distance_edit.deleteLater()
            self._host._dim_distance_edit = None
        if self._host._dim_angle_edit is not None:
            self._host._dim_angle_edit.hide()
            self._host._dim_angle_edit.deleteLater()
            self._host._dim_angle_edit = None
        for attr in ("_dim_distance_label", "_dim_angle_label"):
            label = getattr(self._host, attr, None)
            if label is not None:
                label.hide()
                label.deleteLater()
                setattr(self._host, attr, None)
        self._host._dim_distance_dirty = False
        self._host._dim_angle_dirty = False

    # ── Inline selection-badge dimension editor ───────────────────────────────

    def _show_sel_dim_editor(self, axis: str, rect: QRectF) -> None:
        """Show a floating QLineEdit over a selection badge for direct editing.

        ``axis`` is "w"/"h" (bounding-box size) or, for a single selected
        2-point line, "l" (length) / "a" (absolute angle in degrees).
        """
        self._dismiss_sel_dim_editor()
        if axis in ("l", "a"):
            entity_id = self._host._selected_single_line()
            if entity_id is None:
                return
            entity = self._host._entities_by_id[entity_id]
            (ax, ay), (bx, by) = entity.points
            if axis == "l":
                cur_val = math.hypot(bx - ax, by - ay)
            else:
                cur_val = math.degrees(math.atan2(by - ay, bx - ax))
        else:
            bounds = self._host._selection_bounds()
            if bounds is None:
                return
            x0, y0, x1, y1 = bounds
            cur_val = (x1 - x0) if axis == "w" else (y1 - y0)

        edit = self._make_hud_edit(
            width=max(int(rect.width()) + 20, 112),
            height=32,
            align=Qt.AlignmentFlag.AlignCenter,
        )
        if axis == "a":
            edit.setText(f"{cur_val:.2f}")
        else:
            edit.setText(f"{_to_display(cur_val, self._host._unit_system):.2f}")
        edit.selectAll()
        # Keep the editor registered with the badge it replaces, but never
        # force the user to chase a clipped field beyond the canvas edge.
        edit_x = max(8, min(int(rect.x()), max(8, self._host.width() - edit.width() - 8)))
        edit_y = max(8, min(int(rect.y()), max(8, self._host.height() - edit.height() - 8)))
        edit.move(edit_x, edit_y)
        edit.setFocus()
        edit.returnPressed.connect(lambda: self._apply_sel_dim_editor())
        edit.editingFinished.connect(lambda: self._apply_sel_dim_editor())
        self._host._sel_dim_edit = edit
        self._host._sel_dim_axis = axis

    def _apply_sel_dim_editor(self) -> None:
        if self._host._sel_dim_edit is None or self._host._sel_dim_axis is None:
            return
        text = self._host._sel_dim_edit.text().strip()
        axis = self._host._sel_dim_axis
        # Disconnect editingFinished before dismissing to avoid double-trigger
        try:
            self._host._sel_dim_edit.editingFinished.disconnect()
        except RuntimeError as exc:
            # Qt raises when the editor was already disconnected during
            # teardown; dismissal is still safe and must continue.
            LOGGER.debug("Selection editor was already disconnected: %s", exc)
        self._dismiss_sel_dim_editor()
        if not text:
            return
        try:
            val = _parse_expression(text, self._host._unit_system, is_length=axis != "a")
        except ValueError:
            self._host._show_flash("Enter a valid number or expression", 1200)
            return
        if axis == "a":
            # Absolute angle: any value is valid (normalized by trig)
            self._host._set_selected_line_angle(val)
            self._host._show_flash("Angle updated", 900)
            return
        if val <= 0:
            self._host._show_flash("Value must be greater than zero", 1200)
            return
        if axis == "w":
            self._host._set_selected_width(val)
        elif axis == "h":
            self._host._set_selected_height(val)
        elif axis == "l":
            self._host._set_selected_line_length(val)
        self._host._show_flash("Dimension updated", 900)

    def _dismiss_sel_dim_editor(self) -> None:
        if self._host._sel_dim_edit is not None:
            self._host._sel_dim_edit.hide()
            self._host._sel_dim_edit.deleteLater()
            self._host._sel_dim_edit = None
        self._host._sel_dim_axis = None

    def _update_dim_positions(self, cx: float, cy: float) -> None:
        """Move the dim input widgets near cursor, avoiding snap label overlap.

        Positions the fields below-right of cursor with enough clearance so
        snap indicator icons and labels (drawn at +18, +4 from snap point)
        never get covered.
        """
        vw = max(self._host.width(), 100)
        vh = max(self._host.height(), 100)
        # Default: below-right of cursor
        dx, dy = 28, 22
        # If near right edge, flip to left side
        if cx + dx + 92 > vw:
            dx = -112
        # If near bottom edge, flip above
        if cy + dy + 76 > vh:
            dy = -76
        x = int(cx + dx)
        y = int(cy + dy)
        if self._host._dim_distance_label is not None:
            self._host._dim_distance_label.move(x, y)
        if self._host._dim_distance_edit is not None:
            self._host._dim_distance_edit.move(x, y + 16)
        if self._host._dim_angle_label is not None:
            self._host._dim_angle_label.move(x, y + 44)
        if self._host._dim_angle_edit is not None:
            self._host._dim_angle_edit.move(x, y + 60)

    def _update_dim_values(self, distance: float, angle: float) -> None:
        """Update displayed values in the dim inputs, unless user has typed.

        When a field is focused but untouched, keep its text selected so the
        next keystroke replaces the live value instead of appending to it.
        """
        if self._host._dim_distance_edit is not None and not self._host._dim_distance_dirty:
            # Display units: _apply_dim_input parses this text back with the
            # unit-aware parser, so raw mm here would commit 25.4× too far
            # in inch mode.
            self._host._dim_distance_edit.setText(
                f"{_to_display(distance, self._host._unit_system):.2f}"
            )
            if self._host._dim_distance_edit.hasFocus():
                self._host._dim_distance_edit.selectAll()
        if self._host._dim_angle_edit is not None and not self._host._dim_angle_dirty:
            self._host._dim_angle_edit.setText(f"{angle:.1f}")
            if self._host._dim_angle_edit.hasFocus():
                self._host._dim_angle_edit.selectAll()

    def _typed_draw_angle(self) -> float | None:
        """Return the user-typed segment angle (deg) if the angle field is dirty.

        Returns ``None`` when the field is auto-populated (not dirty) or does not
        parse, so callers only lock to a value the user explicitly entered.
        """
        if not getattr(self._host, "_dim_angle_dirty", False):
            return None
        if self._host._dim_angle_edit is None:
            return None
        text = self._host._dim_angle_edit.text().strip()
        if not text:
            return None
        try:
            return _parse_expression(text, is_length=False)
        except ValueError:
            return None

    def _typed_draw_distance(self) -> float | None:
        """Return the user-typed segment length (mm) if the distance field is dirty.

        Uses the same unit-aware parser as the commit path so the live
        rubber-band preview matches where Enter will actually place the point.
        """
        if not getattr(self._host, "_dim_distance_dirty", False):
            return None
        if self._host._dim_distance_edit is None:
            return None
        text = self._host._dim_distance_edit.text().strip()
        if not text:
            return None
        try:
            return _parse_expression(text, self._host._unit_system, is_length=True)
        except ValueError:
            return None

    def _apply_dim_input(self) -> None:
        """Read distance/angle from the HUD fields and place a point."""
        if not self._host._draw_pts:
            return
        last_wx, last_wy = self._host._draw_pts[-1]
        try:
            dist_text = (
                self._host._dim_distance_edit.text().strip()
                if self._host._dim_distance_edit
                else ""
            )
            angle_text = (
                self._host._dim_angle_edit.text().strip() if self._host._dim_angle_edit else ""
            )
            if angle_text:
                angle_deg = _parse_expression(angle_text, is_length=False)
            elif self._host._cursor_wx is not None and self._host._cursor_wy is not None:
                angle_deg = math.degrees(
                    math.atan2(
                        self._host._cursor_wy - last_wy,
                        self._host._cursor_wx - last_wx,
                    )
                )
            else:
                angle_deg = 0.0
            if dist_text:
                dist = _parse_expression(dist_text, self._host._unit_system, is_length=True)
            elif self._host._cursor_wx is not None and self._host._cursor_wy is not None:
                # Angle-only entry: project the cursor onto the typed-angle ray
                # so the length still tracks the pointer.
                ar = math.radians(angle_deg)
                vx = self._host._cursor_wx - last_wx
                vy = self._host._cursor_wy - last_wy
                dist = max(0.0, vx * math.cos(ar) + vy * math.sin(ar))
            else:
                return
            if dist <= 0:
                return
            angle_rad = math.radians(angle_deg)
            new_x = last_wx + dist * math.cos(angle_rad)
            new_y = last_wy + dist * math.sin(angle_rad)
            self._host._draw_pts.append((new_x, new_y))
            # Reset dirty flags so fields resume auto-updating
            self._host._dim_distance_dirty = False
            self._host._dim_angle_dirty = False
            self._host._refresh_draw_sidebar_state()
            self._host._redraw()
        except ValueError:
            self._host._show_flash("Enter a valid distance and angle", 1000)

    # ── Inference / alignment lines ──────────────────────────────────────────

    def _show_measure_edit(self) -> None:
        """Show a QLineEdit overlay for editing the measured distance."""
        self._dismiss_measure_edit()
        if not self._host._measure_anchor or not self._host._measure_end:
            return
        ax, ay = self._host._measure_anchor
        hx, hy = self._host._measure_end
        dist = math.hypot(hx - ax, hy - ay)
        cax, cay = self._host._w2c(ax, ay)
        chx, chy = self._host._w2c(hx, hy)
        mx, my = (cax + chx) / 2, (cay + chy) / 2

        le = self._make_hud_edit(width=180, height=32)
        display_dist = _to_display(dist, self._host._unit_system)
        le.setText(f"{display_dist:.4g}")
        le.setPlaceholderText(f"Target distance ({_unit_suffix(self._host._unit_system)})")
        le.setToolTip(
            "Enter the real target distance. The first picked point remains fixed.\n"
            "Expressions such as 25.4/2 are accepted."
        )
        le.setAccessibleName("Measure target distance")
        le.move(
            *self._hud_position_near(
                mx,
                my,
                180,
                32,
                offset_x=-90,
                offset_y=-44,
            )
        )
        le.setFocus()
        le.selectAll()
        le.returnPressed.connect(self._apply_measure_scale)
        self._host._measure_edit = le

    def _dismiss_measure_edit(self) -> None:
        """Remove the measure distance QLineEdit overlay."""
        if self._host._measure_edit is not None:
            self._host._measure_edit.hide()
            self._host._measure_edit.deleteLater()
            self._host._measure_edit = None

    def _apply_measure_scale(self) -> None:
        """Read new distance from the edit overlay and scale all polylines."""
        if (
            not self._host._measure_edit
            or not self._host._measure_anchor
            or not self._host._measure_end
        ):
            self._dismiss_measure_edit()
            return
        try:
            new_dist = _parse_expression(
                self._host._measure_edit.text(),
                self._host._unit_system,
                is_length=True,
            )
        except ValueError:
            self._host._show_flash("Enter a positive target distance", 1400)
            self._host._measure_edit.setFocus()
            self._host._measure_edit.selectAll()
            return
        ax, ay = self._host._measure_anchor
        hx, hy = self._host._measure_end
        old_dist = math.hypot(hx - ax, hy - ay)
        if old_dist < 1e-9 or new_dist <= 0:
            self._host._show_flash("Target distance must be greater than zero", 1400)
            self._host._measure_edit.setFocus()
            self._host._measure_edit.selectAll()
            return
        factor = new_dist / old_dist
        if not math.isfinite(factor) or factor > 1_000_000:
            self._host._show_flash("Scale factor is outside the supported range", 1600)
            return
        if abs(factor - 1.0) <= 1e-12:
            self._host._show_flash("Target matches the reference; nothing changed", 1200)
        elif not self._host.scale_by_reference(factor, self._host._measure_anchor):
            self._host._show_flash("Nothing available to scale", 1400)
            return
        else:
            self._host._show_flash(
                f"Scaled by {factor:.4g}× · Undo restores the previous size", 1800
            )
        self._dismiss_measure_edit()
        self._host._measure_locked = False
        self._host._measure_anchor = None
        self._host._measure_hover = None
        self._host._measure_end = None
        self._host._measure_snapped_a = False
        self._host._measure_snapped_b = False
        self._host._redraw()


# ════════════════════════════════════════════════════════════════════════════
# Text-on-path placement/editing
# ════════════════════════════════════════════════════════════════════════════

# ── Text-to-polyline conversion (previously text_shapes.py) ──────────────────


# Render glyphs at a large pixel size, then scale to mm — keeps curve
# flattening smooth regardless of the requested text height.


# ── Text document operations ─────────────────────────────────────────────────


