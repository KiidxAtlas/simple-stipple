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
