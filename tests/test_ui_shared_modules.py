"""Focused characterization tests for decomposed shared UI modules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simple_stipple.platform.settings import project_root, user_cache_dir
from simple_stipple.ui.components import __all__ as component_exports
from simple_stipple.ui.components import recent
from simple_stipple.ui.components.feedback import notification_history, record_notification
from simple_stipple.ui.components.units import (
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


def test_notification_history_records_messages() -> None:
    before = len(notification_history())
    record_notification("Shared UI notification characterization")
    assert notification_history()[-1][1] == "Shared UI notification characterization"
    assert len(notification_history()) == before + 1


def test_settings_paths_resolve_under_the_project_and_cache_homes() -> None:
    assert project_root().name == "simple-stipple"
    assert user_cache_dir().name == "cache"


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


def test_recent_files_preserve_patchable_persistence_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(recent, "save_settings", lambda settings: saved.append(dict(settings)))
    path = tmp_path / "recent.dxf"
    path.touch()
    recent.record_recent({}, recent.KIND_DXF, str(path))
    assert saved


def test_component_facade_is_intentionally_empty() -> None:
    """The facade re-exports were dead code (zero callers) — removed.

    Production code imports directly from the concern-specific submodules
    (e.g. ``from simple_stipple.ui.components.layout import CollapsibleSection``).
    """
    assert not component_exports


def test_runtime_consumers_use_concrete_shared_modules() -> None:
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        # The facade __init__.py is allowed — its job IS to re-export submodules
        if path.name == "__init__.py" and str(path).endswith("/ui/components/__init__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "simple_stipple.ui.components",
                "simple_stipple.ui.util",
            }:
                violations.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert not violations, "\n".join(violations)
    assert not (PACKAGE / "ui" / "util.py").exists()
