"""T edits a selected text object, retaining its font and cached artwork."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase

from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog
from simple_stipple.canvas.operations import drawing
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.formats.starfx import StarfxDocument, StarfxObject
from simple_stipple.features.draft.starfx_import import import_starfx_objects


@pytest.fixture
def canvas(qtbot):
    view = DxfCanvas()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    return view


def _import_text(canvas):
    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    params = {"text": "FIRE", "family": family, "height_mm": 4.07321,
              "bold": True, "italic": True, "edit_notice": "Cached StarFX artwork"}
    paths: list[list[tuple[float, float]]] = [[(0, 0), (2, 0), (2, 4), (0, 0)],
             [(3, 0), (4, 0), (4, 4), (3, 0)]]
    artwork = StarfxDocument(objects=(StarfxObject(
        "FIRE", paths, "polyline", {"text_params": params}),), warnings=())
    canvas._canvas_service.update_document(
        lambda document: import_starfx_objects(document, artwork, append=False))
    canvas._sel = {e.id for e in canvas._entities}
    return params


@pytest.mark.parametrize("imported", [False, True])
def test_t_edits_selected_text_with_original_font(canvas, qtbot, monkeypatch, imported):
    if imported:
        params = _import_text(canvas)
    else:
        family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        params = {"text": "FIRE", "family": family, "height_mm": 4.0,
                  "bold": True, "italic": True}
        canvas.add_text_at(0, 0, **params)
    original_ids = {e.id for e in canvas._entities}
    opened = []

    def edit(dialog):
        opened.append((dialog.windowTitle(), dialog.values()))
        dialog._text_edit.setPlainText("EDITED")
        return AddTextDialog.DialogCode.Accepted

    monkeypatch.setattr(drawing, "confirm", lambda *args: True)
    monkeypatch.setattr(AddTextDialog, "exec", edit)
    qtbot.keyClick(canvas, Qt.Key.Key_T)
    assert opened[0][0] == "Edit Text"
    assert opened[0][1]["text"] == "FIRE"
    assert opened[0][1]["family"] == params["family"]
    assert opened[0][1]["bold"] and opened[0][1]["italic"]
    assert original_ids.isdisjoint(e.id for e in canvas._entities)
    assert {e.meta["text_params"]["text"] for e in canvas._entities} == {"EDITED"}
    canvas._canvas_service.undo()
    assert {e.id for e in canvas._entities} == original_ids


def test_accepting_unchanged_imported_text_keeps_exact_cached_artwork(canvas, monkeypatch):
    _import_text(canvas)
    before = deepcopy(canvas._entities)
    monkeypatch.setattr(drawing, "confirm", lambda *args: True)
    monkeypatch.setattr(AddTextDialog, "exec", lambda self: AddTextDialog.DialogCode.Accepted)
    canvas.prompt_edit_text(before[0].id)
    assert canvas._entities == before


def test_t_without_selection_still_adds_text(canvas, qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(AddTextDialog, "exec", lambda dialog: (
        opened.append(dialog.windowTitle()) or AddTextDialog.DialogCode.Rejected))
    qtbot.keyClick(canvas, Qt.Key.Key_T)
    assert opened == ["Add Text"]


def test_t_with_multiple_text_objects_does_not_add_or_edit(canvas, qtbot, monkeypatch):
    canvas.add_text_at(0, 0, text="ONE", family="Tahoma", height_mm=4)
    first = set(canvas._sel)
    canvas.add_text_at(20, 0, text="TWO", family="Tahoma", height_mm=4)
    canvas._sel |= first
    opened = []
    monkeypatch.setattr(AddTextDialog, "exec", lambda dialog: (
        opened.append(dialog.windowTitle()) or AddTextDialog.DialogCode.Rejected))
    qtbot.keyClick(canvas, Qt.Key.Key_T)
    assert not opened
