"""Display-unit conversion — all internal geometry stays mm; this is purely
the display/input boundary (properties panel, HUD prompts, rulers, dialogs).
"""

from __future__ import annotations

from typing import Literal

UnitSystem = Literal["mm", "in"]

_MM_PER_INCH = 25.4

DEFAULT_UNIT_SYSTEM: UnitSystem = "mm"


def to_display(value_mm: float, unit: str) -> float:
    """Convert an internal mm value to the given display unit ("mm"/"in")."""
    if unit == "in":
        return value_mm / _MM_PER_INCH
    return value_mm


def from_display(value: float, unit: str) -> float:
    """Convert a value typed in the given display unit back to mm."""
    if unit == "in":
        return value * _MM_PER_INCH
    return value


def suffix(unit: str) -> str:
    return "mm" if unit != "in" else "in"


def format_length(value_mm: float, unit: str, *, decimals: int = 2) -> str:
    """Format an mm value for display, e.g. ``"12.70 mm"`` or ``"0.50 in"``."""
    return f"{to_display(value_mm, unit):.{decimals}f} {suffix(unit)}"
