"""HudTextMixin family — floating HUD prompt/editor widgets and
text-on-path operations for PolylineView.

Two previously-separate mixins merged here (``HudMixin``, ``TextOpsMixin``)
— both manage small floating overlay widgets on top of the canvas (numeric
input prompts, dimension editors, text placement/editing), each
individually small enough that a dedicated file didn't pay for itself.

PolylineView inherits these via
``class PolylineView(QWidget, CanvasRenderer, ..., HudMixin,
TextOpsMixin)``. Since methods are resolved through the normal MRO, every
``self.*`` reference works without modification — same pattern as
``CanvasRenderer`` in ``render.py``.

Extracted from ``view.py``/``render.py`` originally as part of shrinking
those files. Every method here was verified to have zero external callers
other than ``self``/other-mixin references before each move (the whole-
codebase grep this repo's git history shows a prior "mixin-inlining"
refactor silently dropped ~40 still-referenced methods — see commit
9a7d3a5 — so this file exists specifically to NOT repeat that).
"""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QLineEdit, QSpinBox

from src.infra.paths import user_data_dir
from src.ui.util import parse_numeric_expression as _parse_expression
from src.ui.util import suffix as _unit_suffix
from src.ui.util import to_display as _to_display

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Protocol

    from PySide6.QtWidgets import QWidget

    class _HudHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``render.py``'s ``_RendererHost`` for why this
        exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _entities: list[Any]
        _unit_system: str
        _draw_pts: list[tuple[float, float]]
        _cursor_wx: float | None
        _cursor_wy: float | None
        _flash_text: str | None
        _flash_timer: QTimer | None
        _hud_prompt_edit: QLineEdit | None
        _dim_distance_edit: QLineEdit | None
        _dim_angle_edit: QLineEdit | None
        _dim_distance_dirty: bool
        _dim_angle_dirty: bool
        _sel_dim_edit: QLineEdit | None
        _sel_dim_axis: str | None
        _measure_anchor: tuple[float, float] | None
        _measure_end: tuple[float, float] | None
        _measure_hover: tuple[float, float] | None
        _measure_edit: QLineEdit | None
        _measure_locked: bool
        _measure_snapped_a: bool
        _measure_snapped_b: bool

        def width(self) -> int: ...
        def height(self) -> int: ...
        def _redraw(self) -> None: ...
        def _clear_operation_preview(self) -> None: ...
        def _w2c(self, x: float, y: float) -> tuple[float, float]: ...
        def _selection_bounds(
            self,
        ) -> tuple[float, float, float, float] | None: ...
        def _selected_single_line(self) -> int | None: ...
        def _set_selected_width(self, width: float) -> bool: ...
        def _set_selected_height(self, height: float) -> bool: ...
        def _set_selected_line_length(self, length: float) -> bool: ...
        def _set_selected_line_angle(self, angle_deg: float) -> bool: ...
        def _refresh_draw_sidebar_state(self) -> None: ...
        def _scale_all(self, factor: float) -> None: ...

    _HudBase = _HudHost
else:
    _HudBase = object


