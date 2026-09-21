"""Global pytest cleanup for the shared offscreen QApplication."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication


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
