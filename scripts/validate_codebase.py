"""Repository validation audit for structure, typing, docs, and safety checks.

Usage:
    python scripts/validate_codebase.py
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"

MAX_FILE_LINES = 300
MAX_FUNCTION_LINES = 100


@dataclass
class AuditIssue:
    kind: str
    path: Path
    line: int
    detail: str


@dataclass
class AuditSummary:
    total_python_files: int
    public_symbols: int
    typed_public_symbols: int
    documented_public_symbols: int


def _iter_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _has_full_annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_return = node.returns is not None
    positional = list(node.args.args) + list(node.args.kwonlyargs)
    if node.args.vararg is not None:
        positional.append(node.args.vararg)
    if node.args.kwarg is not None:
        positional.append(node.args.kwarg)
    has_args = all(arg.annotation is not None for arg in positional)
    return has_return and has_args


def run_audit() -> tuple[AuditSummary, list[AuditIssue]]:
    files = _iter_python_files()
    issues: list[AuditIssue] = []
    public_symbols = 0
    typed_public_symbols = 0
    documented_public_symbols = 0

    for path in files:
        line_count = _count_lines(path)
        if line_count > MAX_FILE_LINES:
            issues.append(
                AuditIssue(
                    kind="file-length",
                    path=path,
                    line=1,
                    detail=f"{line_count} lines (max {MAX_FILE_LINES})",
                )
            )

        source = path.read_text(encoding="utf-8")
        if "breakpoint(" in source:
            issues.append(
                AuditIssue(
                    kind="debug-breakpoint",
                    path=path,
                    line=source.index("breakpoint(") + 1,
                    detail="Contains breakpoint() call",
                )
            )

        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append(
                    AuditIssue(
                        kind="bare-except",
                        path=path,
                        line=node.lineno,
                        detail="Bare except detected",
                    )
                )

            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "print":
                    issues.append(
                        AuditIssue(
                            kind="print-call",
                            path=path,
                            line=node.lineno,
                            detail="print() call detected",
                        )
                    )

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if hasattr(node, "end_lineno"):
                    function_lines = (node.end_lineno or node.lineno) - node.lineno + 1
                    if function_lines > MAX_FUNCTION_LINES:
                        issues.append(
                            AuditIssue(
                                kind="function-length",
                                path=path,
                                line=node.lineno,
                                detail=(
                                    f"{node.name} is {function_lines} lines "
                                    f"(max {MAX_FUNCTION_LINES})"
                                ),
                            )
                        )
                if _is_public(node.name):
                    public_symbols += 1
                    if _has_full_annotations(node):
                        typed_public_symbols += 1
                    if ast.get_docstring(node):
                        documented_public_symbols += 1

            if isinstance(node, ast.ClassDef) and _is_public(node.name):
                public_symbols += 1
                typed_public_symbols += (
                    1  # classes are counted as typed by definition here
                )
                if ast.get_docstring(node):
                    documented_public_symbols += 1

    summary = AuditSummary(
        total_python_files=len(files),
        public_symbols=public_symbols,
        typed_public_symbols=typed_public_symbols,
        documented_public_symbols=documented_public_symbols,
    )
    return summary, issues


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 100.0
    return numerator * 100.0 / denominator


def main() -> int:
    summary, issues = run_audit()
    typing_pct = _percent(summary.typed_public_symbols, summary.public_symbols)
    docs_pct = _percent(summary.documented_public_symbols, summary.public_symbols)

    print("=== Validation Summary ===")
    print(f"Python files: {summary.total_python_files}")
    print(
        f"Public API typed: {summary.typed_public_symbols}/{summary.public_symbols} "
        f"({typing_pct:.1f}%)"
    )
    print(
        f"Public API documented: {summary.documented_public_symbols}/{summary.public_symbols} "
        f"({docs_pct:.1f}%)"
    )

    if issues:
        print("\n=== Issues ===")
        for issue in sorted(
            issues, key=lambda item: (str(item.path), item.line, item.kind)
        ):
            rel = issue.path.relative_to(ROOT)
            print(f"[{issue.kind}] {rel}:{issue.line} - {issue.detail}")
        print(f"\nTotal issues: {len(issues)}")
        return 1

    print("\nNo rule violations detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
