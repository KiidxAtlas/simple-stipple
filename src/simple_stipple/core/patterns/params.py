"""Typed parameter schemas for pattern generators.

A generator receives a plain ``dict`` of parameters. The Pattern form
validates what the user types, but parameters also arrive from saved
workspaces (region treatments), where nothing guaranteed a stored value had
the right type — a bad file failed late, inside a generator, as a
``TypeError``.

``ParamSpec`` is the Qt-free description of one generator parameter, and
:func:`normalize_pattern_params` applies a pattern's specs to a stored dict
without changing what an older, well-formed workspace generates:

* present values are coerced to the declared type (``"1.5"`` → ``1.5``);
* values that cannot be coerced — which could only crash before — are
  replaced by the field default;
* values outside today's form range, or choices the form no longer lists,
  are kept as stored (older releases accepted them) and only reported;
* missing keys stay missing, so the generator's own fallback still applies;
* keys the schema does not describe (rotation, density, tile geometry,
  lattice origin…) pass through untouched.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

ParamKind = Literal["float", "int", "checkbox", "combobox"]
ParamValue = float | int | bool | str

_TRUE_TEXT = frozenset({"true", "1", "yes", "on"})
_FALSE_TEXT = frozenset({"false", "0", "no", "off"})


class InvalidParam(ValueError):
    """A stored value cannot be read as its parameter's type."""


@dataclass(frozen=True)
class ParamSpec:
    """One generator parameter: type, default and accepted range."""

    key: str
    kind: ParamKind
    default: ParamValue
    minimum: float | None = None
    maximum: float | None = None
    items: tuple[str, ...] = ()

    @classmethod
    def from_text_default(
        cls,
        key: str,
        kind: ParamKind,
        default_text: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        items: Iterable[str] = (),
    ) -> ParamSpec:
        """Build a spec from the display-text default the form fields use."""
        choices = tuple(items)
        default = cls(key, kind, default_text, minimum, maximum, choices).coerce(default_text)
        return cls(key, kind, default, minimum, maximum, choices)

    def coerce(self, value: object) -> ParamValue:
        """Return ``value`` as this parameter's type, or raise ``InvalidParam``."""
        if self.kind == "checkbox":
            return _coerce_bool(value)
        if self.kind == "combobox":
            if not isinstance(value, str):
                raise InvalidParam(f"{value!r} is not a choice")
            return value
        number = _coerce_number(value)
        return int(number) if self.kind == "int" else number

    def range_issue(self, value: ParamValue) -> str | None:
        """Describe how a well-typed value falls outside the form's range."""
        if self.kind == "combobox":
            if self.items and value not in self.items:
                return f"{value!r} is not one of {list(self.items)}"
            return None
        if self.kind == "checkbox" or isinstance(value, (bool, str)):
            return None
        if self.minimum is not None and value < self.minimum:
            return f"{value:g} is below the minimum {self.minimum:g}"
        if self.maximum is not None and value > self.maximum:
            return f"{value:g} is above the maximum {self.maximum:g}"
        return None


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
    raise InvalidParam(f"{value!r} is not a boolean")


def _coerce_number(value: object) -> float:
    if isinstance(value, bool):
        raise InvalidParam(f"{value!r} is not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError as exc:
            raise InvalidParam(f"{value!r} is not a number") from exc
    else:
        raise InvalidParam(f"{value!r} is not a number")
    if not math.isfinite(number):
        raise InvalidParam(f"{value!r} is not a finite number")
    return number


def normalize_pattern_params(
    specs: Iterable[ParamSpec], params: Mapping[str, object]
) -> tuple[dict[str, object], list[str]]:
    """Coerce stored ``params`` against ``specs``.

    Returns the normalized copy and one message per value that was replaced
    or lies outside the form's range.
    """
    result: dict[str, object] = dict(params)
    issues: list[str] = []
    for spec in specs:
        if spec.key not in params:
            continue
        try:
            value = spec.coerce(params[spec.key])
        except InvalidParam as exc:
            result[spec.key] = spec.default
            issues.append(f"{spec.key}: {exc}; using {spec.default!r}")
            continue
        result[spec.key] = value
        problem = spec.range_issue(value)
        if problem is not None:
            issues.append(f"{spec.key}: {problem}; kept as stored")
    return result, issues


__all__ = [
    "InvalidParam",
    "ParamKind",
    "ParamSpec",
    "ParamValue",
    "normalize_pattern_params",
]
