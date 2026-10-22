"""Draft entry point: job routing, confirmation, add/replace and failures."""
from pathlib import Path

import pytest

from simple_stipple.core.document.model import EntityRecord
from simple_stipple.features.draft import page as draft


@pytest.mark.parametrize("suffix", [".Fmp", ".Fmb", ".sfx"])
def test_job_entry_point_add_cancel_replace_and_undo(qtbot, monkeypatch, tmp_path, suffix):
    if suffix == ".sfx":
        path = (Path(__file__).resolve().parents[2] / "aa-laser" / "starfx-jobs"
                / "AR RECEIVER ENGRAVINGS" / "accurate arms receiver logo NEW.sfx")
        if not path.is_file():
            pytest.skip("Local SFX sample not installed")
    else:
        path = tmp_path / ("job" + suffix)
        (tmp_path / "job.Fmp").write_text(';CIRCLE1\nMOVEABS 2,3\nDRAWCIRCLE 1\nRETURN\n')
        if suffix == ".Fmb":
            path.write_bytes(b"encoded job is never executed")
    page = draft.DraftPage(settings={})
    qtbot.addWidget(page)
    canvas = page._canvas
    original = EntityRecord(points=[(100, 0), (110, 0)])
    canvas._canvas_service.update_document(lambda doc: doc.entities.append(original))
    monkeypatch.setattr(draft, "confirm", lambda *args: False)
    page._load_vector(str(path), append=True)
    assert [e.id for e in canvas._entities] == [original.id]
    monkeypatch.setattr(draft, "confirm", lambda *args: True)
    page._load_vector(str(path), append=True)
    assert len(canvas._entities) > 1
    assert canvas._entities[0].id == original.id
    assert not canvas._sel
    canvas._canvas_service.undo()
    assert [e.id for e in canvas._entities] == [original.id]
    monkeypatch.setattr(page, "_review_vector_import", lambda *args: False)
    page._load_vector(str(path))
    assert all(e.id != original.id for e in canvas._entities)
    assert any(e.kind == "circle" for e in canvas._entities)
    canvas._canvas_service.undo()
    assert [e.id for e in canvas._entities] == [original.id]


def test_malformed_job_does_not_replace_existing_drawing(qtbot, monkeypatch, tmp_path):
    page = draft.DraftPage(settings={})
    qtbot.addWidget(page)
    original = EntityRecord(points=[(0, 0), (1, 0)])
    page._canvas._canvas_service.update_document(lambda doc: doc.entities.append(original))
    path = tmp_path / "bad.sfx"
    path.write_bytes(b"not NRBF")
    errors = []
    monkeypatch.setattr(draft, "show_error", lambda *args: errors.append(str(args[-1])))
    monkeypatch.setattr(page, "_review_vector_import", lambda *args: False)
    page._load_vector(str(path))
    assert errors
    assert [e.id for e in page._canvas._entities] == [original.id]
