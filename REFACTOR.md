# Refactoring Plan — Target Architecture

> **Goal:** Evolve Simple Stipple from its current mixin-heavy, directly-mutating architecture to a command-based, composition-driven architecture that is testable, maintainable, and scalable.

> **Principle:** Incremental migration. No rewrites. Each phase produces working, testable code.

---

## 1. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                        │
│                     (src/ui/)                                │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ DraftPage│  │PatternPage│  │TracePage │  │ConvertPage│   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│  ┌────▼──────────────────────────────────────────────────┐  │
│  │              CanvasView (Qt widget)                   │  │
│  │  - Renders via CanvasRenderer                        │  │
│  │  - Captures user input (mouse, keyboard)             │  │
│  │  - Dispatches Commands to DocumentService            │  │
│  │  - Subscribes to DocumentEvents for re-render        │  │
│  └────┬──────────────────────────────────────────────────┘  │
└───────┼──────────────────────────────────────────────────────┘
        │ Commands (user actions)
        │ Events (state changes)
┌───────▼──────────────────────────────────────────────────────┐
│                   APPLICATION LAYER                           │
│                   (src/app/)                                  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              DocumentService                         │    │
│  │                                                      │    │
│  │  - Receives Commands from CanvasView                │    │
│  │  - Validates Commands against domain rules          │    │
│  │  - Executes Commands → produces DomainEvents        │    │
│  │  - Manages UndoStore (Command history)              │    │
│  │  - Publishes Events to CanvasView                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────┐    │
│  │ Workspace  │  │ Task       │  │ Settings           │    │
│  │ Manager    │  │ Controller │  │ Bus                │    │
│  └────────────┘  └────────────┘  └────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│                     DOMAIN LAYER                              │
│                   (src/backend/)                              │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  Document (Aggregate)                │    │
│  │                                                      │    │
│  │  entities: frozenset[Entity]                         │    │
│  │  layers: LayerRegistry                               │    │
│  │  selection: frozenset[EntityId]                      │    │
│  │                                                      │    │
│  │  Commands transform Document → new Document          │    │
│  │  (immutable, pure functions)                         │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Geometry     │  │ Constraints  │  │ Recognition      │  │
│  │ Engine       │  │ Solver       │  │ Engine           │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ DXF/SVG      │  │ Pattern      │  │ Trace            │  │
│  │ I/O          │  │ Generator    │  │ Engine           │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└──────────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│                  INFRASTRUCTURE LAYER                         │
│                   (src/core/)                                 │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Launcher     │  │ Settings     │  │ Persistence      │  │
│  │ (Qt bootstrap)│ │ Bus          │  │ (JSON, recovery) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Finalized File Structure

