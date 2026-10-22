"""Recover active SFX artwork from inert NRBF data, never historical undo objects.

Cached vector/text streams contain relative pen moves (40) and lines (41).
Text streams use 1/10000 mm; vector streams use their stored native extents.
No assemblies, embedded paths, job events or serialized methods are executed.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from simple_stipple.core.formats.nrbf import class_name, list_items, read_nrbf
from simple_stipple.core.formats.starfx import StarfxDocument, StarfxObject


def _field(obj: dict, name: str, default: Any = None) -> Any:
    return obj.get(f"<{name}>k__BackingField", default)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("SFX geometry requires finite numbers")
    return float(value)


def _point(value: Any) -> tuple[float, float]:
    if not isinstance(value, dict):
        raise ValueError("Missing SFX point")
    if abs(_number(value.get("zVal", 0))) > 1e-8:
        raise ValueError("Non-planar SFX geometry is unsupported")
    return _number(value.get("xVal")), _number(value.get("yVal"))


def _stream(data: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(data, list):
        raise ValueError("Missing cached SFX outlines")
    paths = []
    current = [(0.0, 0.0)]
    x = y = 0.0
    index = 0
    while index < len(data):
        command = _number(data[index])
        if command == -1 and index == len(data) - 1:
            break
        if command not in (40, 41) or index + 2 >= len(data):
            raise ValueError(f"Unsupported cached SFX vector command {command}")
        x += _number(data[index + 1])
        y += _number(data[index + 2])
        if command == 40:
            if len(current) > 1:
                paths.append(current)
            current = [(x, y)]
        else:
            current.append((x, y))
        index += 3
    if len(current) > 1:
        paths.append(current)
    return paths


def _cached(entity: dict, *, text: bool) -> list[list[tuple[float, float]]]:
    prefix = "TextEntity+" if text else "_"
    data_key = "textRelativeVectorFormatData" if text else "_relativeVectorFormatData"
    lower_key = "TextEntity+relativeVectorFormatLowerLeft" if text else "_relativeVectorFormatLowerLeft"
    upper_key = "TextEntity+relativeVectorFormatUpperRight" if text else "_relativeVectorFormatUpperRight"
    lower = _point(entity[lower_key])
    upper = _point(entity[upper_key])
    location = _point(entity[f"{prefix}location"])
    if text:
        sx = _number(entity.get("scaleX", 1)) / 10000
        sy = _number(entity.get("scaleY", 1)) / 10000
        angle = _number(entity.get("TextEntity+textAngle", 0))
        offset = (0.0, 0.0)
    else:
        if upper[0] <= lower[0] or upper[1] <= lower[1]:
            raise ValueError("Invalid cached SFX vector bounds")
        sx = _number(entity["_width"]) / (upper[0] - lower[0])
        sy = _number(entity["_height"]) / (upper[1] - lower[1])
        angle = _number(entity.get("_angle", 0))
        offset = lower
    c, s = math.cos(angle), math.sin(angle)
    return [[(location[0] + (x-offset[0])*sx*c - (y-offset[1])*sy*s,
              location[1] + (x-offset[0])*sx*s + (y-offset[1])*sy*c)
             for x, y in path] for path in _stream(entity[data_key])]


def _polyline(entity: dict) -> list[tuple[float, float]]:
    points = [_point(entity["BasePolylineEntity+startPointOfPolyline"])]
    for segment in list_items(entity["BasePolylineEntity+subPathList"]):
        if class_name(segment) != "LaserStar.Canvas.Entities.ConnectedLineSubpath":
            raise ValueError(f"Unsupported SFX subpath {class_name(segment)}")
        points.extend(_point(point) for point in list_items(segment["pointList"]))
    if entity.get("BasePolylineEntity+closed") and points[-1] != points[0]:
        points.append(points[0])
    return points


def _font_flags(entity: dict) -> tuple[bool, bool]:
    """System.Drawing.FontStyle flags: 1 = Bold, 2 = Italic."""
    style = entity.get("TextEntity+fontStyle")
    value = style.get("value__") if isinstance(style, dict) else 0
    number = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.5
    flags = int(number) if number.is_integer() else 0
    return bool(flags & 1), bool(flags & 2)


def _positive(value: Any) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok and math.isfinite(value) and value > 0 else None


def _text_meta(entity: dict, paths: list[list[tuple[float, float]]]) -> dict[str, Any]:
    """Native text metadata for cached SFX text; the outlines stay untouched.

    Only string, family, bold/italic and height are mapped to Simple Stipple's
    text controls. Spacing, scale, rotation and line layout are not editable
    there, so they are recorded and named in the pre-edit notice instead.
    """
    text = entity.get("TextEntity+textString")
    family = entity.get("TextEntity+fontFaceName")
    if not isinstance(text, str) or not text.strip() or not isinstance(family, str) or not family:
        return {}
    scale_y = _positive(entity.get("scaleY")) or 1.0
    height = _positive(entity.get("TextEntity+height")) or _positive(entity.get("TextEntity+textHeight"))
    if height is not None:
        height *= scale_y
    else:
        ys = [y for path in paths for _, y in path]
        height = max(ys) - min(ys)
    if not height > 0:
        return {}
    bold, italic = _font_flags(entity)
    spacing = entity.get("interCharacterSpacing")
    scale_x = _positive(entity.get("scaleX")) or 1.0
    angle = entity.get("TextEntity+textAngle")
    angle = math.degrees(angle) % 360 if isinstance(angle, (int, float)) and math.isfinite(angle) else 0.0
    lost = []
    if isinstance(spacing, (int, float)) and abs(spacing) > 1e-9:
        lost.append(f"character spacing {spacing:g}")
    if abs(scale_x - 1) > 1e-9 or abs(scale_y - 1) > 1e-9:
        lost.append(f"stretch {scale_x * 100:g}% x {scale_y * 100:g}%")
    if min(angle, 360 - angle) > 1e-6:
        lost.append(f"rotation {angle:g}°")
    notice = (f"This text was imported from a StarFX job as its original cached outlines "
              f"(font '{family}'). Editing redraws it with Simple Stipple's text tool and "
              "local fonts, replacing the cached outlines, any baked-in hatch lines and "
              "the original glyph spacing")
    notice += (f" (also not kept: {', '.join(lost)})." if lost else ".")
    return {
        "text_params": {"text": text, "family": family, "height_mm": height,
                        "bold": bold, "italic": italic, "edit_notice": notice},
        "starfx_text": {"cached_outlines": True, "font": family, "spacing": spacing,
                        "scale_x_percent": scale_x * 100, "scale_y_percent": scale_y * 100,
                        "rotation_deg": angle},
    }


def _object(job: dict) -> StarfxObject:
    entity = _field(job, "CadEntity")
    name = str(_field(job, "Name", "StarFX object"))
    meta: dict[str, Any] = {"name": name}
    kind = "polyline"
    entity_type = (class_name(entity) or "").rsplit(".", 1)[-1]
    if entity_type in {"VectorEntity", "MultilineTextEntity", "TextEntity"}:
        text = entity_type != "VectorEntity"
        paths = _cached(entity, text=text)
        if text:
            meta["name"] = str(entity.get("TextEntity+textString", name))
            # Keep the original outlines (unavailable fonts, spacing) as imported;
            # text_params only make them editable through the native text tool.
            meta.update(_text_meta(entity, paths))
    elif entity_type in {"RectangleEntity", "PolylineEntity"}:
        points = _polyline(entity)
        paths = [points]
        if entity_type == "RectangleEntity" and len(points) == 5:
            a, b, c = points[:3]
            kind = "rectangle"
            meta.update(center=((a[0]+c[0])/2, (a[1]+c[1])/2),
                        width=math.dist(a, b), height=math.dist(b, c),
                        rotation=math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])))
    elif entity_type == "CircleEntity":
        center = _point(entity["BaseArcEntity+_centerPoint"])
        radius = _number(entity["BaseArcEntity+_majorAxisLength"])
        if radius <= 0:
            raise ValueError("SFX circle radius must be positive")
        points = [(center[0] + radius*math.cos(i*math.tau/96),
                   center[1] + radius*math.sin(i*math.tau/96)) for i in range(96)]
        paths = [points + [points[0]]]
        kind = "circle"
        meta.update(center=center, radius=radius)
    else:
        raise ValueError(f"Unsupported SFX entity {entity_type}")
    if not paths:
        raise ValueError("SFX object has no cached artwork")
    if any(not math.isfinite(value) for path in paths for point in path for value in point):
        raise ValueError("SFX geometry overflows finite coordinates")
    return StarfxObject(name, paths, kind, meta)


def read_sfx(path: Path) -> StarfxDocument:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("SFX file exceeds 32 MiB")
    root = read_nrbf(path.read_bytes())
    if class_name(root) != "StarFX_Control.Classes.JobBase":
        raise ValueError("Not a StarFX SFX job")
    warnings = ["Static SFX artwork only: laser settings, events and pass logic are not executed.",
                "Hidden artwork is included for recovery and may overlap visible objects. "
                "Cached text retains any baked-in hatch lines; inspect before use."]
    objects = []
    for job in list_items(_field(root, "JobObjects")):
        if not isinstance(job, dict):
            raise ValueError("Malformed SFX job object")
        if _field(job, "CadEntity") is None:
            if not _field(job, "IsLayer") and not _field(job, "IsGroup"):
                warnings.append(f"{_field(job, 'Name', 'Object')}: non-drawing job item skipped.")
            continue
        try:
            objects.append(_object(job))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{_field(job, 'Name', 'Object')}: {exc}") from exc
    if not objects:
        raise ValueError("No supported SFX artwork found")
    return StarfxDocument(tuple(objects), tuple(warnings))
