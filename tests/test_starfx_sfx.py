"""SFX data-only import, cached typography and active-object isolation."""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from simple_stipple.core.formats.starfx import read_starfx


def _encode(root):
    """Minimal NRBF fixture writer: objects, arrays and boxed primitives only."""
    next_id = 0

    def string(text):
        data = text.encode()
        size = len(data)
        prefix = bytearray()
        while size >= 128:
            prefix.append((size & 127) | 128)
            size >>= 7
        return bytes(prefix) + bytes([size]) + data

    def record(value):
        nonlocal next_id
        if value is None:
            return b"\x0a"
        if isinstance(value, bool):
            return b"\x08\x01" + bytes([value])
        if isinstance(value, (int, float)):
            return b"\x08\x06" + struct.pack("<d", value)
        next_id += 1
        obj_id = struct.pack("<i", next_id)
        if isinstance(value, str):
            return b"\x06" + obj_id + string(value)
        if isinstance(value, list):
            return b"\x10" + obj_id + struct.pack("<i", len(value)) + b"".join(map(record, value))
        fields = [(k, v) for k, v in value.items() if k != "__class__"]
        return (b"\x04" + obj_id + string(value["__class__"]) + struct.pack("<i", len(fields))
                + b"".join(string(k) for k, v in fields) + b"\x02" * len(fields)
                + b"".join(record(v) for k, v in fields))

    return b"\x00" + struct.pack("<iiii", 1, -1, 1, 0) + record(root) + b"\x0b"


def _point(x, y):
    return {"__class__": "LaserStar.Canvas.Entities.SimPoint", "xVal": x, "yVal": y}


def _job(entity, name="Artwork"):
    return {"__class__": "StarFX_Control.Classes.JobObject",
            "<Name>k__BackingField": name, "<CadEntity>k__BackingField": entity}


def _read(tmp_path, entities):
    path = tmp_path / "job.SFX"
    path.write_bytes(_encode({"__class__": "StarFX_Control.Classes.JobBase",
                              "<JobObjects>k__BackingField": entities}))
    return read_starfx(path, lambda *args: pytest.fail("SFX must use original cached glyphs"))


def test_cached_vectors_are_scaled_rotated_and_never_open_external_files(tmp_path):
    entity = {"__class__": "LaserStar.Canvas.Entities.VectorEntity",
              "_relativeVectorFormatData": [40, -1, -2, 41, 2, 0, 41, 0, 4],
              "_relativeVectorFormatLowerLeft": _point(-1, -2),
              "_relativeVectorFormatUpperRight": _point(1, 2),
              "_location": _point(10, 20), "_width": 4, "_height": 8,
              "_angle": math.pi / 2, "referencefilename": "C:\\private\\missing.Fvi"}
    document = _read(tmp_path, [_job(entity)])
    assert document.object_count == 1
    for actual, expected in zip(document.paths[0], [(10, 20), (10, 24), (2, 24)], strict=True):
        assert actual == pytest.approx(expected)


