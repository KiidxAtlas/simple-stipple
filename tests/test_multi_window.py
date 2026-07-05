"""New Window multi-document support at the app level."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture()
def app_window(qapp):
    from src.app import App

    w = App()
    w.resize(1200, 800)
    yield w
    for extra in list(App._open_windows):
        extra.deleteLater()
    App._open_windows.clear()
    w.deleteLater()
    qapp.processEvents()


def test_new_window_creates_independent_instance(app_window, qapp):
    from src.app import App

    before = len(App._open_windows)
    app_window._new_window()
    qapp.processEvents()
    assert len(App._open_windows) == before + 1
    new_win = App._open_windows[-1]
    assert new_win is not app_window
    assert new_win._workspace_path is None
    # Independent state: dirtying one must not affect the other.
    new_win._workspace_dirty = True
    assert app_window._workspace_dirty is False


def test_closing_new_window_removes_it_from_tracking(app_window, qapp, monkeypatch):
    from src import app as app_module
    from src.app import App

    monkeypatch.setattr(app_module, "save_settings", lambda _settings: None)
    app_window._new_window()
    qapp.processEvents()
    new_win = App._open_windows[-1]
    new_win.close()
    qapp.processEvents()
    assert new_win not in App._open_windows
