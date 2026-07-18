# Architecture and responsibility map

This is the project memory for where code belongs. Keep it synchronized with
structural changes. `REFACTOR.md` records the migration rationale and command /
composition design; this file is authoritative for the resulting module layout
and ownership.

## Dependency rule

Dependencies flow inward:

```text
main.py -> core -> app -> ui -> backend
                    \-------> backend
```

- `backend` is the Qt-free domain layer. It never imports `app`, `ui`, or
  PySide6.
- `ui` owns Qt widgets, rendering, and input adaptation. It requests changes
  through application services instead of mutating domain state.
- `app` composes windows and coordinates commands, events, workspaces, and
  background tasks. Algorithms do not belong here.
- `core` owns process-wide bootstrap and infrastructure.
- Avoid generic helper modules and speculative facades. A module must have one
  nameable responsibility.

## `src/core` — infrastructure

| Module | Responsibility |
| --- | --- |
| `launcher.py` | Logging, single-instance enforcement, Qt bootstrap, process lifecycle. |
| `paths.py` | User data, cache, runtime, and log locations. |
| `settings.py` | Defaults, validation, persistence, and the cross-window settings bus. |
| `updates.py` | Version detection, update checks, and downloads. |
| `error_reporting.py` | Exception reporting, optional remote reporting, and error notifications. |

Core contains no page behavior, canvas state, or domain catalogs.

## `src/backend` — domain

| Area | Responsibility |
| --- | --- |
| `model/document.py` | Canonical runtime `Document`, `EntityRecord`, operation results, and validated workspace serialization schema. |
| `model/commands.py` | Immutable, schema-versioned, serializable, reversible document commands, including topology, transforms, selection, and snapshot-based entity updates. |
| `model/editor_history.py` | Command undo/redo stack; it contains no UI-state snapshot history. |
| `cad/` | Geometry, shapes, construction, constraints, coordinates, recognition, snapping, preflight, primitives, and editor-geometry adaptation. |
| `editing/` | Pure split, boolean, trim/extend, offset, transform, merge/explode, resample, and smoothing operations. |
| `persistence.py` | Safe bounded reads and atomic writes, without dialogs. |
| `trace.py` | Image-to-outline processing and cancellation. |
| `dxf/` | DXF/FVI/SVG parsing, repair, validation, conversion, and writing. |
| `pattern/` | Pure pattern generation, fill, tiling, organic algorithms, output, presets, cancellation, and cohesive shared internals. |

Placement rule: code expressible with plain values and having geometric,
document, conversion, or persistence semantics belongs here.

## `src/app` — application orchestration

| Area | Responsibility |
| --- | --- |
| `window.py` | Construct the main window and explicitly wire controllers and pages. |
| `page_runtime.py` | Page registration and settings fan-out. |
| `workspace_session.py` | Collect/apply page sessions and recent workspace identity. |
| `controllers/menu.py` | Menus, shell header, shortcuts, palette, and active-canvas dispatch. |
| `controllers/workspace.py` | Workspace identity, open/save/recovery, and title state. |
| `controllers/tasks.py` | Autosave, update polling, and background task lifecycle. |
| `controllers/settings.py` | Apply settings and publish changes to windows/pages. |
| `services/document_service.py` | The document command boundary: validate, execute, undo/redo, and publish domain events. |
| `services/canvas_service.py` | Bridge `CanvasModel`, `CanvasView`, and `DocumentService` lifecycles. |

Controllers coordinate collaborators and expose explicit methods. They do not
use catch-all delegation or implement reusable widgets/domain algorithms.

## `src/ui` — presentation

| Area | Responsibility |
| --- | --- |
| `components.py` | Shared widget/layout factories and programmatic icons. |
| `util.py` | UI-only recent-file, dialog-location, and notification state. |
| `style/` | Theme loading, QSS, and icon assets. |
| `widgets/dialogs/` | Reusable application dialogs and file/configuration workflows. |
| `widgets/canvas/` | Toolbars, sidebars, status, precision, and properties controls surrounding the canvas. |
| `widgets/controls/` | Small reusable controls that are neither dialogs nor canvas subsystems. |
| `pages/` | Top-level workflows; pages own layout and delegate computation. |
| `canvas/` | Canvas presentation, Qt interaction, rendering, and page adapters. |

### Pages

- `tab.py` owns a page's layout, stateful presentation, and signal wiring.
- `session.py` adapts workspace/preset state.
- `form.py`, `form_spec.py`, `params.py`, and `defaults.py` own declarative form
  construction, specifications, collection, and defaults.
- `workers.py` owns Qt/background-thread entry points and cancellation wrappers;
  the algorithms remain in `backend`.
- Pattern zones, exclusions, generation scheduling, and preview state are
  presentation concerns of `PatternPage`; they never contain generation
  algorithms.

### Canvas

- `view.py` owns `CanvasView`: QWidget layout, shared presentation state,
  signal wiring, command dispatch, and event subscription. It composes services
  and does not inherit behavior mixins.
- `canvas_model.py` is the thin reactive `Document`-to-Qt adapter.
- `rendering/renderer.py` performs canvas painting.
- `interaction/commands.py`, `tools.py`, and `select.py` own command metadata,
  mode-specific input strategies, radial interaction, and selection behavior.
- `services/` contains focused editing, hit-test, snap, layer, drawing,
  smoothing, HUD, clipboard, grouping, and gizmo collaborators. The editing
  service adapts UI actions to pure backend operations and command dispatch.
- `canvas_runtime.py` is the narrow page-facing canvas adapter.
- `dxf_canvas.py` adds vector-file canvas presentation; parsing stays in
  `backend/dxf`.

## Mutation and event flow

```text
Qt input -> CanvasView -> Command -> DocumentService -> new Document
                ^                         |
                |---- DocumentEvent ------|
```

Commands use stable entity IDs. `DocumentService` is the mutation boundary and
records reversible command pairs. Focused entity edits use immutable entity
snapshots; aggregate changes such as layer operations use serializable document
snapshots. `CanvasService` publishes replacement documents through
`CanvasModel`, and rendering reacts to model events. Interactive drags mutate a
transient preview and commit or cancel it with an immutable document snapshot;
there is no second UI-owned undo store. `EditingService` delegates geometric
work to backend operations and routes resulting changes through command
services.

## Change checklist

1. Choose the owning responsibility above; do not add `helpers`, `common`, or
   generic `utils` modules.
2. Preserve the Qt-free backend boundary and inward dependency direction.
3. Route document changes through commands and `DocumentService`.
4. Prefer a cohesive existing module over a one-function file.
5. Compose collaborators explicitly; do not add mixin inheritance or
   `__getattr__` delegation.
6. Update this memory when ownership changes.
7. Run Ruff, formatting, compilation, import-boundary checks, and the relevant
   verification requested for the change.
8. Keep the roots of `ui/`, `backend/`, and `ui/widgets/` at four Python files
   or fewer, including `__init__.py`; add files to an existing responsibility
   package or create a cohesive multi-file package.