```
src/
├── __init__.py
│
├── core/                          # Infrastructure — process-wide, Qt-dependent
│   ├── __init__.py
│   ├── launcher.py                # Logging, single-instance, Qt bootstrap
│   ├── paths.py                   # User data, cache, runtime, log locations
│   ├── settings.py                # Settings defaults, validation, persistence, bus
│   ├── updates.py                 # Version detection, update checks, downloads
│   └── error_reporting.py         # Global exception reporting, error notification
│
├── backend/                       # Domain layer — pure, Qt-free, testable
│   ├── __init__.py
│   │
│   ├── document.py                # Canonical Document aggregate + EntityRecord
│   │                              #   - entities, layers, selection (immutable)
│   │                              #   - apply(command) → new Document
│   │                              #   - OperationResult
│   │
│   ├── commands.py                # Command types — immutable, serializable, reversible
│   │                              #   - SplitCommand, BooleanOpCommand, TransformCommand
│   │                              #   - SelectCommand, CreateCommand, DeleteCommand
│   │                              #   - MoveEntityCommand, ResampleCommand, etc.
│   │
│   ├── editor_history.py          # CommandStack — undo/redo via reversible commands
│   │
│   ├── geometry.py                # Geometric primitives, curve fitting, tessellation
│   ├── coordinates.py             # Parse coordinate expressions → model coordinates
│   ├── snapping.py                # Qt-free snap candidate resolution
│   ├── shapes.py                  # Shape domain objects, transformations, factory
│   ├── recognition.py             # Infer parametric shapes from path geometry
│   ├── primitives.py              # Procedural primitive construction
│   ├── path_ops.py                # Operations on paths: reverse, resample, fit, morph
│   ├── preflight.py               # Geometry validation, fabrication-readiness
│   ├── persistence.py             # Safe bounded reads, atomic writes (no UI)
│   ├── document.py                # Workspace schema (Pydantic) — persisted state
│   │
│   ├── editing/                   # Pure CAD operations (moved from ui/mixins)
│   │   ├── __init__.py
│   │   ├── split.py               # Shapely-based polyline/polygon splitting
│   │   ├── boolean.py             # Union, difference, intersection
│   │   ├── trim_extend.py         # Trim and extend operations
│   │   ├── offset.py              # Offset geometry
│   │   ├── transform.py           # Translate, rotate, scale, mirror
│   │   ├── merge_explode.py       # Merge and explode operations
│   │   ├── resample.py            # Resample polylines
│   │   └── smoothing.py           # Simplify, smooth operations
│   │
│   ├── constraints.py             # Constraint models and solving
│   ├── construction.py            # Pure construction geometry (tangents, bisectors)
│   ├── trace.py                   # Image-to-outline processing, cancellation
│   │
│   ├── dxf/                       # File I/O — pure parsing/conversion
│   │   ├── __init__.py
│   │   ├── io.py                  # DXF read/write
│   │   ├── fvi.py                 # FVI read/write
│   │   ├── svg_dxf.py             # SVG ↔ DXF conversion
│   │   ├── fix.py                 # DXF repair
│   │   └── schema.py              # DXF schema validation
│   │
│   ├── pattern/                   # Pattern generation — pure algorithms
│   │   ├── __init__.py
│   │   ├── processing.py          # Core pattern generation engine
│   │   ├── fill.py                # Fill algorithms
│   │   ├── tiling.py              # Tiling patterns
│   │   ├── organic.py             # Organic/stipple patterns
│   │   ├── output.py              # Fabrication output
│   │   ├── presets.py             # Pattern presets
│   │   ├── cancellation.py        # Cancellation support
│   │   └── _shared.py             # Shared utilities
│   │
│   └── editor_geometry.py         # Geometry behavior for editor entities
│                              #   (parametric-shape adaptation)
│
├── app/                           # Application layer — composition, coordination
│   ├── __init__.py
│   ├── window.py                  # Main window — composes controllers + pages
│   ├── page_runtime.py            # Page registration, settings fan-out
│   ├── workspace_session.py       # Collect/apply page sessions, recent files
│   │
│   ├── controllers/               # Cross-page coordination (no domain logic)
│   │   ├── __init__.py
│   │   ├── menu.py                # Menus, shell header, commands, shortcuts
│   │   ├── workspace.py           # Workspace identity, open/save/recovery
│   │   ├── tasks.py               # Autosave, update polling, background tasks
│   │   └── settings.py            # Apply settings, publish changes to windows/pages
│   │
│   └── services/                  # Application services (orchestration)
│       ├── __init__.py
│       ├── document_service.py    # Orchestrates commands, manages undo/redo
│       │                              #   - execute(command) → OperationResult
│       │                              #   - undo(), redo()
│       │                              #   - publish DocumentEvents
│       └── canvas_service.py      # Canvas-specific orchestration
│                              #   - Manages CanvasModel lifecycle
│                              #   - Bridges CanvasView ↔ DocumentService
│
└── ui/                            # Presentation layer — Qt widgets, signals, rendering
    ├── __init__.py
    ├── components.py              # Shared widget factories, icons
    ├── util.py                    # UI-only recent-files, dialogs, notifications
    │
    ├── style/                     # Theme, QSS, icon assets
    │   ├── __init__.py
    │   └── theme.py
    │
    ├── widgets/                   # Reusable widgets (not complete pages)
    │   ├── __init__.py
    │   ├── draw_sidebar.py        # Drawing tool sidebar
    │   ├── properties_panel.py    # Canvas properties panel
    │   ├── precision_bar.py       # Precision display bar
    │   ├── status_strip.py        # Canvas status strip
    │   ├── toolbar.py             # Canvas toolbar
    │   ├── cycle_icon_button.py   # Icon button with cycling actions
    │   ├── import_dialog.py       # DXF import preview dialog
    │   ├── fvi_dialog.py          # FVI export dialog
    │   ├── settings_dialog.py     # App settings dialog
    │   ├── keybindings_dialog.py  # Keybindings configuration
    │   ├── update_dialog.py       # Update available dialog
    │   ├── text_dialog.py         # Text input dialog
    │   ├── command_palette.py     # Command palette dialog
    │   ├── customize_dialogs.py   # Customization dialogs
    │   └── layer_tree/            # Layer tree widget + logic
    │       ├── __init__.py
    │       ├── widget.py          # DxfLayersTree widget
    │       └── logic.py           # Layer tree business logic
    │
    ├── pages/                     # Top-level application pages
    │   ├── __init__.py
    │   ├── base.py                # BasePage — state protocol, task phase
    │   ├── draft.py               # Draft page — 2D drafting canvas
    │   ├── pattern/               # Pattern generation page
    │   │   ├── __init__.py
    │   │   ├── tab.py             # Layout + signal wiring (page owner)
    │   │   ├── session.py         # Workspace/preset state adaptation
    │   │   ├── form.py            # Declarative form construction
    │   │   ├── form_spec.py       # Field specifications
    │   │   ├── params.py          # Parameter collection
    │   │   ├── defaults.py        # Default values
    │   │   ├── workers.py         # Qt background-worker wrappers
    │   │   └── presets_dialog.py  # Preset selection dialog
    │   ├── trace/                 # Image tracing page
    │   │   ├── __init__.py
    │   │   ├── tab.py             # Layout + signal wiring
    │   │   ├── form.py            # Trace form
    │   │   └── session.py         # Trace session state
    │   ├── convert.py             # File conversion page
    │   ├── help.py                # Help page
    │   └── repository.py          # Template/library page
    │
    └── canvas/                    # Canvas subsystem
        ├── __init__.py
        ├── view.py                # CanvasView — Qt widget, layout, signal wiring
        │                              #   - Composes services (not mixin inheritance)
        │                              #   - Dispatches commands, subscribes to events
        ├── canvas_model.py        # Reactive wrapper around Document (Qt signals)
        │                              #   - Thin adapter: Document → Qt signals
        │                              #   - Selection, layer state, entity access
        ├── canvas_runtime.py      # Canvas-to-page wiring (layer adapter, toolbar sync)
        ├── dxf_canvas.py          # Vector-file canvas behavior
        ├── constants.py           # Canvas constants (drag threshold, min scale)
        ├── snap.py                # Snap engine (Qt-integrated snap resolution)
        │
        ├── rendering/             # Pure rendering (moved from mixins)
        │   ├── __init__.py
        │   └── renderer.py        # Canvas rendering (was CanvasRenderer mixin)
        │
        ├── interaction/           # User interaction handling
        │   ├── __init__.py
        │   ├── commands.py        # Canvas command registry (undo, cut, copy, etc.)
        │   ├── tools.py           # Mode-specific event strategies (select, draw, edit)
        │   └── select.py          # Selection logic (was selection_ops mixin)
        │
        ├── services/              # Focused canvas services (composition targets)
        │   ├── __init__.py
        │   ├── hit_test.py        # Hit testing (was HitTestMixin)
        │   ├── snap_service.py    # Snap resolution (was SnapGlueMixin)
        │   ├── layer_service.py   # Layer operations (was LayerMixin)
        │   ├── draw_ops.py        # Drawing operations (was DrawSidebarMixin)
        │   ├── smoothing.py       # Smoothing/simplify (was SmoothingMixin)
        │   ├── hud_text.py        # HUD text rendering (was HudMixin)
        │   ├── clipboard.py       # Clipboard operations (was ClipboardMixin)
        │   ├── grouping.py        # Grouping operations (was GroupingMixin)
        │   └── gizmo.py           # Gizmo/drag handles (was GizmoDragMixin)
        │
        └── mixins/                # DEPRECATED — migrate to services/
            └── editing_ops.py     # DEPRECATED — moved to backend/editing/
                                   #   Keep temporarily for compatibility
```

