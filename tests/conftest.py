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
def _isolate_qapp_style():
    """Restore process-global QApplication style state around every test.

    Constructing ``App()`` runs ``_apply_accessibility_settings``, which mutates
    the *shared* QApplication font (scaled by ``ui_scale``), stylesheet, and
    palette and never restores them. Those mutations then leak into unrelated
    later tests: an inflated global font grows every widget, so a geometry
    assertion that passes in isolation fails once an ``App()`` test has run
    first. Snapshotting and restoring here makes each test see the same global
    style baseline regardless of order.
    """
    from PySide6.QtGui import QFont, QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        yield
        return
    saved = (
        QFont(app.font()),
        app.styleSheet(),
        QPalette(app.palette()),
        app.property("basePointSize"),
    )
    try:
        yield
    finally:
        app = QApplication.instance()
        if app is not None:
            font, style_sheet, palette, base_point_size = saved
            app.setFont(font)
            app.setStyleSheet(style_sheet)
            app.setPalette(palette)
            app.setProperty("basePointSize", base_point_size)


@pytest.fixture(autouse=True)
def _drain_qt_widgets():
    """Tear leaked top-level Qt widgets down deterministically after each test.

    Tests build widgets (views, dialogs, the App window) that have no parent, so
    nothing deletes them when the test function returns — they stay alive until
    Python happens to garbage-collect them, which lands in the *middle of a later
    test* when pytest-qt processes pending events during setup. Two failure modes
    follow from that non-determinism:

    * a leaked, still-shown window perturbs another test's widget-geometry
      assertions (offscreen screen/layout state is process-global), and
    * processing a queued ``DeferredDelete`` against a C++ object that has
      already been freed segfaults the interpreter.

    Draining here — while the ``QApplication`` is unquestionably valid — makes
    each test's widget destruction happen inside that test's own teardown, so
    neither failure mode can reach across the test boundary. This runs after the
    test body but is defensive about partially constructed state.
    """
    yield
    import gc

    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    # Force Python to collect now — while the QApplication is valid — instead of
    # at an arbitrary point during a later test's pytest-qt setup, where a
    # DeferredDelete posted for a just-collected wrapper lands against C++ state
    # that has moved on and segfaults. Do NOT close() widgets: that runs their
    # close handlers (App.closeEvent → discard-confirm) against half-torn-down
    # state, which is its own crash. Just drain the deletion queue deterministically.
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
    from src.app.window import App

    monkeypatch.setattr(
        App,
        "_autosave_path",
        staticmethod(lambda: tmp_path / "autosave.workspace.json"),
    )
