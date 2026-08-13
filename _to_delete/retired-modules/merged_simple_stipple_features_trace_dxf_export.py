"""DXF outline export workflow for the Trace feature.

The functions operate on the composed :class:`TracePage` instance so the
page keeps its Qt public methods and status ownership while this file owns
the repeated preflight, file-choice, and native-shape export sequence.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from simple_stipple.engine.formats.service import DxfService
from simple_stipple.ui.components.feedback import show_error
from simple_stipple.ui.dialogs.export_preflight import export_preflight
from simple_stipple.ui.dialogs.files import pick_save_file
from simple_stipple.ui.style.theme import STATUS_OK


def get_save_path(self, title: str) -> str | None:
    """Choose the destination for a traced-outline DXF export."""
    stem = Path(self._img_path).stem if self._img_path else "outline"
    path = pick_save_file(
        self,
        self._settings,
        "trace_output",
        title,
        f"{stem}_outline.dxf",
        "DXF files (*.dxf);;All files (*)",
    )
    return path or None


def _export_records(self, records: list[dict], *, title: str, selected: bool) -> None:
    if not records:
        message = "Nothing is selected." if selected else "No polylines to export."
        if selected:
            QMessageBox.information(self, "Export Selected", message)
        else:
            QMessageBox.critical(self, "Export", message)
        return
    action = "Export Selected" if selected else "Export"
    proceed, _report = export_preflight(
        self,
        [list(record["polyline"]) for record in records],
        action=action,
        allow_open_paths=True,
    )
    if not proceed:
        self._canvas.set_geometry_health_visible(True, announce=True)
        return
    out = get_save_path(self, title)
    if not out:
        return
    try:
        DxfService.write_polylines_dxf(
            [list(record["polyline"]) for record in records],
            out,
            close=False,
            entity_kinds=[str(record.get("kind", "polyline")) for record in records],
            entity_meta=[record.get("meta") for record in records],
        )
        self._last_out = out
        self._reveal_action.setEnabled(True)
        qualifier = " selected" if selected else ""
        self._set_status(f"Exported {len(records)}{qualifier} shapes → {Path(out).name}", STATUS_OK)
    except (OSError, ValueError) as exc:
        show_error(self, "Export Error", exc)


def export_all(self) -> None:
    """Preflight and export every traced outline as native DXF where possible."""
    _export_records(
        self,
        self._canvas.get_export_dxf_state(),
        title="Export all outlines as DXF",
        selected=False,
    )


def export_selected(self) -> None:
    """Preflight and export just the currently selected traced outlines."""
    selected = set(self._canvas.get_selected_ids())
    records = [
        record
        for record in self._canvas.get_export_dxf_state()
        if record.get("entity_id") in selected
    ]
    _export_records(self, records, title="Export selected outlines as DXF", selected=True)


__all__ = ["export_all", "export_selected", "get_save_path"]
