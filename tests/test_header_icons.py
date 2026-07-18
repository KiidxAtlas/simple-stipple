"""Header glyph buttons (settings, update-check) are hand-drawn icons, not
Unicode symbol characters — those depend on font coverage and can render as
the wrong glyph (the settings gear "⚙" showed up as a plain circle).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from src.ui.components import download_icon, gear_icon  # noqa: E402


def test_gear_icon_renders_a_non_empty_pixmap(qapp):
    from PySide6.QtCore import QSize

    icon = gear_icon(size=32)
    pixmap = icon.pixmap(QSize(32, 32))
    assert not pixmap.isNull()
    assert pixmap.width() == 32 and pixmap.height() == 32


def test_download_icon_renders_a_non_empty_pixmap(qapp):
    from PySide6.QtCore import QSize

    icon = download_icon(size=32)
    pixmap = icon.pixmap(QSize(32, 32))
    assert not pixmap.isNull()


def test_icons_actually_paint_visible_pixels(qapp):
    """Guard against a silent no-op draw (e.g. wrong color/alpha) leaving
    the pixmap fully transparent — count non-transparent pixels."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    for icon in (gear_icon(size=32), download_icon(size=32)):
        image = icon.pixmap(QSize(32, 32)).toImage()
        opaque_pixels = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if QImage.pixelColor(image, x, y).alpha() > 0
        )
        assert opaque_pixels > 20


def test_header_buttons_use_icons_not_unicode_glyphs(qapp):
    """Regression guard for the original bug: settings/update buttons must
    carry a real QIcon, not rely on a Unicode character as their label."""
    from PySide6.QtWidgets import QPushButton

    from src.app.window import App

    win = App()
    settings_btn = None
    update_btn = None
    for btn in win.findChildren(QPushButton):
        tip = btn.toolTip()
        if tip.startswith("Settings ("):
            settings_btn = btn
        elif tip == "Check for updates":
            update_btn = btn
    assert settings_btn is not None and not settings_btn.icon().isNull()
    assert settings_btn.text() == ""
    assert update_btn is not None and not update_btn.icon().isNull()
    assert update_btn.text() == ""
