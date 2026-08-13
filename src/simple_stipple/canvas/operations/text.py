"""Text entity creation, font management, and glyph tessellation."""

from __future__ import annotations
import math
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainterPath
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.platform.settings import user_data_dir

Polyline = list[tuple[float, float]]
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


class TextService:
    """Own text contour creation, editing, and path attachment."""

    def __init__(self, host) -> None:
        self._host = host

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
        new_ids = self._place_text_contours(
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
        self._host._sel = set(new_ids)
        self._host._show_flash(f"Text placed ({len(new_ids)} contours)", 900)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return len(new_ids)

    def _place_text_contours(
        self,
        polys: list[list[tuple[float, float]]],
        wx: float,
        wy: float,
        params: dict[str, Any],
    ) -> list[str]:
        """Create grouped, editable text contours through the command boundary."""
        entities = self._text_entities(polys, wx, wy, params)
        result = self._host._canvas_service.create_entities(entities)
        return list(result.created_ids)

    def _text_entities(
        self,
        polys: list[list[tuple[float, float]]],
        wx: float,
        wy: float,
        params: dict[str, Any],
        *,
        group_id: int | None = None,
    ) -> list[EntityRecord]:
        if len(polys) > 1 and group_id is None:
            group_id = self._host._next_group_id
        return [
            EntityRecord(
                points=[(x + wx, y + wy) for x, y in poly],
                meta={"text_params": dict(params)},
                group=group_id if len(polys) > 1 else None,
                layer=self._host._active_layer,
            )
            for poly in polys
        ]

    def text_params_at(self, entity_id: str) -> dict[str, Any] | None:
        for entity in self._host._entities:
            if entity.id == entity_id:
                params = (entity.meta or {}).get("text_params")
                return dict(params) if isinstance(params, dict) else None
        return None

    def _text_member_ids(self, entity_id: str) -> list[str]:
        for entity in self._host._entities:
            if entity.id == entity_id:
                gid = entity.group
                if gid is None:
                    return [entity_id]
                return [e.id for e in self._host._entities if e.group == gid]
        return [entity_id]

    def rebuild_text(self, entity_id: str, values: dict[str, Any]) -> bool:
        """Replace a text entity's contours with newly rendered ones (same
        bottom-left anchor)."""
        members = self._text_member_ids(entity_id)
        member_entities = [e for e in self._host._entities if e.id in members]
        pts = [pt for entity in member_entities for pt in entity.points]
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
            self._host._show_flash("Text rendered no contours", 1000)
            return False

        # If this text was attached to a path, remember which one so it can
        # be re-flowed after the rebuild replaces the glyph contours.
        existing_params = self.text_params_at(entity_id) or {}
        raw_path_id = existing_params.get("attached_path_id")
        attached_path_id: str | None = None
        if isinstance(raw_path_id, str) and raw_path_id in self._host._entities_by_id:
            attached_path_id = raw_path_id

        member_entity = next((e for e in self._host._entities if e.id == entity_id), None)
        group_id = member_entity.group if member_entity else None
        replacements = self._text_entities(
            polys,
            anchor_x,
            anchor_y,
            values,
            group_id=group_id,
        )
        source_ids = tuple(members)
        self._host._canvas_service.update_entities(
            replacements,
            source_ids=source_ids,
        )
        new_ids = [entity.id for entity in replacements]
        if attached_path_id is not None and new_ids:
            # The contour replacement already owns this user-visible undo step.
            self.attach_text_to_path(new_ids[0], attached_path_id, record_undo=False)
        self._host._sel = set(new_ids)
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash("Text updated", 800)
        return True

    def attach_text_to_path(self, text_id: str, path_id: str, *, record_undo: bool = True) -> bool:
        """Reposition a text entity's glyph contours to sit tangent to an
        open/closed path, ordered left-to-right along its arc length.

        The path's own geometry is untouched; only the text's contours move.
        """
        path_entity = next((e for e in self._host._entities if e.id == path_id), None)
        if path_entity is None:
            return False
        members = self._text_member_ids(text_id)
        if not members or path_id in members:
            return False
        path_pts = path_entity.points
        if len(path_pts) < 2:
            return False

        member_entities = [e for e in self._host._entities if e.id in members]
        all_pts = [pt for entity in member_entities for pt in entity.points]
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

        candidates = []
        for member_id in members:
            entity = next((e for e in self._host._entities if e.id == member_id), None)
            if entity is None:
                continue
            entity = deepcopy(entity)
            pts = entity.points
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
            entity.points = new_pts
            meta = entity.meta
            if isinstance(meta, dict) and isinstance(meta.get("text_params"), dict):
                meta["text_params"]["attached_path_id"] = path_id
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates, record=record_undo)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def prompt_edit_text(self, entity_id: str) -> None:
        """Reopen the text dialog prefilled with an entity's parameters."""
        params = self.text_params_at(entity_id)
        if params is None:
            return
        from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog

        dlg = AddTextDialog(self._host, unit=self._host._unit_system)
        dlg.set_values(params)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            return
        self.rebuild_text(entity_id, vals)

    def prompt_add_text(self, wx: float, wy: float) -> None:
        """Open the Add Text dialog and place the result at world (wx, wy)."""
        from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog

        dlg = AddTextDialog(self._host, unit=self._host._unit_system)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            self._host._show_flash("No text entered", 900)
            return
        self.add_text_at(wx, wy, **vals)