---

## 3. Key Architectural Changes

### 3.1 Commands Replace Direct Mutation

**Before (current):**

```python
# Direct mutation — no audit trail, no undo integration
self._document.entities = new_entities
self._document.selection = set()
```

**After (target):**

```python
# Command-based — reversible, auditable, testable
command = SplitCommand(
    cutter_start=(10.0, 20.0),
    cutter_end=(30.0, 40.0),
    entity_ids=("abc123", "def456"),
)
result = self._document_service.execute(command)
```

### 3.2 Composition Replaces Mixin Inheritance

**Before (current):**

```python
class PolylineView(
    QWidget,
    CanvasRenderer,
    EditingOperationsMixin,
    ClipboardMixin,
    GizmoDragMixin,
    TextOpsMixin,
    HudMixin,
    GroupingMixin,
    SmoothingMixin,
    LayerMixin,
    HitTestMixin,
    DrawSidebarMixin,
    SnapGlueMixin,  # 13 base classes — fragile MRO
):
```

**After (target):**

```python
class CanvasView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Compose focused services — explicit dependencies
        self._model = CanvasModel()
        self._renderer = CanvasRenderer(self._model)
        self._hit_test = HitTestService(self._model)
        self._snap = SnapService(self._model)
        self._commands = CommandStack(self._model)
        self._draw_sidebar = DrawSidebar(self._model)

        # Wire events → rendering
        self._model.events.geometry_changed.connect(self._renderer.render)
```

