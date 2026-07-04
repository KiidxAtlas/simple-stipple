"""Workspace document save/load round trips at the app level."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import square  # noqa: E402


@pytest.fixture()
def app_window(qapp):
    from src.app import App

    w = App()
    w.resize(1200, 800)
    yield w
    w.deleteLater()
    qapp.processEvents()


def _draft_page(w):
    for entry in w._workspace_pages():
        page = entry[-1] if isinstance(entry, tuple) else entry
        if page.__class__.__name__ == "DraftPage":
            return page
    raise AssertionError("DraftPage not found")


def test_workspace_document_round_trip(app_window, qapp):
    w = app_window
    draft = _draft_page(w)
    rt = draft._rt()
    rt.load_polys_by_layer(
        {"Layer 1": [square(0, 0)], "Cut": [square(30, 0)]}, fit=True
    )
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
    monkeypatch.setattr(
        type(w), "_autosave_path", staticmethod(lambda: tmp_path / "auto.json")
    )
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
    monkeypatch.setattr(
        type(w), "_autosave_path", staticmethod(lambda: tmp_path / "auto.json")
    )
    w._workspace_dirty = False
    w._autosave_workspace()
    assert not (tmp_path / "auto.json").exists()
