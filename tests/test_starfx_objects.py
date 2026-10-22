"""Object-aware StarFX import: independent text, circles and undoable edits."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt

from simple_stipple.canvas.operations.drawing import text_to_polylines
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.document.model import Document, EntityRecord
from simple_stipple.core.formats.starfx import read_starfx
from simple_stipple.features.draft.starfx_import import import_starfx_objects


def _job(tmp_path):
    source = tmp_path / "job.Fmp"
    source.write_text('''DEFTTF 1,"Tahoma"
;CIRCLE1
MOVEABS 20,0
DRAWCIRCLE 2
;MULTILINETEXT2
MOVEABS 0,0
TEXTHEIGHT 4
USETTF 1
DRAWTEXT "FIRE"
;MULTILINETEXT3
MOVEABS 0,-10
TEXTHEIGHT 4
USETTF 1
DRAWTEXT "SAFE"
RETURN
''')
    return read_starfx(source, lambda text, family, height: text_to_polylines(
        text, family=family, height_mm=height
    ))


def test_word_selection_and_move_leave_circle_and_other_word_unchanged(qtbot, tmp_path):
    canvas = DxfCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(900, 600)
    canvas.show()
    artwork = _job(tmp_path)
    canvas._canvas_service.update_document(
        lambda doc: import_starfx_objects(doc, artwork, append=False)
    )
    canvas.set_mode("select")
    canvas._needs_fit = True
    canvas._fit()
    assert not canvas._sel
    fire = [e for e in canvas._entities if (e.meta or {}).get("name") == "FIRE"]
    safe = [e for e in canvas._entities if (e.meta or {}).get("name") == "SAFE"]
    circle = next(e for e in canvas._entities if e.kind == "circle")
    assert len(fire) > 1
    assert len({e.group for e in fire}) == 1
    assert fire[0].group != safe[0].group
    assert circle.group is None
    assert circle.meta["radius"] == 2
    before = {e.id: deepcopy(e.points) for e in canvas._entities}
    px, py = fire[0].points[0]
    cx, cy = canvas._w2c(px, py)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(cx), round(cy)))
    assert canvas._sel == {e.id for e in fire}
    qtbot.keyClick(canvas, Qt.Key.Key_Right)
    assert all(e.points != before[e.id] for e in canvas._entities if e.id in canvas._sel)
    assert all(e.points == before[e.id] for e in canvas._entities if e.id not in canvas._sel)
    canvas._canvas_service.undo()
    for entity in canvas._entities:
        for point, original in zip(entity.points, before[entity.id], strict=True):
            assert point == pytest.approx(original)


def test_append_avoids_group_collisions_and_replace_is_undoable(qtbot, tmp_path):
    canvas = DxfCanvas()
    qtbot.addWidget(canvas)
    existing = [EntityRecord(points=[(0, 0), (1, 0)], group=0),
                EntityRecord(points=[(0, 1), (1, 1)], group=0)]
    original = Document(entities=existing, group_labels={0: "Existing"})
    canvas._canvas_service.replace_document(original)
    artwork = _job(tmp_path)
    canvas._canvas_service.update_document(
        lambda doc: import_starfx_objects(doc, artwork, append=True)
    )
    assert all(e.group != 0 for e in canvas._entities[2:])
    assert canvas._document.group_labels[0] == "Existing"
    canvas._canvas_service.undo()
    assert [e.id for e in canvas._entities] == [e.id for e in existing]
    canvas._canvas_service.redo()
    count = len(canvas._entities)
    canvas._canvas_service.update_document(
        lambda doc: import_starfx_objects(doc, artwork, append=False)
    )
    assert len(canvas._entities) == count - 2
    canvas._canvas_service.undo()
    assert len(canvas._entities) == count
