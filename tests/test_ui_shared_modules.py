"""Focused characterization tests for decomposed shared UI modules."""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import pytest

from simple_stipple.ui import recent
from simple_stipple.ui.components import __all__ as component_exports
from simple_stipple.ui.units import (
    format_length,
    from_display,
    parse_numeric_expression,
    to_display,
)

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"


def test_numeric_units_preserve_expression_behavior() -> None:
    assert parse_numeric_expression("1in + 3mm") == pytest.approx(28.4)
    assert parse_numeric_expression("1/2", unit="in") == pytest.approx(12.7)
    assert to_display(25.4, "in") == pytest.approx(1.0)
    assert from_display(2.0, "in") == pytest.approx(50.8)
    assert format_length(25.4, "in") == "1.00 in"
    with pytest.raises(ValueError, match="Only arithmetic"):
        parse_numeric_expression("__import__('os')")


def test_recent_files_dedupe_filter_and_persist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(recent, "save_settings", lambda settings: saved.append(dict(settings)))
    first = tmp_path / "first.dxf"
    second = tmp_path / "second.dxf"
    first.touch()
    second.touch()
    settings: dict = {}

    recent.record_recent(settings, recent.KIND_DXF, str(first))
    recent.record_recent(settings, recent.KIND_DXF, str(second))
    recent.record_recent(settings, recent.KIND_DXF, str(first))

    assert recent.list_recent(settings, recent.KIND_DXF) == [
        str(first.resolve()),
        str(second.resolve()),
    ]
    first.unlink()
    assert recent.list_recent(settings, recent.KIND_DXF) == [str(second.resolve())]
    recent.prune_missing(settings)
    assert settings["recent_files.dxf"] == [str(second.resolve())]
    assert saved


def test_component_facade_is_explicit_and_implementation_free() -> None:
    import simple_stipple.ui.components as components

    assert component_exports
    assert set(component_exports) == {
        name
        for name, value in vars(components).items()
        if not name.startswith("_") and not isinstance(value, ModuleType)
    }
    assert all(
        getattr(components, name).__module__ != "simple_stipple.ui.components"
        for name in component_exports
        if hasattr(getattr(components, name), "__module__")
    )


def test_runtime_consumers_use_concrete_shared_modules() -> None:
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "simple_stipple.ui.components",
                "simple_stipple.ui.util",
            }:
                violations.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert not violations, "\n".join(violations)
    assert not (PACKAGE / "ui" / "util.py").exists()
