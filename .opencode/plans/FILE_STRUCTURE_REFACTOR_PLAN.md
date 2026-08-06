# File Structure Refactor Plan

## Current State Summary

The project follows a Python `src` layout and is managed via `uv`. While the overall structure is logical, there are several areas for improvement:

- **Redundant Root Files**: `main.py` acts as a development wrapper that is redundant given the entry point defined in `pyproject.toml`. `modules.txt` and `plan.md` appear to be auxiliary/process files that clutter the root.
- **UI Leakage in `canvas`**: The `src/simple_stipple/canvas/` package mixes core editor logic (e.g., `operations/`) with UI-specific components (e.g., `layers/widget.py`, `widgets/`). This creates a confusing boundary between the "Editor Engine" and the "Canvas View".
- **Interaction vs. Logic Ambiguity**: `canvas/operations/` contains both pure logical operations (like `editing.py`) and UI interaction services (like `gizmo.py`, `hud_text.py`, `snap_service.py`).
- **Inconsistent Widget Locations**: Canvas widgets are scattered between `canvas/layers/`, `canvas/widgets/`, and potentially other locations.

## Target Pattern

The refactor will enforce a strict separation of concerns based on the **Engine | Canvas Component | Application** hierarchy:

1. **`engine/`**: Pure, mathematical, and geometric logic. Zero dependencies on UI frameworks (PySide6).
2. **`canvas/`**: The reusable interactive editor component.
   - `core/`: Pure interaction logic, models, and command patterns (no direct widget dependency).
   - `ui/`: All PySide6-specific widgets, overlays, and interaction services (gizmos, HUDs).
3. **`app/` & `ui/`**: High-level application orchestration and top-level windowed widgets.

*Sources: PyPA "src" layout recommendations; standard MVC/MVP patterns for complex interactive components.*

## Before/After Tree

```text
# BEFORE
src/simple_stipple/
├── __main__.py
├── app/
├── canvas/
│   ├── layers/ (contains widget.py)
│   ├── operations/ (mix of logic and UI services)
│   ├── widgets/ (canvas-specific widgets)
│   └── ...
├── engine/
├── features/
├── platform/
├── resources/
└── ui/

# AFTER
src/simple_stipple/
├── __main__.py
├── app/
├── canvas/
│   ├── core/ (logic, models, commands)
│   ├── ui/ (widgets, gizmos, layers, services)
│   └── ...
├── engine/
├── features/
├── platform/
├── resources/
└── ui/
```

## Full Path Mapping Table

| Old Path                                       | New Path                                                                                                  | Reason                                                        | Risk   |
| :--------------------------------------------- | :-------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------ | :----- |
| `main.py`                                    | *Deleted*                                                                                               | Redundant wrapper; entry point in`pyproject.toml`.          | Low    |
| `modules.txt`                                | *Deleted*                                                                                               | Auxiliary file; inventory is in project map.                  | Low    |
| `plan.md`                                    | `.opencode/plans/FILE_STRUCTURE_REFACTOR_PLAN.md`                                                       | Move process documentation to dedicated folder.               | Low    |
| `src/simple_stipple/canvas/layers/widget.py` | `src/simple_stipple/canvas/ui/layers_widget.py`                                                         | Consolidate canvas widgets into a dedicated`ui` subpackage. | Medium |
| `src/simple_stipple/canvas/widgets/*`        | `src/simple_stipple/canvas/ui/*`                                                                        | Consolidate canvas widgets.                                   | Medium |
| `src/simple_stipple/canvas/operations/*`     | `src/simple_stipple/canvas/core/operations/*` (logic)  `src/simple_stipple/canvas/ui/services/*` (UI) | Separate interaction services from core operations.           | High   |

## Migration Phases

### Phase 1: Root & Metadata Cleanup (Low Risk)

- [ ] Delete `main.py`.
- [ ] Delete `modules.txt`.
- [ ] Move `plan.md` to `.opencode/plans/`.
- [ ] *Automation: Manual.*

### Phase 2: Canvas UI Consolidation (Medium Risk)

- [ ] Create `src/simple_stipple/canvas/ui/`.
- [ ] Move all widgets from `canvas/layers/`, `canvas/widgets/` to `canvas/ui/`.
- [ ] Rename and relocate files to follow a consistent pattern.
- [ ] Update all imports in `app/`, `features/`, and `ui/`.
- [ ] *Automation: Move + automated import rewrite (using tools like `rope` or LSP).*

### Phase 3: Operation/Service Decoupling (High Risk)

- [ ] Create `src/simple_stipple/canvas/core/` and `src/simple_stipple/canvas/ui/services/`.
- [ ] Split `canvas/operations/` into logic (core) and interaction (services).
- [ ] Update all internal `canvas` imports.
- [ ] *Automation: Manual verification required due to heavy logic/UI interdependency.*

## Import/Reference Update Strategy

- Use `ruff` and automated refactoring tools to identify broken imports.
- Perform a dry run of `pytest` afte every sub-phase.
- Verify all `engine/` imports remain free of `PySide6`.

## Risk & Rollback

- **Risk**: Breaking the complex interaction loop between `canvas.runtime`, `canvas.operations`, and `ui`.
- **Rollback**: All changes will be performed in a dedicated `refactor/file-structure` git branch. Reverting is as simple as `git reset --hard origin/main`.

## Verification Plan

1. **Static Analysis**: Run `ruff` and `mypy` to ensure no broken imports or type errors.
2. **Unit Tests**: Run `pytest` (specifically targeting `engine/` and `canvas/core/`).
3. **Integration Tests**: Run `pytest-qt` to ensure the UI still renders and responds to interactions.
4. **Manual Smoke Test**: Launch the application via `simple-stipple` and perform a basic draw/edit loop.
