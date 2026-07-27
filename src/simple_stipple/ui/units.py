"""Safe numeric-expression parsing and display-unit conversion."""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from typing import Any, Literal

UnitSystem = Literal["mm", "in"]
DEFAULT_UNIT_SYSTEM: UnitSystem = "mm"
_MM_PER_INCH = 25.4

_EXPR_OPS: dict[type[ast.operator | ast.unaryop], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def parse_numeric_expression(text: str, unit: str = "mm", *, is_length: bool = True) -> float:
    """Evaluate a small arithmetic/unit expression and return mm for lengths.

    Supports ``12/2``, ``1in + 3mm``, parentheses, and decimal arithmetic;
    names, calls, attributes, and every other Python construct are rejected.
    Bare values use the active display unit.
    """
    source = str(text).strip().lower().replace("×", "*")
    if not source:
        raise ValueError("Enter a value")
    factor = _MM_PER_INCH if unit == "in" and is_length else 1.0
    if is_length:
        source = re.sub(
            r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(mm|in)\b",
            lambda m: str(
                float(m.group(1)) * (_MM_PER_INCH if m.group(2) == "in" else 1.0) / factor
            ),
            source,
        )
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid expression") from exc

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value) * factor
        if isinstance(node, ast.UnaryOp) and type(node.op) in _EXPR_OPS:
            return float(_EXPR_OPS[type(node.op)](_eval(node.operand)))
        if isinstance(node, ast.BinOp) and type(node.op) in _EXPR_OPS:
            left, right = _eval(node.left), _eval(node.right)
            # Multipliers/divisors are dimensionless when written bare.
            if is_length and isinstance(node.op, (ast.Mult, ast.Div, ast.Pow, ast.Mod)):
                right /= factor
            return float(_EXPR_OPS[type(node.op)](left, right))
        raise ValueError("Only arithmetic and mm/in units are supported")

    try:
        value = _eval(tree)
    except ArithmeticError as exc:
        raise ValueError("Invalid arithmetic") from exc
    if not math.isfinite(value):
        raise ValueError("Result must be finite")
    return value


def to_display(value_mm: float, unit: str) -> float:
    return value_mm / _MM_PER_INCH if unit == "in" else value_mm


def from_display(value: float, unit: str) -> float:
    return value * _MM_PER_INCH if unit == "in" else value


def suffix(unit: str) -> str:
    return "in" if unit == "in" else "mm"


def format_length(value_mm: float, unit: str, *, decimals: int = 2) -> str:
    return f"{to_display(value_mm, unit):.{decimals}f} {suffix(unit)}"