### 3.3 Domain Logic Moves to `backend/`

**Before (current):**

```python
# src/ui/canvas/mixins/editing_ops.py — imports Shapely directly
from shapely.geometry import LineString, Polygon
from shapely.ops import split as shapely_split
```

**After (target):**

```python
# src/backend/editing/split.py — pure, testable, no Qt
from shapely.geometry import LineString, Polygon
from shapely.ops import split as shapely_split

def split_entities(entities, cutter_start, cutter_end) -> list[Entity]:
    """Pure function: entities + cutter line → new entities."""
    ...
```

### 3.4 One Canonical Document Model

**Before (current):** Two models with fuzzy boundaries:

- `CanvasDocument` (backend/editor.py) — runtime canvas state
- `WorkspaceDocument` (backend/document.py) — persisted Pydantic schema

**After (target):** One canonical `Document` aggregate:

- `Document` (backend/document.py) — entities, layers, selection
- Commands transform `Document → new Document` (immutable)
- `WorkspaceDocument` (Pydantic) serializes/deserializes `Document` for persistence

### 3.5 Explicit Composition Replaces `__getattr__` Delegation

**Before (current):**

```python
def __getattr__(self, name: str):
    """Delegate extracted shell/command compatibility methods."""
    for key in ("_workspace_controller", "_menu_controller", "_command_controller"):
        controller = self.__dict__.get(key)
        if controller is not None and hasattr(type(controller), name):
            return getattr(controller, name)
    raise AttributeError(name)
```

**After (target):**

```python
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self._shell = ShellFacade(
            menu_controller=MenuController(self),
            workspace_controller=WorkspaceController(self),
            command_controller=CommandController(self),
        )

    # Explicit, type-checkable
    def menu_bar(self):
        return self._shell.menu_bar()
```

---

## 4. Migration Phases

### Phase 1: Extract Pure Domain Layer (Week 1–2)

**Goal:** Move Shapely-based editing from UI mixins to `backend/editing/`

| Task                                           | Files                                                     | Effort |
| ---------------------------------------------- | --------------------------------------------------------- | ------ |
| Create `backend/editing/` package              | `__init__.py`, `split.py`, `boolean.py`, `trim_extend.py` | Small  |
| Move Shapely split logic from `editing_ops.py` | `backend/editing/split.py`                                | Medium |
| Move boolean operations from `editing_ops.py`  | `backend/editing/boolean.py`                              | Medium |
| Move trim/extend from `editing_ops.py`         | `backend/editing/trim_extend.py`                          | Medium |
| Move offset from `editing_ops.py`              | `backend/editing/offset.py`                               | Small  |
| Move transform from `editing_ops.py`           | `backend/editing/transform.py`                            | Small  |
| Move merge/explode from `editing_ops.py`       | `backend/editing/merge_explode.py`                        | Small  |
| Move resample from `editing_ops.py`            | `backend/editing/resample.py`                             | Small  |
| Move smoothing from `editing_ops.py`           | `backend/editing/smoothing.py`                            | Small  |
| Write pure unit tests for each module          | `tests/test_editing_*.py`                                 | Medium |
| Update `editing_ops.py` to delegate to backend | `ui/canvas/mixins/editing_ops.py`                         | Small  |

**Success criteria:** All editing operations are testable with plain values. No Qt imports in `backend/editing/`.

### Phase 2: Introduce Command Types (Week 3–4)

