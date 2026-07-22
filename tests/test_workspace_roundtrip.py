"""Workspace document save/load round trips at the app level."""

from __future__ import annotations

import json

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


def test_recovery_write_failure_is_persistent_actionable_and_deduplicated(
    app_window, monkeypatch
):
    from PySide6.QtWidgets import QPushButton

    from src.ui import util

    w = app_window
    notifications = []
    monkeypatch.setattr(util, "record_notification", notifications.append)

    w._autosave_controller._on_recovery_failed("disk full")
    w._autosave_controller._on_recovery_failed("disk full")

    assert not w._system_banner.isHidden()
    assert "Recovery snapshot failed" in w._system_banner_text.text()
    assert "durable protection" in w._system_banner_text.text()
    assert any(
        button.text() == "Manage storage"
        for button in w._system_banner.findChildren(QPushButton)
    )
    assert len(notifications) == 1


def test_successful_durable_write_clears_failure_and_updates_header(app_window):
    w = app_window
    w._autosave_controller._on_recovery_failed("read-only folder")

    w._autosave_controller._on_recovery_saved()

    assert w._system_banner.isHidden()
    assert w._last_autosave_at is not None
    assert "Last durable autosave:" in w._workspace_title_label.toolTip()
    assert "Not yet" not in w._workspace_title_label.toolTip()


def test_autosave_keeps_distinct_rolling_recovery_states(app_window, tmp_path, monkeypatch):
    w = app_window
    current = tmp_path / "window.workspace.json"
    monkeypatch.setattr(type(w), "_autosave_path", staticmethod(lambda: current))
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    w._workspace_dirty = True
    w._autosave_workspace()

    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0), square(20, 0)]}, fit=False)
    w._autosave_workspace()

    snapshots = list(tmp_path.glob("window*.workspace.json"))
    assert len(snapshots) == 2
    documents = [
        json.loads(snapshot.read_text(encoding="utf-8"))["document"] for snapshot in snapshots
    ]
    assert documents[0] != documents[1]


def test_autosave_reads_existing_large_snapshot_with_workspace_limit(
    app_window, tmp_path, monkeypatch
):
    from src.app.controllers import tasks
    from src.backend.persistence import MAX_WORKSPACE_FILE_BYTES

    w = app_window
    current = tmp_path / "window.workspace.json"
    current.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(type(w), "_autosave_path", staticmethod(lambda: current))
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    w._workspace_dirty = True
    observed: list[int] = []
    real_read = tasks.read_json_file

    def capture_limit(path, default=None, *, max_bytes):
        observed.append(max_bytes)
        return real_read(path, default, max_bytes=max_bytes)

    monkeypatch.setattr(tasks, "read_json_file", capture_limit)
    w._autosave_workspace()

    assert observed == [MAX_WORKSPACE_FILE_BYTES]


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


def test_startup_recovery_keeps_snapshot_until_recovered_work_is_saved(
    app_window, tmp_path, monkeypatch
):
    from src.app.controllers import tasks

    w = app_window
    draft = _draft_page(w)
    draft._rt().load_polys_by_layer({"Layer 1": [square(0, 0)]}, fit=True)
    document = w._collect_workspace_document()
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    snapshot = recovery_dir / "crash.workspace.json"
    snapshot.write_text(
        json.dumps(
            {
                "recovery": {
                    "timestamp": "2026-07-20T12:00:00+00:00",
                    "workspace_path": str(tmp_path / "original.workspace.json"),
                },
                "document": document,
            }
        ),
        encoding="utf-8",
    )
    w._clear_workspace_state()
    w._workspace_path = tmp_path / "unrelated.workspace.json"
    w._autosave_controller._recovery_offered = False
    monkeypatch.setattr(tasks, "user_data_dir", lambda: tmp_path)
    opened_sources: list[str] = []

    def open_recovery_library(*, initial_source="saved"):
        opened_sources.append(initial_source)

    monkeypatch.setattr(w, "_open_saved_workspaces", open_recovery_library)

    w._autosave_controller.offer_startup_autosave_recovery()

    assert opened_sources == ["recovery"]
    assert snapshot.exists()


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