def test_text_cache_preserves_font_spacing_scaling_and_separate_objects(tmp_path):
    entity = {"__class__": "LaserStar.Canvas.Entities.MultilineTextEntity",
              "textRelativeVectorFormatData": [40, 0, -10000, 41, 10000, 0, 41, 0, 10000, -1],
              "TextEntity+relativeVectorFormatLowerLeft": _point(0, -10000),
              "TextEntity+relativeVectorFormatUpperRight": _point(10000, 0),
              "TextEntity+location": _point(3, 4), "TextEntity+textAngle": 0,
              "TextEntity+textString": "FIRE", "scaleX": 2, "scaleY": 3,
              "TextEntity+fontFaceName": "Unavailable Font"}
    circle = {"__class__": "LaserStar.Canvas.Entities.CircleEntity",
              "BaseArcEntity+_centerPoint": _point(10, 11),
              "BaseArcEntity+_majorAxisLength": .6,
              # Stale historical artwork must not be traversed.
              "CadEntity+<InitialEntity>k__BackingField": entity}
    document = _read(tmp_path, [_job(entity, "Text1"), _job(circle, "Circle1")])
    assert document.object_count == 2
    for actual, expected in zip(document.objects[0].paths[0], [(3, 1), (5, 1), (5, 4)], strict=True):
        assert actual == pytest.approx(expected)
    assert document.objects[0].meta["name"] == "FIRE"
    params = document.objects[0].meta["text_params"]
    assert {k: params[k] for k in ("text", "family", "bold", "italic")} == {
        "text": "FIRE", "family": "Unavailable Font", "bold": False, "italic": False}
    assert params["height_mm"] == pytest.approx(3.0)  # cached glyph box height, not re-rendered
    assert "Unavailable Font" in params["edit_notice"] and "stretch 200% x 300%" in params["edit_notice"]
    assert "text_params" not in document.objects[1].meta
    assert document.objects[1].kind == "circle"
    assert document.objects[1].meta["radius"] == .6


def _text(string, x, **extra):
    return {"__class__": "LaserStar.Canvas.Entities.MultilineTextEntity",
            "textRelativeVectorFormatData": [40, 0, 0, 41, 10000, 0, 41, 0, 10000, 40, 20000, 0,
                                             41, 5000, 0, 41, 0, 5000, -1],
            "TextEntity+relativeVectorFormatLowerLeft": _point(0, 0),
            "TextEntity+relativeVectorFormatUpperRight": _point(2.5, 1),
            "TextEntity+location": _point(x, 0), "TextEntity+textAngle": 0,
            "TextEntity+textString": string, "TextEntity+fontFaceName": "Tahoma",
            "TextEntity+fontStyle": {"__class__": "System.Drawing.FontStyle", "value__": 3},
            "TextEntity+textHeight": 4.0, "TextEntity+height": 1.0, **extra}


def test_text_style_height_and_invalid_text_fallback(tmp_path):
    document = _read(tmp_path, [_job(_text("A\nB", 0, scaleY=2.0)),
                                _job(_text("  ", 5), "Blank")])
    params = document.objects[0].meta["text_params"]
    assert (params["bold"], params["italic"], params["height_mm"]) == (True, True, 2.0)
    assert "text_params" not in document.objects[1].meta  # blank text stays plain paths


def test_imported_text_edits_as_native_text_and_undoes(qtbot, monkeypatch, tmp_path):
    from simple_stipple.canvas.operations import drawing
    from simple_stipple.features.draft import page as draft

    circle = {"__class__": "LaserStar.Canvas.Entities.CircleEntity",
              "BaseArcEntity+_centerPoint": _point(30, 1), "BaseArcEntity+_majorAxisLength": 1}
    path = tmp_path / "job.SFX"
    path.write_bytes(_encode({"__class__": "StarFX_Control.Classes.JobBase",
                              "<JobObjects>k__BackingField": [
                                  _job(_text("FIRE", 0, interCharacterSpacing=0.5), "T1"),
                                  _job(_text("SAFE", 10), "T2"), _job(circle, "C")]}))
    page = draft.DraftPage(settings={})
    qtbot.addWidget(page)
    monkeypatch.setattr(draft, "confirm", lambda *args: True)
    monkeypatch.setattr(page, "_review_vector_import", lambda *args: False)
    page._load_vector(str(path))
    canvas = page._canvas
    before = list(canvas._entities)
    fire = [e for e in before if e.meta.get("text_params", {}).get("text") == "FIRE"]
    safe = [e for e in before if e.meta.get("text_params", {}).get("text") == "SAFE"]
    assert len(fire) == len(safe) == 2 and len({e.group for e in fire + safe}) == 2
    assert all(canvas.text_params_at(e.id) for e in fire + safe)
    circle_entity = next(e for e in before if e.kind == "circle")
    assert canvas.text_params_at(circle_entity.id) is None
    # Cached outlines are imported unchanged, not re-rendered.
    assert fire[0].points == [(0, 0), (1.0, 0), (1.0, 1.0)]

    asked = []
    monkeypatch.setattr(drawing, "confirm", lambda _p, title, text: asked.append(text) or False)
    canvas.prompt_edit_text(fire[0].id)  # declining the warning leaves everything untouched
    assert "StarFX" in asked[0] and "character spacing 0.5" in asked[0]
    assert canvas._entities == before

    values = {"text": "FIRED", "family": "Tahoma", "height_mm": 6.0, "bold": False, "italic": False}
    assert canvas.rebuild_text(fire[0].id, values)
    after = canvas._entities
    assert not {e.id for e in fire} & {e.id for e in after}
    assert {e.id for e in safe + [circle_entity]} <= {e.id for e in after}
    rebuilt = [e for e in after if (e.meta.get("text_params") or {}).get("text") == "FIRED"]
    assert rebuilt and all("edit_notice" not in e.meta["text_params"] for e in rebuilt)
    assert {e.group for e in rebuilt}.isdisjoint({e.group for e in safe})
    ys = [y for e in rebuilt for _, y in e.points]
    assert max(ys) - min(ys) == pytest.approx(6.0, rel=1e-3)
    assert min(x for e in rebuilt for x, _ in e.points) == pytest.approx(0.0, abs=1e-6)
    canvas._canvas_service.undo()
    assert [e.id for e in canvas._entities] == [e.id for e in before]
    assert canvas.text_params_at(fire[0].id)["text"] == "FIRE"


