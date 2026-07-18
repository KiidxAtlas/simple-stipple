"""Executable dependency and layout boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_backend_has_no_qt_or_ui_dependencies():
    violations: list[str] = []
    for path in (ROOT / "src/backend").rglob("*.py"):
        forbidden = sorted(
            name for name in _imports(path) if name.startswith(("PySide6", "src.ui"))
        )
        if forbidden:
            violations.append(f"{path.relative_to(ROOT)}: {', '.join(forbidden)}")
    assert not violations, "\n".join(violations)


def test_core_has_no_ui_dependencies():
    violations: list[str] = []
    for path in (ROOT / "src/core").rglob("*.py"):
        forbidden = sorted(name for name in _imports(path) if name.startswith("src.ui"))
        if forbidden:
            violations.append(f"{path.relative_to(ROOT)}: {', '.join(forbidden)}")
    assert not violations, "\n".join(violations)


def test_removed_ui_namespaces_do_not_return():
    ui = ROOT / "src/ui"
    assert not [name for name in ("core", "shell", "sidebars") if (ui / name).exists()]


def test_consolidated_scaffold_modules_do_not_return():
    removed = (
        "backend/operations.py",
        "core/settings_bus.py",
        "core/constants.py",
        "ui/notifications.py",
        "ui/units.py",
        "ui/workspace_session.py",
        "ui/canvas/contracts.py",
        "ui/canvas/document.py",
        "ui/canvas/geometry_model.py",
        "ui/canvas/undo.py",
        "ui/pages/repo_tab.py",
        "ui/pages/registry.py",
        "ui/pages/task_state.py",
        "ui/pages/pattern/_spec.py",
        "ui/pages/pattern/fill.py",
        "ui/pages/pattern/output.py",
        "ui/pages/pattern/presets.py",
        "ui/pages/pattern/services.py",
    )
    assert not [relative for relative in removed if (ROOT / "src" / relative).exists()]


def test_synchronized_settings_have_defaults():
    from src.app.page_runtime import SETTINGS_SYNC_TABLE
    from src.core.settings import load_settings

    settings = load_settings()
    missing = [row.key for row in SETTINGS_SYNC_TABLE if row.key not in settings]
    assert not missing
