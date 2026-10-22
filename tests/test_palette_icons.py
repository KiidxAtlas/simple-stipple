"""Existing toolbar icons stay readable when the application appearance changes."""

import pytest
from PySide6.QtGui import QColor, QIcon, QPalette

from simple_stipple.ui.components.icons import download_icon, gear_icon, tool_icon


@pytest.mark.parametrize("factory", [gear_icon, download_icon, lambda: tool_icon("circle")])
def test_existing_icon_follows_palette_and_disabled_state(qapp, factory):
    original = qapp.palette()
    icon = factory()
    try:
        for foreground in ("#243342", "#e6edf3"):
            palette = QPalette(original)
            palette.setColor(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText, QColor(foreground)
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#657586")
            )
            qapp.setPalette(palette)
            for mode, expected in (
                (QIcon.Mode.Normal, foreground),
                (QIcon.Mode.Disabled, "#657586"),
            ):
                image = icon.pixmap(24, 24, mode).toImage()
                opaque = [
                    image.pixelColor(x, y)
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() == 255
                ]
                assert opaque
                assert all(color.name() == expected for color in opaque)
    finally:
        qapp.setPalette(original)


def test_explicit_icon_color_is_preserved(qapp):
    image = gear_icon(color="#ff0000").pixmap(24, 24).toImage()
    opaque = [
        image.pixelColor(x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    ]
    assert opaque
    assert all(color.name() == "#ff0000" for color in opaque)
