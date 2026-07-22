from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.app.controllers.menu import CommandController
from src.app.page_runtime import PageSpec
from src.ui.widgets.dialogs.command_palette import CommandPaletteDialog


class _Page:
    def __init__(self, marker: list[str], page_id: str) -> None:
        self.marker = marker
        self.page_id = page_id

    def command_palette_commands(self):
        return [
            {
                "title": "Do page work",
                "subtitle": f"Action on {self.page_id}",
                "run": lambda: self.marker.append(f"run:{self.page_id}"),
            }
        ]


def test_palette_collects_commands_from_inactive_pages_and_switches_before_run():
    events: list[str] = []
    specs = tuple(
        PageSpec(page_id, page_id.title(), f"page {page_id}", lambda _settings: None)
        for page_id in ("draft", "pattern")
    )
    pages = {page_id: _Page(events, page_id) for page_id in ("draft", "pattern")}
    app = SimpleNamespace(
        _page_specs=specs,
        _page_runtime=SimpleNamespace(get=pages.get),
        _switch_to_page=lambda page_id: events.append(f"switch:{page_id}"),
    )
    controller = CommandController(app)
    controller._build_commands = lambda: []

    commands = controller._build_command_palette_commands()

    titles = {entry["title"] for entry in commands}
    assert {"Draft: Do page work", "Pattern: Do page work"}.issubset(titles)
    pattern_command = next(entry for entry in commands if entry["title"].startswith("Pattern:"))
    pattern_command["run"]()
    assert events == ["switch:pattern", "run:pattern"]


def test_palette_shows_disabled_reason_and_does_not_dispatch(qapp):
    called: list[str] = []
    dialog = CommandPaletteDialog(
        [
            {
                "title": "Delete selection",
                "enabled": False,
                "disabled_reason": "Select geometry that supports this command",
                "run": lambda: called.append("ran"),
            }
        ]
    )

    item = dialog._list.item(0)
    assert "Select geometry" in item.text()
    assert "Select geometry" in item.toolTip()
    dialog._list.setCurrentRow(0)
    dialog._run_selected()

    assert called == []
    assert dialog.result() == 0
    dialog.deleteLater()
    qapp.processEvents()
