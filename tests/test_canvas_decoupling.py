"""Ratchets for the canvas decoupling work.

`CanvasView` is one class spread across many files: the ``operations/*``
services hold a back-reference to the view and reach into its private
attributes, and several modules hold functions that were lifted out of the
class but still take ``self``. Both patterns keep the canvas untestable —
nothing can be exercised without constructing a live Qt view.

These are ratchets, not pass/fail gates. They record the current counts and
fail if either grows. Lower the baseline in the same commit that removes
occurrences; when a baseline reaches zero, convert the test to a hard
assertion of zero and delete the constant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"

# Baselines measured 2026-08-13. These may only ever go down.
MAX_VIEW_PRIVATE_REACH_INS = 1681
MAX_MODULE_LEVEL_SELF_FUNCTIONS = 75

_REACH_IN = re.compile(r"\b_(?:host|view)\._[A-Za-z_]")


def _python_files() -> list[Path]:
    # This ratchet measures CanvasView coupling.  Document services have an
    # explicit host protocol and are intentionally outside that UI boundary.
    return [path for path in (PACKAGE / "canvas").rglob("*.py") if "__pycache__" not in path.parts]


def _reach_ins() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _python_files():
        found = len(_REACH_IN.findall(path.read_text(encoding="utf-8")))
        if found:
            counts[str(path.relative_to(PACKAGE))] = found
    return counts


def _module_level_self_functions() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - compileall guards this
            continue
        found = sum(
            1
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.args.args
            and node.args.args[0].arg == "self"
        )
        if found:
            counts[str(path.relative_to(PACKAGE))] = found
    return counts


def _report(counts: dict[str, int]) -> str:
    return "\n".join(f"  {name}: {n}" for name, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def test_canvas_ops_do_not_grow_view_private_reach_ins() -> None:
    """A service that reads ``self._host._entities`` is view code in another
    file: it cannot be constructed, called, or tested without the view.
    Replace the back-reference with explicit parameters as each op moves.
    """
    counts = _reach_ins()
    total = sum(counts.values())
    assert total <= MAX_VIEW_PRIVATE_REACH_INS, (
        f"view-private reach-ins rose to {total} "
        f"(baseline {MAX_VIEW_PRIVATE_REACH_INS}):\n{_report(counts)}"
    )


def test_no_new_module_level_functions_take_self() -> None:
    """A module-level ``def f(self, ...)`` is a method that was moved to cut a
    file's line count. It keeps the coupling and loses the class.
    """
    counts = _module_level_self_functions()
    total = sum(counts.values())
    assert total <= MAX_MODULE_LEVEL_SELF_FUNCTIONS, (
        f"module-level self-functions rose to {total} "
        f"(baseline {MAX_MODULE_LEVEL_SELF_FUNCTIONS}):\n{_report(counts)}"
    )


def test_baselines_are_not_stale() -> None:
    """Keep the ratchet honest: if the real count has dropped well below a
    baseline, the baseline should be lowered so it keeps holding the line.
    """
    stale = []
    for label, actual, baseline in (
        ("view-private reach-ins", sum(_reach_ins().values()), MAX_VIEW_PRIVATE_REACH_INS),
        (
            "module-level self-functions",
            sum(_module_level_self_functions().values()),
            MAX_MODULE_LEVEL_SELF_FUNCTIONS,
        ),
    ):
        if actual < baseline * 0.9:
            stale.append(f"{label}: baseline {baseline} but actual is {actual} — lower it")
    assert not stale, "\n".join(stale)