def test_vector_can_draw_from_origin_without_initial_move(tmp_path):
    entity = {"__class__": "LaserStar.Canvas.Entities.VectorEntity",
              "_relativeVectorFormatData": [41, 1, 1],
              "_relativeVectorFormatLowerLeft": _point(0, 0),
              "_relativeVectorFormatUpperRight": _point(1, 1),
              "_location": _point(0, 0), "_width": 1, "_height": 1}
    assert _read(tmp_path, [_job(entity)]).paths == [[(0, 0), (1, 1)]]


@pytest.mark.parametrize("data", [[40, 1], [99, 0, 0], [40, float('nan'), 0]])
def test_corrupt_cached_geometry_fails_before_import(tmp_path, data):
    entity = {"__class__": "LaserStar.Canvas.Entities.VectorEntity",
              "_relativeVectorFormatData": data,
              "_relativeVectorFormatLowerLeft": _point(0, 0),
              "_relativeVectorFormatUpperRight": _point(1, 1),
              "_location": _point(0, 0), "_width": 1, "_height": 1}
    with pytest.raises(ValueError):
        _read(tmp_path, [_job(entity)])


def test_unsupported_entity_is_not_silently_dropped(tmp_path):
    with pytest.raises(ValueError, match="Unsupported SFX entity"):
        _read(tmp_path, [_job({"__class__": "LaserStar.Canvas.Entities.UnknownEntity"})])


SAMPLES = Path(__file__).resolve().parents[2] / "aa-laser" / "starfx-jobs"


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="Local user sample jobs are not installed")
def test_real_receiver_cached_artwork():
    document = read_starfx(SAMPLES / "AR RECEIVER ENGRAVINGS" / "accurate arms receiver logo NEW.sfx",
                           lambda *args: pytest.fail("Cached SFX must not substitute fonts"))
    assert document.object_count == 10
    circles = [o for o in document.objects if o.kind == "circle"]
    assert len(circles) == 3
    assert [o.meta["radius"] for o in circles] == pytest.approx([.5982, .6, .6])
    assert {o.meta["name"] for o in document.objects} >= {"FIRE", "SAFE"}
    vector = document.objects[0]
    points = [p for path in vector.paths for p in path]
    assert max(x for x,y in points) - min(x for x,y in points) == pytest.approx(58.1646)
    assert max(y for x,y in points) - min(y for x,y in points) == pytest.approx(45.3375)
