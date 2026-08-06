# File Structure Refactor Plan
Date: Wed Aug 05 2026

## Current State Summary
The project follows a modern Python `src` layout and adheres to a capability-first modular monolith architecture.
- **Layout**: `src/` layout (canonical).
- **Patterns**: Capability-based subpackages (`canvas`, `engine`, `features`, `ui`).
- **Detected Issues**: 
  - Root clutter: `main.py` (shim) and `modules.txt` (manual module list) exist at the root.
  - Test structure: `tests/` is a flat directory, which will become difficult to manage as the project grows.

## Target Pattern
**Canonical Python `src` layout with structured testing.**
- Source: PyPA (Python Packaging Authority) guidance for `src` layout.
- Testing structure: Mirroring the package structure within `tests/`.

## Before/After Tree
**Before:**
```
.
├── main.py
├── modules.txt
├── pyproject.toml
├── src/
│   └── simple_stipple/
│       ├── canvas/
│       ├── engine/
│       ├── features/
│       └── ui/
└── tests/
    ├── test_canvas.py
    ├── test_engine.py
    └── ...
```

**After:**
```
.
├── pyproject.toml
├── src/
│   └── simple_stipple/
│       ├── canvas/
│       ├── engine/
│       ├── features/
│       └── ui/
└── tests/
    ├── canvas/
    ├── engine/
    ├── features/
    └── ui/
```

## Full Path Mapping Table
| Old path | New path | Reason | Risk |
|---|---|---|---|
| `main.py` | (deleted) | Redundant shim; entry point is in `pyproject.toml`. | Low |
| `modules.txt` | (deleted) | Redundant manual module list. | Low |
| `tests/test_canvas_*.py` | `tests/canvas/test_*.py` | Mirroring source structure for better organization. | Medium (requires import updates) |
| `tests/test_engine_*.py` | `tests/engine/test_*.py` | Mirroring source structure for better organization. | Medium (requires import updates) |
| `tests/test_ui_*.py` | `tests/ui/test_*.py` | Mirroring source structure for better organization. | Medium (requires import updates) |
| `tests/test_features_*.py` | `tests/features/test_*.py` | Mirroring source structure for better organization. | Medium (requires import updates) |

## Migration Phases
- [ ] **Phase 1: Root Cleanup** (Low risk, automated)
  - Remove `main.py` and `modules.txt`.
- [ ] **Phase 2: Test Reorganization** (Medium risk, requires import fixes)
  - Create `tests/canvas`, `tests/engine`, `tests/features`, `tests/ui` directories.
  - Move corresponding test files into their new homes.
  - Update imports if necessary (though tests usually import from `src`).

## Import/Reference Update Strategy
- For root file removal: No code imports are affected as they are development shims.
- For test reorganization: Since `tests` uses `src` in the python path (as seen in `pyproject.toml`), moving files within `tests/` won't break imports from the package itself, but might require checking if tests import each other or use relative paths.

## Risk & Rollback
- **Risk**: Moving tests might break path-dependent test data loading if any tests use `__file__` to find files in `tests/data`.
- **Rollback**: `git checkout .` or `git reset --hard HEAD` after each phase.

## Verification Plan
- After Phase 1: `pip install -e .` then run any script.
- After Phase 2: `pytest` (as defined in `pyproject.toml`).
