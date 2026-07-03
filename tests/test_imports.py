"""Every module under src/ must import cleanly.

Catches NameErrors from removed symbols, broken re-exports, and missing
dependencies without needing to drive the GUI.
"""

import importlib
import pkgutil

import pytest

import src

MODULES = sorted(m.name for m in pkgutil.walk_packages(src.__path__, "src."))


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)
