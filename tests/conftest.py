"""Shared fixtures. Qt tests run on the offscreen platform plugin."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_canvas_keymap():
    """canvas_commands' keymap is process-global and mutable (so a live
    keybinding rebind applies everywhere); constructing a real App() applies
    whatever the machine's actual settings.json has. Reset it around every
    test so one test's App() can't change which key another test's plain
    key() call resolves to."""
    from src.ui.canvas.interaction import commands as canvas_commands

    canvas_commands.apply_keybindings(None)
    yield
    canvas_commands.apply_keybindings(None)


@pytest.fixture(autouse=True)
def _isolate_app_autosave(monkeypatch, tmp_path):
    """Never let App tests read or write the developer's real recovery file."""
    from src.app import App

    monkeypatch.setattr(
        App,
        "_autosave_path",
        staticmethod(lambda: tmp_path / "autosave.workspace.json"),
    )
