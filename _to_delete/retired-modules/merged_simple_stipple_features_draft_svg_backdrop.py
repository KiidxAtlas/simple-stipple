"""Editable reference-artwork handling for SVG imports in Draft.

Draft owns linework, but an SVG's placed image must remain visible and
editable while that linework is adjusted. These functions retain DraftPage's
existing action methods while isolating that imported-artwork workflow from
the page coordinator.
"""

from __future__ import annotations

import io

from PIL import Image

from simple_stipple.engine.formats.svg import read_svg_images


def show_imported_svg_image(self, path: str) -> int:
    """Place embedded SVG artwork as an editable translucent canvas backdrop."""
    placements = read_svg_images(path)
    if not placements:
        return 0
    first = placements[0]
    try:
        with Image.open(io.BytesIO(first.png_bytes)) as source:
            backdrop = source.convert("RGBA")
            backdrop.putalpha(110)
            self._canvas.set_background_image(
                backdrop.copy(),
                first.width_mm,
                first.height_mm,
                first.x_mm,
                first.y_mm,
                first.rotation_deg,
            )
        self._canvas.set_background_image_editable(True, self._on_backdrop_transform)
        self._canvas.set_background_image_key_callback(self._on_backdrop_key)
    except (OSError, ValueError):
        self._canvas.clear_background_image()
        return 0
    return len(placements)


def on_backdrop_transform(
    self, _x: float, _y: float, _w: float, _h: float, _rotation: float = 0.0
) -> None:
    """Refresh status after the canvas changes reference-artwork placement."""
    self._refresh_status()


def on_backdrop_key(self, action: str, reverse: bool = False) -> None:
    """Clear the imported reference image with the normal remove command."""
    if action != "remove":
        return
    self._canvas.clear_background_image()
    self._set_import_note("")
    self._canvas._show_flash("Reference image removed", 1200)
    self._refresh_status()


__all__ = ["on_backdrop_key", "on_backdrop_transform", "show_imported_svg_image"]