**Goal:** Define immutable, serializable, reversible command types

| Task                                                       | Files                    | Effort |
| ---------------------------------------------------------- | ------------------------ | ------ |
| Define command base class                                  | `backend/commands.py`    | Small  |
| Define `SplitCommand`, `BooleanOpCommand`                  | `backend/commands.py`    | Small  |
| Define `TransformCommand`, `MoveEntityCommand`             | `backend/commands.py`    | Small  |
| Define `SelectCommand`, `CreateCommand`, `DeleteCommand`   | `backend/commands.py`    | Small  |
| Define `ResampleCommand`, `MergeCommand`, `ExplodeCommand` | `backend/commands.py`    | Small  |
| Make commands reversible (`.reverse()` method)             | `backend/commands.py`    | Medium |
| Make commands serializable (`.to_dict()`, `.from_dict()`)  | `backend/commands.py`    | Small  |
| Write unit tests for command reversibility                 | `tests/test_commands.py` | Medium |

**Success criteria:** Every command can be serialized, reversed, and tested without Qt.

### Phase 3: Replace Mixin Composition (Week 5–7)

**Goal:** Create focused services, compose them in CanvasView

| Task                                      | Files                                 | Effort |
| ----------------------------------------- | ------------------------------------- | ------ |
| Create `CanvasModel` (reactive wrapper)   | `ui/canvas/canvas_model.py`           | Large  |
| Create `HitTestService`                   | `ui/canvas/services/hit_test.py`      | Medium |
| Create `SnapService`                      | `ui/canvas/services/snap_service.py`  | Medium |
| Create `LayerService`                     | `ui/canvas/services/layer_service.py` | Medium |
| Create `DrawOpsService`                   | `ui/canvas/services/draw_ops.py`      | Medium |
| Create `SmoothingService`                 | `ui/canvas/services/smoothing.py`     | Small  |
| Create `HudTextService`                   | `ui/canvas/services/hud_text.py`      | Small  |
| Create `ClipboardService`                 | `ui/canvas/services/clipboard.py`     | Medium |
| Create `GroupingService`                  | `ui/canvas/services/grouping.py`      | Medium |
| Create `GizmoService`                     | `ui/canvas/services/gizmo.py`         | Medium |
| Create `CanvasRenderer` (standalone)      | `ui/canvas/rendering/renderer.py`     | Medium |
| Refactor `CanvasView` to compose services | `ui/canvas/view.py`                   | Large  |
| Update all mixin consumers                | Various                               | Medium |

**Success criteria:** `CanvasView` has no mixin inheritance beyond `QWidget`. Each service is independently testable.

### Phase 4: Centralize State Management (Week 8–10)

**Goal:** Create `DocumentService`, replace direct mutation

| Task                                              | Files                              | Effort |
| ------------------------------------------------- | ---------------------------------- | ------ |
| Create `DocumentService`                          | `app/services/document_service.py` | Large  |
| Implement `execute(command)`                      | `app/services/document_service.py` | Large  |
| Implement `undo()`, `redo()`                      | `app/services/document_service.py` | Medium |
| Create `DocumentEvents` signal bus                | `app/services/document_service.py` | Small  |
| Wire `DocumentEvents` → `CanvasView`              | `ui/canvas/view.py`                | Medium |
| Replace direct mutation in pages                  | `ui/pages/*.py`                    | Medium |
| Replace direct mutation in controllers            | `app/controllers/*.py`             | Medium |
| Update `editor_history.py` for command-based undo | `backend/editor_history.py`        | Medium |

**Success criteria:** All document changes flow through `DocumentService`. No direct mutation of `Document.entities`.

### Phase 5: Clean Up (Week 11–12)

**Goal:** Remove legacy patterns, consolidate models

| Task                                               | Files                 | Effort |
| -------------------------------------------------- | --------------------- | ------ |
| Remove `__getattr__` delegation from `App`         | `app/window.py`       | Medium |
| Create `ShellFacade` for explicit composition      | `app/facade.py`       | Medium |
| Consolidate `CanvasDocument` + `WorkspaceDocument` | `backend/document.py` | Large  |
| Remove deprecated mixins                           | `ui/canvas/mixins/`   | Small  |
| Update `ARCHITECTURE.md` to reflect target         | `ARCHITECTURE.md`     | Small  |
| Run full test suite, fix regressions               | `tests/`              | Medium |
| Update type stubs, mypy overrides                  | `pyproject.toml`      | Small  |

