"""Static guards for canonical runtime module locations."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "simple_stipple"

DEPRECATED_MODULES = {
    "simple_stipple.backend.jit",
    "simple_stipple.backend.laserstar_package",
    "simple_stipple.backend.raster_engraving",
    "simple_stipple.backend.spatial",
    "simple_stipple.backend.trace",
    "simple_stipple.backend.voronoi",
    "simple_stipple.canvas.canvas_model",
    "simple_stipple.canvas.canvas_runtime",
    "simple_stipple.canvas.dxf_canvas",
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


def test_runtime_uses_only_canonical_module_homes() -> None:
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        for imported in _runtime_imports(path):
            if imported in DEPRECATED_MODULES or imported.startswith(
                "simple_stipple.canvas.services"
            ):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_compatibility_facades_are_removed() -> None:
    module_paths = {
        PACKAGE.joinpath(*module.removeprefix("simple_stipple.").split(".")).with_suffix(".py")
        for module in DEPRECATED_MODULES
    }
    module_paths.add(PACKAGE / "ui" / "canvas" / "services")
    leftovers = sorted(str(path.relative_to(ROOT)) for path in module_paths if path.exists())
    assert not leftovers, "\n".join(leftovers)
