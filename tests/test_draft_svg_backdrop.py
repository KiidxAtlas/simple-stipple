from __future__ import annotations

from types import SimpleNamespace

from simple_stipple.features.draft import session as svg_backdrop
from simple_stipple.features.draft.page import DraftPage


def test_draft_page_preserves_svg_backdrop_callback_surface() -> None:
    assert DraftPage._show_imported_svg_image is svg_backdrop.show_imported_svg_image
    assert DraftPage._on_backdrop_key is svg_backdrop.on_backdrop_key


def test_removing_svg_backdrop_clears_artwork_note_and_refreshes_status() -> None:
    calls: list[object] = []
    canvas = SimpleNamespace(
        clear_background_image=lambda: calls.append("clear"),
        _show_flash=lambda text, ms: calls.append((text, ms)),
    )
    page = SimpleNamespace(
        _canvas=canvas,
        _set_import_note=lambda note: calls.append(("note", note)),
        _refresh_status=lambda: calls.append("refresh"),
    )

    svg_backdrop.on_backdrop_key(page, "remove")

    assert calls == [
        "clear",
        ("note", ""),
        ("Reference image removed", 1200),
        "refresh",
    ]
