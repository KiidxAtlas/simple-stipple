# File Structure Refactor Plan: Simple Stipple

## Scope
Whole repository, specifically focusing on decomposing oversized files within `src/simple_stipple` to adhere to Single Responsibility Principle (SRP) and reduce cyclomatic complexity.

## Diagnosis
The top-level package structure follows the canonical `src`-layout and a well-designed capability-first modular monolith architecture. The "disorganization" is not in package placement, but in **file-level bloat**. Multiple files exceed 1,500 lines and several approach 3,000 lines, violating SRP and making maintenance difficult. Existing attempts to use mixins for decomposition failed; delegation to sibling modules is the established successful pattern here.

## Proposed Structure (Refined)

### Before/After Tree (Targeted Changes)
```text
src/simple_stipple/
├── canvas/
│   ├── tools/
│   │   └── tools.py (SPLIT)          → base.py, scale.py, dimension_interaction.py, edit.py, draw.py, trim_extend.py, select_tool.py, knife.py, radial_menu.py
│   └── (other canvas files)
├── engine/
│   ├── cad/
│   │   └── shapes.py (SPLIT)         → shapes/base.py, lines_arcs.py, polygons.py, curves.py, factory.py
│   └── (other engine files)
├── features/
│   ├── convert/
│   │   └── tasks.py (SPLIT)          → tasks/base.py, fvi.py, fixer.py, svg.py, svg_to_dxf.py
│   ├── help.py (SPLIT)               → help/content.py, help/dialog.py
│   ├── pattern/
│   │   └── page.py (POTENTIAL SPLIT) → page.py, page_output.py, page_build.py, page_dxf_io.py, page_presets.py, page_generation.py
│   └── (other features)
└── ...
```

### Path-Mapping Table

| Original File | New Files/Paths | Type of Change |
|---|---|---|
| `ARCHITECTURE.md` (missing) | `ARCHITECTURE.md` | Restore from Git history |
| `src/simple_stipple/canvas/tools/tools.py` | `.../canvas/tools/{base,scale,dimension_interaction,edit,draw,trim_extend,select_tool,knife,radial_menu}.py` | Decomposition (Mechanical) |
| `src/simple_stipple/engine/cad/shapes.py` | `.../engine/cad/shapes/{base,lines_arcs,polygons,curves,factory}.py` | Decomposition (Mechanical) |
| `src/simple_stipple/features/convert/tasks.py` | `.../features/convert/tasks/{base,fvi,fixer,svg,svg_to_dxf}.py` | Decomposition (Mechanical) |
| `src/simple_stipple/features/help.py` | `.../features/help/{content,dialog}.py` | Decomposition (Mechanical) |
| `src/simple_stipple/features/pattern/page.py` | `.../features/pattern/{page,page_output,page_build,page_dxf_io,page_presets,page_generation}.py` | Decomposition (High Confidence) |
| `src/simple_stipple/canvas/view/main.py` | *Requires Phase 4 Read* | Decomposition (High Risk) |
| `src/simple_stipple/canvas/operations/editing.py` | *Requires Phase 4 Read* | Decomposition (High Risk) |

## Phased Migration Order

### Phase 1: Documentation & Environment
- Restore `ARCHITECTURE.md` at root.
- Update `README.md` to fix dangling reference.

### Phase 2: Mechanical File Splits (Low Risk)
- Split `canvas/tools/tools.py`.
- Split `engine/cad/shapes.py`.
- Split `features/convert/tasks.py`.
- Split `features/help.py`.

### Phase 3: High-Complexity Splits (Medium Risk)
- Split `features/pattern/page.py` following existing delegation pattern.

### Phase 4: High-Risk Investigation & Splits
- Perform full read of `canvas/view/main.py`.
- Perform full read of `canvas/operations/editing.py`.
- Propose and execute splits only if seams are clearly identified and safe to extract.

### Phase 5: Final Verification
- Global audit of imports.
- Run linting and testing.

## Risk & Rollback
- **Risk:** Broken imports in existing files due to moving classes/functions.
- **Mitigation:** `file-structure-refactor-executor` will use automated refactoring to update imports.
- **Rollback:** All changes are performed in discrete phases. If a phase fails, `git checkout <last-successful-phase-commit>` can be used.

## Verification Commands
- `ruff check .` (Linting)
- `mypy src` (Type checking)
- `pytest` (Functional verification)
