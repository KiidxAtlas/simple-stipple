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


# =============================================================================
# FINAL ARCHITECTURE ENFORCEMENT (After plan.md completion)
# =============================================================================

def test_ui_never_imports_backend_directly():
    """Final state: UI ONLY imports from app.services, never backend directly."""
    violations: list[str] = []
    for path in (ROOT / "src/ui").rglob("*.py"):
        imports = _imports(path)
        # Allowed: backend.model for types only
        forbidden = sorted(
            imp for imp in imports
            if imp.startswith("src.backend") and "backend.model" not in imp
        )
        if forbidden:
            violations.append(f"{path.relative_to(ROOT)}: {', '.join(forbidden)}")

    assert not violations, (
        "UI must import from app.services, not backend directly:\n"
        + "\n".join(violations)
    )


def test_app_services_completely_wraps_backend():
    """Final state: app.services must be complete wrapper layer."""
    from src.app.services import geometry_service, dxf_service, model_service, pattern_service

    # Must re-export key functions
    assert hasattr(geometry_service, 'offset_polyline')
    assert hasattr(geometry_service, 'mirror_polyline')
    assert hasattr(geometry_service, 'rotate_polyline')
    assert hasattr(geometry_service, 'scale_polyline')

    assert hasattr(dxf_service, 'load_dxf_polylines_with_report')

    assert hasattr(model_service, 'CanvasDocument')
    assert hasattr(model_service, 'CommandStack')

    assert hasattr(pattern_service, 'PatternProcessor')


def test_command_handlers_exist():
    """Final state: Handler infrastructure must be complete."""
    from src.ui.canvas.handlers.base import CommandHandler
    from src.ui.canvas.handlers.geometry_handlers import (
        OffsetHandler, MirrorHandler, RotateHandler, ScaleHandler, BooleanHandler
    )
    from src.ui.canvas.handlers.dimension_handlers import DimensionHandler
    from src.ui.canvas.handlers.text_handlers import TextHandler
    from src.ui.canvas.handlers.layer_handlers import LayerHandler

    # All must have execute method
    for handler_class in [
        OffsetHandler, MirrorHandler, RotateHandler, ScaleHandler, BooleanHandler,
        DimensionHandler, TextHandler, LayerHandler
    ]:
        assert hasattr(handler_class, 'execute')


def test_view_py_no_direct_mutations():
    """Final state: view.py must not mutate document directly."""
    view_file = ROOT / "src/ui/canvas/view.py"
    content = view_file.read_text()

    # These should only be in handlers/commands, not view
    forbidden_patterns = [
        "self._document.replace(",
        "self._document.append(",
        "document.replace(",
        "document.append(",
    ]

    for pattern in forbidden_patterns:
        assert pattern not in content, (
            f"view.py must not call {pattern} — "
            "mutations should go through handlers"
        )


def test_view_has_reactive_signals():
    """Final state: CanvasView must emit signals, not mutate directly."""
    from src.ui.canvas.view import CanvasView
    from PySide6.QtCore import Signal

    # Must have these signals
    assert hasattr(CanvasView, 'document_changed')
    assert hasattr(CanvasView, 'operation_failed')


def test_circular_imports_prevented():
    """Final state: No circular imports in core modules."""
    # Try importing all key modules — should not raise circular import
    try:
        from src.ui.canvas import view
        from src.ui.canvas.interaction import tools
        from src.ui.canvas.interaction import select
        from src.ui.canvas.snap import SnapEngine
        from src.app.services import geometry_service, dxf_service, model_service
        from src.backend.model import document, commands
    except ImportError as e:
        if "circular" in str(e).lower():
            raise AssertionError(f"Circular import detected: {e}")


def test_view_py_final_line_count():
    """Final state: view.py must be <800 lines (from 4330)."""
    view_file = ROOT / "src/ui/canvas/view.py"
    lines = len([l for l in view_file.read_text().split('\n') if l.strip()])

    assert lines < 800, (
        f"view.py is {lines} lines (target: <800). "
        "Phase 0 must extract handlers."
    )


def test_pattern_tab_final_line_count():
    """Final state: pattern/tab.py must be <1500 lines (from 4268)."""
    tab_file = ROOT / "src/ui/pages/pattern/tab.py"
    lines = len([l for l in tab_file.read_text().split('\n') if l.strip()])

    assert lines < 1500, (
        f"pattern/tab.py is {lines} lines (target: <1500). "
        "Phase 2 must decompose it."
    )


def test_no_old_mutation_methods_in_view():
    """Final state: Old operation methods should not exist in view.py."""
    view_file = ROOT / "src/ui/canvas/view.py"
    content = view_file.read_text()

    old_methods = [
        "def _offset_selected",
        "def _mirror_selected",
        "def _rotate_selected",
        "def _scale_selected",
        "def _boolean_operation",
    ]

    for method in old_methods:
        assert method not in content, (
            f"{method}() should not exist in view.py — "
            "should be in handler instead"
        )
