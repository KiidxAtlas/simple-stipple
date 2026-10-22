"""Global pytest setup: the ``gui`` marker and shared offscreen QApplication cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

# A test module that imports any of these drives Qt widgets (directly or via
# a page/canvas) and is marked ``gui``; ``pytest -m "not gui"`` is the fast
# headless core suite.
_GUI_IMPORTS = (
    "PySide6",
    "qtbot",
    "simple_stipple.app",
    "simple_stipple.canvas",
    "simple_stipple.features",
    "simple_stipple.ui",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "gui: needs a QApplication and Qt widgets")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    gui_modules: dict[Path, bool] = {}
    for item in items:
        path = item.path
        if path not in gui_modules:
            source = path.read_text(encoding="utf-8")
            gui_modules[path] = any(name in source for name in _GUI_IMPORTS)
        if gui_modules[path]:
            item.add_marker(pytest.mark.gui)


@pytest.fixture(autouse=True)
def suppress_first_run_welcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Automated runs never get the first-run welcome over the window under test.

    Windows read the shared live settings dict, so marking it seen here keeps
    ``App.showEvent`` startup prompts quiet; welcome tests opt back in.
    """
    from simple_stipple.platform.settings import load_settings

    monkeypatch.setitem(load_settings(), "welcome_seen", True)


@pytest.fixture(autouse=True)
def reset_export_acknowledgments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no session "Export Anyway" acknowledgments."""
    from simple_stipple.ui.dialogs import export_preflight

    monkeypatch.setattr(export_preflight, "_ACKNOWLEDGED", set())


@pytest.fixture(autouse=True)
def close_qt_top_levels():
    """Close and delete test-created top-level widgets after every test.

    The suite intentionally shares one QApplication. Without an explicit drain,
    closed dialogs/pages accumulate deferred-delete events and make later tests
    progressively slower or stall in Qt teardown.
    """
    yield
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
