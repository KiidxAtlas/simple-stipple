"""Phase-0 structural characterization for the approved reorganization.

The checks establish the import graph and stable public surfaces before any
production module is relocated.  They intentionally validate the current
layout rather than prescribe the later target tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

from simple_stipple.app.launcher import main
from simple_stipple.app.pages import default_page_specs
from simple_stipple.core.document.service import DocumentService
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.draft import DraftPage
from simple_stipple.features.pattern import PatternPage
from simple_stipple.features.pattern.page import PatternPage as ConcretePatternPage
from simple_stipple.features.trace import TracePage

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "simple_stipple"


def _module_path(module: str) -> Path | None:
    if not module.startswith("simple_stipple"):
        return None
    relative = module.removeprefix("simple_stipple").lstrip(".").replace(".", "/")
    base = PACKAGE / relative
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    # ``engine.editing`` is intentionally a namespace directory while it is
    # being consolidated; it is still an importable current module home.
    if base.is_dir() and any(base.glob("*.py")):
        return base
    return None


def _relative_module(path: Path, node: ast.ImportFrom) -> str:
    parent = path.relative_to(PACKAGE).parent.parts
    base = parent[: len(parent) - node.level + 1]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join(("simple_stipple", *base, *filter(None, suffix)))


def _internal_imports(path: Path) -> set[str]:
    """Enumerate all statically-known local imports, including relative/dynamic forms."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.name for alias in node.names if alias.name.startswith("simple_stipple")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module if node.level == 0 else _relative_module(path, node)
            if module and module.startswith("simple_stipple"):
                modules.add(module)
                if node.module is None:
                    modules.update(f"{module}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ):
            modules.add(node.args[0].value)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            modules.add(node.args[0].value)
    return modules


def test_all_statically_known_internal_imports_resolve_to_current_module_homes() -> None:
    missing = [
        f"{path.relative_to(ROOT)} imports {module}"
        for path in PACKAGE.rglob("*.py")
        for module in _internal_imports(path)
        if _module_path(module) is None
    ]
    assert not missing, "\n".join(sorted(missing))


def test_public_facades_and_entry_points_are_explicit_before_reorganization() -> None:
    """Moves may change homes but must preserve these integration surfaces."""
    assert callable(main)
    assert DxfCanvas.__module__ == "simple_stipple.canvas.widget"
    assert DocumentService.__module__ == "simple_stipple.core.document.service"
    assert PatternPage is ConcretePatternPage
    assert all(
        page.__name__.endswith("Page") for page in (DraftPage, PatternPage, TracePage, ConvertPage)
    )
    assert [spec.page_id for spec in default_page_specs()] == [
        "draft",
        "pattern",
        "trace",
        "convert",
        "repository",
    ]


def test_architecture_document_describes_current_boundaries_and_public_surfaces() -> None:
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for phrase in (
        "## Dependency direction",
        "tests/test_dependency_boundaries.py",
        "## Public surfaces",
        "editor.widget.DxfCanvas",
        "document.service.DocumentService",
    ):
        assert phrase in architecture