**Success criteria:** No `__getattr__` delegation. No deprecated mixins. One canonical `Document` model.

---

## 5. Risk Mitigation

| Risk                                             | Likelihood | Impact | Mitigation                                                    |
| ------------------------------------------------ | ---------- | ------ | ------------------------------------------------------------- |
| Breaking existing functionality during migration | High       | High   | Phase-by-phase migration with tests at each phase             |
| Performance regression from immutable documents  | Medium     | Medium | Use `frozenset` + structural sharing; benchmark at each phase |
| Mixin → service migration introduces bugs        | High       | Medium | Keep old mixins as thin wrappers during transition            |
| Two-document-model consolidation is complex      | Medium     | High   | Keep both models during Phases 1–4, consolidate in Phase 5    |
| Command serialization format drift               | Low        | Medium | Define schema version, add migration tests                    |

---

## 6. What "Done" Looks Like

- [ ] All editing operations are in `backend/editing/`, testable without Qt
- [ ] Every document change goes through a Command
- [ ] `CanvasView` composes services instead of inheriting 13 mixins
- [ ] `DocumentService` is the single point of document mutation
- [ ] `DocumentEvents` drives all UI updates
- [ ] No `__getattr__` delegation in `App` window
- [ ] One canonical `Document` model
- [ ] All 100+ existing tests pass
- [ ] New tests cover all command types and services
- [ ] `ARCHITECTURE.md` reflects target architecture
- [ ] Performance is within 5% of pre-refactor

---

## 7. References

- **Clean Architecture** — Robert C. Martin (Dependency Rule, inversion of dependencies)
- **Domain-Driven Design** — Eric Evans (Aggregate Roots, bounded contexts)
- **CQRS** — Greg Young (Command-Query Responsibility Segregation)
- **Command Pattern** — Gang of Four (encapsulate requests as objects)
- **Composition over Inheritance** — Alan Kay (favor object composition over class inheritance)
- **Figma Canvas Architecture** — Figma Engineering Blog (immutable document model, operation log)
- **Qt Architecture** — Qt Documentation (QGraphicsView composes QGraphicsScene, not inherits)

---

## 8. High-Impact Libraries to Add

These libraries address the biggest performance and correctness bottlenecks in the current codebase.
Add them incrementally — each is independent and can be adopted without blocking other phases.

### 8.1 `clipper2` — Polygon Boolean Operations & Offsetting

**Why:** Shapely's GEOS backend is slow for the repeated boolean/offset operations your pattern engine and editing tools do. Clipper2 is 5–50x faster because it's native C++ with no Python overhead per call.

**Where it replaces Shapely:**

| Current                                                                        | Replace With                                                         |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `src/backend/editing/boolean.py` — `unary_union`, `difference`, `intersection` | `clipper2.Clipper.Execute(CT_UNION, CT_DIFFERENCE, CT_INTERSECTION)` |
| `src/backend/editing/offset.py` — `Polygon.buffer()` for offsetting            | `clipper2.Clipper.Execute(CT_OFFSET)`                                |
| `src/backend/pattern/fill.py` — fill region computation                        | `clipper2.Clipper.Execute(CT_UNION)`                                 |
| `src/backend/pattern/topology.py` — nested polygon region building             | `clipper2.Clipper.Execute(CT_UNION)`                                 |

**Migration plan:**

| Task                                                                             | Phase   | Files                                   |
| -------------------------------------------------------------------------------- | ------- | --------------------------------------- |
| Add `clipper2` to dependencies                                                   | Phase 1 | `pyproject.toml`                        |
| Create `backend/editing/clipper_engine.py` — thin wrapper around Clipper2        | Phase 1 | `backend/editing/clipper_engine.py`     |
| Implement `clipper_union()`, `clipper_difference()`, `clipper_intersection()`    | Phase 1 | `backend/editing/clipper_engine.py`     |
| Implement `clipper_offset()` — CAD-grade offsetting (handles self-intersections) | Phase 1 | `backend/editing/clipper_engine.py`     |
| Write pure unit tests (no Shapely, no Qt)                                        | Phase 1 | `tests/test_clipper_engine.py`          |
| Replace Shapely calls in `editing/boolean.py` → `clipper_engine`                 | Phase 1 | `backend/editing/boolean.py`            |
| Replace Shapely calls in `editing/offset.py` → `clipper_engine`                  | Phase 1 | `backend/editing/offset.py`             |
| Replace Shapely calls in `pattern/fill.py` → `clipper_engine`                    | Phase 2 | `backend/pattern/fill.py`               |
| Replace Shapely calls in `pattern/topology.py` → `clipper_engine`                | Phase 2 | `backend/pattern/topology.py`           |
| Benchmark: measure speedup on pattern generation + editing operations            | Phase 2 | `benchmarks/test_clipper_benchmarks.py` |
| Remove Shapely imports from editing/pattern modules (keep for file I/O)          | Phase 3 | Various                                 |

