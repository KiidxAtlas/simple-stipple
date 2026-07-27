"""Static circular-import detector for the ``src`` tree — CI gate per plan.md
Section 9.6 ("Fail PRs that introduce circular dependencies").

Only counts imports that actually execute at import time: top-level
``import``/``from ... import`` statements. Imports guarded by
``if TYPE_CHECKING:`` or nested inside a function/method body are deferred
past module load and are the standard, idiomatic way to break a real cycle
(see plan.md Section 9.3) — they are deliberately excluded so this script
doesn't flag already-resolved cycles as violations.

Usage:
    python scripts/check_circular_imports.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def _walk_module_level(tree: ast.Module) -> list[ast.stmt]:
    """Statements that execute at import time: top-level minus function/class bodies."""
    stmts: list[ast.stmt] = []

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If) and _is_type_checking_guard(node):
                continue
            stmts.append(node)
            if isinstance(node, (ast.If, ast.Try, ast.With)):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    child = getattr(node, attr, None)
                    if not child:
                        continue
                    if attr == "handlers":
                        for handler in child:
                            visit(handler.body)
                    else:
                        visit(child)

    visit(tree.body)
    return stmts


def build_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    known_modules = set()
    files = sorted(SRC_ROOT.rglob("*.py"))
    for path in files:
        known_modules.add(_module_name(path))

    for path in files:
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        edges: set[str] = set()
        for node in _walk_module_level(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    edges.add(node.module)
        # Keep only edges into modules we actually track, collapsed to the
        # longest known-module prefix (``from simple_stipple.a.b import c`` may
        # import symbol ``c`` from that package, or from the module itself).
        resolved: set[str] = set()
        for target in edges:
            candidate = target
            while candidate and candidate not in known_modules:
                candidate = candidate.rpartition(".")[0]
            if candidate:
                resolved.add(candidate)
        resolved.discard(module)
        graph[module] = resolved
    return graph


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for neighbor in sorted(graph.get(node, ())):
            if neighbor not in graph:
                continue
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                return [*path[cycle_start:], neighbor]
            if color[neighbor] == WHITE:
                result = dfs(neighbor)
                if result is not None:
                    return result
        path.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            result = dfs(node)
            if result is not None:
                return result
    return None


def main() -> int:
    graph = build_import_graph()
    cycle = find_cycle(graph)
    if cycle is None:
        print(f"No circular imports found ({len(graph)} modules scanned).")
        return 0
    print("Circular import detected (runtime-executed imports only):")
    print("  " + " -> ".join(cycle))
    return 1


if __name__ == "__main__":
    sys.exit(main())
