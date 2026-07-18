"""Workspace document save/load round trips at the app level."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import square  # noqa: E402


@pytest.fixture()
def app_window(qapp):
    from src.app.window import App

    w = App()
    w.resize(1200, 800)
    yield w
    w.deleteLater()
    qapp.processEvents()


def _draft_page(w):
    for entry in w._page_runtime.iter_workspace_pages():
        page = entry[-1] if isinstance(entry, tuple) else entry
        if page.__class__.__name__ == "DraftPage":
            return page
    raise AssertionError("DraftPage not found")


def test_workspace_document_round_trip(app_window, qapp):
    w = app_window
    draft = _draft_page(w)
    rt = draft._rt()
    rt.load_polys_by_layer({"Layer 1": [square(0, 0)], "Cut": [square(30, 0)]}, fit=True)
    draft._canvas._entities[1].hidden = True
    rt.rename_shape("Layer 1", 0, "Base")

    doc = w._collect_workspace_document()
    # wipe and restore
    w._clear_workspace_state()
    assert draft._canvas.poly_count == 0
    w._apply_workspace_document(doc)

    canvas = draft._canvas
    assert canvas.poly_count == 2
    assert canvas.layer_names() == ["Layer 1", "Cut"]
    assert canvas.active_layer == "Layer 1"
    assert canvas._entities[1].hidden
    assert canvas._entities[0].meta["label"] == "Base"


def test_workspace_document_is_json_serializable(app_window):
    import json

    w = app_window
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    doc = w._collect_workspace_document()
    text = json.dumps(doc)
    assert "Layer 1" in text


def test_autosave_writes_and_recovery_cleans_up(app_window, tmp_path, monkeypatch):
    w = app_window
    monkeypatch.setattr(type(w), "_autosave_path", staticmethod(lambda: tmp_path / "auto.json"))
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    w._workspace_dirty = True
    w._autosave_workspace()
    assert (tmp_path / "auto.json").exists()

    # a successful save discards the snapshot
    w._workspace_path = tmp_path / "doc.json"
    assert w._save_workspace()
    assert not (tmp_path / "auto.json").exists()


def test_autosave_skips_when_clean(app_window, tmp_path, monkeypatch):
    w = app_window
    monkeypatch.setattr(type(w), "_autosave_path", staticmethod(lambda: tmp_path / "auto.json"))
    w._workspace_dirty = False
    w._autosave_workspace()
    assert not (tmp_path / "auto.json").exists()


def test_successful_save_removes_restored_snapshot(app_window, tmp_path):
    w = app_window
    snapshot = tmp_path / "restored.workspace.json"
    snapshot.write_text("{}", encoding="utf-8")
    w._restored_recovery_path = snapshot
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    w._workspace_path = tmp_path / "saved.workspace.json"

    assert w._save_workspace()
    assert not snapshot.exists()
    assert w._restored_recovery_path is None


def test_failed_save_as_does_not_rebind_workspace(app_window, monkeypatch, tmp_path):
    from src.app.controllers import workspace as workspace_controller

    w = app_window
    old_path = tmp_path / "old.workspace.json"
    w._workspace_path = old_path
    monkeypatch.setattr(
        workspace_controller.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "new.workspace.json"), ""),
    )
    monkeypatch.setattr(
        workspace_controller,
        "write_json_file_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(workspace_controller.QMessageBox, "critical", lambda *_args: None)

    assert not w._save_workspace_as()
    assert w._workspace_path == old_path


def test_workspace_apply_rolls_back_after_page_failure(app_window, monkeypatch):
    w = app_window
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    before = w._collect_workspace_document()
    pages = list(w._page_runtime.iter_workspace_pages())
    failing_page = pages[-1][1]
    original_apply = failing_page.apply_workspace_state
    calls = 0

    def fail_once(state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("bad page state")
        return original_apply(state)

    monkeypatch.setattr(failing_page, "apply_workspace_state", fail_once)
    changed = w._collect_workspace_document()
    changed["tabs"]["draft"] = {}

    with pytest.raises(ValueError, match="bad page state"):
        w._apply_workspace_document(changed)
    assert w._collect_workspace_document() == before