**Expected impact:** 5–50x faster boolean operations and offsetting. Pattern generation becomes noticeably snappier. Editing operations (union, difference, offset) feel instant.

**Install:** `pip install clipper2`

---

### 8.2 `scipy.spatial` — Spatial Indexing & Voronoi/Delaunay

**Why:** You're hand-rolling spatial hashing for snapping and Voronoi generation. SciPy's C-backed implementations are faster, more correct, and handle edge cases you haven't considered.

**Where it replaces hand-rolled code:**

**a) KD-tree for snapping** (replaces manual grid hashing in `snapping.py`):

```python
# Before (manual grid hashing — O(n²) worst case)
buckets: dict[tuple[int, int], list[int]] = {}
for i, (x, y) in enumerate(endpoints):
    cell = (math.floor(x / tol), math.floor(y / tol))
    # ... manual bucket lookup

# After (KD-tree — O(log n) lookup)
from scipy.spatial import KDTree
tree = KDTree(points)
nearest = tree.query([(cx, cy)], k=1, distance_upper_bound=snap_dist)
```

**b) Voronoi/Delaunay** (replaces hand-rolled code in `organic.py`):

```python
# Before (hand-rolled Voronoi — fragile, slow, 100+ lines)
# Lines 100+ of manual Voronoi computation in organic.py

# After (SciPy — correct, fast, tested)
from scipy.spatial import Voronoi
vor = Voronoi(points)
```

**Migration plan:**

| Task                                                                    | Phase   | Files                                            |
| ----------------------------------------------------------------------- | ------- | ------------------------------------------------ | -------------------- |
| Create `backend/spatial.py` — KDTree wrapper for snapping               | Phase 1 | `backend/spatial.py`                             |
| Implement `build_snap_tree(points) → KDTree`                            | Phase 1 | `backend/spatial.py`                             |
| Implement `find_nearest(tree, query_point, max_dist) → Point            | None`   | Phase 1                                          | `backend/spatial.py` |
| Replace manual grid hashing in `snapping.py` → `spatial.KDTree`         | Phase 1 | `backend/snapping.py`                            |
| Create `backend/voronoi.py` — Voronoi/Delaunay utilities                | Phase 2 | `backend/voronoi.py`                             |
| Implement `voronoi_diagram(points) → list[Polygon]`                     | Phase 2 | `backend/voronoi.py`                             |
| Implement `delaunay_triangulation(points) → list[tuple[int, int, int]]` | Phase 2 | `backend/voronoi.py`                             |
| Replace hand-rolled Voronoi in `pattern/organic.py` → `voronoi` module  | Phase 2 | `backend/pattern/organic.py`                     |
| Write unit tests for KDTree snapping + Voronoi generation               | Phase 2 | `tests/test_spatial.py`, `tests/test_voronoi.py` |
| Benchmark: measure speedup on snapping + Voronoi generation             | Phase 2 | `benchmarks/test_spatial_benchmarks.py`          |

**Expected impact:** Snapping becomes O(log n) instead of O(n). Voronoi generation becomes correct and fast. Pattern generation (Voronoi-based patterns) becomes noticeably snappier.

**Install:** Already in your deps (`scipy>=1.10`) — just use `scipy.spatial` more.

---

### 8.3 `numba` — JIT-Compile Hot Geometry Paths

**Why:** Your geometry calculations (arc tessellation, curve fitting, snapping, pattern generation) are tight loops over Python lists. Numba can 10–100x speed these up with zero algorithm changes.

**Where it replaces pure Python loops:**

**a) Arc tessellation** (`geometry.py`):

