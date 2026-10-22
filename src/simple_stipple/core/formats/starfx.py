"""Static artwork recovery from StarFX FMP companions; never run job code.

FMB is encoded program data. It is accepted only with a same-stem readable
FMP companion. Only literal drawing blocks before RETURN are recovered once,
regardless of pass counts. This is not a StarFX interpreter.

Layout semantics, with the evidence each rests on (FiberStar command reference
``Contents.pdf`` plus the ``accurate arms receiver logo NEW`` and ``hi standard``
SFX/FMP pairs, whose SFX entities carry explicit centre/width/height/radius):

* ``DRAWCIRCLE`` takes a *radius* (reference text; SFX ``_majorAxisLength`` .5982
  for ``DRAWCIRCLE .598``). ``DRAWRECT`` is width, height, centred on ``MOVEABS``.
* ``IMAGEHEIGHT`` is the final height of the FVI bounding box in job units
  (SFX ``_height`` 45.3375 = 1.5 x the 30.225 raw FVI extent; width scales equally).
  Without it the FVI keeps its native 0.01 inch units.
* ``IMAGEJUSTIFY 0`` places the scaled FVI bounding-box centre on ``MOVEABS``
  (SFX entity location == ``MOVEABS``; ``ROTATEABS`` pivots there too).
  Codes 1-9 follow the reference's Top/Middle/Bottom rows; the left-to-right
  column order is assumed and reported.
* ``DRAWTEXT`` with ``TEXTJUSTIFYC`` puts the outline-box bottom centre on
  ``MOVEABS`` (SFX box [-6.136, .1004, 5.106, 4.1004] for ``MOVEABS -.515,.1``,
  height 4). ``TEXTJUSTIFYL``/``R`` anchor the left/right edge. The numeric
  argument (200 in jobs) is kept in metadata, not interpreted.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from simple_stipple.core.formats.fvi import read_fvi

Point = tuple[float, float]
Polys = list[list[Point]]
TextRenderer = Callable[[str, str, float], Polys]
_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class StarfxObject:
    name: str
    paths: Polys
    kind: str
    meta: dict[str, Any]


@dataclass(frozen=True)
class StarfxDocument:
    objects: tuple[StarfxObject, ...]
    warnings: tuple[str, ...]

    @property
    def paths(self) -> Polys:
        return [poly for obj in self.objects for poly in obj.paths]

    @property
    def object_count(self) -> int:
        return len(self.objects)


def _read(path: Path) -> str:
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError(f"StarFX file exceeds 32 MiB: {path.name}")
    return path.read_text(encoding="utf-8-sig")


def _companion(path: Path, name: str) -> Path:
    # Resolve only siblings, never follow absolute paths embedded in a job.
    matches = [p for p in path.parent.iterdir() if p.name.casefold() == name.casefold()]
    if len(matches) != 1 or not matches[0].is_file():
        raise ValueError(f"Missing or ambiguous StarFX companion: {name}")
    resolved = matches[0].resolve()
    if resolved.parent != path.parent.resolve():
        raise ValueError(f"StarFX companion must be inside the job folder: {name}")
    return resolved


def _strip_comment(payload: str) -> str:
    quoted = False
    for index, char in enumerate(payload):
        if char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            return payload[:index].rstrip()
    return payload.strip()


def _numbers(payload: str, count: int) -> list[float]:
    parts = [part for part in re.split(r"[,\s]+", payload.strip()) if part]
    values = [float(part) for part in parts]
    if len(values) != count or not all(math.isfinite(v) for v in values):
        raise ValueError(f"Expected {count} finite literal numbers: {payload}")
    return values


def _quoted(payload: str) -> str:
    match = re.fullmatch(r'"([^"\r\n]*)"', payload.strip())
    if not match:
        raise ValueError(f"Expected a literal quoted string: {payload}")
    return match[1]


# Commands that change where or how geometry lands but are not modelled here.
# Importing past them would silently misplace artwork, so they are hard errors.
_UNSUPPORTED_LAYOUT = frozenset({
    "MOVEREL", "MOVEDIST", "ROTATEREL", "FIELDROTATION", "FIELDSCALE",
    "MIRRORHORIZ", "MIRRORVERT", "MIRRORON", "MIRROROFF", "TEXTCIRCLE",
    "TEXTVERTICAL", "TEXTRADIUS", "TEXTLEFT", "TEXTCENTER", "TEXTRIGHT",
    "TEXTCW", "TEXTCCW", "USEFONT", "DEFFONT", "DEFIMAGE",
})
_JUSTIFY = {"TEXTJUSTIFYL": "L", "TEXTJUSTIFYC": "C", "TEXTJUSTIFYR": "R"}
# IMAGEJUSTIFY: 0 = centred (observed); 1-9 = rows top/middle/bottom per the
# reference, columns left/centre/right (column order assumed).
_IMAGE_ROWS = ("top", "middle", "bottom")
_IMAGE_COLS = ("left", "center", "right")


@dataclass
class _State:
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    height: float = 4.0
    sx: float = 1.0
    sy: float = 1.0
    font_id: int = 1
    unit: float = 1.0  # millimetres per job unit
    justify: str = "C"
    justify_arg: float | None = None
    justify_explicit: bool = False
    spacing: float = 0.0
    image_height: float | None = None
    image_justify: int = 0
    image_sx: float = 1.0
    image_sy: float = 1.0

    def configure(self, command: str, payload: str, warnings: list[str]) -> None:
        if command == "MOVEABS":
            x, y = _numbers(payload, 2)
            self.x, self.y = x * self.unit, y * self.unit
        elif command == "UNITSMM":
            self.unit = 1.0
        elif command == "UNITSINCH":
            self.unit = 25.4
        elif command == "USETTF":
            self.font_id = int(payload)
        elif command == "ROTATEABS":
            self.angle = _numbers(payload, 1)[0]
            if self.angle % 180:
                warnings.append(
                    "ROTATEABS direction is not documented; positive angles are "
                    "assumed counter-clockwise (only 0 and 180 are verified)."
                )
        elif command.startswith("TTF"):
            if command != "TTFREGULAR":
                raise ValueError(f"Unsupported font style command {command}")
        elif command.startswith("IMAGE"):
            self._configure_image(command, payload, warnings)
        else:
            self._configure_text(command, payload)

    def _configure_text(self, command: str, payload: str) -> None:
        if command == "TEXTHEIGHT":
            value = _numbers(payload, 1)[0]
            if value <= 0:
                raise ValueError("Text height must be positive")
            self.height = value * self.unit
        elif command in {"TEXTSCALEX", "TEXTSCALEY"}:
            value = _numbers(payload, 1)[0]
            if value <= 0:
                raise ValueError(f"{command} must be positive")
            setattr(self, "sx" if command.endswith("X") else "sy", value / 100)
        elif command in _JUSTIFY:
            self.justify = _JUSTIFY[command]
            self.justify_explicit = True
            self.justify_arg = _numbers(payload, 1)[0] if payload.strip() else None
        elif command == "SPACING":
            self.spacing = _numbers(payload, 1)[0]

    def _configure_image(self, command: str, payload: str, warnings: list[str]) -> None:
        value = _numbers(payload, 1)[0]
        if command == "IMAGEHEIGHT":
            if value <= 0:
                raise ValueError("Image height must be positive")
            self.image_height = value * self.unit
        elif command == "IMAGEJUSTIFY":
            if value != int(value) or not 0 <= value <= 9:
                raise ValueError(f"IMAGEJUSTIFY must be an integer 0-9: {payload}")
            self.image_justify = int(value)
            if value:
                warnings.append(
                    "IMAGEJUSTIFY 1-9 column order (left to right) is assumed; "
                    "only 0 (centred) is verified against SFX data."
                )
        elif command in {"IMAGESCALEX", "IMAGESCALEY"}:
            if value <= 0:
                raise ValueError(f"{command} must be positive")
            setattr(self, "image_sx" if command.endswith("X") else "image_sy", value / 100)
            warnings.append(f"{command} is read as a percentage (unit not documented).")


@dataclass(frozen=True)
class _Drawn:
    kind: str
    paths: Polys  # local to the anchor, before ROTATEABS
    meta: dict[str, Any]


def _bounds(polys: Polys) -> tuple[float, float, float, float]:
    xs = [px for poly in polys for px, _ in poly]
    ys = [py for poly in polys for _, py in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _draw_text(payload: str, state: _State, fonts: dict[int, str],
               render_text: TextRenderer, warnings: list[str], number: int) -> _Drawn:
    family = fonts.get(state.font_id)
    if family is None:
        raise ValueError(f"Undefined font {state.font_id}")
    text = _quoted(payload)
    local = render_text(text, family, state.height)
    if not local:
        raise ValueError("Text renderer returned no outlines")
    if not state.justify_explicit:
        warnings.append(
            f"Line {number}: no TEXTJUSTIFY before DRAWTEXT; the default is undocumented, "
            "centre assumed."
        )
    if state.spacing:
        warnings.append(f"Line {number}: SPACING {state.spacing:g} is not applied to text.")
    minx, miny, maxx, _ = _bounds(local)
    anchor_x = {"L": minx, "C": (minx + maxx) / 2, "R": maxx}[state.justify]
    paths = [[((px - anchor_x) * state.sx, (py - miny) * state.sy) for px, py in poly]
             for poly in local]
    meta = {
        "name": text,
        "text_params": {"text": text, "family": family, "height_mm": state.height,
                        "bold": False, "italic": False},
        "starfx_text": {
            "justify": state.justify, "justify_arg": state.justify_arg,
            "rotation_deg": state.angle, "scale_x_percent": state.sx * 100,
            "scale_y_percent": state.sy * 100, "spacing": state.spacing,
            "anchor": (state.x, state.y),
        },
    }
    return _Drawn("polyline", paths, meta)


def _draw_image(payload: str, state: _State, source: Path, warnings: list[str]) -> _Drawn:
    name = PureWindowsPath(_quoted(payload)).name
    document = read_fvi(_companion(source, name))
    if document.report.has_issues:
        warnings.append(f"{name}: unsupported or malformed FVI commands were skipped.")
    polys = [list(poly) for poly in document.paths]
    if not polys:
        raise ValueError(f"No drawable geometry in {name}")
    minx, miny, maxx, maxy = _bounds(polys)
    scale = 1.0
    if state.image_height is None:
        warnings.append(f"{name}: no IMAGEHEIGHT; native FVI size (0.01 inch units) used.")
    elif maxy - miny <= 0:
        raise ValueError(f"{name} has zero height; IMAGEHEIGHT cannot be applied")
    else:
        scale = state.image_height / (maxy - miny)
    if state.sx != 1 or state.sy != 1:
        warnings.append(f"{name}: TEXTSCALEX/Y are not applied to images.")
    row, col = (1, 1) if state.image_justify == 0 else divmod(state.image_justify - 1, 3)
    anchor_x = (minx, (minx + maxx) / 2, maxx)[col]
    anchor_y = (maxy, (miny + maxy) / 2, miny)[row]
    fx, fy = scale * state.image_sx, scale * state.image_sy
    paths = [[((px - anchor_x) * fx, (py - anchor_y) * fy) for px, py in poly]
             for poly in polys]
    meta = {"name": name, "starfx_image": {
        "file": name, "scale": scale, "scale_x_percent": state.image_sx * 100,
        "scale_y_percent": state.image_sy * 100, "rotation_deg": state.angle,
        "justify": state.image_justify,
        "justify_anchor": (_IMAGE_ROWS[row], _IMAGE_COLS[col]),
        "width_mm": (maxx - minx) * fx, "height_mm": (maxy - miny) * fy,
        "anchor": (state.x, state.y),
    }}
    return _Drawn("polyline", paths, meta)


def _draw(command: str, payload: str, state: _State, fonts: dict[int, str],
          source: Path, render_text: TextRenderer, warnings: list[str],
          number: int) -> _Drawn | None:
    if command == "DRAWRECT":
        w, h = (v * state.unit for v in _numbers(payload, 2))
        if w <= 0 or h <= 0:
            raise ValueError("Rectangle width and height must be positive")
        path = [(-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2), (-w/2, -h/2)]
        return _Drawn("rectangle", [path], {
            "center": (state.x, state.y), "width": w, "height": h,
            "rotation": state.angle})
    if command == "DRAWCIRCLE":
        radius = _numbers(payload, 1)[0] * state.unit
        if radius <= 0:
            raise ValueError("Circle radius must be positive")
        points = [(radius * math.cos(i*math.tau/96), radius * math.sin(i*math.tau/96))
                  for i in range(96)]
        return _Drawn("circle", [points + [points[0]]], {
            "center": (state.x, state.y), "radius": radius})
    if command == "DRAWTEXT":
        return _draw_text(payload, state, fonts, render_text, warnings, number)
    if command == "DRAWFVI":
        return _draw_image(payload, state, source, warnings)
    warnings.append(f"Line {number}: unsupported {command} skipped.")
    return None


def _place(name: str, drawn: _Drawn, state: _State) -> StarfxObject:
    radians = math.radians(state.angle)
    c, s = math.cos(radians), math.sin(radians)
    paths = [[(state.x + px*c - py*s, state.y + px*s + py*c) for px, py in poly]
             for poly in drawn.paths]
    meta = {**drawn.meta}
    meta.setdefault("name", name)
    return StarfxObject(name, paths, drawn.kind, meta)


def _fmp_source(source: Path) -> Path:
    if source.suffix.lower() == ".fmb":
        return _companion(source, source.with_suffix(".fmp").name)
    if source.suffix.lower() != ".fmp":
        raise ValueError("Choose a StarFX FMP or FMB job.")
    return source


def _is_object_header(line: str) -> bool:
    return bool(re.fullmatch(
        r"(?:VECTOR|RECTANGLE|CIRCLE|MULTILINETEXT)\d+", line[1:].strip(), re.I
    ))


def read_starfx(path: str | Path, render_text: TextRenderer) -> StarfxDocument:
    source = Path(path)
    if source.suffix.lower() == ".sfx":
        from simple_stipple.core.formats.starfx_sfx import read_sfx  # avoids a cycle

        return read_sfx(source)
    source = _fmp_source(source)
    lines = _read(source).splitlines()
    warnings = [
        "Static StarFX artwork recovery only: laser settings, pass loops and program logic "
        "are not executed. Inspect the result before use.",
        "Layout is read as millimetres unless UNITSINCH is set: circles take a radius, "
        "rectangles are centred, images use IMAGEHEIGHT with the bounding-box centre on "
        "MOVEABS, and text anchors its outline-box bottom at MOVEABS per TEXTJUSTIFY.",
    ]
    fonts: dict[int, str] = {}
    objects: list[StarfxObject] = []
    unit = 1.0
    state = _State()
    in_object = False
    name = ""
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.upper() == "RETURN":
            break  # Do not import geometry from function definitions.
        if line.startswith(";"):
            in_object = _is_object_header(line)
            if in_object:
                name = line[1:].strip()
                state = _State(unit=unit)
            else:
                warnings.append(f"Line {number}: unrecognised object block {line}.")
            continue
        command, _, payload = line.partition(" ")
        command, payload = command.upper(), _strip_comment(payload)
        try:
            if command == "DEFTTF":
                index, family = payload.split(",", 1)
                fonts[int(index)] = _quoted(family)
                continue
            if command in _UNSUPPORTED_LAYOUT:
                raise ValueError(f"Unsupported layout command {command}; "
                                 "refusing to import misplaced artwork")
            if command in {"UNITSMM", "UNITSINCH"}:
                unit = 25.4 if command == "UNITSINCH" else 1.0
                state.unit = unit
                continue
            if not in_object:
                if command.startswith("DRAW"):
                    warnings.append(f"Line {number}: drawing outside a supported object block skipped.")
                continue
            if not command.startswith("DRAW"):
                state.configure(command, payload, warnings)
                continue
            drawn = _draw(command, payload, state, fonts, source, render_text, warnings, number)
            if drawn:
                objects.append(_place(name, drawn, state))
        except (ValueError, OSError) as exc:
            # Fail before replacing the canvas rather than silently lose an object.
            raise ValueError(f"{source.name}, line {number}: {exc}") from exc
    if not objects:
        raise ValueError("No supported StarFX artwork found. A readable FMP with labelled drawing blocks is required.")
    return StarfxDocument(tuple(objects), tuple(dict.fromkeys(warnings)))
