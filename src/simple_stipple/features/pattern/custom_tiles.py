"""Custom-tile library management for the Pattern page — save, load, delete,
locate, and repair user-managed vector tiles used as the "Custom Tile"
pattern source. Extracted from ``PatternPage`` (see plan.md Section 9.1);
follows the same ``page: Any``-first free-function convention already used
by ``domain/session.py``.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from simple_stipple.core.formats.service import (
    load_dxf_polylines_with_report,
    read_fvi,
    svg_to_dxf,
    write_polylines_dxf,
)
from simple_stipple.features.pattern.form import collect_form_state
from simple_stipple.platform.settings import custom_tiles_dir, save_settings
from simple_stipple.ui.dialogs.files import pick_open_file
from simple_stipple.ui.style import STATUS_ERR, STATUS_OK, STATUS_WARN

LOGGER = logging.getLogger(__name__)


def update_custom_pattern_actions(page: Any, value: str) -> None:
    if not hasattr(page, "_save_tile_btn"):
        return
    name = page._custom_pattern_name(value)
    page._save_tile_btn.setVisible(value == "Custom Tile" or name is not None)
    page._save_tile_btn.setText("Update custom tile" if name else "Save custom tile")
    page._save_tile_btn.setToolTip(
        "Overwrite this custom tile's saved settings"
        if name
        else "Save the current Custom Tile geometry and settings into the Pattern list"
    )
    page._tile_name_edit.setVisible(value == "Custom Tile")
    page._delete_tile_btn.setVisible(name is not None)
    asset = page._tile_assets.get(name or "", {})
    status = asset.get("status", "embedded" if name else "")
    page._tile_asset_status.setVisible(bool(name))
    page._locate_tile_btn.setVisible(bool(name) and status in {"missing", "invalid"})
    page._repair_tile_btn.setVisible(bool(name) and status == "invalid")
    if not name:
        page._tile_asset_status.clear()
    elif status == "valid":
        page._tile_asset_status.setText(f"Valid · {Path(asset.get('path', '')).name}")
    elif status == "missing":
        page._tile_asset_status.setText("Missing source · embedded fallback remains available")
    elif status == "invalid":
        page._tile_asset_status.setText(
            f"Invalid source · {asset.get('error', 'could not read geometry')}"
        )
    else:
        page._tile_asset_status.setText("Embedded custom tile")


def apply_custom_tile(page: Any, polys: list[list[tuple[float, float]]]) -> None:
    """Use selected canvas geometry as the repeated pattern source."""
    normalized = [[(float(x), float(y)) for x, y in poly] for poly in polys if poly]
    if not normalized:
        return
    page._custom_tile_polys = normalized
    page._pattern_combo.setCurrentText("Custom Tile")
    page._schedule_preview()
    page._canvas._show_flash(f"Custom tile: {len(normalized)} shape(s)", 1200)


def refresh_tile_motif_combo(page: Any, current: str | None = None) -> None:
    load_custom_tiles_from_disk(page)
    selected = f"Custom · {current}" if current else None
    page._refresh_pattern_choices(current=selected or page._pattern_combo.currentText())


def load_custom_tiles_from_disk(page: Any) -> None:
    """Discover supported user-managed vector tiles without an app restart."""
    folder = custom_tiles_dir(page._settings.get("custom_tiles_dir"))
    for asset in page._tile_assets.values():
        path_text = asset.get("path", "")
        if path_text and not Path(path_text).exists():
            asset["status"] = "missing"
    paths = (
        [
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".dxf", ".svg", ".fvi"}
        ]
        if folder.exists()
        else []
    )
    for path in sorted(paths, key=lambda item: item.name.lower()):
        try:
            if path.suffix.lower() == ".dxf":
                polys, _report = load_dxf_polylines_with_report(str(path))
            elif path.suffix.lower() == ".fvi":
                polys = [list(poly) for poly in read_fvi(path).paths]
            else:
                with tempfile.TemporaryDirectory(prefix="simple-stipple-tile-svg-") as temp_folder:
                    converted = Path(temp_folder) / "tile.dxf"
                    svg_to_dxf(path, converted)
                    polys, _report = load_dxf_polylines_with_report(str(converted))
        except (OSError, ValueError, RuntimeError) as exc:
            LOGGER.warning("Skipping unreadable custom tile: %s", path)
            page._tile_assets[path.stem] = {
                "path": str(path),
                "status": "invalid",
                "error": str(exc),
            }
            continue
        if polys:
            page._tile_motifs[path.stem] = [list(poly) for poly in polys]
            page._tile_assets[path.stem] = {
                "path": str(path),
                "status": "valid",
                "format": path.suffix.lower(),
            }
        else:
            page._tile_assets[path.stem] = {
                "path": str(path),
                "status": "invalid",
                "error": "No drawable geometry",
            }


def persist_tile_motifs(page: Any) -> None:
    page._settings["custom_tile_motifs"] = page._tile_motifs
    page._settings["custom_tile_assets"] = page._tile_assets
    page._settings["custom_tile_settings"] = page._tile_settings
    save_settings(page._settings)


def open_custom_tiles_folder(page: Any) -> None:
    folder = custom_tiles_dir(page._settings.get("custom_tiles_dir"))
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def save_tile_motif(page: Any) -> None:
    current = page._pattern_combo.currentText()
    existing_name = page._custom_pattern_name(current)
    if current != "Custom Tile" and existing_name is None:
        return
    if not page._custom_tile_polys:
        page._set_status("Send geometry to Custom Tile before saving a motif.", STATUS_WARN)
        return
    name = existing_name or page._tile_name_edit.text().strip()
    if not name:
        page._set_status("Enter a custom tile name beside Save.", STATUS_WARN)
        page._tile_name_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        return
    page._tile_motifs[name] = [list(poly) for poly in page._custom_tile_polys]
    saved_state = collect_form_state(page)
    saved_state.pop("custom_tile_polys", None)
    saved_state["pattern"] = "Custom Tile"
    page._tile_settings[name] = saved_state
    if existing_name is not None:
        persist_tile_motifs(page)
        page._set_status(f"Updated custom tile settings: {name}", STATUS_OK)
        return
    safe_name = "".join(character for character in name if character not in '<>:"/\\|?*').strip()
    if not safe_name:
        page._set_status("Choose a name that can be used as a file name.", STATUS_ERR)
        return
    tile_path = custom_tiles_dir(page._settings.get("custom_tiles_dir")) / f"{safe_name}.dxf"
    tile_path.parent.mkdir(parents=True, exist_ok=True)
    write_polylines_dxf(page._custom_tile_polys, str(tile_path), close=False)
    page._tile_assets[name] = {"path": str(tile_path), "status": "valid", "format": ".dxf"}
    persist_tile_motifs(page)
    refresh_tile_motif_combo(page, name)
    page._pattern_combo.setCurrentText(f"Custom · {name}")
    page._tile_name_edit.clear()
    page._set_status(f"Saved custom tile: {tile_path.name} · Custom Tiles", STATUS_OK)


def delete_tile_motif(page: Any) -> None:
    name = page._custom_pattern_name(page._pattern_combo.currentText()) or ""
    if not name or name not in page._tile_motifs:
        return
    answer = QMessageBox.question(
        page,
        "Delete custom pattern?",
        f'Delete the custom pattern "{name}"? This cannot be undone.',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    del page._tile_motifs[name]
    page._tile_settings.pop(name, None)
    asset = page._tile_assets.pop(name, {})
    safe_name = "".join(character for character in name if character not in '<>:"/\\|?*').strip()
    tile_folder = custom_tiles_dir(page._settings.get("custom_tiles_dir"))
    asset_path = Path(asset.get("path", "")) if asset.get("path") else None
    if asset_path is not None and asset_path.parent == tile_folder:
        asset_path.unlink(missing_ok=True)
    else:
        (tile_folder / f"{safe_name}.dxf").unlink(missing_ok=True)
    persist_tile_motifs(page)
    refresh_tile_motif_combo(page)
    page._pattern_combo.setCurrentText("— None —")
    page._set_status(f"Deleted custom pattern: {name}")


def locate_tile_asset(page: Any) -> None:
    name = page._custom_pattern_name(page._pattern_combo.currentText()) or ""
    if not name:
        return
    path = pick_open_file(
        page,
        page._settings,
        "custom_tile_locate",
        "Locate custom tile",
        "Vector tiles (*.dxf *.DXF *.svg *.SVG *.fvi *.FVI);;All files (*)",
        fallback_dir=str(custom_tiles_dir(page._settings.get("custom_tiles_dir"))),
    )
    if not path:
        return
    source = Path(path)
    page._tile_assets[name] = {"path": str(source), "status": "missing"}
    try:
        if source.suffix.lower() == ".dxf":
            polys, _report = load_dxf_polylines_with_report(str(source))
        elif source.suffix.lower() == ".fvi":
            polys = [list(poly) for poly in read_fvi(source).paths]
        elif source.suffix.lower() == ".svg":
            with tempfile.TemporaryDirectory(prefix="simple-stipple-locate-tile-") as temp_folder:
                converted = Path(temp_folder) / "tile.dxf"
                svg_to_dxf(source, converted)
                polys, _report = load_dxf_polylines_with_report(str(converted))
        else:
            raise ValueError("Choose a DXF, SVG, or FVI tile.")
        if not polys:
            raise ValueError("No drawable geometry was found.")
        page._tile_motifs[name] = [list(poly) for poly in polys]
        page._tile_assets[name] = {
            "path": str(source),
            "status": "valid",
            "format": source.suffix.lower(),
        }
        persist_tile_motifs(page)
        page._refresh_pattern_choices(current=f"Custom · {name}")
        page._set_status(f"Located custom tile: {source.name}", STATUS_OK)
    except (OSError, ValueError, RuntimeError) as exc:
        page._tile_assets[name] = {"path": str(source), "status": "invalid", "error": str(exc)}
        persist_tile_motifs(page)
        update_custom_pattern_actions(page, f"Custom · {name}")


def repair_tile_asset(page: Any) -> None:
    name = page._custom_pattern_name(page._pattern_combo.currentText()) or ""
    path = page._tile_assets.get(name, {}).get("path", "")
    if path:
        page.repairTileRequested.emit(path)
        page._set_status("Opened the invalid tile in Convert for repair.", STATUS_WARN)
