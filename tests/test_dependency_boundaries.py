"""Executable dependency rules for the capability-first runtime package."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "simple_stipple"


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _violations(layer: str, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{path.relative_to(PACKAGE)} imports {module}"
        for path in (PACKAGE / layer).rglob("*.py")
        for module in _absolute_imports(path)
        if module.startswith(forbidden)
    ]


def test_dependencies_point_toward_shared_subsystems() -> None:
    violations: list[str] = []
    violations += _violations(
        "platform",
        (
            "simple_stipple.app",
            "simple_stipple.canvas",
            "simple_stipple.document",
            "simple_stipple.engine",
            "simple_stipple.features",
            "simple_stipple.ui",
        ),
    )
    violations += _violations(
        "engine",
        (
            "simple_stipple.app",
            "simple_stipple.canvas",
            "simple_stipple.features",
            "simple_stipple.ui",
        ),
    )
    violations += _violations(
        "document",
        (
            "simple_stipple.app",
            "simple_stipple.canvas",
            "simple_stipple.features",
            "simple_stipple.ui",
        ),
    )
    violations += _violations("canvas", ("simple_stipple.app", "simple_stipple.features"))
    assert not violations, "\n".join(violations)


def test_engine_and_document_do_not_depend_on_qt() -> None:
    violations = [
        f"{path.relative_to(PACKAGE)} imports {module}"
        for layer in ("engine", "document")
        for path in (PACKAGE / layer).rglob("*.py")
        for module in _absolute_imports(path)
        if module.startswith(("PySide6", "PyQt"))
    ]
    assert not violations, "\n".join(violations)


def test_features_do_not_import_other_feature_internals() -> None:
    violations: list[str] = []
    root = PACKAGE / "features"
    for source in (path for path in root.iterdir() if path.is_dir()):
        for path in source.rglob("*.py"):
            for module in _absolute_imports(path):
                prefix = "simple_stipple.features."
                if not module.startswith(prefix):
                    continue
                target = module.removeprefix(prefix).split(".", 1)[0]
                if target not in {source.name, "base"}:
                    violations.append(f"{path.relative_to(PACKAGE)} imports {module}")
    assert not violations, "\n".join(violations)


def test_workflow_services_preserve_structured_results_and_dxf_surface() -> None:
    from simple_stipple.engine.formats.service import DxfService
    from simple_stipple.engine.geometry.service import GeometryService

    assert callable(DxfService.load_dxf_polylines_by_layer_with_report)
    diagnostics = GeometryService.analyze_geometry(
        [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]]
    )
    assert diagnostics.paths == 1
    assert isinstance(diagnostics.closed, int)