class HudMixin(_HudBase):
    """Mixin providing flash messages and floating HUD editors for
    :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        """Show a brief flash indicator on the canvas."""
        from src.ui.util import record_notification

        record_notification(text)
        settings = getattr(self, "_settings", {})
        if settings.get("persistent_notifications"):
            duration_ms = max(duration_ms, 5000)
        elif settings.get("reduced_motion"):
            duration_ms = min(duration_ms, 700)
        self._flash_text = text
        if self._cursor_wx is not None and self._cursor_wy is not None:
            self._flash_anchor_c = self._w2c(self._cursor_wx, self._cursor_wy)
        else:
            bounds = self._selection_bounds()
            self._flash_anchor_c = (
                self._w2c((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
                if bounds is not None
                else None
            )
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(cast("QWidget", self))
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)
        self._flash_timer.start(duration_ms)
        self._redraw()

    def _clear_flash(self) -> None:
        self._flash_text = None
        self._flash_anchor_c = None
        self._flash_timer = None
        self._redraw()

    # ── Auto-dimension HUD (Fusion 360 style) ──────────────────────────────

    _DIM_STYLE = (
        "background: #161b22; color: #f0f6fc; border: 1px solid #30363d;"
        "border-radius: 6px; font-size: 12px; font-family: Menlo, Courier;"
        "padding: 3px 6px;"
    )
    _DIM_STYLE_HOVER = (
        "background: #1c2128; color: #f0f6fc; border: 1px solid #58a6ff;"
        "border-radius: 6px; font-size: 12px; font-family: Menlo, Courier;"
        "padding: 3px 6px;"
    )

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
        edit = QLineEdit(cast("QWidget", self))
        edit.setFixedWidth(max(width, 60))
        edit.setFixedHeight(height)
        edit.setAlignment(align)
        edit.setStyleSheet(self._DIM_STYLE)

        # Store hover style for focus events.
        edit.setProperty("_dim_hover_style", self._DIM_STYLE_HOVER)

        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.installEventFilter(cast("QWidget", self))
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
        spin = QSpinBox(cast("QWidget", self))
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setFixedWidth(max(width, 60))
        spin.setFixedHeight(height)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setStyleSheet(self._DIM_STYLE)
        spin.show()
        return spin

    def _show_hud_prompt(
        self,
        label: str,
        default: float,
        callback,
        *,
        minimum: float | None = None,
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
        unit = self._unit_system if is_length else None
        display_label = label.replace("mm", _unit_suffix(unit)) if unit else label
        display_default = _to_display(default, unit) if unit else default
        edit = self._make_hud_edit(placeholder=display_label, width=120, height=22)
        edit.setText(f"{display_default:g}")
        edit.selectAll()
        edit.setToolTip(display_label)
        edit.move(*self._context_hud_position(120, 22))
        self._hud_prompt_edit = edit
        self._show_flash(display_label, 1600)

        def _preview(text: str) -> None:
            if preview is None:
                return
            try:
                value = _parse_expression(text, unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                self._clear_operation_preview()
                return
            if minimum is not None and value < minimum:
                self._clear_operation_preview()
                return
            preview(value)

        if preview is not None:
            edit.textChanged.connect(_preview)
            _preview(edit.text())

        def _commit() -> None:
            try:
                value = _parse_expression(edit.text(), unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                self._dismiss_hud_prompt()
                return
            if minimum is not None and value < minimum:
                self._dismiss_hud_prompt()
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
        edit = self._make_hud_edit(placeholder=label, width=width, height=24)
        edit.setText(initial)
        edit.move(*self._context_hud_position(width, 24))
        edit.setToolTip(label)
        self._hud_prompt_edit = edit
        self._show_flash(label, 1800)

        def _commit() -> None:
            try:
                callback(edit.text().strip())
            except ValueError as exc:
                edit.setProperty("invalid", True)
                edit.setToolTip(str(exc))
                edit.style().unpolish(edit)
                edit.style().polish(edit)
                self._show_flash(str(exc), 1400)
                edit.selectAll()
                return
            self._dismiss_hud_prompt()

        edit.returnPressed.connect(_commit)
        edit.setFocus()

    def _context_hud_position(self, width: int, height: int) -> tuple[int, int]:
        """Anchor prompts near cursor/selection while keeping them on canvas."""
        if self._cursor_wx is not None and self._cursor_wy is not None:
            anchor_x, anchor_y = self._w2c(self._cursor_wx, self._cursor_wy)
        else:
            bounds = self._selection_bounds()
            if bounds is not None:
                anchor_x, anchor_y = self._w2c(
                    (bounds[0] + bounds[2]) / 2.0,
                    (bounds[1] + bounds[3]) / 2.0,
                )
            else:
                anchor_x, anchor_y = self.width() / 2.0, self.height() / 2.0
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
        x = max(8, min(int(anchor_x + offset_x), max(8, self.width() - width - 8)))
        y = max(8, min(int(anchor_y + offset_y), max(8, self.height() - height - 8)))
        return x, y

    def _dismiss_hud_prompt(self) -> None:
        edit = getattr(self, "_hud_prompt_edit", None)
        if edit is not None:
            edit.deleteLater()
        self._hud_prompt_edit = None
        self._clear_operation_preview()

    def _show_dim_inputs(self) -> None:
        """Create both distance and angle QLineEdits that float near the cursor."""
        self._dismiss_dim_inputs()
        if not self._draw_pts:
            return

        dist_edit = self._make_hud_edit("d:", 70)
        dist_edit.returnPressed.connect(self._apply_dim_input)
        # textEdited fires only on user keystrokes (not setText), so the dirty
        # flag tracks genuine typing; clearing the field resumes live updates.
        dist_edit.textEdited.connect(
            lambda t: setattr(self, "_dim_distance_dirty", bool(t.strip()))
        )
        self._dim_distance_edit = dist_edit
        self._dim_distance_dirty = False

        angle_edit = self._make_hud_edit("∠:", 55)
        angle_edit.returnPressed.connect(self._apply_dim_input)
        angle_edit.textEdited.connect(lambda t: setattr(self, "_dim_angle_dirty", bool(t.strip())))
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

    # ── Inline selection-badge dimension editor ───────────────────────────────

    def _show_sel_dim_editor(self, axis: str, rect: QRectF) -> None:
        """Show a floating QLineEdit over a selection badge for direct editing.

        ``axis`` is "w"/"h" (bounding-box size) or, for a single selected
        2-point line, "l" (length) / "a" (absolute angle in degrees).
        """
        self._dismiss_sel_dim_editor()
        if axis in ("l", "a"):
            line_idx = self._selected_single_line()
            if line_idx is None:
                return
            (ax, ay), (bx, by) = self._entities[line_idx].points
            if axis == "l":
                cur_val = math.hypot(bx - ax, by - ay)
            else:
                cur_val = math.degrees(math.atan2(by - ay, bx - ax))
        else:
            bounds = self._selection_bounds()
            if bounds is None:
                return
            x0, y0, x1, y1 = bounds
            cur_val = (x1 - x0) if axis == "w" else (y1 - y0)

        edit = self._make_hud_edit(
            width=max(int(rect.width()) + 10, 70),
            height=22,
            align=Qt.AlignmentFlag.AlignCenter,
        )
        edit.setText(f"{cur_val:.3f}")
        edit.selectAll()
        # Keep the editor registered with the badge it replaces, but never
        # force the user to chase a clipped field beyond the canvas edge.
        edit_x = max(8, min(int(rect.x()), max(8, self.width() - edit.width() - 8)))
        edit_y = max(8, min(int(rect.y()), max(8, self.height() - edit.height() - 8)))
        edit.move(edit_x, edit_y)
        edit.setFocus()
        edit.returnPressed.connect(lambda: self._apply_sel_dim_editor())
        edit.editingFinished.connect(lambda: self._apply_sel_dim_editor())
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
        except RuntimeError as exc:
            # Qt raises when the editor was already disconnected during
            # teardown; dismissal is still safe and must continue.
            LOGGER.debug("Selection editor was already disconnected: %s", exc)
        self._dismiss_sel_dim_editor()
        try:
            val = float(text)
        except ValueError:
            return
        if axis == "a":
            # Absolute angle: any value is valid (normalized by trig)
            self._set_selected_line_angle(val)
            self._show_flash("Angle updated", 900)
            return
        if val <= 0:
            return
        if axis == "w":
            self._set_selected_width(val)
        elif axis == "h":
            self._set_selected_height(val)
        elif axis == "l":
            self._set_selected_line_length(val)
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
        vw = max(self.width(), 100)
        vh = max(self.height(), 100)
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
        """Update displayed values in the dim inputs, unless user has typed.

        When a field is focused but untouched, keep its text selected so the
        next keystroke replaces the live value instead of appending to it.
        """
        if self._dim_distance_edit is not None and not self._dim_distance_dirty:
            self._dim_distance_edit.setText(f"{distance:.2f}")
            if self._dim_distance_edit.hasFocus():
                self._dim_distance_edit.selectAll()
        if self._dim_angle_edit is not None and not self._dim_angle_dirty:
            self._dim_angle_edit.setText(f"{angle:.1f}")
            if self._dim_angle_edit.hasFocus():
                self._dim_angle_edit.selectAll()

    def _typed_draw_angle(self) -> float | None:
        """Return the user-typed segment angle (deg) if the angle field is dirty.

        Returns ``None`` when the field is auto-populated (not dirty) or does not
        parse, so callers only lock to a value the user explicitly entered.
        """
        if not getattr(self, "_dim_angle_dirty", False):
            return None
        if self._dim_angle_edit is None:
            return None
        text = self._dim_angle_edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _typed_draw_distance(self) -> float | None:
        """Return the user-typed segment length if the distance field is dirty."""
        if not getattr(self, "_dim_distance_dirty", False):
            return None
        if self._dim_distance_edit is None:
            return None
        text = self._dim_distance_edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _apply_dim_input(self) -> None:
        """Read distance/angle from the HUD fields and place a point."""
        if not self._draw_pts:
            return
        last_wx, last_wy = self._draw_pts[-1]
        try:
            dist_text = self._dim_distance_edit.text().strip() if self._dim_distance_edit else ""
            angle_text = self._dim_angle_edit.text().strip() if self._dim_angle_edit else ""
            if angle_text:
                angle_deg = _parse_expression(angle_text, is_length=False)
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                angle_deg = math.degrees(
                    math.atan2(
                        self._cursor_wy - last_wy,
                        self._cursor_wx - last_wx,
                    )
                )
            else:
                angle_deg = 0.0
            if dist_text:
                dist = _parse_expression(dist_text, self._unit_system, is_length=True)
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                # Angle-only entry: project the cursor onto the typed-angle ray
                # so the length still tracks the pointer.
                ar = math.radians(angle_deg)
                vx = self._cursor_wx - last_wx
                vy = self._cursor_wy - last_wy
                dist = max(0.0, vx * math.cos(ar) + vy * math.sin(ar))
            else:
                return
            if dist <= 0:
                return
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
            self._show_flash("Enter a valid distance and angle", 1000)

    # ── Inference / alignment lines ──────────────────────────────────────────

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

        le = QLineEdit(cast("QWidget", self))
        le.setText(f"{dist:.2f}")
        le.setFixedWidth(100)
        le.setFixedHeight(24)
        le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        le.setStyleSheet(
            "background: #001522; color: #ffffff; border: 1px solid #00d8ff;"
            "border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        le.move(
            *self._hud_position_near(
                mx,
                my,
                100,
                24,
                offset_x=-50,
                offset_y=-40,
            )
        )
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


# ════════════════════════════════════════════════════════════════════════════
# Text-on-path placement/editing
# ════════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from typing import Protocol

    class _TextOpsHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``render.py``'s ``_RendererHost`` for why this
        exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _sel: set[int]
        _entities: list[Any]
        _next_group_id: int
        _unit_system: str

        def _push_undo(self, coalesce: str | None = None) -> None: ...
        def _append_entity(
            self, poly: list[tuple[float, float]], *, kind: str = ..., meta: Any = ...
        ) -> int: ...
        def _compact_entities(self, drop: set[int]) -> None: ...
        def _sync_shape_storage_from_entities(self) -> None: ...
        def _show_flash(self, text: str, ms: int) -> None: ...
        def _redraw(self) -> None: ...
        def _notify(self) -> None: ...
        def _fire_poly_change(self) -> None: ...

    _TextOpsBase = _TextOpsHost
else:
    _TextOpsBase = object


# ── Text-to-polyline conversion (previously text_shapes.py) ──────────────────

Polyline = list[tuple[float, float]]

# Render glyphs at a large pixel size, then scale to mm — keeps curve
# flattening smooth regardless of the requested text height.
_RENDER_PX = 256


def text_to_polylines(
    text: str,
    *,
    family: str,
    height_mm: float,
    bold: bool = False,
    italic: bool = False,
) -> list[Polyline]:
    """Return closed polyline contours for ``text`` (``\\n`` starts a new line).

    ``height_mm`` is the total height of the rendered text block (cap
    height plus descenders for mixed-case input, stacked across every
    line). Coordinates are y-up with the block's bottom-left at the origin.
    """
    text = str(text)
    if not text.strip() or height_mm <= 0:
        return []

    font = QFont(family)
    font.setPixelSize(_RENDER_PX)
    font.setBold(bool(bold))
    font.setItalic(bool(italic))

    # QPainterPath.addText does NOT lay embedded newlines out as separate
    # lines (it places every character on one baseline) — each line needs
    # its own addText() call at a manually-advanced baseline Y.
    line_height = QFontMetrics(font).lineSpacing()
    path = QPainterPath()
    for i, line in enumerate(text.split("\n")):
        if line:
            path.addText(0.0, i * line_height, font, line)
    rect = path.boundingRect()
    if rect.height() <= 0:
        return []
    scale = float(height_mm) / rect.height()

    polys: list[Polyline] = []
    for sub in path.toSubpathPolygons():
        pts: Polyline = [
            (
                (p.x() - rect.x()) * scale,
                (rect.bottom() - p.y()) * scale,  # flip: Qt y-down → canvas y-up
            )
            for p in sub  # type: ignore[attr-defined]  # QPolygonF is iterable at runtime; missing from stubs
        ]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        polys.append(pts)
    return polys


def user_fonts_dir() -> Path:
    """Folder scanned for extra .ttf/.otf fonts (drop files in to add fonts)."""
    d = user_data_dir() / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_user_fonts() -> list[str]:
    """Register every font file in the user fonts folder; return families."""
    families: list[str] = []
    for f in sorted(user_fonts_dir().iterdir()):
        if f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
            font_id = QFontDatabase.addApplicationFont(str(f))
            if font_id >= 0:
                families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def install_font_file(path: str) -> str | None:
    """Copy a font file into the user fonts folder and register it.

    Returns the first family name on success, None on failure.
    """
    src_path = Path(path)
    if src_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
        return None
    dest = user_fonts_dir() / src_path.name
    try:
        shutil.copyfile(src_path, dest)
    except OSError:
        return None
    font_id = QFontDatabase.addApplicationFont(str(dest))
    if font_id < 0:
        return None
    fams = QFontDatabase.applicationFontFamilies(font_id)
    return fams[0] if fams else None


# ── TextOpsMixin ─────────────────────────────────────────────────────────────


class TextOpsMixin(_TextOpsBase):
    """Mixin providing add/rebuild/attach-to-path text operations for
    :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

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

        # If this text was attached to a path, remember which one so it can
        # be re-flowed after the rebuild (indices shift once the old
        # contours are compacted out, so remap it through that removal).
        existing_params = self.text_params_at(idx) or {}
        raw_path_idx = existing_params.get("attached_path_idx")
        attached_path_idx: int | None = None
        if (
            isinstance(raw_path_idx, int)
            and raw_path_idx not in members
            and 0 <= raw_path_idx < len(self._entities)
        ):
            attached_path_idx = raw_path_idx

        self._push_undo()
        self._compact_entities(set(members))
        if attached_path_idx is not None:
            attached_path_idx -= sum(1 for m in members if m < attached_path_idx)
        new_indices = self._place_text_contours(polys, anchor_x, anchor_y, values)
        self._sel = set(new_indices)
        if (
            attached_path_idx is not None
            and new_indices
            and 0 <= attached_path_idx < len(self._entities)
        ):
            # record_undo=False: the _push_undo() above already covers this
            # whole "edit text" action — a second push here would split one
            # user-visible edit into two separate undo steps.
            self.attach_text_to_path(new_indices[0], attached_path_idx, record_undo=False)
        self._sync_shape_storage_from_entities()
        self._redraw()
        self._notify()
        self._fire_poly_change()
        self._show_flash("Text updated", 800)
        return True

    def attach_text_to_path(
        self, text_idx: int, path_idx: int, *, record_undo: bool = True
    ) -> bool:
        """Reposition a text entity's glyph contours to sit tangent to an
        open/closed path, ordered left-to-right along its arc length.

        The path's own geometry is untouched; only the text's contours move.
        """
        if not (0 <= path_idx < len(self._entities)):
            return False
        members = self._text_member_indices(text_idx)
        if not members or path_idx in members:
            return False
        path_pts = self._entities[path_idx].points
        if len(path_pts) < 2:
            return False

        all_pts = [pt for i in members for pt in self._entities[i].points]
        if not all_pts:
            return False
        anchor_x = min(x for x, _ in all_pts)
        anchor_y = min(y for _, y in all_pts)

        seg_lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path_pts, path_pts[1:])]
        total_len = sum(seg_lengths)
        if total_len <= 1e-9:
            return False

        def point_and_angle_at(s: float) -> tuple[float, float, float]:
            s = max(0.0, min(total_len, s))
            acc = 0.0
            for (a, b), seg_len in zip(zip(path_pts, path_pts[1:]), seg_lengths):
                if seg_len > 1e-9 and acc + seg_len >= s:
                    t = (s - acc) / seg_len
                    px = a[0] + (b[0] - a[0]) * t
                    py = a[1] + (b[1] - a[1]) * t
                    return px, py, math.atan2(b[1] - a[1], b[0] - a[0])
                acc += seg_len
            a, b = path_pts[-2], path_pts[-1]
            return path_pts[-1][0], path_pts[-1][1], math.atan2(b[1] - a[1], b[0] - a[0])

        if record_undo:
            self._push_undo()
        for i in members:
            pts = self._entities[i].points
            xs = [x for x, _ in pts]
            local_cx = (min(xs) + max(xs)) / 2.0
            s = local_cx - anchor_x  # glyph mm-position == arc-length position
            px, py, angle = point_and_angle_at(s)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            new_pts = []
            for x, y in pts:
                dx = x - local_cx
                dy = y - anchor_y  # height above the text's own baseline
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                new_pts.append((px + rx, py + ry))
            self._entities[i].points = new_pts
            meta = self._entities[i].meta
            if isinstance(meta, dict) and isinstance(meta.get("text_params"), dict):
                meta["text_params"]["attached_path_idx"] = path_idx
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def prompt_edit_text(self, idx: int) -> None:
        """Reopen the text dialog prefilled with an entity's parameters."""
        params = self.text_params_at(idx)
        if params is None:
            return
        from src.ui.widgets.text_dialog import AddTextDialog

        dlg = AddTextDialog(self, unit=self._unit_system)
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

        dlg = AddTextDialog(self, unit=self._unit_system)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            self._show_flash("No text entered", 900)
            return
        self.add_text_at(wx, wy, **vals)
