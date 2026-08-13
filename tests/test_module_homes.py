"""Static guards for the canonical runtime module homes."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "simple_stipple"

CANONICAL_TOP_LEVEL_HOMES = {
    "app",
    "canvas",
    "core",
    "features",
    "platform",
    "resources",
    "ui",
}
CANONICAL_CANVAS_ROOT_MODULES = {
    "__init__.py",
    "commands.py",
    "constants.py",
    "hit_testing.py",
    "objects.py",
    "renderer.py",
    "rendering.py",
    "runtime.py",
    "snap.py",
    "widget.py",
}
CANONICAL_CANVAS_SUBPACKAGES = {
    "dialogs",
    "layers",
    "operations",
    "tools",
    "view",
    "widgets",
}
CANONICAL_CORE_ROOT_MODULES = {
    "__init__.py",
    "geometry.py",
    "imaging.py",
}
CANONICAL_CORE_SUBPACKAGES = {
    "cad",
    "document",
    "editing",
    "formats",
    "patterns",
}


def _packages(directory: Path) -> set[str]:
    """Subdirectories that actually contain Python modules.

    An empty directory is not a package — Python will not import it — so the
    layout guard ignores filesystem residue left by a move rather than
    failing on it.
    """
    return {
        path.name
        for path in directory.iterdir()
        if path.is_dir() and path.name != "__pycache__" and any(path.rglob("*.py"))
    }


def _runtime_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_runtime_uses_only_canonical_top_level_homes() -> None:
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        for imported in _runtime_imports(path):
            if not imported.startswith("simple_stipple."):
                continue
            home = imported.removeprefix("simple_stipple.").split(".", 1)[0]
            if home not in CANONICAL_TOP_LEVEL_HOMES:
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_runtime_tree_has_one_canonical_home_per_major_capability() -> None:
    assert _packages(PACKAGE) == CANONICAL_TOP_LEVEL_HOMES
    canvas = PACKAGE / "canvas"
    assert {
        path.name for path in canvas.iterdir() if path.is_file()
    } == CANONICAL_CANVAS_ROOT_MODULES
    assert _packages(canvas) == CANONICAL_CANVAS_SUBPACKAGES
    core = PACKAGE / "core"
    assert {path.name for path in core.iterdir() if path.is_file()} == CANONICAL_CORE_ROOT_MODULES
    assert _packages(core) == CANONICAL_CORE_SUBPACKAGES


def test_shared_ui_and_platform_homes_have_no_root_level_facades() -> None:
    ui = PACKAGE / "ui"
    assert {path.name for path in ui.iterdir() if path.is_file()} == {"__init__.py"}
    assert _packages(ui) == {"components", "dialogs", "style"}
    assert {path.name for path in (PACKAGE / "platform").iterdir() if path.is_file()} == {
        "__init__.py",
        "error_reporting.py",
        "launcher.py",
        "settings.py",
        "storage.py",
        "updates.py",
    }