```python
from numba import njit

@njit(parallel=True)
def _tessellate_arc_fast(center_x, center_y, radius, start_angle, end_angle, segments):
    """JIT-compiled arc tessellation — 50x faster than pure Python."""
    pts = np.empty((segments + 1, 2))
    for i in range(segments + 1):
        t = i / segments
        angle = start_angle + (end_angle - start_angle) * t
        pts[i, 0] = center_x + radius * math.cos(angle)
        pts[i, 1] = center_y + radius * math.sin(angle)
    return pts
```

**b) Snapping nearest-vertex search** (`snapping.py`):

```python
@njit
def _find_nearest_vertex_jit(cx, cy, polylines_flat, offsets, snap_dist):
    """JIT-compiled nearest vertex search — eliminates Python loop overhead."""
    best_dist = snap_dist
    best_idx = -1
    for i in range(len(offsets) - 1):
        x0 = polylines_flat[offsets[i]]
        y0 = polylines_flat[offsets[i] + 1]
        dx = cx - x0
        dy = cy - y0
        d = dx * dx + dy * dy
        if d < best_dist * best_dist:
            best_dist = d
            best_idx = i
    return best_idx
```

**c) Pattern generation loops** (`pattern/organic.py`, `pattern/tiling.py`):

```python
@njit(parallel=True)
def _generate_stipple_dots_jit(bounds, count, min_dist):
    """JIT-compiled stipple dot placement — parallel across dots."""
    ...
```

**Migration plan:**

| Task                                                                 | Phase   | Files                                 |
| -------------------------------------------------------------------- | ------- | ------------------------------------- |
| Add `numba` to dependencies                                          | Phase 1 | `pyproject.toml`                      |
| Create `backend/jit.py` — Numba configuration + utility decorators   | Phase 1 | `backend/jit.py`                      |
| JIT-compile arc tessellation in `geometry.py`                        | Phase 1 | `backend/geometry.py`                 |
| JIT-compile snapping nearest-vertex search in `snapping.py`          | Phase 1 | `backend/snapping.py`                 |
| JIT-compile curve fitting in `geometry.py`                           | Phase 1 | `backend/geometry.py`                 |
| JIT-compile pattern generation loops in `pattern/organic.py`         | Phase 2 | `backend/pattern/organic.py`          |
| JIT-compile pattern generation loops in `pattern/tiling.py`          | Phase 2 | `backend/pattern/tiling.py`           |
| JIT-compile path resampling in `path_ops.py`                         | Phase 2 | `backend/path_ops.py`                 |
| Write benchmarks for all JIT-compiled functions                      | Phase 2 | `benchmarks/test_numba_benchmarks.py` |
| Add cold-start mitigation (lazy initialization, pre-warm on startup) | Phase 2 | `backend/jit.py`                      |
| Document which functions are JIT-compiled and why                    | Phase 2 | `backend/jit.py`                      |

**Expected impact:** 10–100x speedup on hot geometry paths. Arc tessellation, snapping, and pattern generation become noticeably snappier. Cold-start penalty (~2–3 seconds for first Numba call) is mitigated by pre-warming on app startup.

**Caveat:** Numba has a cold-start penalty. Use `@njit` for functions called frequently, not one-shot operations. Pre-warm JIT-compiled functions on app startup.

**Install:** `pip install numba`

---

### 8.4 Summary: Installation & Migration Order

| Priority | Library                    | Install Command        | Phase | Files Added                                | Files Modified                                                                         |
| -------- | -------------------------- | ---------------------- | ----- | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| 1        | `clipper2`                 | `pip install clipper2` | 1–2   | `backend/editing/clipper_engine.py`        | `editing/boolean.py`, `editing/offset.py`, `pattern/fill.py`, `pattern/topology.py`    |
| 2        | `scipy.spatial` (use more) | Already installed      | 1–2   | `backend/spatial.py`, `backend/voronoi.py` | `snapping.py`, `pattern/organic.py`                                                    |
| 3        | `numba`                    | `pip install numba`    | 1–2   | `backend/jit.py`                           | `geometry.py`, `snapping.py`, `pattern/organic.py`, `pattern/tiling.py`, `path_ops.py` |

**Total new files:** ~6
**Total modified files:** ~10
**Estimated effort:** 2–3 weeks (can be done in parallel with Phases 1–2 of the main refactor)

**Risk:** Low. Each library is additive — you can use them alongside existing Shapely code during migration. Roll back any single change without affecting others.
