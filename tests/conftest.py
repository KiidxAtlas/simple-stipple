"""Global pytest cleanup for the shared offscreen QApplication."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication


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
    app.sendPostedEvents()
    app.processEvents()
