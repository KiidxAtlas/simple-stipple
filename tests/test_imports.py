"""Every module under src/ must import cleanly.

Catches NameErrors from removed symbols, broken re-exports, and missing
dependencies without needing to drive the GUI.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import src

# Deliberately not pkgutil.walk_packages: it silently refuses to recurse into
# any directory without an __init__.py (a namespace package), so anything
# under a namespace-package subdirectory (which most of this codebase's
# subdirectories are, by convention — see issues.md's "no code in __init__.py"
# principle) would vanish from this test's parametrization without a single
# failure to flag it. A plain filesystem walk sees every .py file regardless.
_SRC_ROOT = Path(src.__path__[0])


def _module_name(py_file: Path) -> str:
    parts = py_file.relative_to(_SRC_ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("src", *parts))


MODULES = sorted({_module_name(p) for p in _SRC_ROOT.rglob("*.py")})


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


# =============================================================================
# FINAL ARCHITECTURE: UI/backend import boundary
#
# Phase 2 of plan.md (Extract app.services Interfaces) requires that UI code
# never import backend modules directly — only through src/app/services/*.
# This is enforced once, comprehensively, here — rather than duplicated as a
# per-file assertion in every backend test module. A backend test file (e.g.
# tests/test_boolean_ops.py) legitimately imports src.backend.* directly,
# because it is testing backend code; that is correct and unrelated to this
# boundary. What this guards is the UI *source* tree, not test files.
# =============================================================================

import ast


def _module_imports_from(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _ui_source_files() -> list[Path]:
    return sorted((_SRC_ROOT / "ui").rglob("*.py"))


UI_FILES = _ui_source_files()


@pytest.mark.parametrize("ui_file", UI_FILES, ids=lambda p: str(p.relative_to(_SRC_ROOT)))
def test_ui_module_does_not_import_backend_directly(ui_file):
    """Every src/ui/* module must import backend functionality via app.services.

    src.backend.model is exempt: Document/EntityRecord/Command types are the
    shared vocabulary passed across the service boundary, not backend logic
    UI reaches around services to call.
    """
    imports = _module_imports_from(ui_file)
    violations = sorted(
        name for name in imports
        if name.startswith("src.backend") and not name.startswith("src.backend.model")
    )
    assert not violations, (
        f"{ui_file.relative_to(_SRC_ROOT)} imports backend directly (must go through "
        f"src.app.services instead): {', '.join(violations)}"
    )


def test_app_services_package_covers_every_backend_package():
    """Every top-level src/backend/* package must have a corresponding wrapper
    in src/app/services/, so UI code has somewhere to import it from."""
    backend_root = _SRC_ROOT / "backend"
    services_root = _SRC_ROOT / "app" / "services"

    backend_packages = sorted(
        p.name for p in backend_root.iterdir()
        if p.is_dir() and not p.name.startswith("_") and not p.name.startswith(".")
    )

    service_files = {p.stem for p in services_root.glob("*.py") if not p.name.startswith("_")}

    # Each backend package should be reachable from *some* service module.
    # We don't require a strict 1:1 name match (e.g. backend/cad -> geometry_service
    # + cad_service), but every backend package's public symbols must be
    # re-exported from at least one service module.
    missing = []
    for package in backend_packages:
        referenced_anywhere = any(
            f"src.backend.{package}" in service_file.read_text(encoding="utf-8")
            for service_file in services_root.glob("*.py")
        )
        if not referenced_anywhere:
            missing.append(package)

    assert not missing, (
        f"backend packages with no app.services wrapper: {missing}. "
        "UI cannot reach these without importing backend directly."
    )
